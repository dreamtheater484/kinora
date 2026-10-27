"""Upscale old low-resolution video to 1080p, fully offline.

Pipeline per input video (or a section of it):
  1. prep         ffmpeg: cut the section, crop black borders, deblock, denoise, optional brightness fix
  2. refs         reduce the reference photos to crops of the one person they share
  3. seedvr2      SeedVR2 video diffusion upscaler (temporally consistent, whole frame)
  4. faces        FaceFusion self-swap from the reference crops, to lock the person's identity
  5. interpolate  RIFE frame interpolation to a higher frame rate (optional)
  6. encode       final H.264 file with the original audio, plus a side-by-side comparison

Every stage caches its result in work/<video name>/, so changing only later settings
re-runs only the later stages. SeedVR2 runs in parts of a few minutes that are cached too,
so a stopped (or paused) run continues with the part it was working on.

Usage:
  upscale.bat input\\clip.avi --refs refs\\person
  upscale.bat input\\clip.avi --refs refs\\person --start 1:58 --end 2:35 --fps 30
  start.bat                                   (the front end)
"""
import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy

import shrink
from paths import DATA, WINDOWS, venv_python

SCRIPTS = Path(__file__).resolve().parent / 'tools'  # this program's launchers and helpers
TOOLS = DATA / 'tools'  # what install.ps1 / install.sh installed
SEEDVR2_PYTHON = venv_python(TOOLS / 'seedvr2')
FACEFUSION_PYTHON = venv_python(TOOLS / 'facefusion')
SEEDVR2_MODEL_DIR = DATA / 'models' / 'seedvr2'
RIFE_EXE = TOOLS / 'rife' / ('rife-ncnn-vulkan.exe' if WINDOWS else 'rife-ncnn-vulkan')
RIFE_MODEL = TOOLS / 'rife' / 'rife-v4.26'
STATS_FILE = DATA / 'work' / '_stats.json'

SEEDVR2_MODELS =\
{
	'7b': 'seedvr2_ema_7b_fp16.safetensors',
	'7b-sharp': 'seedvr2_ema_7b_sharp_fp16.safetensors',
	'3b': 'seedvr2_ema_3b_fp16.safetensors'
}
SEEDVR2_SPEED = { '7b': 1.0, '7b-sharp': 1.0, '3b': 0.6 }
DENOISE_FILTERS =\
{
	'off': [],
	'light': [ 'deblock', 'hqdn3d=1.5:1.5:3:3' ],
	'medium': [ 'deblock=filter=strong', 'hqdn3d=3:3:6:6' ],
	'strong': [ 'deblock=filter=strong', 'hqdn3d=5:4:9:9' ]
}
PREPEND_FRAMES = 4
SEEDVR2_OVERLAP = 3  # frames shared by neighbouring batches, and context frames before each part
SEEDVR2_PART_SECONDS = 300  # SeedVR2 runs once per part of about this much work, so a pause (or stop) loses at most one part
REPEAT_THRESHOLD = 0.5  # mean absolute difference (0-255) of 160x120 greyscale frames below which a frame repeats the last picture
BROKEN_RATIO = 0.7  # see find_broken_pictures
BROKEN_MIN_CHANGE = 1.5
SMOOTH_MIN_REPEATS = 0.1  # smooth at the original frame rate when at least this share of frames are repeats
FACE_SWAPPER_MODEL = 'hyperswap_1c_256'
FACE_ENHANCER_MODELS = [ 'gfpgan_1.4', 'codeformer' ]
REF_EXTENSIONS = { '.jpg', '.jpeg', '.png', '.webp', '.bmp' }
VIDEO_EXTENSIONS = { '.avi', '.mov', '.mp4', '.m4v', '.mkv', '.mpg', '.mpeg', '.wmv', '.3gp', '.mts', '.m2ts', '.vob', '.dv', '.flv', '.webm' }

PHASE_LABELS =\
{
	'prep': 'Preparing video',
	'refs': 'Selecting reference faces',
	'seedvr2': 'Upscaling (SeedVR2)',
	'faces': 'Locking face identity',
	'interpolate': 'Smoothing motion (RIFE)',
	'encode': 'Encoding result'
}
# expected seconds per phase = fixed + per_unit * units * scale, where units are frames (photos for refs)
# and scale is (height / 1080)^2. per_unit is re-calibrated from every finished run (work/_stats.json).
DEFAULT_COSTS =\
{
	'prep': [ 2.0, 0.005 ],
	'refs': [ 3.0, 0.25 ],
	'seedvr2': [ 20.0, 0.95 ],
	'faces': [ 8.0, 0.23 ],
	'interpolate': [ 3.0, 0.05 ],
	'encode': [ 3.0, 0.1 ]
}


class PipelineError(Exception):
	pass


def facefusion_face_args(face_enhancer_model : str, expression : bool) -> list:
	"""FaceFusion options shared by the pipeline and download_models.py (which downloads their models)."""
	processors = [ 'face_swapper' ]
	if expression:
		processors.append('expression_restorer')
	processors.append('face_enhancer')
	return\
	[
		'--processors', *processors,
		'--face-swapper-model', FACE_SWAPPER_MODEL,
		'--face-enhancer-model', face_enhancer_model,
		'--face-detector-model', 'yolo_face',
		'--face-mask-types', 'box', 'occlusion',
		'--execution-providers', 'cuda'
	]


# ---------------------------------------------------------------- time helpers

def parse_time(text) -> float:
	"""'118', '118.5', '1:58', '1:58.5' or '0:01:58' -> seconds."""
	if text is None or str(text).strip() == '':
		return None
	parts = str(text).strip().split(':')
	if len(parts) > 3 or not all(re.fullmatch(r'\d+(\.\d+)?', part) for part in parts):
		raise ValueError('not a time: ' + str(text))
	seconds = 0.0
	for part in parts:
		seconds = seconds * 60 + float(part)
	return seconds


def format_clock(seconds : float) -> str:
	seconds = int(round(seconds))
	hours, rest = divmod(seconds, 3600)
	return '{}:{:02d}:{:02d}'.format(hours, *divmod(rest, 60)) if hours else '{}:{:02d}'.format(*divmod(rest, 60))


def time_tag(seconds : float) -> str:
	minutes, rest = divmod(seconds, 60)
	return '{}m{}s'.format(int(minutes), ('{:.1f}'.format(rest).rstrip('0').rstrip('.')).zfill(2))


# ---------------------------------------------------------------- progress

