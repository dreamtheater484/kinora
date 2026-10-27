"""Local front end for upscale.py: a queue of videos, processed one by one, optionally merged;
and a video editor (the Edit tab, editor.py) for trimming, joining and speeding up videos.

Serves ui/index.html on http://127.0.0.1:8766 (this PC only, not reachable from the network)
and opens it in the default browser. The page loads nothing from the internet.
The queue, the settings and the edits are kept in work/_ui/, so they survive a restart.
"""
import base64
import collections
import datetime
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
import offline_guard  # noqa: E402,F401
import upscale  # noqa: E402
import merge  # noqa: E402
import shrink  # noqa: E402
import editor  # noqa: E402

HOST = '127.0.0.1'
PORT = 8766
UI_FILE = ROOT / 'ui' / 'index.html'
INPUT_DIR = ROOT / 'input'
PREVIEW_DIR = ROOT / 'work' / '_preview'
OUTPUT_DIR = ROOT / 'output'
REFS_DIR = ROOT / 'refs'
UI_DIR = ROOT / 'work' / '_ui'
QUEUE_FILE = UI_DIR / 'queue.json'
JOB_LOG = UI_DIR / 'job.log'
DEFAULT_SETTINGS =\
{
	'person': None, 'height': 1080, 'fps': None, 'smooth': True, 'motion': 'best', 'denoise': 'off', 'gamma': 1.0,
	'model': '7b', 'batch': 33, 'identity': 0.5, 'enhance': 50, 'face_enhancer': 'gfpgan_1.4', 'expression': False,
	'compare': True, 'audio': True, 'merge': False, 'merge_name': '', 'size': 'original', 'format': 'av1',
	# "All videos" crop: every video that is not made yet gets shared_crop ({ crop, width, height } of the video it was set on)
	'crop_all': False, 'shared_crop': None
}
MASTERS_DIR = ROOT / 'work' / '_masters'
EDIT_WORK = ROOT / 'work' / '_edit'
# an edit saved as a file of its own (JSON), to open again later or on another PC; made in projects\ unless chosen otherwise
PROJECTS_DIR = ROOT / 'projects'
PROJECT_EXTENSION = '.vedit'
PROJECT_FORMAT = 'video-upscaler edit'
SHRINK_FILE = UI_DIR / 'shrinks.json'
# the Brightness slider in ui/index.html (0.7 – 1.8 in steps of 0.05); 1.0 is the original picture
GAMMA_STEPS = [ round(0.7 + 0.05 * step, 2) for step in range(23) ]
FILE_LOCKS = {}
PICKER_SCRIPT = '''
import sys, tkinter, tkinter.filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes('-topmost', True)
if sys.argv[1] == 'folder':
	print(tkinter.filedialog.askdirectory(parent = root, title = 'Choose a folder with videos', initialdir = sys.argv[2]))
elif sys.argv[1] == 'project-open':
	chosen = tkinter.filedialog.askopenfilename(parent = root, title = 'Open a video edit project', initialdir = sys.argv[2],
		filetypes = [ ('Video edit projects', '*.vedit'), ('All files', '*.*') ])
	print(chosen if isinstance(chosen, str) else '')
elif sys.argv[1] == 'project-save':
	chosen = tkinter.filedialog.asksaveasfilename(parent = root, title = 'Save the edit as a project file', initialdir = sys.argv[2], initialfile = sys.argv[3],
		defaultextension = '.vedit', filetypes = [ ('Video edit projects', '*.vedit') ])
	print(chosen if isinstance(chosen, str) else '')
else:
	paths = tkinter.filedialog.askopenfilenames(parent = root, title = 'Choose videos', initialdir = sys.argv[2],
		filetypes = [ ('Videos', ' '.join('*' + ext for ext in sys.argv[3].split())), ('All files', '*.*') ])
	print('\\n'.join(paths))
'''


def now() -> float:
	return time.time()


# ---------------------------------------------------------------- helpers

def list_refs() -> list:
	people = []
	if REFS_DIR.is_dir():
		for folder in sorted(REFS_DIR.iterdir()):
			if folder.is_dir():
				count = sum(1 for path in folder.iterdir() if path.suffix.lower() in upscale.REF_EXTENSIONS)
				if count:
					people.append({ 'name': folder.name, 'photos': count })
	return people


def list_models() -> list:
	return [ key for key, file_name in upscale.SEEDVR2_MODELS.items() if (upscale.SEEDVR2_MODEL_DIR / file_name).is_file() ]


def list_results() -> list:
	if not OUTPUT_DIR.is_dir():
		return []
	videos = sorted((path for path in OUTPUT_DIR.glob('*.mp4') if not path.stem.endswith('.part')), key = lambda path: path.stat().st_mtime, reverse = True)
	results = []
	for video in videos[:30]:
		compare = OUTPUT_DIR / 'compare' / (video.stem + '_compare.mp4')
		merged = video.stem in RUNNER.merged_names or any(video.stem.startswith(name + '_') and re.search(r'_(near|balanced|small)-(av1|h265|h264)$', video.stem) for name in RUNNER.merged_names)
		edited = bool(EDITOR) and str(video.resolve()) in EDITOR.exported
		results.append(annotate({ 'name': video.name, 'video': str(video), 'compare': str(compare) if compare.is_file() else None, 'time': video.stat().st_mtime, 'size': video.stat().st_size, 'merged': merged, 'edited': edited }))
	return results


def ask_path(mode : str, folder : Path, name : str = '') -> str:
	"""A Windows dialog to open or save a project file (mode 'project-open' or 'project-save'); '' when cancelled."""
	result = subprocess.run([ sys.executable, '-c', PICKER_SCRIPT, mode, str(folder), name ], capture_output = True, text = True)
	return result.stdout.strip()


def find_videos(folder : Path) -> list:
	return sorted((path for path in folder.rglob('*') if path.is_file() and path.suffix.lower() in upscale.VIDEO_EXTENSIONS), key = lambda path: str(path).lower())


def file_lock(path : Path) -> threading.Lock:
	"""One writer per generated file: a second request for the same preview waits instead of running ffmpeg into the same file."""
	return FILE_LOCKS.setdefault(str(path), threading.Lock())


def replace_file(source : Path, target : Path) -> None:
	"""Path.replace that waits out short locks on Windows (a virus scanner or the indexer reading a fresh file)."""
	for attempt in range(20):
		try:
			return source.replace(target)
		except PermissionError:
			if attempt == 19:
				raise
			time.sleep(0.1)


def preview_folder(path : Path) -> Path:
	stat = path.stat()
	key = hashlib.sha1('{}|{}|{}'.format(path.resolve(), stat.st_mtime, stat.st_size).encode()).hexdigest()[:16]
	folder = PREVIEW_DIR / key
	folder.mkdir(parents = True, exist_ok = True)
	return folder


def make_thumbnail(path : Path, duration : float) -> Path:
	thumb = preview_folder(path) / 'thumb.jpg'
	if not thumb.is_file():
		subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', '{:.2f}'.format(min(duration * 0.15, 5)), '-i', str(path), '-frames:v', '1', '-vf', 'scale=192:-2', '-q:v', '4', str(thumb) ], check = False)
	return thumb


def make_strip(path : Path, info : dict) -> Path:
	"""30 small pictures spread over the whole video, side by side in one image (the timelines of both tabs).
	Long videos are read at their keyframes only, which is many times quicker and still enough pictures."""
	strip = preview_folder(path) / 'strip.jpg'
	with file_lock(strip):
		if not strip.is_file():
			keyframes = [ '-skip_frame', 'nokey' ] if info['duration'] >= 120 else []
			subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *keyframes, '-i', str(path), '-vf', 'fps={:.6f},scale=-2:72,tile=30x1'.format(30 / max(info['duration'], 0.1)), '-frames:v', '1', '-q:v', '4', str(strip) ], check = False)
	return strip if strip.is_file() else None


def make_brightness_frames(path : Path, info : dict, seconds : float) -> dict:
	"""The picture at one moment at every Brightness setting, stacked top to bottom, so the UI can switch between them
	instantly. Uses the same eq filter as upscale.py's prep step, so the adjusted picture is exactly what the video gets."""
	folder = preview_folder(path)
	seconds = min(max(seconds, 0.0), max(info['duration'] - 1 / max(info['fps'], 1), 0.0))
	sprite = folder / 'brightness_{}.jpg'.format(int(round(seconds * 1000)))
	with file_lock(sprite):
		if not sprite.is_file():
			width = min(info['width'], 960) // 2 * 2
			branches = [ '[s{0}]{1}format=yuv420p,scale={2}:-2[o{0}]'.format(index, 'eq=gamma={},'.format(gamma) if gamma != 1.0 else '', width) for index, gamma in enumerate(GAMMA_STEPS) ]
			graph = '[0:v]split={}{};{};{}vstack=inputs={}'.format(len(GAMMA_STEPS), ''.join('[s{}]'.format(index) for index in range(len(GAMMA_STEPS))),
				';'.join(branches), ''.join('[o{}]'.format(index) for index in range(len(GAMMA_STEPS))), len(GAMMA_STEPS))
			temp = folder / ('tmp_' + sprite.name)
			temp.unlink(missing_ok = True)
			subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', '{:.3f}'.format(seconds), '-i', str(path), '-filter_complex', graph, '-frames:v', '1', '-q:v', '2', str(temp) ], capture_output = True)
			if not temp.is_file() or not temp.stat().st_size:
				raise ValueError('there is no picture at {}'.format(upscale.format_clock(seconds)))
			replace_file(temp, sprite)
			# keep the last few moments per video
			try:
				for old in sorted(folder.glob('brightness_*.jpg'), key = lambda file: file.stat().st_mtime, reverse = True)[6:]:
					old.unlink()
			except OSError:
				pass
	return { 'image': str(sprite), 'gammas': GAMMA_STEPS, 'time': seconds }


