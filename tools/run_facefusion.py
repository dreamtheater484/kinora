"""Run FaceFusion with CUDA DLLs preloaded and, unless VU_ONLINE=1, with all network access disabled.

FaceFusion pings GitHub/Hugging Face on every start to resolve model URLs, even when the
models are already on disk. In offline mode those pings are replaced by no-ops, so it only
uses the models that download_models.py downloaded.
"""
import os
import runpy
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
# runs on FaceFusion's own Python, tools/facefusion/.venv in the data folder: the checkout is the folder around it
FACEFUSION_DIR = os.path.dirname(sys.prefix)

sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.stderr.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.path.insert(0, TOOLS_DIR)
import offline_guard  # noqa: E402

os.chdir(FACEFUSION_DIR)
sys.path.insert(0, FACEFUSION_DIR)

import onnxruntime  # noqa: E402

onnxruntime.preload_dlls()

if not offline_guard.ONLINE:
	import facefusion.download as download

	def blocked_curl(commands):
		raise RuntimeError('offline mode: FaceFusion tried to download a missing model, run download_models.py first')

	download.ping_static_url = lambda url: False
	download.get_static_download_size = lambda url: 0
	download.open_curl = blocked_curl

sys.argv = [ os.path.join(FACEFUSION_DIR, 'facefusion.py') ] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name = '__main__')