def load_costs() -> dict:
	costs = { key: list(value) for key, value in DEFAULT_COSTS.items() }
	try:
		for key, value in json.loads(STATS_FILE.read_text()).items():
			if key in costs:
				costs[key][1] = float(value)
	except (OSError, ValueError):
		pass
	return costs


def encode_factor(size : str, video_format : str, keep_master : bool = False) -> float:
	"""Relative cost of the final encode: other codecs are slower than the default H.264."""
	if size == 'original':
		return 1.0
	return shrink.ENCODE_SPEED[video_format] + (1.0 if keep_master else 0.0)


def plan_phases(frames_in : int, frames_out : int, height : int, model : str, ref_photos : int, interpolate : bool, motion_first : bool = True, encode_cost : float = 1.0) -> list:
	scale = (height / 1080) ** 2
	units = [ ('prep', frames_in, 1.0) ]
	if ref_photos:
		units.append(('refs', ref_photos, 1.0))
	if interpolate and motion_first:
		# interpolate at source resolution, then upscale and face-lock every output frame
		units += [ ('interpolate', frames_out, 0.1), ('seedvr2', frames_out, scale * SEEDVR2_SPEED.get(model, 1.0)) ]
		if ref_photos:
			units.append(('faces', frames_out, scale))
	else:
		units.append(('seedvr2', frames_in, scale * SEEDVR2_SPEED.get(model, 1.0)))
		if ref_photos:
			units.append(('faces', frames_in, scale))
		if interpolate:
			units.append(('interpolate', frames_out, scale))
	units.append(('encode', frames_out, scale * encode_cost))
	costs = load_costs()
	return [ { 'key': key, 'label': PHASE_LABELS[key], 'units': count, 'scale': factor, 'expected': costs[key][0] + costs[key][1] * count * factor, 'state': 'pending', 'actual': None } for key, count, factor in units ]


def estimate_seconds(duration : float, fps : float, target_fps : float, height : int, model : str, ref_photos : int, motion_first : bool = True, encode_cost : float = 1.0) -> float:
	frames_in = max(int(duration * fps), 1)
	interpolate = bool(target_fps and target_fps > fps * 1.01)
	frames_out = int(duration * target_fps) if interpolate else frames_in
	return sum(phase['expected'] for phase in plan_phases(frames_in, frames_out, height, model, ref_photos, interpolate, motion_first, encode_cost))


class Progress:
	"""Overall progress over weighted phases, with a time-left estimate that adapts to the measured speed."""

	def __init__(self, phases : list, json_mode : bool) -> None:
		self.phases = phases
		self.json_mode = json_mode
		self.started = time.time()
		self.current = None
		self.phase_started = self.started
		self.fraction = 0.0
		self.fraction_start = 0.0
		self.detail = ''
		self.percent = 0.0
		self.last_emit = 0.0
		self.lock = threading.Lock()

	def begin(self, key : str, detail : str = '') -> None:
		with self.lock:
			self._close_current()
			self.current = next(phase for phase in self.phases if phase['key'] == key)
			self.current['state'] = 'running'
			self.phase_started = time.time()
			self.fraction = self.fraction_start = 0.0
			self.detail = detail
		self.emit(force = True)

	def resume_from(self, fraction : float, detail : str) -> None:
		"""This much of the current phase was done by an earlier run (before a pause): it counts as done, but not for the speed."""
		with self.lock:
			self.fraction = self.fraction_start = self.current['resumed_from'] = fraction
			self.detail = detail
		self.emit(force = True)

	def cached(self) -> None:
		"""The current phase's result is reused from an earlier run."""
		with self.lock:
			self.current['state'] = 'cached'
			self.current['expected'] = 0.0
			self.detail = 'reused from an earlier run'
		self.emit(force = True)

	def skip(self, key : str) -> None:
		"""A planned phase turned out not to be needed."""
		with self.lock:
			phase = next(phase for phase in self.phases if phase['key'] == key)
			phase['state'] = 'skipped'
			phase['expected'] = 0.0

	def update(self, fraction : float = None, detail : str = None) -> None:
		with self.lock:
			if fraction is not None:
				self.fraction = min(max(fraction, self.fraction), 1.0)
			if detail is not None:
				self.detail = detail
		self.emit()

	def _close_current(self) -> None:
		if self.current and self.current['state'] == 'running':
			self.current['state'] = 'done'
			self.current['actual'] = time.time() - self.phase_started

	def remaining(self) -> float:
		done = [ phase for phase in self.phases if phase['state'] == 'done' and phase['expected'] > 0 ]
		# measured speed versus the model, damped with 30 s of "as expected" so short phases do not swing it
		speed = (sum(phase['actual'] for phase in done) + 30) / (sum(phase['expected'] for phase in done) + 30)
		speed = min(max(speed, 0.4), 2.5)
		future = sum(phase['expected'] for phase in self.phases if phase['state'] == 'pending') * speed
		current = 0.0
		if self.current and self.current['state'] == 'running':
			in_phase = time.time() - self.phase_started
			expected = self.current['expected'] * speed
			made = self.fraction - self.fraction_start
			if made >= 0.15 * (1 - self.fraction_start) and in_phase > 3:
				current = in_phase * (1 - self.fraction) / made
			else:
				current = max(expected * (1 - self.fraction_start) - in_phase, expected * (1 - self.fraction) * 0.3, 1.0)
		return current + future

	def snapshot(self, status : str = 'running') -> dict:
		now = time.time()
		elapsed = now - self.started
		remaining = self.remaining() if status == 'running' else 0.0
		if status == 'running':
			self.percent = min(max(self.percent, 100 * elapsed / max(elapsed + remaining, 0.001)), 99.9)
		elif status == 'done':
			self.percent = 100.0
		return\
		{
			'status': status,
			'phase': self.current['key'] if self.current else None,
			'phase_label': self.current['label'] if self.current else 'Starting',
			'phase_index': (self.phases.index(self.current) + 1) if self.current else 0,
			'phase_count': len(self.phases),
			'phase_percent': round(self.fraction * 100, 1),
			'detail': self.detail,
			'percent': round(self.percent, 1),
			'elapsed': round(elapsed, 1),
			'remaining': round(remaining, 1),
			'eta': now + remaining,
			'started': self.started,
			'time': now,
			'phases': [ { 'key': phase['key'], 'label': phase['label'], 'state': phase['state'] } for phase in self.phases ]
		}

	def emit(self, force : bool = False, status : str = 'running', extra : dict = None) -> None:
		now = time.time()
		if not force and now - self.last_emit < (0.5 if self.json_mode else 10):
			return
		self.last_emit = now
		with self.lock:
			snapshot = self.snapshot(status)
		snapshot.update(extra or {})
		if self.json_mode:
			print('@@PROGRESS ' + json.dumps(snapshot), flush = True)
		elif status == 'running':
			print('[{:5.1f}%] {} {}/{} {} | elapsed {} | left {} | ETA {}'.format(snapshot['percent'], snapshot['phase_label'], snapshot['phase_index'], snapshot['phase_count'], ('- ' + snapshot['detail']) if snapshot['detail'] else '', format_clock(snapshot['elapsed']), format_clock(snapshot['remaining']), time.strftime('%H:%M', time.localtime(snapshot['eta']))), flush = True)

	def finish(self, outputs : dict) -> None:
		with self.lock:
			self._close_current()
			self.detail = 'finished'
		self.emit(force = True, status = 'done', extra = { 'outputs': outputs })
		self.calibrate()

	def fail(self, message : str) -> None:
		self.emit(force = True, status = 'error', extra = { 'message': message })

	def calibrate(self) -> None:
		"""Blend this run's measured speed into the cost model used for future estimates."""
		costs = load_costs()
		stats = { key: value[1] for key, value in costs.items() }
		for phase in self.phases:
			# a phase continued after a pause measures only the part of it done in this run
			units = phase['units'] * (1 - phase.get('resumed_from', 0.0))
			if phase['state'] == 'done' and units >= 1 and phase['actual'] and phase['actual'] > costs[phase['key']][0]:
				measured = (phase['actual'] - costs[phase['key']][0]) / (units * phase['scale'])
				stats[phase['key']] = 0.5 * stats[phase['key']] + 0.5 * measured
		STATS_FILE.parent.mkdir(parents = True, exist_ok = True)
		STATS_FILE.write_text(json.dumps(stats, indent = 2))