def detect_picture_area(path : Path) -> dict:
	"""The part of the frame that changes over time: drops black borders and static window frames
	(e.g. the webcam image inside a screen recording). A suggestion the user can adjust."""
	info = upscale.probe(path)
	width, height = info['width'], info['height']
	scale = min(1.0, 480 / width)
	small_width, small_height = max(int(width * scale) // 2 * 2, 2), max(int(height * scale) // 2 * 2, 2)
	rate = min(2.0, 600 / max(info['duration'], 1))
	raw = subprocess.run([ 'ffmpeg', '-v', 'error', '-i', str(path), '-vf', 'fps={:.4f},scale={}:{},format=gray'.format(rate, small_width, small_height), '-f', 'rawvideo', '-' ], capture_output = True).stdout
	frames = upscale.numpy.frombuffer(raw, dtype = upscale.numpy.uint8).reshape(-1, small_height, small_width).astype(upscale.numpy.float32)
	if len(frames) < 3:
		raise ValueError('video too short to analyse')
	changing = frames.std(axis = 0) > 4
	rows = upscale.numpy.where(changing.mean(axis = 1) > 0.4)[0]
	columns = upscale.numpy.where(changing.mean(axis = 0) > 0.4)[0]
	if not len(rows) or not len(columns):
		return { 'x': 0, 'y': 0, 'w': width, 'h': height }
	x0, x1 = columns.min() / small_width * width, (columns.max() + 1) / small_width * width
	y0, y1 = rows.min() / small_height * height, (rows.max() + 1) / small_height * height
	# round inwards to even numbers, so no line of the static frame stays visible
	x, y = int(math.ceil(x0 / 2)) * 2, int(math.ceil(y0 / 2)) * 2
	right, bottom = min(int(x1 // 2) * 2, width), min(int(y1 // 2) * 2, height)
	return { 'x': x, 'y': y, 'w': right - x, 'h': bottom - y }


def fit_crop(crop : str, source : tuple, target : tuple) -> str:
	"""A crop chosen on a video of size source, for a video of size target: the same pixels when the size is the same,
	otherwise the same part of the picture (rounded to even numbers like the crop editor)."""
	if crop in ('auto', 'off') or tuple(source) == tuple(target):
		return crop
	w, h, x, y = (int(part) for part in crop.split(':'))
	scale_x, scale_y = target[0] / source[0], target[1] / source[1]
	x = min(int(round(x * scale_x / 2)) * 2, (target[0] - 2) // 2 * 2)
	y = min(int(round(y * scale_y / 2)) * 2, (target[1] - 2) // 2 * 2)
	w = max(min(int(round(w * scale_x / 2)) * 2, (target[0] - x) // 2 * 2), 2)
	h = max(min(int(round(h * scale_y / 2)) * 2, (target[1] - y) // 2 * 2), 2)
	return '{}:{}:{}:{}'.format(w, h, x, y)


def build_command(settings : dict, item : dict) -> list:
	command = [ sys.executable, ROOT / 'upscale.py', item['path'], '--json-progress',
		'--start', '{:.3f}'.format(float(item['start'])), '--end', '{:.3f}'.format(float(item['end'])),
		'--height', int(settings.get('height') or 1080),
		'--model', settings.get('model') or '7b',
		'--batch', int(settings.get('batch') or 33),
		'--denoise', settings.get('denoise') or 'off',
		'--gamma', float(settings.get('gamma') or 1.0),
		'--motion', 'fast' if settings.get('motion') == 'fast' else 'best' ]
	if item.get('worked'):
		command += [ '--elapsed', '{:.0f}'.format(item['worked']) ]
	if settings.get('fps'):
		command += [ '--fps', float(settings['fps']) ]
	if not settings.get('smooth', True):
		command.append('--no-smooth')
	if not settings.get('compare', True):
		command.append('--no-compare')
	if not settings.get('audio', True):
		command.append('--no-audio')
	size = settings.get('size') or 'original'
	if size != 'original':
		if size not in shrink.QUALITIES or settings.get('format') not in shrink.FORMATS:
			raise ValueError('invalid file size setting')
		command += [ '--size', size, '--format', settings['format'] ]
		if settings.get('merge'):
			command += [ '--keep-master', MASTERS_DIR / (item['id'] + '.mp4') ]
	crop = str(item.get('crop') or 'auto')
	if crop not in ('auto', 'off') and not re.fullmatch(r'\d+:\d+:\d+:\d+', crop):
		raise ValueError('invalid crop: ' + crop)
	command += [ '--crop', crop ]
	if settings.get('person'):
		person_dir = (REFS_DIR / settings['person']).resolve()
		if person_dir.parent != REFS_DIR.resolve() or not person_dir.is_dir():
			raise ValueError('unknown person: ' + str(settings['person']))
		command += [ '--refs', person_dir,
			'--identity', float(settings.get('identity') or 0.5),
			'--enhance', int(settings.get('enhance') if settings.get('enhance') is not None else 50),
			'--face-enhancer', settings.get('face_enhancer') or 'gfpgan_1.4' ]
		if settings.get('expression'):
			command.append('--expression')
	return [ str(part) for part in command ]


def safe_name(name : str) -> str:
	return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'video'


def is_servable(path : Path) -> bool:
	path = path.resolve()
	return (any(path.is_relative_to(folder.resolve()) for folder in (OUTPUT_DIR, PREVIEW_DIR, REFS_DIR)) or bool(EDITOR) and str(path) in EDITOR.allowed) and path.is_file()


# ---------------------------------------------------------------- queue and runner

class Runner:
	"""The queue of videos, the settings for all of them, and the background run through them."""

	def __init__(self) -> None:
		self.lock = threading.RLock()
		self.items = []
		self.settings = dict(DEFAULT_SETTINGS)
		self.merged_names = []
		self.thread = None
		self.process = None
		self.state = None          # progress of the item being processed (upscale.py --json-progress)
		self.stop_requested = False
		self.pause_requested = False
		self.batch = None          # { started, finished, ids, merge, stopped, paused_at, paused_total }
		self.log = collections.deque(maxlen = 400)
		self.thumb_queue = collections.deque()
		self.thumb_event = threading.Event()
		self.load()
		threading.Thread(target = self.thumbnail_worker, daemon = True).start()

	# persistence
	def load(self) -> None:
		try:
			saved = json.loads(QUEUE_FILE.read_text(encoding = 'utf-8'))
		except (OSError, ValueError):
			saved = {}
		self.settings.update(saved.get('settings', {}))
		self.merged_names = saved.get('merged_names', [])
		for item in saved.get('items', []):
			if item['status'] == 'running':
				# the app was closed while this video was being made: it continues where it was
				item['status'] = 'paused'
			if Path(item['path']).is_file():
				self.items.append(item)
		if self.settings.get('person') is None:
			people = list_refs()
			self.settings['person'] = people[0]['name'] if len(people) == 1 else ''

	def save(self) -> None:
		UI_DIR.mkdir(parents = True, exist_ok = True)
		# one save at a time: the web requests, the runner and the thumbnail worker all save, and on Windows
		# a second writer can neither open nor rename queue.tmp while the first one is busy with it
		with self.lock:
			data = json.dumps({ 'settings': self.settings, 'items': self.items, 'merged_names': self.merged_names }, indent = 1)
			temp = QUEUE_FILE.with_suffix('.tmp')
			temp.write_text(data, encoding = 'utf-8')
			replace_file(temp, QUEUE_FILE)

	# queue editing
	def add(self, paths : list) -> dict:
		added, skipped = [], []
		for raw in paths:
			path = Path(raw)
			if not path.is_file() or path.suffix.lower() not in upscale.VIDEO_EXTENSIONS:
				skipped.append({ 'path': str(path), 'reason': 'not a video file' })
				continue
			try:
				info = upscale.probe(path)
			except Exception:
				skipped.append({ 'path': str(path), 'reason': 'cannot be read' })
				continue
			if info['duration'] <= 0:
				skipped.append({ 'path': str(path), 'reason': 'has no length' })
				continue
			item =\
			{
				'id': uuid.uuid4().hex[:10], 'path': str(path.resolve()), 'name': path.name,
				'width': info['width'], 'height': info['height'], 'fps': float(info['fps']), 'duration': info['duration'], 'has_audio': info['has_audio'],
				'start': 0.0, 'end': info['duration'], 'crop': 'auto',
				'status': 'queued', 'output': None, 'compare': None, 'message': None, 'thumb': None,
				'added': now(), 'started': None, 'finished': None, 'estimate': None
			}
			with self.lock:
				if self.settings.get('crop_all'):
					item['crop'] = self.shared_crop(item)
				self.items.append(item)
			self.thumb_queue.append(item['id'])
			added.append(item['id'])
		self.thumb_event.set()
		self.save()
		return { 'added': added, 'skipped': skipped }

	def find(self, item_id : str) -> dict:
		item = next((item for item in self.items if item['id'] == item_id), None)
		if not item:
			raise ValueError('unknown queue item')
		return item

	def update(self, item_id : str, changes : dict) -> None:
		with self.lock:
			item = self.find(item_id)
			if item['status'] == 'running':
				raise ValueError('this video is being processed right now')
			if 'start' in changes or 'end' in changes:
				start = max(0.0, float(changes.get('start', item['start'])))
				end = min(item['duration'], float(changes.get('end', item['end'])))
				if end - start < 0.2:
					raise ValueError('the section is too short')
				item['start'], item['end'] = start, end
			if 'crop' in changes:
				crop = str(changes['crop'])
				if crop not in ('auto', 'off') and not re.fullmatch(r'\d+:\d+:\d+:\d+', crop):
					raise ValueError('invalid crop')
				item['crop'] = crop
				if self.settings.get('crop_all'):
					self.share_crop(item)
			if item['status'] in ('done', 'error', 'cancelled', 'paused'):
				self.back_in_queue(item)
		self.save()

	# one crop for all videos
	def shared_crop(self, item : dict) -> str:
		"""The "All videos" crop, fitted to the size of this video."""
		shared = self.settings.get('shared_crop') or { 'crop': 'auto', 'width': item['width'], 'height': item['height'] }
		return fit_crop(shared['crop'], (shared['width'], shared['height']), (item['width'], item['height']))

	def share_crop(self, source : dict) -> int:
		"""Gives every video that is not made yet the crop of this one; finished videos keep the crop they were made with.
		Call with the lock held."""
		self.settings['shared_crop'] = { 'crop': source['crop'], 'width': source['width'], 'height': source['height'] }
		targets = [ item for item in self.items if item is not source and item['status'] not in ('running', 'done') ]
		for item in targets:
			item['crop'] = self.shared_crop(item)
		return len(targets)

	def set_crop_all(self, item_id : str, on : bool) -> int:
		"""On: this video's crop is used for every video from now on. Off: each video keeps the crop it has and is edited on its own."""
		with self.lock:
			source = self.find(item_id)
			self.settings['crop_all'] = on
			count = self.share_crop(source) if on else 0
		self.save()
		return count

	def back_in_queue(self, item : dict) -> None:
		item.update(status = 'queued', message = None, resume = False, worked = 0.0)
		if self.settings.get('crop_all'):
			item['crop'] = self.shared_crop(item)

	def remove(self, item_id : str) -> None:
		with self.lock:
			item = self.find(item_id)
			if item['status'] == 'running':
				raise ValueError('stop the queue before removing the video that is being processed')
			self.items.remove(item)
		self.save()

	def move(self, item_id : str, delta : int) -> None:
		with self.lock:
			item = self.find(item_id)
			index = self.items.index(item)
			target = min(max(index + delta, 0), len(self.items) - 1)
			self.items.insert(target, self.items.pop(index))
		self.save()

	def requeue(self, item_id : str) -> None:
		with self.lock:
			item = self.find(item_id)
			if item['status'] != 'running':
				self.back_in_queue(item)
		self.save()

	def clear(self, which : str) -> None:
		with self.lock:
			if which == 'all':
				self.items = [ item for item in self.items if item['status'] == 'running' ]
			else:
				self.items = [ item for item in self.items if item['status'] not in ('done', 'cancelled', 'error') ]
		self.save()

	def set_settings(self, settings : dict) -> None:
		with self.lock:
			for key in DEFAULT_SETTINGS:
				if key in settings:
					self.settings[key] = settings[key]
		self.save()

	# thumbnails
	def thumbnail_worker(self) -> None:
		while True:
			self.thumb_event.wait()
			self.thumb_event.clear()
			while self.thumb_queue:
				item_id = self.thumb_queue.popleft()
				item = next((item for item in self.items if item['id'] == item_id), None)
				if item and not item.get('thumb'):
					thumb = make_thumbnail(Path(item['path']), item['duration'])
					if thumb.is_file():
						item['thumb'] = str(thumb)
			try:
				self.save()
			except OSError as error:
				# keep the worker alive: the next save writes the thumbnails too
				print('Could not save the queue: {}'.format(error))

	# running
	def running(self) -> bool:
		return self.thread is not None and self.thread.is_alive()

	def paused(self) -> bool:
		"""Paused by pause() (or by closing the app): nothing runs, and start() continues where it was."""
		return not self.running() and (any(item['status'] == 'paused' for item in self.items)
			or bool(self.batch and self.batch.get('paused_at') and not self.batch['finished']))

	def start(self) -> None:
		"""Starts the queue, or continues a paused one."""
		with self.lock:
			if self.running():
				raise RuntimeError('the queue is already running')
			resuming = bool(self.batch and self.batch.get('paused_at') and not self.batch['finished'])
			for item in self.items:
				if item['status'] == 'paused':
					item.update(status = 'queued', resume = True)
			if not any(item['status'] == 'queued' for item in self.items):
				if resuming:
					self.batch.update(finished = self.batch['paused_at'], stopped = True)
				raise RuntimeError('there is nothing queued')
			self.stop_requested = self.pause_requested = False
			if resuming:
				self.batch['paused_total'] += now() - self.batch['paused_at']
				self.batch['paused_at'] = None
			else:
				self.state = None
				self.batch = { 'started': now(), 'finished': None, 'ids': [], 'merge': None, 'stopped': False, 'paused_at': None, 'paused_total': 0.0 }
			self.thread = threading.Thread(target = self.run, daemon = True)
			self.thread.start()

	def pause(self) -> None:
		"""Stops the video that is being made right now, so the GPU is free. start() continues it: upscale.py keeps every
		finished step and every upscaled part, so only the step or part in progress is done again."""
		with self.lock:
			if not self.running():
				raise RuntimeError('the queue is not running')
			if self.batch.get('merge') and self.batch['merge']['status'] == 'running':
				raise RuntimeError('the merged video is being made; that cannot be paused')
			self.pause_requested = True
			self.kill_process()

	def stop(self) -> None:
		with self.lock:
			self.stop_requested = True
			if not self.running():
				# a paused queue: the paused video counts as stopped
				for item in self.items:
					if item['status'] == 'paused':
						item.update(status = 'cancelled', message = 'stopped', finished = now())
				if self.batch and not self.batch['finished']:
					self.batch.update(finished = now(), stopped = True)
			self.kill_process()
		self.save()

	def shutdown(self) -> None:
		"""The app is closing: like a pause, so the video continues where it was after the next start."""
		with self.lock:
			self.pause_requested = True
			self.kill_process()

	def kill_process(self) -> None:
		if self.process and self.process.poll() is None:
			subprocess.run([ 'taskkill', '/PID', str(self.process.pid), '/T', '/F' ], capture_output = True)

	def run(self) -> None:
		paused = False
		try:
			while not self.stop_requested and not self.pause_requested:
				with self.lock:
					item = next((item for item in self.items if item['status'] == 'queued'), None)
					if not item:
						break
					resumed = bool(item.pop('resume', False))
					item.update(status = 'running', finished = None, message = None, output = None, compare = None, run_started = now())
					if not resumed:
						item.update(started = now(), worked = 0.0)
					if item['id'] not in self.batch['ids']:
						self.batch['ids'].append(item['id'])
					settings = dict(self.settings)
				self.save()
				self.process_item(item, settings, resumed)
				self.save()
			with self.lock:
				# paused with videos left: no merge yet (a pause after the last video does not hold up the merge)
				paused = self.pause_requested and not self.stop_requested and any(item['status'] in ('queued', 'paused') for item in self.items)
				merge_wanted = self.settings.get('merge') and not self.stop_requested and not paused
				parts = [ item for item in self.items if item['status'] == 'done' and item.get('output') and Path(item['output']).is_file() ]
			if merge_wanted and parts:
				self.merge(parts)
		finally:
			with self.lock:
				if paused:
					self.batch['paused_at'] = now()
				else:
					self.batch['finished'] = now()
					self.batch['stopped'] = self.stop_requested
			self.save()

	def process_item(self, item : dict, settings : dict, resumed : bool) -> None:
		try:
			command = build_command(settings, item)
		except ValueError as error:
			item.update(status = 'error', message = str(error), finished = now())
			return
		UI_DIR.mkdir(parents = True, exist_ok = True)
		if not resumed:
			self.log.clear()
		self.state = { 'status': 'running', 'phase_label': 'Continuing' if resumed else 'Starting', 'percent': 0, 'started': now(), 'time': now(), 'elapsed': item.get('worked', 0.0), 'remaining': None, 'phases': [] }
		with self.lock:
			# not when a pause or stop came in while this video was being set up
			self.process = None if self.stop_requested or self.pause_requested else\
				subprocess.Popen(command, stdout = subprocess.PIPE, stderr = subprocess.STDOUT, cwd = ROOT, env = dict(os.environ, PYTHONUNBUFFERED = '1'))
		code = None
		if self.process:
			with open(JOB_LOG, 'a' if resumed else 'w', encoding = 'utf-8') as log_file:
				log_file.write(('\n=== continued after a pause\n' if resumed else '') + ' '.join(command) + '\n')
				for raw in iter(self.process.stdout.readline, b''):
					line = raw.decode('utf-8', errors = 'replace').rstrip()
					if line.startswith('@@PROGRESS '):
						try:
							self.state = dict(self.state, **json.loads(line[11:]))
						except ValueError:
							pass
					elif line.strip():
						self.log.append(line)
						log_file.write(line + '\n')
						log_file.flush()
			code = self.process.wait()
		state = self.state or {}
		with self.lock:
			worked = item.get('worked', 0.0) + now() - item['run_started']
			if self.stop_requested:
				item.update(status = 'cancelled', message = 'stopped', finished = now())
			elif code == 0 and state.get('status') == 'done':
				outputs = state.get('outputs') or {}
				master = MASTERS_DIR / (item['id'] + '.mp4')
				item.update(status = 'done', finished = now(), worked = worked, output = outputs.get('video'), compare = outputs.get('compare'), elapsed = worked, master = str(master) if '--keep-master' in command and master.is_file() else None)
			elif self.pause_requested:
				item.update(status = 'paused', worked = worked)
				self.state = dict(state, status = 'paused')
			else:
				item['finished'] = now()
				tail = '\n'.join(list(self.log)[-12:])
				item.update(status = 'error', message = (state.get('message') or 'stopped unexpectedly (exit code {})'.format(code)) + ('\n\n' + tail if tail else ''))

	def merge(self, parts : list) -> None:
		name = safe_name(self.settings.get('merge_name') or 'merged_' + datetime.datetime.now().strftime('%Y-%m-%d_%H%M'))
		output = OUTPUT_DIR / (name + '.mp4')
		counter = 2
		while output.exists():
			output = OUTPUT_DIR / '{} ({}).mp4'.format(name, counter)
			counter += 1
		self.batch['merge'] = { 'status': 'running', 'fraction': 0.0, 'detail': 'starting', 'output': str(output), 'parts': len(parts), 'started': now() }

		def on_progress(fraction : float, detail : str) -> None:
			if self.stop_requested:
				raise InterruptedError('stopped')
			self.batch['merge'].update(fraction = fraction, detail = detail)

		try:
			titles = [ Path(item['name']).stem + ('' if item['start'] < 0.05 and item['end'] > item['duration'] - 0.05 else ' {}–{}'.format(upscale.format_clock(item['start']), upscale.format_clock(item['end']))) for item in parts ]
			# merge from the full-quality copies when the results themselves were made smaller, so the merged video is compressed once
			sources = [ item['master'] if item.get('master') and Path(item['master']).is_file() else item['output'] for item in parts ]
			merge.merge_videos(sources, output, titles = titles, fps = self.settings.get('fps'), on_progress = on_progress, audio = self.settings.get('audio', True),
				size = self.settings.get('size') or 'original', video_format = self.settings.get('format') or 'av1')
			self.batch['merge'].update(status = 'done', fraction = 1.0, detail = 'done')
			with self.lock:
				self.merged_names.append(output.stem)
		except InterruptedError:
			self.batch['merge'].update(status = 'cancelled', detail = 'stopped')
			output.unlink(missing_ok = True)
		except Exception as error:
			self.batch['merge'].update(status = 'error', detail = str(error))

	# reporting
	def estimate(self, item : dict, photos : int) -> float:
		s = self.settings
		cost = upscale.encode_factor(s.get('size') or 'original', s.get('format') or 'av1', bool(s.get('merge')))
		return upscale.estimate_seconds(item['end'] - item['start'], item['fps'], float(s['fps']) if s.get('fps') else None, int(s.get('height') or 1080), s.get('model') or '7b', photos, s.get('motion') != 'fast', cost)

	def snapshot(self) -> dict:
		people = list_refs()
		photos = next((person['photos'] for person in people if person['name'] == self.settings.get('person')), 0)
		t = now()
		with self.lock:
			items = [ dict(item) for item in self.items ]
			batch = dict(self.batch) if self.batch else None
			state = dict(self.state) if self.state else None
		for item in items:
			item['estimate'] = self.estimate(item, photos)
		running = self.running()
		paused = self.paused()
		active = running or paused

		summary = None
		if batch:
			in_batch = [ item for item in items if item['id'] in batch['ids'] ]
			done = [ item for item in in_batch if item['status'] == 'done' and item.get('elapsed') ]
			# measured speed versus the estimates, damped like the per-video estimate
			speed = (sum(item['elapsed'] for item in done) + 60) / (sum(item['estimate'] for item in done) + 60)
			speed = min(max(speed, 0.4), 2.5)
			current = next((item for item in items if item['status'] in ('running', 'paused')), None)
			remaining = 0.0
			if current and state and state.get('remaining') is not None:
				# while paused, the last estimate stands still
				remaining += max(state['remaining'] - (t - state.get('time', t)), 0) if running else state['remaining']
			elif current:
				remaining += current['estimate'] * speed
			queued = [ item for item in items if item['status'] == 'queued' ]
			remaining += sum(item['estimate'] for item in queued) * speed if active else 0
			merge_state = batch.get('merge')
			if active and self.settings.get('merge'):
				total_seconds = sum((item['end'] - item['start']) for item in items if item['status'] in ('done', 'running', 'paused', 'queued'))
				merge_estimate = 10 + total_seconds * 0.35
				remaining += merge_estimate * (1 - (merge_state or {}).get('fraction', 0.0))
			# time paused does not count
			elapsed = (batch['finished'] or batch.get('paused_at') or t) - batch['started'] - batch.get('paused_total', 0.0)
			count_total = len(batch['ids']) + (len(queued) if active else 0)
			summary =\
			{
				'running': running, 'paused': paused, 'elapsed': elapsed, 'remaining': remaining if active else 0, 'eta': t + remaining if running else None,
				'percent': 100.0 if not active else min(99.9, 100 * elapsed / max(elapsed + remaining, 0.001)),
				'index': len(batch['ids']), 'total': count_total,
				'done': len([ item for item in in_batch if item['status'] == 'done' ]),
				'failed': len([ item for item in in_batch if item['status'] == 'error' ]),
				'current': current['id'] if current else None, 'merge': merge_state, 'stopped': batch.get('stopped'),
				'finished': batch['finished']
			}
		if state and state.get('status') == 'error':
			state['log_tail'] = list(self.log)[-20:]
		return\
		{
			'items': items, 'settings': self.settings, 'running': running, 'paused': paused, 'batch': summary, 'job': state,
			'people': people, 'models': list_models(), 'rife': upscale.RIFE_EXE.is_file(), 'results': list_results(),
			'shrink_jobs': SHRINKER.snapshot() if SHRINKER else [], 'size_table': size_table(), 'edit': EDITOR.snapshot() if EDITOR else None,
			'root': str(ROOT), 'output_dir': str(OUTPUT_DIR), 'input_dir': str(INPUT_DIR)
		}


class Shrinker:
	"""Makes existing videos smaller, one at a time, and remembers the measured result per file."""

	def __init__(self) -> None:
		self.lock = threading.Lock()
		self.jobs = []
		self.records = {}          # output path -> measured result (sizes, reduction, VMAF)
		self.stop_requested = False
		self.wake = threading.Event()
		try:
			self.records = json.loads(SHRINK_FILE.read_text(encoding = 'utf-8'))
		except (OSError, ValueError):
			pass
		threading.Thread(target = self.run, daemon = True).start()

	def add(self, paths : list, quality : str, video_format : str) -> int:
		if quality not in shrink.QUALITIES or video_format not in shrink.FORMATS:
			raise ValueError('invalid size setting')
		count = 0
		with self.lock:
			for raw in paths:
				source = Path(raw)
				if not source.is_file() or source.suffix.lower() not in upscale.VIDEO_EXTENSIONS:
					continue
				output = shrink.output_name(source, quality, video_format)
				if not output.resolve().is_relative_to(OUTPUT_DIR.resolve()):
					output = OUTPUT_DIR / output.name
				self.jobs.append({ 'id': uuid.uuid4().hex[:10], 'source': str(source), 'name': source.name, 'output': str(output), 'quality': quality, 'format': video_format,
					'status': 'queued', 'fraction': 0.0, 'detail': '', 'result': None, 'error': None, 'source_size': source.stat().st_size })
				count += 1
		self.stop_requested = False
		self.wake.set()
		return count

	def stop(self) -> None:
		self.stop_requested = True

	def clear(self) -> None:
		with self.lock:
			self.jobs = [ job for job in self.jobs if job['status'] in ('queued', 'running') ]

	def run(self) -> None:
		while True:
			self.wake.wait()
			self.wake.clear()
			while True:
				with self.lock:
					job = next((job for job in self.jobs if job['status'] == 'queued'), None)
					if not job:
						break
					if self.stop_requested:
						for waiting in self.jobs:
							if waiting['status'] == 'queued':
								waiting.update(status = 'cancelled')
						break
					job['status'] = 'running'

				def on_progress(fraction : float, detail : str) -> None:
					if self.stop_requested:
						raise InterruptedError('stopped')
					job.update(fraction = fraction, detail = detail)

				try:
					result = shrink.shrink_video(Path(job['source']), Path(job['output']), job['quality'], job['format'], on_progress = on_progress)
					job.update(status = 'done', fraction = 1.0, result = result)
					with self.lock:
						self.records[str(Path(job['output']).resolve())] = result
					UI_DIR.mkdir(parents = True, exist_ok = True)
					SHRINK_FILE.write_text(json.dumps(self.records, indent = 1), encoding = 'utf-8')
				except InterruptedError:
					job.update(status = 'cancelled', detail = 'stopped')
				except Exception as error:
					job.update(status = 'error', error = str(error))

	def snapshot(self) -> list:
		with self.lock:
			return [ dict(job) for job in self.jobs[-50:] ]


class Editor:
	"""The Edit tab: saved edits, the videos the page may play, and exporting an edit (one at a time)."""

	def __init__(self) -> None:
		self.lock = threading.RLock()
		self.folder = UI_DIR / 'edits'
		self.infos = {}            # resolved path -> ((mtime, size), editor.probe result)
		self.allowed = set()       # files the page may play (/media) besides the output and preview folders
		self.proxy_slots = threading.BoundedSemaphore(2)
		self.preparing = {}        # resolved path -> how far its playable copy is (0-1), while it is made
		self.job = None
		self.stop_requested = False
		self.exported = set()      # videos made by an edit, marked in the results
		self.project_folder = None # where the last project file was opened or saved
		for project in self.load_all():
			self.exported.update(str(Path(path).resolve()) for path in project.get('exports', []))
		self.thumb_queue = collections.deque()
		self.thumb_event = threading.Event()
		threading.Thread(target = self.thumbnail_worker, daemon = True).start()

	# videos
	def info(self, path : Path) -> dict:
		path = Path(path).resolve()
		if not path.is_file() or path.suffix.lower() not in upscale.VIDEO_EXTENSIONS:
			raise ValueError('not a video file: {}'.format(path.name))
		stat = path.stat()
		identity = (stat.st_mtime_ns, stat.st_size)
		cached = self.infos.get(str(path))
		if cached and cached[0] == identity:
			return cached[1]
		try:
			info = editor.probe(path)
		except (subprocess.CalledProcessError, StopIteration, KeyError, ValueError):
			raise ValueError('{} cannot be read'.format(path.name))
		if info['duration'] <= 0:
			raise ValueError('{} has no length'.format(path.name))
		info.update(path = str(path), name = path.name, size = stat.st_size)
		with self.lock:
			self.infos[str(path)] = (identity, info)
			self.allowed.add(str(path))
		return info

	def media(self, path : Path) -> dict:
		"""What the page plays for a video (both tabs): the file itself when browsers can play it, otherwise a copy
		(made once; snapshot() reports how far it is). The timeline's strip of pictures is included when it exists
		already (see strip())."""
		info = self.info(path)
		source = Path(info['path'])
		url = info['path']
		if not info['playable']:
			folder = preview_folder(source)
			proxy = folder / 'edit.mp4'
			# a full-size copy made by earlier versions plays just as well
			url = str(folder / 'preview.mp4') if (folder / 'preview.mp4').is_file() else str(proxy)
			if url == str(proxy):
				# selecting a video again while its copy is being made waits for that copy instead of making a second
				with file_lock(proxy):
					if not proxy.is_file():
						self.preparing[str(source)] = 0.0
						try:
							with self.proxy_slots:
								editor.make_proxy(source, info, proxy, lambda fraction: self.preparing.__setitem__(str(source), fraction))
						finally:
							self.preparing.pop(str(source), None)
		strip = preview_folder(source) / 'strip.jpg'
		return { 'url': url, 'strip': str(strip) if strip.is_file() else None }

	def strip(self, path : Path) -> dict:
		"""The timeline's strip of pictures; it reads the whole video, so the page asks for it after the video plays."""
		info = self.info(path)
		strip = make_strip(Path(info['path']), info)
		return { 'strip': str(strip) if strip else None }

	def speed_preview(self, path : Path, start : float, end : float, speed : float) -> dict:
		info = self.info(path)
		if speed not in editor.SPEEDS:
			raise ValueError('unknown speed')
		start = min(max(start, 0.0), info['duration'])
		end = min(max(end, start + 0.05), info['duration'])
		source = Path(info['path'])
		folder = preview_folder(source)
		target = folder / 'speed_{}.mp4'.format(hashlib.sha1('{:.3f}|{:.3f}|{}'.format(start, end, speed).encode()).hexdigest()[:12])
		with file_lock(target):
			if not target.is_file():
				with self.proxy_slots:
					editor.make_speed_preview(source, info, start, end, speed, target)
				try:
					for old in sorted(folder.glob('speed_*.mp4'), key = lambda file: file.stat().st_mtime, reverse = True)[12:]:
						old.unlink()
				except OSError:
					pass
		return { 'url': str(target) }

	def library(self) -> dict:
		"""The videos the Edit tab offers: results, the Upscale queue's videos and the videos used in edits."""
		def entry(path : str, name : str = None) -> dict:
			cached = self.infos.get(str(Path(path).resolve()))
			info = cached[1] if cached else None
			thumb = None
			if info:
				thumb_file = preview_folder(Path(path)) / 'thumb.jpg'
				thumb = str(thumb_file) if thumb_file.is_file() else None
			if not info or not thumb:
				self.thumb_queue.append(path)
			return { 'path': path, 'name': name or Path(path).name, 'duration': info['duration'] if info else None, 'size': info['size'] if info else None,
				'width': info['width'] if info else None, 'height': info['height'] if info else None, 'thumb': thumb }

		results = [ entry(result['video'], result['name']) for result in list_results() ]
		seen = { result['path'] for result in results }
		queue = [ entry(item['path'], item['name']) for item in RUNNER.items if item['path'] not in seen and Path(item['path']).is_file() ]
		seen.update(item['path'] for item in queue)
		used = []
		for project in self.load_all():
			for clip in project.get('clips', []):
				if clip['path'] not in seen and Path(clip['path']).is_file():
					seen.add(clip['path'])
					used.append(entry(clip['path']))
		self.thumb_event.set()
		return { 'results': results, 'queue': queue, 'used': used }

	def thumbnail_worker(self) -> None:
		while True:
			self.thumb_event.wait()
			self.thumb_event.clear()
			while self.thumb_queue:
				path = self.thumb_queue.popleft()
				try:
					info = self.info(Path(path))
					make_thumbnail(Path(info['path']), info['duration'])
				except (ValueError, OSError):
					pass

	# edits
	def file_of(self, edit_id : str) -> Path:
		if not re.fullmatch(r'[0-9a-f]{10}', str(edit_id or '')):
			raise ValueError('unknown edit')
		return self.folder / (edit_id + '.json')

	def load_all(self) -> list:
		projects = []
		for file in self.folder.glob('*.json') if self.folder.is_dir() else []:
			try:
				projects.append(json.loads(file.read_text(encoding = 'utf-8')))
			except (OSError, ValueError):
				pass
		return sorted(projects, key = lambda project: project.get('updated', 0), reverse = True)

	def list(self) -> list:
		return [ { 'id': project['id'], 'name': project.get('name', ''), 'updated': project.get('updated'), 'clips': len(project.get('clips', [])),
			'duration': sum((clip['out'] - clip['in']) / clip['speed'] for clip in project.get('clips', [])), 'file': project.get('file') } for project in self.load_all() ]

	def load(self, edit_id : str) -> dict:
		try:
			return json.loads(self.file_of(edit_id).read_text(encoding = 'utf-8'))
		except OSError:
			raise ValueError('this edit no longer exists')

	def create(self) -> dict:
		project = { 'id': uuid.uuid4().hex[:10], 'name': 'Edit ' + datetime.datetime.now().strftime('%Y-%m-%d %H.%M'), 'created': now(), 'updated': now(),
			'clips': [], 'fade_in': 0.0, 'fade_out': 0.0, 'exports': [] }
		self.write(project)
		return project

	def save(self, data : dict) -> dict:
		# the project file is kept from the saved edit: only a dialog (or opening one) chooses where it goes
		project = self.clean(data)
		try:
			old = self.load(project['id'])
			project.update(created = old.get('created', now()), exports = old.get('exports', []), file = old.get('file'), file_saved = old.get('file_saved'))
		except ValueError:
			project.update(created = now(), exports = [], file = None, file_saved = None)
		self.write(project)
		return project

	# project files
	def save_file(self, data : dict, choose : bool) -> dict:
		"""Saves the edit as a project file: where it was saved before, or (the first time, or choose) where a Save
		dialog says. Clip paths are kept as they are and also relative to the file, so a project that is moved
		together with its videos still finds them."""
		project = self.save(data)
		target = project.get('file') if not choose else None
		if not target:
			folder = Path(project['file']).parent if project.get('file') else self.project_folder or PROJECTS_DIR
			folder.mkdir(parents = True, exist_ok = True)
			target = ask_path('project-save', folder, safe_name(project['name']) + PROJECT_EXTENSION)
			if not target:
				return { 'cancelled': True }
		target = Path(target)
		if target.suffix.lower() != PROJECT_EXTENSION:
			target = target.with_name(target.name + PROJECT_EXTENSION)

		def relative(path : str):
			try:
				return os.path.relpath(path, target.parent)
			except ValueError:
				return None  # another drive

		content = { 'format': PROJECT_FORMAT, 'version': 1, 'saved': now(), 'name': project['name'], 'fade_in': project['fade_in'], 'fade_out': project['fade_out'],
			'clips': [ dict(clip, relative = relative(clip['path'])) for clip in project['clips'] ], 'texts': project['texts'] }
		target.parent.mkdir(parents = True, exist_ok = True)
		temp = target.with_name(target.name + '.tmp')
		temp.write_text(json.dumps(content, indent = 1, ensure_ascii = False), encoding = 'utf-8')
		replace_file(temp, target)
		project.update(file = str(target), file_saved = now())
		self.write(project)
		self.project_folder = target.parent
		return { 'path': str(target), 'project': project }

	def open_file(self, path : str = None, text : str = None, name : str = '') -> dict:
		"""A project file as an edit: chosen in an Open dialog (no path or text), at path, or its text (a file
		dropped on the page). An edit that was opened from or saved to the same file is replaced by the file's
		version; otherwise it becomes a new edit."""
		source = None
		if text is None:
			if not path:
				path = ask_path('project-open', self.project_folder or (PROJECTS_DIR if PROJECTS_DIR.is_dir() else ROOT))
				if not path:
					return { 'cancelled': True }
			source = Path(path)
			try:
				text = source.read_text(encoding = 'utf-8')
			except OSError as error:
				raise ValueError('the project file could not be read: {}'.format(error))
		try:
			content = json.loads(text)
		except ValueError:
			content = None
		if not isinstance(content, dict) or content.get('format') != PROJECT_FORMAT:
			raise ValueError('{} is not a video edit project'.format(source.name if source else name or 'this'))
		clips, missing = [], 0
		for raw in list(content.get('clips') or []):
			clip = dict(raw)
			# moved together with the project file: found next to it again
			if not Path(str(clip.get('path'))).is_file() and source and clip.get('relative'):
				moved = (source.parent / str(clip['relative'])).resolve()
				if moved.is_file():
					clip['path'] = str(moved)
			missing += not Path(str(clip.get('path'))).is_file()
			clips.append(clip)
		same = next((project for project in self.load_all() if source and project.get('file') and Path(project['file']).resolve() == source.resolve()), None)
		project = self.save(dict(content, id = same['id'] if same else uuid.uuid4().hex[:10], clips = clips,
			name = content.get('name') or (source.stem if source else Path(name).stem) or 'Edit'))
		if source:
			project.update(file = str(source), file_saved = now())
			self.write(project)
			self.project_folder = source.parent
		return { 'project': project, 'missing': missing }

	def write(self, project : dict) -> None:
		file = self.file_of(project['id'])
		with self.lock:
			self.folder.mkdir(parents = True, exist_ok = True)
			temp = file.with_suffix('.tmp')
			temp.write_text(json.dumps(project, indent = 1), encoding = 'utf-8')
			replace_file(temp, file)

	def delete(self, edit_id : str) -> None:
		self.file_of(edit_id).unlink(missing_ok = True)
		shutil.rmtree(EDIT_WORK / edit_id, ignore_errors = True)

	def clean(self, data : dict) -> dict:
		"""An edit as the page sends it, checked: only known speeds and transitions, sensible numbers."""
		clips = []
		for raw in list(data.get('clips') or [])[:2000]:
			start, end = max(float(raw['in']), 0.0), float(raw['out'])
			if end - start < 0.01:
				raise ValueError('a clip is too short')
			speed = float(raw.get('speed') or 1)
			if speed not in editor.SPEEDS:
				raise ValueError('unknown speed {}'.format(speed))
			transition = raw.get('transition') or {}
			crop = raw.get('crop')
			if crop:
				crop = { key: int(crop[key]) for key in ('x', 'y', 'w', 'h') }
				if crop['w'] < 2 or crop['h'] < 2 or crop['x'] < 0 or crop['y'] < 0:
					raise ValueError('invalid crop')
			path = str(Path(str(raw['path'])))
			clips.append({ 'id': re.sub(r'[^0-9a-z]', '', str(raw.get('id') or ''))[:16] or uuid.uuid4().hex[:10], 'path': path, 'name': Path(path).name,
				'in': start, 'out': end, 'speed': speed, 'sound': bool(raw.get('sound', True)),
				'gamma': min(max(round(float(raw.get('gamma') or 1.0), 2), 0.5), 2.0), 'crop': crop or None,
				'transition': { 'type': transition.get('type') if transition.get('type') in editor.TRANSITIONS else 'cut',
					'duration': min(max(float(transition.get('duration') or 1.0), 0.2), 4.0) } })
		return { 'id': self.file_of(data.get('id')).stem, 'name': str(data.get('name') or 'Edit').strip()[:100] or 'Edit', 'updated': now(), 'clips': clips,
			'texts': [ self.clean_text(raw) for raw in list(data.get('texts') or [])[:500] ],
			'fade_in': min(max(float(data.get('fade_in') or 0), 0.0), 4.0), 'fade_out': min(max(float(data.get('fade_out') or 0), 0.0), 4.0) }

	@staticmethod
	def clean_text(raw : dict) -> dict:
		"""A text on the video as the page sends it (see ui/editor.js), checked."""
		def colour(value, fallback):
			return value.lower() if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) else fallback

		def number(key, low, high, fallback):
			try:
				return min(max(float(raw.get(key, fallback)), low), high)
			except (TypeError, ValueError):
				return fallback

		start = max(float(raw['start']), 0.0)
		return { 'id': re.sub(r'[^0-9a-z]', '', str(raw.get('id') or ''))[:16] or uuid.uuid4().hex[:10], 'text': str(raw.get('text') or '')[:2000],
			'start': start, 'end': max(float(raw['end']), start + 0.05), 'x': number('x', -0.5, 1.5, 0.5), 'y': number('y', 0.0, 1.0, 0.5),
			'font': re.sub(r'[^\w \-]', '', str(raw.get('font') or 'Segoe UI'))[:60] or 'Segoe UI', 'size': round(number('size', *editor.FONT_SIZES, 96)),
			'bold': bool(raw.get('bold')), 'italic': bool(raw.get('italic')), 'underline': bool(raw.get('underline')),
			'color': colour(raw.get('color'), '#ffffff'), 'align': raw.get('align') if raw.get('align') in ('left', 'center', 'right') else 'center',
			'box': colour(raw.get('box'), None), 'box_opacity': number('box_opacity', 0.0, 1.0, 0.6),
			'outline': int(number('outline', 0, 2, 0)), 'outline_color': colour(raw.get('outline_color'), '#000000'),
			'shadow': bool(raw.get('shadow')), 'fade': number('fade', 0.0, 2.0, 0.0) }

	def text_pictures(self, project : dict, pictures : list) -> list:
		"""The pictures of the edit's texts, drawn by the page at the size of the exported frame, as files in the edit's
		work folder, with the times of their texts; in the order of the edit's texts (the front-most last)."""
		folder = EDIT_WORK / project['id']
		order = { text['id']: number for number, text in enumerate(project['texts']) }
		found = []
		for picture in list(pictures or [])[:500]:
			text = next((text for text in project['texts'] if text['id'] == str(picture.get('id'))), None)
			data = str(picture.get('png') or '')
			if not text or not text['text'].strip() or not data.startswith('data:image/png;base64,'):
				continue
			png = base64.b64decode(data.split(',', 1)[1], validate = True)
			if len(png) > 32 << 20 or not png.startswith(b'\x89PNG'):
				raise ValueError('a text could not be prepared')
			folder.mkdir(parents = True, exist_ok = True)
			file = folder / 'text-{}.png'.format(hashlib.sha1(png).hexdigest()[:20])
			if not file.is_file():
				file.write_bytes(png)
			frame = list(picture.get('frame') or [ 0, 0 ])[:2]
			found.append({ 'file': str(file), 'x': int(picture['x']), 'y': int(picture['y']), 'frame': [ int(frame[0]), int(frame[1]) ],
				'start': text['start'], 'end': text['end'], 'fade': text['fade'], 'order': order[text['id']] })
		found.sort(key = lambda item: item.pop('order'))
		return found

	# export
	def export(self, data : dict, pictures : list = None) -> dict:
		with self.lock:
			if self.job and self.job['status'] == 'running':
				raise RuntimeError('an edit is being exported already')
			project = self.save(data)
			if not project['clips']:
				raise ValueError('add videos to the edit first')
			texts = self.text_pictures(project, pictures)
			settings = dict(RUNNER.settings)
			size, video_format = settings.get('size') or 'original', settings.get('format') or 'av1'
			if size != 'original' and (size not in shrink.QUALITIES or video_format not in shrink.FORMATS):
				raise ValueError('invalid file size setting')
			# named like "Make smaller" names its files, so the results show the level; an edit's own earlier export is
			# replaced, unless the edit uses that video itself
			name = safe_name(project['name'])
			suffix = '' if size == 'original' else '_{}-{}'.format(size, video_format)
			output = OUTPUT_DIR / (name + suffix + '.mp4')
			replaceable = { str(Path(path).resolve()) for path in project['exports'] } - { str(Path(clip['path']).resolve()) for clip in project['clips'] }
			counter = 2
			while output.exists() and str(output.resolve()) not in replaceable:
				output = OUTPUT_DIR / '{} ({}){}.mp4'.format(name, counter, suffix)
				counter += 1
			self.stop_requested = False
			self.job = { 'id': uuid.uuid4().hex[:10], 'edit': project['id'], 'name': output.name, 'output': str(output), 'status': 'running', 'fraction': 0.0,
				'detail': 'starting', 'started': now(), 'finished': None, 'error': None, 'result': None, 'quality': size, 'format': video_format }
			threading.Thread(target = self.run_export, args = (project, output, size, video_format, bool(settings.get('audio', True)), texts, self.job), daemon = True).start()
			return dict(self.job)

	def run_export(self, project : dict, output : Path, size : str, video_format : str, audio : bool, texts : list, job : dict) -> None:
		def on_progress(fraction : float, detail : str) -> None:
			if self.stop_requested:
				raise InterruptedError('stopped')
			job.update(fraction = fraction, detail = detail)

		try:
			result = editor.export_edit(project, output, EDIT_WORK / project['id'], size, video_format, audio, on_progress, texts)
			with self.lock:
				self.exported.add(str(output.resolve()))
				try:
					saved = self.load(project['id'])
					if str(output) not in saved.get('exports', []):
						saved['exports'] = saved.get('exports', []) + [ str(output) ]
						self.write(saved)
				except ValueError:
					pass
			job.update(status = 'done', fraction = 1.0, detail = 'done', result = result, finished = now())
		except InterruptedError:
			job.update(status = 'cancelled', detail = 'stopped', finished = now())
		except Exception as error:
			job.update(status = 'error', error = str(error), finished = now())

	def stop(self) -> None:
		self.stop_requested = True

	def snapshot(self) -> dict:
		return { 'export': dict(self.job) if self.job else None, 'preparing': dict(self.preparing) }


RUNNER = None
SHRINKER = None
EDITOR = None


def size_table() -> dict:
	return\
	{
		'qualities': shrink.QUALITIES,
		'formats': { key: value['label'] for key, value in shrink.FORMATS.items() },
		'expected': { '{}-{}'.format(quality, video_format): { 'reduction': reduction, 'vmaf': vmaf, 'label': shrink.describe(vmaf) } for (quality, video_format), (reduction, vmaf) in shrink.EXPECTED.items() }
	}


def annotate(result : dict) -> dict:
	"""Size-level info for a result file: measured (when it was made smaller here) or from its name."""
	record = SHRINKER.records.get(str(Path(result['video']).resolve())) if SHRINKER else None
	match = re.search(r'_(near|balanced|small)-(av1|h265|h264)$', Path(result['video']).stem)
	if record:
		result['shrink'] = { key: record.get(key) for key in ('quality', 'format', 'source_size', 'size', 'reduction', 'vmaf', 'vmaf_low') }
		result['shrink']['label'] = shrink.describe(record['vmaf']) if record.get('vmaf') is not None else None
	elif match:
		reduction, vmaf = shrink.EXPECTED[(match.group(1), match.group(2))]
		result['shrink'] = { 'quality': match.group(1), 'format': match.group(2), 'expected_reduction': reduction, 'expected_vmaf': vmaf, 'label': shrink.describe(vmaf) }
	return result


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
	server_version = 'upscaler'

	def log_message(self, format, *args) -> None:
		pass

	def allowed_host(self) -> bool:
		return self.headers.get('Host', '') in ('127.0.0.1:{}'.format(PORT), 'localhost:{}'.format(PORT))

	def send_json(self, data, status = HTTPStatus.OK) -> None:
		body = json.dumps(data).encode()
		self.send_response(status)
		self.send_header('Content-Type', 'application/json')
		self.send_header('Content-Length', str(len(body)))
		self.send_header('Cache-Control', 'no-store')
		try:
			self.end_headers()
			self.wfile.write(body)
		except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
			# the page stopped waiting (another video was selected, the page was reloaded)
			pass

	def do_GET(self) -> None:
		if not self.allowed_host():
			return self.send_error(HTTPStatus.FORBIDDEN)
		url = urlparse(self.path)
		if url.path in ('/', '/editor.js', '/editor.css'):
			file = UI_FILE if url.path == '/' else UI_FILE.parent / url.path[1:]
			body = file.read_bytes()
			self.send_response(HTTPStatus.OK)
			self.send_header('Content-Type', { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[file.suffix] + '; charset=utf-8')
			self.send_header('Content-Length', str(len(body)))
			self.send_header('Cache-Control', 'no-store')
			self.end_headers()
			self.wfile.write(body)
		elif url.path.startswith('/fonts/') and re.fullmatch(r'[a-z0-9-]+\.woff2', url.path[7:]) and (UI_FILE.parent / 'fonts' / url.path[7:]).is_file():
			# the page's typefaces ship with the app (ui/fonts), so nothing is loaded from the internet
			body = (UI_FILE.parent / 'fonts' / url.path[7:]).read_bytes()
			self.send_response(HTTPStatus.OK)
			self.send_header('Content-Type', 'font/woff2')
			self.send_header('Content-Length', str(len(body)))
			self.send_header('Cache-Control', 'max-age=86400')
			self.end_headers()
			self.wfile.write(body)
		elif url.path == '/api/state':
			self.send_json(RUNNER.snapshot())
		elif url.path == '/api/log':
			self.send_json({ 'lines': list(RUNNER.log)[-200:] })
		elif url.path == '/media':
			path = Path(parse_qs(url.query).get('path', [ '' ])[0])
			if not path.is_absolute() or not is_servable(path):
				return self.send_error(HTTPStatus.NOT_FOUND)
			self.send_file(path)
		else:
			self.send_error(HTTPStatus.NOT_FOUND)

	def send_file(self, path : Path) -> None:
		size = path.stat().st_size
		start, end = 0, size - 1
		match = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
		if match and (match.group(1) or match.group(2)):
			if match.group(1):
				start = int(match.group(1))
				end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
			else:
				start = max(size - int(match.group(2)), 0)
			if start > end:
				self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
				self.send_header('Content-Range', 'bytes */{}'.format(size))
				self.end_headers()
				return
			self.send_response(HTTPStatus.PARTIAL_CONTENT)
			self.send_header('Content-Range', 'bytes {}-{}/{}'.format(start, end, size))
		else:
			self.send_response(HTTPStatus.OK)
		self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
		self.send_header('Content-Length', str(end - start + 1))
		self.send_header('Accept-Ranges', 'bytes')
		self.send_header('Cache-Control', 'no-cache')
		self.end_headers()
		try:
			with open(path, 'rb') as file:
				file.seek(start)
				left = end - start + 1
				while left > 0:
					chunk = file.read(min(1 << 20, left))
					if not chunk:
						break
					self.wfile.write(chunk)
					left -= len(chunk)
		except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
			pass

	def receive_upload(self) -> None:
		"""A dropped file, streamed into input/ (keeping the dropped folder name). Identical existing files are reused."""
		length = int(self.headers.get('Content-Length') or 0)
		relative = [ safe_name(part) for part in unquote(self.headers.get('X-Filename', '')).replace('\\', '/').split('/') if part not in ('', '.', '..') ]
		if not relative or Path(relative[-1]).suffix.lower() not in upscale.VIDEO_EXTENSIONS:
			self.rfile.read(length)
			return self.send_json({ 'error': 'not a video file' }, HTTPStatus.BAD_REQUEST)
		target = INPUT_DIR.joinpath(*relative)
		if target.is_file() and target.stat().st_size == length:
			remaining = length
			while remaining > 0:
				remaining -= len(self.rfile.read(min(1 << 20, remaining)))
			return self.send_json({ 'path': str(target), 'reused': True })
		counter = 2
		while target.exists():
			target = target.with_name('{} ({}){}'.format(Path(relative[-1]).stem, counter, Path(relative[-1]).suffix))
			counter += 1
		target.parent.mkdir(parents = True, exist_ok = True)
		temp = target.with_name(target.name + '.part')
		remaining = length
		with open(temp, 'wb') as file:
			while remaining > 0:
				chunk = self.rfile.read(min(1 << 20, remaining))
				if not chunk:
					break
				file.write(chunk)
				remaining -= len(chunk)
		if remaining:
			temp.unlink(missing_ok = True)
			return self.send_json({ 'error': 'upload interrupted' }, HTTPStatus.BAD_REQUEST)
		temp.replace(target)
		self.send_json({ 'path': str(target), 'reused': False })

	def do_POST(self) -> None:
		# the custom header forces a CORS preflight, so other web pages cannot drive this server
		if not self.allowed_host() or self.headers.get('X-Upscaler') != '1':
			return self.send_error(HTTPStatus.FORBIDDEN)
		route = urlparse(self.path).path
		if route == '/api/upload':
			return self.receive_upload()
		length = int(self.headers.get('Content-Length') or 0)
		try:
			data = json.loads(self.rfile.read(length) or b'{}')
		except ValueError:
			return self.send_json({ 'error': 'bad request' }, HTTPStatus.BAD_REQUEST)
		try:
			if route == '/api/browse':
				mode = 'folder' if data.get('mode') == 'folder' else 'files'
				initial = OUTPUT_DIR if data.get('pick') else INPUT_DIR
				result = subprocess.run([ sys.executable, '-c', PICKER_SCRIPT, mode, str(initial), ' '.join(sorted(upscale.VIDEO_EXTENSIONS)) ], capture_output = True, text = True)
				chosen = [ line.strip() for line in result.stdout.splitlines() if line.strip() ]
				if mode == 'folder':
					chosen = [ str(path) for path in find_videos(Path(chosen[0])) ] if chosen else []
				if data.get('pick'):
					self.send_json({ 'files': [ { 'path': path, 'size': Path(path).stat().st_size } for path in chosen if Path(path).is_file() ] })
				else:
					self.send_json(RUNNER.add(chosen) if chosen else { 'added': [], 'skipped': [] })
			elif route == '/api/shrink':
				self.send_json({ 'added': SHRINKER.add(data.get('paths', []), data.get('quality'), data.get('format')) })
			elif route == '/api/shrink/stop':
				SHRINKER.stop()
				self.send_json({ 'ok': True })
			elif route == '/api/shrink/clear':
				SHRINKER.clear()
				self.send_json({ 'ok': True })
			elif route == '/api/queue/add':
				self.send_json(RUNNER.add(data.get('paths', [])))
			elif route == '/api/queue/update':
				RUNNER.update(data['id'], { key: data[key] for key in ('start', 'end', 'crop') if key in data })
				self.send_json({ 'ok': True })
			elif route == '/api/queue/crop-all':
				self.send_json({ 'count': RUNNER.set_crop_all(data['id'], bool(data['all'])) })
			elif route == '/api/queue/remove':
				RUNNER.remove(data['id'])
				self.send_json({ 'ok': True })
			elif route == '/api/queue/move':
				RUNNER.move(data['id'], int(data['delta']))
				self.send_json({ 'ok': True })
			elif route == '/api/queue/requeue':
				RUNNER.requeue(data['id'])
				self.send_json({ 'ok': True })
			elif route == '/api/queue/clear':
				RUNNER.clear(data.get('which', 'finished'))
				self.send_json({ 'ok': True })
			elif route == '/api/preview':
				self.send_json(EDITOR.media(Path(RUNNER.find(data['id'])['path'])))
			elif route == '/api/brightness':
				item = RUNNER.find(data['id'])
				self.send_json(make_brightness_frames(Path(item['path']), item, float(data['time'])))
			elif route == '/api/detect-crop':
				item = RUNNER.find(data['id'])
				self.send_json(detect_picture_area(Path(item['path'])))
			elif route == '/api/settings':
				RUNNER.set_settings(data)
				self.send_json({ 'ok': True })
			elif route == '/api/start':
				if data.get('settings'):
					RUNNER.set_settings(data['settings'])
				RUNNER.start()
				self.send_json({ 'ok': True })
			elif route == '/api/stop':
				RUNNER.stop()
				self.send_json({ 'ok': True })
			elif route == '/api/pause':
				RUNNER.pause()
				self.send_json({ 'ok': True })
			elif route == '/api/edit/list':
				self.send_json({ 'edits': EDITOR.list() })
			elif route == '/api/edit/open':
				self.send_json(EDITOR.load(data['id']))
			elif route == '/api/edit/new':
				self.send_json(EDITOR.create())
			elif route == '/api/edit/save':
				EDITOR.save(data['project'])
				self.send_json({ 'ok': True })
			elif route == '/api/edit/delete':
				EDITOR.delete(data['id'])
				self.send_json({ 'ok': True })
			elif route == '/api/edit/probe':
				self.send_json(EDITOR.info(Path(data['path'])))
			elif route == '/api/edit/media':
				self.send_json(EDITOR.media(Path(data['path'])))
			elif route == '/api/edit/strip':
				self.send_json(EDITOR.strip(Path(data['path'])))
			elif route == '/api/edit/speed':
				self.send_json(EDITOR.speed_preview(Path(data['path']), float(data['start']), float(data['end']), float(data['speed'])))
			elif route == '/api/edit/library':
				self.send_json(EDITOR.library())
			elif route == '/api/edit/save-file':
				self.send_json(EDITOR.save_file(data['project'], bool(data.get('as'))))
			elif route == '/api/edit/open-file':
				self.send_json(EDITOR.open_file(text = data.get('text'), name = str(data.get('name') or '')) if 'text' in data else EDITOR.open_file())
			elif route == '/api/edit/export':
				self.send_json(EDITOR.export(data['project'], data.get('texts')))
			elif route == '/api/edit/stop':
				EDITOR.stop()
				self.send_json({ 'ok': True })
			elif route == '/api/reveal':
				path = Path(data['path'])
				if path.resolve() in (OUTPUT_DIR.resolve(), INPUT_DIR.resolve()) and path.is_dir():
					subprocess.Popen([ 'explorer', str(path.resolve()) ])
				elif path.is_file() and (is_servable(path) or path.resolve().is_relative_to(INPUT_DIR.resolve()) or any(item['path'] == str(path) for item in RUNNER.items) or str(path.resolve()) in EDITOR.infos):
					subprocess.Popen([ 'explorer', '/select,', str(path.resolve()) ])
				self.send_json({ 'ok': True })
			else:
				self.send_error(HTTPStatus.NOT_FOUND)
		except (KeyError, ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
			self.send_json({ 'error': str(error) }, HTTPStatus.BAD_REQUEST)


class Server(ThreadingHTTPServer):
	daemon_threads = True

	def server_bind(self) -> None:
		# skip HTTPServer's reverse DNS lookup of the address
		self.socket.bind(self.server_address)
		self.server_name, self.server_port = 'localhost', self.server_address[1]

	def handle_error(self, request, client_address) -> None:
		# a browser that closes a connection early (a video it stopped loading) is normal, not an error
		if not isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
			super().handle_error(request, client_address)


def main() -> None:
	global PORT, RUNNER, SHRINKER, EDITOR, UI_DIR, QUEUE_FILE, JOB_LOG, SHRINK_FILE
	if '--port' in sys.argv:
		PORT = int(sys.argv[sys.argv.index('--port') + 1])
	if '--state-dir' in sys.argv:
		# a separate queue/settings folder, e.g. for a test instance next to the real one
		UI_DIR = Path(sys.argv[sys.argv.index('--state-dir') + 1]).resolve()
		QUEUE_FILE, JOB_LOG, SHRINK_FILE = UI_DIR / 'queue.json', UI_DIR / 'job.log', UI_DIR / 'shrinks.json'
	try:
		server = Server((HOST, PORT), Handler)
	except OSError:
		print('Port {} is in use: Kinora is probably already running.{}'.format(PORT, '' if '--no-browser' in sys.argv else ' Opening it.'))
		if '--no-browser' not in sys.argv:
			webbrowser.open('http://{}:{}/'.format(HOST, PORT))
		return
	EDITOR = Editor()
	RUNNER = Runner()
	SHRINKER = Shrinker()
	url = 'http://{}:{}/'.format(HOST, PORT)
	print('Kinora: ' + url)
	print('Keep this window open while you use it. Close it (or press Ctrl+C) to stop.')
	if '--no-browser' not in sys.argv:
		threading.Timer(0.5, webbrowser.open, [ url ]).start()
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		RUNNER.shutdown()
		SHRINKER.stop()
		EDITOR.stop()


if __name__ == '__main__':
	main()
