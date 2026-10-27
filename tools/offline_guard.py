"""Refuse every non-loopback network connection made from this Python process.

Imported first by the tool launchers. Set VU_ONLINE=1 to disable the guard
(only needed for the one-time model download in download_models.py).
"""
import ipaddress
import os
import socket

ONLINE = os.environ.get('VU_ONLINE') == '1'

_LOCAL_NAMES = { 'localhost', '' }


def _is_local_host(host) -> bool:
	if isinstance(host, bytes):
		host = host.decode(errors = 'ignore')
	host = str(host).strip('[]').split('%')[0]
	if host.lower() in _LOCAL_NAMES:
		return True
	try:
		return ipaddress.ip_address(host).is_loopback
	except ValueError:
		return False


def _check(address) -> None:
	host = address[0] if isinstance(address, tuple) else address
	if not _is_local_host(host):
		raise ConnectionRefusedError('offline mode: blocked network access to ' + repr(host))


def install() -> None:
	if ONLINE:
		return
	original_connect = socket.socket.connect
	original_connect_ex = socket.socket.connect_ex
	original_getaddrinfo = socket.getaddrinfo

	def guarded_connect(self, address):
		if self.family in (socket.AF_INET, socket.AF_INET6):
			_check(address)
		return original_connect(self, address)

	def guarded_connect_ex(self, address):
		if self.family in (socket.AF_INET, socket.AF_INET6):
			_check(address)
		return original_connect_ex(self, address)

	def guarded_getaddrinfo(host, *args, **kwargs):
		if host is not None:
			_check((host,))
		return original_getaddrinfo(host, *args, **kwargs)

	socket.socket.connect = guarded_connect
	socket.socket.connect_ex = guarded_connect_ex
	socket.getaddrinfo = guarded_getaddrinfo
	os.environ.update(
	{
		'HF_HUB_OFFLINE': '1',
		'HF_HUB_DISABLE_TELEMETRY': '1',
		'TRANSFORMERS_OFFLINE': '1',
		'DIFFUSERS_OFFLINE': '1',
		'GRADIO_ANALYTICS_ENABLED': 'False',
		'DO_NOT_TRACK': '1'
	})


install()