progress : Progress = None


# ---------------------------------------------------------------- running tools

def log(message : str) -> None:
	print('\n=== ' + message, flush = True)


def run(command : list, on_line = None, poll = None, done_marker : str = None) -> None:
	"""Run a tool, echoing its output. on_line receives every output line (including \\r progress-bar updates), poll runs twice a second.
	A tool that printed done_marker counts as successful even if it crashed while exiting."""
	process = subprocess.Popen([ str(part) for part in command ], stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
	tail = collections.deque(maxlen = 25)
	finished = []

	def read() -> None:
		buffer = b''
		last_bar = 0.0
		while True:
			chunk = process.stdout.read1(65536)
			if not chunk:
				break
			parts = re.split(rb'[\r\n]', buffer + chunk)
			buffer = parts.pop()
			for part in parts:
				line = part.decode('utf-8', errors = 'replace').rstrip()
				if not line.strip():
					continue
				tail.append(line)
				if done_marker and line.startswith(done_marker):
					finished.append(line)
				if on_line:
					try:
						on_line(line)
					except Exception:
						pass
				if re.fullmatch(r'[a-z0-9_]+=\S*', line):
					continue  # ffmpeg -progress key=value lines
				is_bar = '%|' in line or line.startswith('progress ')
				if not is_bar or time.time() - last_bar > 2:
					print(line, flush = True)
					last_bar = time.time() if is_bar else last_bar

	reader = threading.Thread(target = read, daemon = True)
	reader.start()
	while process.poll() is None:
		if poll:
			poll()
		time.sleep(0.5)
	reader.join()
	if process.returncode != 0 and not finished:
		raise PipelineError('{} failed (exit code {}). Last output:\n{}'.format(Path(str(command[1] if Path(str(command[0])).name.startswith('python') else command[0])).name, process.returncode, '\n'.join(tail)))


def ffmpeg(*arguments, total_frames : int = 0, span : tuple = (0.0, 1.0)) -> None:
	"""Run ffmpeg quietly, reporting its frame progress as the [span] part of the current phase."""
	low, high = span

	def on_line(line : str) -> None:
		if total_frames and line.startswith('frame='):
			progress.update(low + (high - low) * min(int(line[6:].strip() or 0) / total_frames, 1.0))

	run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', '-y', *arguments ], on_line = on_line)


def probe(path : Path) -> dict:
	output = subprocess.run([ 'ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path) ], capture_output = True, text = True, check = True).stdout
	info = json.loads(output)
	video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
	# the nominal rate: files with skipped time slots have a lower average rate than they were recorded at
	rate = video.get('r_frame_rate', '0/0')
	if rate in ('0/0', '0') or Fraction(rate) > 120:
		rate = video.get('avg_frame_rate', '0/0')
	return\
	{
		'width': int(video['width']),
		'height': int(video['height']),
		'fps': Fraction(rate).limit_denominator(1001),
		'duration': float(info['format'].get('duration') or video.get('duration') or 0),
		'has_audio': any(stream['codec_type'] == 'audio' for stream in info['streams'])
	}


def count_frames(path : Path) -> int:
	output = subprocess.run([ 'ffprobe', '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', str(path) ], capture_output = True, text = True, check = True).stdout
	return int(output.strip())


def is_cached(stage_file : Path, params : dict) -> bool:
	params_file = stage_file.with_suffix('.json')
	if stage_file.is_file() and params_file.is_file() and json.loads(params_file.read_text()) == params:
		return True
	# the stage is about to be made again: a run that is paused or stopped half-way must not leave
	# an earlier run's params next to a half-written file
	params_file.unlink(missing_ok = True)
	return False


def mark_done(stage_file : Path, params : dict) -> None:
	stage_file.with_suffix('.json').write_text(json.dumps(params, indent = 2))


def stage_params(stage_file : Path) -> dict:
	return json.loads(stage_file.with_suffix('.json').read_text())


# ---------------------------------------------------------------- stages

def trim_args(args : argparse.Namespace) -> list:
	trim = []
	if args.start_seconds:
		trim += [ '-ss', '{:.3f}'.format(args.start_seconds) ]
	if args.end_seconds is not None:
		trim += [ '-t', '{:.3f}'.format(args.end_seconds - args.start_seconds) ]
	return trim


def detect_crop(args : argparse.Namespace, width : int, height : int) -> str:
	output = subprocess.run([ 'ffmpeg', '-hide_banner', *trim_args(args), '-i', str(args.input), '-vf', 'cropdetect=limit=24:round=2:reset=0', '-f', 'null', '-' ], capture_output = True, text = True).stderr
	crops = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', output)
	if not crops:
		return ''
	crop_width, crop_height, x, y = (int(value) for value in crops[-1])
	if crop_width * crop_height < 0.6 * width * height or (crop_width, crop_height) == (width, height):
		return ''
	return 'crop={}:{}:{}:{}'.format(crop_width, crop_height, x, y)


def find_real_pictures(args : argparse.Namespace, base_filters : list) -> list:
	"""Indices of the frames that show a new picture rather than a repeated copy.

	Old webcams, phones and screen recorders often captured far fewer pictures than the file's
	frame rate and repeated the last one in between. Compared on the untouched source, so that
	denoising cannot make a copy look new.
	"""
	raw = subprocess.run([ 'ffmpeg', '-v', 'error', *trim_args(args), '-i', str(args.input), '-vf', ','.join(base_filters + [ 'scale=160:120', 'format=gray' ]), '-f', 'rawvideo', '-' ], capture_output = True).stdout
	frames = numpy.frombuffer(raw, dtype = numpy.uint8).reshape(-1, 120, 160).astype(numpy.float32)
	unique = [ 0 ]
	for index in range(1, len(frames)):
		if numpy.abs(frames[index] - frames[unique[-1]]).mean() > REPEAT_THRESHOLD:
			unique.append(index)
	return unique, find_broken_pictures(frames, unique)


def find_broken_pictures(frames : numpy.ndarray, unique : list) -> list:
	"""Real pictures that are damaged (block garbage from transmission or decoding errors).

	Such a picture differs strongly from both its neighbours while the neighbours resemble each
	other; with genuine movement the change from previous to next is at least as large.
	"""
	if len(unique) < 3:
		return []
	def change(a, b):
		return float(numpy.abs(frames[a] - frames[b]).mean())
	typical = float(numpy.median([ change(a, b) for a, b in zip(unique, unique[1:]) ])) or 1.0
	broken = []
	for previous, current, following in zip(unique, unique[1:], unique[2:]):
		into, out = change(previous, current), change(current, following)
		if min(into, out) > BROKEN_MIN_CHANGE * typical and change(previous, following) < BROKEN_RATIO * (into + out) / 2:
			broken.append(current)
	return broken


def picture_times(unique : list, fps : float) -> dict:
	"""Moment of each real picture, lightly evened out.

	A picture's first appearance is only known to one source frame, and webcams/networks deliver
	unevenly while the camera itself captured at a steady pace; a light neighbour average removes
	most of that jitter (moves a picture by at most a fraction of a frame)."""
	raw = [ index / fps for index in unique ]
	smooth = list(raw)
	for k in range(1, len(raw) - 1):
		smooth[k] = 0.25 * raw[k - 1] + 0.5 * raw[k] + 0.25 * raw[k + 1]
	for k in range(1, len(smooth)):
		smooth[k] = max(smooth[k], smooth[k - 1] + 0.2 / fps)
	return dict(zip(unique, smooth))


def stage_prep(args : argparse.Namespace, info : dict, work : Path) -> tuple:
	progress.begin('prep', 'detecting black borders')
	prep_file = work / '01_prep.mp4'
	timeline_file = work / '01_prep_timeline.json'
	crop = detect_crop(args, info['width'], info['height']) if args.crop == 'auto' else ('' if args.crop == 'off' else 'crop=' + args.crop)
	# fps: fill skipped time slots so every frame is exactly 1/fps long and the audio stays in sync
	base_filters = [ 'fps={}'.format(info['fps']) ] + ([ crop ] if crop else [])
	filters = base_filters + DENOISE_FILTERS[args.denoise]
	if args.gamma != 1.0 or args.contrast != 1.0 or args.saturation != 1.0:
		filters.append('eq=gamma={}:contrast={}:saturation={}'.format(args.gamma, args.contrast, args.saturation))
	filters += [ 'setsar=1', 'format=yuv420p' ]
	params = { 'input': str(args.input.resolve()), 'trim': trim_args(args), 'filters': filters }

	if is_cached(prep_file, params) and timeline_file.is_file():
		progress.cached()
		return prep_file, json.loads(timeline_file.read_text())
	log('prep: ' + ', '.join(filters))
	progress.update(0.05, 'cutting section and cleaning up')
	ffmpeg(*trim_args(args), '-i', args.input, '-vf', ','.join(filters), '-an', '-c:v', 'libx264', '-qp', '0', '-preset', 'veryfast', prep_file, total_frames = args.frames_in, span = (0.05, 0.85))
	audio_file = work / 'audio.wav'
	audio_file.unlink(missing_ok = True)
	if info['has_audio']:
		ffmpeg(*trim_args(args), '-i', args.input, '-vn', '-c:a', 'pcm_s16le', audio_file)

	progress.update(0.9, 'finding repeated frames')
	frames = count_frames(prep_file)
	unique, broken = find_real_pictures(args, base_filters)
	unique = [ index for index in unique if index < frames and index not in broken ]
	timeline = { 'fps': float(info['fps']), 'frames': frames, 'unique': unique, 'broken': broken }
	log('real pictures: {} of {} frames ({:.1f} per second){}'.format(len(unique), frames, len(unique) * float(info['fps']) / frames, ', skipping {} damaged: frames {}'.format(len(broken), broken) if broken else ''))
	timeline_file.write_text(json.dumps(timeline))
	mark_done(prep_file, params)
	return prep_file, timeline


def collect_refs(refs : list) -> list:
	paths = []
	for ref in refs:
		ref_path = Path(ref)
		if ref_path.is_dir():
			paths += sorted(path for path in ref_path.iterdir() if path.suffix.lower() in REF_EXTENSIONS)
		elif ref_path.is_file():
			paths.append(ref_path)
		else:
			raise PipelineError('reference path not found: ' + ref)
	if not paths:
		raise PipelineError('no reference images (.jpg/.png/.webp) found in: ' + ', '.join(refs))
	return paths


def prepare_refs(args : argparse.Namespace) -> list:
	"""Reduce the reference photos to single-face crops of the person they have in common."""
	progress.begin('refs', 'finding the shared face')
	photos = collect_refs(args.refs)
	crops_dir = DATA / 'work' / '_refs' / '_'.join(Path(ref).resolve().stem for ref in args.refs)[:80]
	params = { 'photos': [ [ str(photo.resolve()), photo.stat().st_mtime ] for photo in photos ], 'min_face': args.min_ref_face }
	params_file = crops_dir.with_suffix('.json')

	if crops_dir.is_dir() and params_file.is_file() and json.loads(params_file.read_text()) == params:
		progress.cached()
	else:
		log('refs: selecting the shared face in ' + str(len(photos)) + ' photos')
		params_file.unlink(missing_ok = True)  # see is_cached
		crops_dir.parent.mkdir(parents = True, exist_ok = True)
		run([ FACEFUSION_PYTHON, SCRIPTS / 'prepare_refs.py', crops_dir, args.min_ref_face, *[ photo.resolve() for photo in photos ] ])
		params_file.write_text(json.dumps(params, indent = 2))
	crops = sorted(crops_dir.glob('*.png'))
	if not crops:
		raise PipelineError('none of the reference photos has a usable face')
	return crops


SEEDVR2_STEPS = { 'Encoding': (0.04, 0.24, 'encoding'), 'Upscaling': (0.24, 0.49, 'upscaling'), 'Decoding': (0.49, 0.93, 'decoding'), 'Post-processing': (0.93, 0.95, 'post-processing') }


def seedvr2_parts(frames : int, args : argparse.Namespace) -> list:
	"""Frame ranges [start, end) for the SeedVR2 runs: whole batches (they advance by batch - overlap frames) adding up to
	about SEEDVR2_PART_SECONDS of work. Depends only on the settings, so a resumed run finds the parts of the paused one."""
	step = args.batch - SEEDVR2_OVERLAP
	seconds_per_frame = DEFAULT_COSTS['seedvr2'][1] * (args.height / 1080) ** 2 * SEEDVR2_SPEED.get(args.model, 1.0)
	length = max(int(round(SEEDVR2_PART_SECONDS / (seconds_per_frame * step))), 1) * step
	starts = list(range(0, frames, length))
	if len(starts) > 1 and frames - starts[-1] < step:
		starts.pop()  # a short tail goes with the part before it
	return list(zip(starts, starts[1:] + [ frames ]))


def upscale_part(source : Path, options : list, start : int, end : int, part_file : Path, span : tuple, label : str) -> None:
	"""SeedVR2 on frames [start, end) of source. Like SeedVR2's own streaming mode, a part after the first gets the
	SEEDVR2_OVERLAP frames before it as context, and their upscaled copies are dropped again."""
	low, high = span
	context = min(SEEDVR2_OVERLAP, start)
	part_input = part_file.with_name(part_file.stem + '_in.mp4')
	part_raw = part_file.with_name(part_file.stem + '_raw.mp4')

	def on_line(line : str) -> None:
		match = re.search(r'(Encoding|Upscaling|Decoding|Post-processing) batch (\d+)/(\d+)', line)
		if match:
			step_low, step_high, name = SEEDVR2_STEPS[match.group(1)]
			done, total = int(match.group(2)) - 1, int(match.group(3))
			progress.update(low + (high - low) * (step_low + (step_high - step_low) * done / total), '{}{} batch {}/{}'.format(label, name, done + 1, total))
		elif 'Output saved' in line:
			progress.update(low + (high - low) * 0.95, label + 'saving')

	progress.update(low, label + 'loading model')
	# cut by frame number (exact), lossless like the prep file
	ffmpeg('-i', source, '-vf', 'trim=start_frame={}:end_frame={},setpts=PTS-STARTPTS'.format(start - context, end), '-an', '-c:v', 'libx264', '-qp', 0, '-preset', 'veryfast', part_input)
	part_options = list(options)
	if start > 0:
		part_options[part_options.index('--prepend_frames') + 1] = 0
	part_raw.unlink(missing_ok = True)
	run([ SEEDVR2_PYTHON, SCRIPTS / 'run_seedvr2.py', part_input, '--output', part_raw, '--model_dir', SEEDVR2_MODEL_DIR, *part_options ], on_line = on_line)
	# SeedVR2's CLI leaves its --prepend_frames lead-in (reversed copies of the first frames) in the output; cut it and the
	# context frames, so video and audio stay in sync
	frames = end - start
	lead = context + (PREPEND_FRAMES if start == 0 else 0)
	returned = count_frames(part_raw)
	if returned == frames + context:
		lead = context
	elif returned != frames + lead:
		print('warning: SeedVR2 returned {} frames instead of {}, keeping {} after the first {}'.format(returned, frames + lead, frames, lead))
	ffmpeg('-i', part_raw, '-vf', 'trim=start_frame={}:end_frame={},setpts=PTS-STARTPTS'.format(lead, lead + frames), '-c:v', 'libx264', '-crf', 10, '-preset', 'medium', '-pix_fmt', 'yuv420p', part_file,
		total_frames = frames, span = (low + (high - low) * 0.95, high))
	part_input.unlink(missing_ok = True)
	part_raw.unlink(missing_ok = True)


def stage_seedvr2(args : argparse.Namespace, prep_file : Path, work : Path) -> Path:
	progress.begin('seedvr2', 'loading model')
	seedvr2_file = work / '02_seedvr2.mp4'
	parts_dir = work / '02_seedvr2_parts'
	options =\
	[
		'--dit_model', SEEDVR2_MODELS[args.model],
		'--resolution', args.height,
		'--batch_size', args.batch,
		'--uniform_batch_size',
		'--temporal_overlap', SEEDVR2_OVERLAP,
		'--prepend_frames', PREPEND_FRAMES,
		'--color_correction', 'lab',
		'--seed', args.seed,
		'--vae_encode_tiled',
		'--vae_decode_tiled',
		'--video_backend', 'ffmpeg'
	]
	params = { 'prep': stage_params(prep_file), 'options': [ str(option) for option in options ] }

	if is_cached(seedvr2_file, params):
		progress.cached()
		return seedvr2_file

	frames = count_frames(prep_file)
	parts = seedvr2_parts(frames, args)
	parts_dir.mkdir(exist_ok = True)
	part_files = [ parts_dir / 'part_{:03d}.mp4'.format(index) for index in range(len(parts)) ]
	part_params = [ dict(params, frames = [ start, end ]) for start, end in parts ]
	done = [ is_cached(part_file, part_param) for part_file, part_param in zip(part_files, part_params) ]
	log('seedvr2: {} to {}p, batch {}, {} part{}{}'.format(args.model, args.height, args.batch, len(parts), 's' if len(parts) > 1 else '',
		', {} already done'.format(sum(done)) if any(done) else ''))
	if any(done):
		finished = sum(end - start for (start, end), part_done in zip(parts, done) if part_done)
		progress.resume_from(0.97 * finished / frames, 'continuing: {} of {} parts were done'.format(sum(done), len(parts)))
	for index, ((start, end), part_file, part_param, part_done) in enumerate(zip(parts, part_files, part_params, done)):
		if part_done:
			continue
		label = 'part {}/{} · '.format(index + 1, len(parts)) if len(parts) > 1 else ''
		upscale_part(prep_file, options, start, end, part_file, (0.97 * start / frames, 0.97 * end / frames), label)
		mark_done(part_file, part_param)

	progress.update(0.97, 'joining parts' if len(parts) > 1 else 'finishing')
	list_file = parts_dir / 'parts.txt'
	list_file.write_text(''.join("file '{}'\n".format(part_file.name) for part_file in part_files))
	ffmpeg('-f', 'concat', '-safe', 0, '-i', list_file, '-c', 'copy', seedvr2_file, total_frames = frames, span = (0.97, 1.0))
	if count_frames(seedvr2_file) != frames:
		raise PipelineError('the upscaled parts add up to {} frames instead of {}'.format(count_frames(seedvr2_file), frames))
	mark_done(seedvr2_file, params)
	shutil.rmtree(parts_dir, ignore_errors = True)
	return seedvr2_file


FACEFUSION_STEPS = { 'extracting': (0.0, 0.1), 'analysing': (0.1, 0.2), 'processing': (0.2, 0.95), 'merging': (0.95, 1.0) }


def stage_faces(args : argparse.Namespace, seedvr2_file : Path, work : Path, refs : list) -> Path:
	progress.begin('faces', 'starting FaceFusion')
	faces_file = work / '03_faces.mp4'
	options = facefusion_face_args(args.face_enhancer, args.expression) +\
	[
		'--face-selector-mode', args.faces,
		'--face-selector-order', 'best-worst',
		'--face-swapper-pixel-boost', args.pixel_boost,
		'--face-swapper-weight', args.identity,
		'--face-enhancer-blend', args.enhance,
		'--face-detector-score', args.detect_score,
		'--face-tracker-score', 0.25,
		'--target-frame-amount', 4,
		'--output-video-encoder', 'libx264',
		'--output-video-quality', 95,
		'--output-video-preset', 'veryfast'
	]
	params = { 'seedvr2': stage_params(seedvr2_file), 'refs': [ [ str(ref), ref.stat().st_mtime ] for ref in refs ], 'options': [ str(option) for option in options ] }

	if is_cached(faces_file, params):
		progress.cached()
		return faces_file

	def on_line(line : str) -> None:
		match = re.match(r'(\w+):\s+\d+%\|.*?\|\s*(\d+)/(\d+)', line)
		if match and match.group(1) in FACEFUSION_STEPS:
			low, high = FACEFUSION_STEPS[match.group(1)]
			done, total = int(match.group(2)), max(int(match.group(3)), 1)
			progress.update(low + (high - low) * done / total, '{} frame {}/{}'.format(match.group(1), done, total))
		elif 'TO_VIDEO' in line and 'merging' in line.lower():
			progress.update(0.95, 'merging video')

	log('faces: ' + str(len(refs)) + ' reference faces, ' + FACE_SWAPPER_MODEL + ' + ' + args.face_enhancer)
	temp_path = work / 'facefusion_temp'
	shutil.rmtree(temp_path, ignore_errors = True)  # frames of a run that was paused or stopped
	run([ FACEFUSION_PYTHON, SCRIPTS / 'run_facefusion.py', 'headless-run',
		'--source-paths', *[ ref.resolve() for ref in refs ],
		'--target-path', seedvr2_file.resolve(),
		'--output-path', faces_file.resolve(),
		'--temp-path', temp_path.resolve(),
		'--jobs-path', (work / 'facefusion_jobs').resolve(),
		'--log-level', 'info',
		*options ], on_line = on_line)
	shutil.rmtree(temp_path, ignore_errors = True)
	mark_done(faces_file, params)
	return faces_file


def rife_gpu() -> str:
	"""RIFE (Vulkan) numbers GPUs differently from CUDA; prefer the NVIDIA card over an integrated GPU."""
	result = subprocess.run([ str(RIFE_EXE), '-l' ], capture_output = True, text = True, errors = 'replace')
	output = result.stdout + result.stderr
	devices = dict(re.findall(r'^\[(\d+) ([^\]]+)\]', output, re.MULTILINE))
	nvidia = [ index for index, name in devices.items() if 'NVIDIA' in name.upper() ]
	return nvidia[0] if nvidia else 'auto'


def stage_interpolate(args : argparse.Namespace, source_file : Path, interpolated_file : Path, timeline : dict) -> Path:
	"""Render every output frame at its exact moment between the two real pictures around it (see tools/interpolate.py).
	Damaged pictures are left out, and the moments are lightly evened out (picture_times)."""
	progress.begin('interpolate', 'starting RIFE')
	target_fps = args.fps or timeline['fps']
	params = { 'source': stage_params(source_file), 'source_file': source_file.name, 'fps': target_fps, 'model': RIFE_MODEL.name, 'unique': timeline['unique'], 'method': 'timed-even-snap' }

	if is_cached(interpolated_file, params):
		progress.cached()
		return interpolated_file
	if not RIFE_MODEL.is_dir():
		raise PipelineError('RIFE model not found: ' + str(RIFE_MODEL))

	frames = count_frames(source_file)
	unique = [ index for index in timeline['unique'] if index < frames ]
	times = picture_times(unique, timeline['fps'])
	timeline_file = interpolated_file.with_name(interpolated_file.stem + '_timeline.json')
	timeline_file.write_text(json.dumps({ 'times': [ times.get(index, index / timeline['fps']) for index in range(frames) ], 'unique': unique, 'duration': frames / timeline['fps'] }))
	log('interpolate: {} real pictures of {} frames -> {:g} fps with {}'.format(len(unique), frames, target_fps, RIFE_MODEL.name))

	def on_line(line : str) -> None:
		match = re.fullmatch(r'progress (\d+) (\d+)', line)
		if match:
			done, total = int(match.group(1)), int(match.group(2))
			progress.update(done / total, 'frame {}/{}'.format(done, total))

	run([ SEEDVR2_PYTHON, SCRIPTS / 'interpolate.py', source_file, interpolated_file, timeline_file, '{:g}'.format(target_fps), RIFE_MODEL, rife_gpu() ], on_line = on_line, done_marker = 'done ')
	expected = int(round(frames / timeline['fps'] * target_fps))
	if count_frames(interpolated_file) != expected:
		raise PipelineError('interpolation produced {} frames instead of {}'.format(count_frames(interpolated_file), expected))
	mark_done(interpolated_file, params)
	return interpolated_file


def needs_smoothing(args : argparse.Namespace, timeline : dict) -> bool:
	"""Interpolate when raising the frame rate, or at the same rate when many frames only repeat the previous picture."""
	if args.fps and args.fps > timeline['fps']:
		return True
	repeats = 1 - len(timeline['unique']) / max(timeline['frames'], 1)
	return args.smooth and (repeats >= SMOOTH_MIN_REPEATS or bool(timeline.get('broken')))


def output_stem(args : argparse.Namespace, info : dict) -> str:
	parts = [ args.input.stem ]
	if args.is_section:
		parts.append(time_tag(args.start_seconds) + '-' + time_tag(args.end_seconds))
	parts.append(str(args.height) + 'p')
	if args.fps:
		parts.append('{:g}fps'.format(args.fps))
	if not args.refs:
		parts.append('noface')
	if args.size != 'original':
		parts.append('{}-{}'.format(args.size, args.format))
	return '_'.join(parts)


def stage_encode(args : argparse.Namespace, info : dict, prep_file : Path, seedvr2_file : Path, final_stage : Path, work : Path, interpolated : bool) -> dict:
	progress.begin('encode', 'encoding final video')
	output_dir = DATA / 'output'
	output_dir.mkdir(exist_ok = True)
	stem = output_stem(args, info)
	output_file = output_dir / (stem + '.mp4')
	compare_dir = output_dir / 'compare'
	compare_file = compare_dir / (stem + '_compare.mp4')
	audio_file = work / 'audio.wav'
	final_frames = count_frames(final_stage)
	fps_filter = [ 'fps={:g}'.format(args.fps) ] if args.fps and not interpolated else []

	log('encode: ' + str(output_file))
	audio_inputs = [ '-i', audio_file ] if args.audio and audio_file.is_file() else []
	video_filter = [ '-vf', ','.join(fps_filter + [ 'scale=trunc(iw/2)*2:trunc(ih/2)*2' ]) ]
	full_quality = ([ '-c:v', 'libx264', '-crf', 16, '-preset', 'slow', '-pix_fmt', 'yuv420p' ], [ '-c:a', 'aac', '-b:a', '192k' ])
	smaller = args.size != 'original'
	encodes = []
	if smaller and args.keep_master:
		# a full-quality copy for merging later, so the merged video is compressed only once
		encodes.append((Path(args.keep_master), *full_quality))
	encodes.append((output_file, *((shrink.video_args(args.size, args.format), shrink.audio_args(args.size)) if smaller else full_quality)))
	share = 0.5 if args.compare else 1.0
	for index, (target, video, audio) in enumerate(encodes):
		target.parent.mkdir(parents = True, exist_ok = True)
		maps = [ '-map', '0:v' ] + ([ '-map', '1:a', *audio, '-shortest' ] if audio_inputs else [])
		# a .part name until it is complete, so a paused or stopped run leaves no half video among the results
		temp = target.with_name(target.stem + '.part.mp4')
		ffmpeg('-i', final_stage, *audio_inputs, *maps, *video_filter, *video, '-movflags', '+faststart', temp,
			total_frames = final_frames, span = (share * index / len(encodes), share * (index + 1) / len(encodes)))
		temp.replace(target)
	if not args.compare:
		return { 'video': str(output_file), 'compare': None, 'stills': [] }

	# side by side: original (plain bicubic resize, what a normal player shows) | SeedVR2 | final
	progress.update(0.5, 'building comparison video')
	compare_dir.mkdir(exist_ok = True)
	panels = [ prep_file, seedvr2_file ] + ([ final_stage ] if final_stage != seedvr2_file else [])
	compare_fps = 'fps={:g},'.format(args.fps) if args.fps else ''
	inputs = []
	for panel in panels:
		inputs += [ '-i', panel ]
	scaled = ''.join('[{}:v]{}scale=-2:{}:flags=bicubic,setsar=1[p{}];'.format(index, compare_fps, args.height, index) for index in range(len(panels)))
	stacked = ''.join('[p{}]'.format(index) for index in range(len(panels))) + 'hstack=inputs={}[v]'.format(len(panels))
	compare_temp = compare_file.with_name(compare_file.stem + '.part.mp4')
	ffmpeg(*inputs, '-filter_complex', scaled + stacked, '-map', '[v]', '-c:v', 'libx264', '-crf', 18, '-preset', 'medium', '-pix_fmt', 'yuv420p', compare_temp, total_frames = final_frames, span = (0.5, 0.95))
	compare_temp.replace(compare_file)

	stills = []
	clip_duration = args.end_seconds - args.start_seconds
	for percent in (25, 50, 75):
		still = compare_dir / '{}_compare_{}.png'.format(stem, percent)
		ffmpeg('-ss', '{:.2f}'.format(clip_duration * percent / 100), '-i', compare_file, '-frames:v', 1, still)
		stills.append(str(still))
	return { 'video': str(output_file), 'compare': str(compare_file), 'stills': stills }


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
	program = argparse.ArgumentParser(description = 'Offline 1080p upscaler with reference-photo identity lock.', formatter_class = argparse.ArgumentDefaultsHelpFormatter)
	program.add_argument('input', type = Path, help = 'input video')
	program.add_argument('--refs', nargs = '+', help = 'folder(s) or photo(s) of the person; omit to skip the face stages')
	program.add_argument('--start', help = "section start, e.g. '1:58' or '118'")
	program.add_argument('--end', help = "section end, e.g. '2:35'")
	program.add_argument('--duration', help = 'section length (alternative to --end)')
	program.add_argument('--json-progress', action = 'store_true', help = 'print machine-readable progress lines (used by the front end)')
	program.add_argument('--no-compare', dest = 'compare', action = 'store_false', help = 'skip the side-by-side comparison video and stills (output\\compare\\)')
	program.add_argument('--no-audio', dest = 'audio', action = 'store_false', help = 'make the result without sound')
	program.add_argument('--elapsed', type = float, default = 0.0, help = 'seconds already spent on this video by runs before a pause (front end; counted in the progress)')
	group = program.add_argument_group('file size')
	group.add_argument('--size', default = 'original', choices = [ 'original' ] + list(shrink.QUALITIES), help = 'original = full quality (largest); near/balanced/small make the file 54-92%% smaller (see shrink.py)')
	group.add_argument('--format', default = 'av1', choices = shrink.FORMATS.keys(), help = 'codec for --size near/balanced/small: av1 smallest, h265 Apple-friendly, h264 plays everywhere')
	group.add_argument('--keep-master', help = 'with a smaller --size: also save a full-quality copy here (for merging)')
	group = program.add_argument_group('prep')
	group.add_argument('--crop', default = 'auto', help = "'auto', 'off' or W:H:X:Y")
	group.add_argument('--denoise', default = 'off', choices = DENOISE_FILTERS.keys(), help = 'SeedVR2 already repairs compression damage; use light/medium only for really noisy footage')
	group.add_argument('--gamma', type = float, default = 1.0, help = '>1 brightens dark footage, e.g. 1.3')
	group.add_argument('--contrast', type = float, default = 1.0)
	group.add_argument('--saturation', type = float, default = 1.0)
	group = program.add_argument_group('upscale')
	group.add_argument('--height', type = int, default = 1080, help = 'output height (short side)')
	group.add_argument('--model', default = '7b', choices = SEEDVR2_MODELS.keys())
	group.add_argument('--batch', type = int, default = 33, help = 'frames processed together, must be 4n+1; higher = more temporal consistency, more VRAM')
	group.add_argument('--seed', type = int, default = 42)
	group = program.add_argument_group('frame rate')
	group.add_argument('--fps', type = float, help = 'target frame rate, e.g. 24, 25 or 30; higher than the source uses RIFE interpolation')
	group.add_argument('--no-smooth', dest = 'smooth', action = 'store_false', help = 'keep repeated frames as they are instead of replacing them with in-between frames')
	group.add_argument('--motion', default = 'best', choices = [ 'best', 'fast' ], help = "'best' creates in-between frames first and then upscales and face-locks every frame (sharper, slower); 'fast' interpolates the finished frames")
	group = program.add_argument_group('faces')
	group.add_argument('--faces', default = 'one', choices = [ 'one', 'many' ], help = "'one' for single-person videos, 'many' swaps every detected face")
	group.add_argument('--identity', type = float, default = 0.5, help = '0.5 = the reference identity; lower keeps more of how the face looks in the video, higher pushes past the references')
	group.add_argument('--face-enhancer', default = FACE_ENHANCER_MODELS[0], choices = FACE_ENHANCER_MODELS)
	group.add_argument('--enhance', type = int, default = 50, help = 'face enhancer blend 0-100')
	group.add_argument('--expression', action = 'store_true', help = 'restore the original facial expression after the swap (LivePortrait)')
	group.add_argument('--pixel-boost', default = '512x512', choices = [ '256x256', '512x512', '768x768', '1024x1024' ])
	group.add_argument('--detect-score', type = float, default = 0.5, help = 'face detector confidence; lower finds more small/turned faces but risks false hits')
	group.add_argument('--min-ref-face', type = int, default = 90, help = 'ignore faces smaller than this (px) in the reference photos')
	return program


def main() -> None:
	global progress
	args = build_parser().parse_args()
	sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')

	def fail(message : str) -> None:
		if args.json_progress:
			print('@@PROGRESS ' + json.dumps({ 'status': 'error', 'message': message }), flush = True)
		sys.exit('error: ' + message)

	if (args.batch - 1) % 4:
		fail('--batch must be 4n+1 (e.g. 21, 33, 45, 61)')
	if not args.input.is_file():
		fail('input not found: ' + str(args.input))
	if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
		fail('ffmpeg/ffprobe not found in PATH')
	try:
		info = probe(args.input)
		args.start_seconds = parse_time(args.start) or 0.0
		end = parse_time(args.end)
		if end is None and args.duration:
			end = args.start_seconds + parse_time(args.duration)
		args.end_seconds = min(end, info['duration']) if end is not None else info['duration']
	except (ValueError, subprocess.CalledProcessError, StopIteration) as error:
		fail(str(error))
	if args.start_seconds >= args.end_seconds:
		fail('the section is empty: start {} is not before end {}'.format(format_clock(args.start_seconds), format_clock(args.end_seconds)))
	args.is_section = args.start_seconds > 0 or args.end_seconds < info['duration'] - 0.05
	if args.fps is not None and abs(args.fps - float(info['fps'])) < 0.01:
		args.fps = None

	section = args.end_seconds - args.start_seconds
	args.frames_in = max(int(round(section * float(info['fps']))), 1)
	# whether repeated frames need smoothing is only known after prep; plan for it and skip it if not
	maybe_interpolate = bool(args.fps and args.fps > float(info['fps'])) or args.smooth
	frames_out = int(round(section * args.fps)) if args.fps else args.frames_in
	try:
		ref_photos = len(collect_refs(args.refs)) if args.refs else 0
	except PipelineError as error:
		fail(str(error))
	progress = Progress(plan_phases(args.frames_in, frames_out, args.height, args.model, ref_photos, maybe_interpolate, args.motion == 'best', encode_factor(args.size, args.format, bool(args.keep_master))), args.json_progress)
	progress.started -= args.elapsed

	started = time.time()
	work = DATA / 'work' / args.input.stem
	work.mkdir(parents = True, exist_ok = True)
	print('{}: {}x{} @ {:.3f} fps, {} long{}; section {} - {} ({} frames)'.format(args.input.name, info['width'], info['height'], float(info['fps']), format_clock(info['duration']), ', audio' if info['has_audio'] else '', format_clock(args.start_seconds), format_clock(args.end_seconds), args.frames_in), flush = True)
	try:
		prep_file, timeline = stage_prep(args, info, work)
		interpolate = needs_smoothing(args, timeline)
		if maybe_interpolate and not interpolate:
			progress.skip('interpolate')
		refs = prepare_refs(args) if args.refs else []
		motion_first = interpolate and args.motion == 'best'
		upscale_input = stage_interpolate(args, prep_file, work / '02_motion.mp4', timeline) if motion_first else prep_file
		seedvr2_file = stage_seedvr2(args, upscale_input, work)
		final_stage = stage_faces(args, seedvr2_file, work, refs) if refs else seedvr2_file
		if interpolate and not motion_first:
			final_stage = stage_interpolate(args, final_stage, work / '04_interpolated.mp4', timeline)
		outputs = stage_encode(args, info, prep_file, seedvr2_file, final_stage, work, interpolate)
	except PipelineError as error:
		progress.fail(str(error))
		sys.exit('error: ' + str(error))
	progress.finish(outputs)
	log('done in {}: {}'.format(format_clock(time.time() - started), outputs['video']))


if __name__ == '__main__':
	main()
