"""The Edit tab's back end: what the page needs to play an edit live, and the export of an edit.

An edit is a list of clips. A clip is a part (in–out, seconds) of a video, played at a speed, with its own sound
on/off, brightness and crop, and a transition from the clip before it. The page plays the edit straight from
browser-playable copies, so nothing is rendered while editing. Export makes the video in pieces (the middle of
each clip and each transition) with the final encoder settings, joins them without encoding again and adds the
sound, mixed once for the whole edit: every frame and every sound sample is compressed once. The pieces are kept,
so exporting again after a small change only makes the pieces that changed.

The timing rules here (frame size, frame rate, how long a transition can be) are mirrored in ui/editor.js.
"""
import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy

import shrink

SPEEDS = [ 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 20, 30 ]
TRANSITIONS = [ 'cut', 'fade', 'black', 'wipe', 'slide' ]
# transitions where the two clips overlap (ffmpeg xfade names); 'black' fades out and in at the cut instead
XFADE = { 'fade': 'fade', 'wipe': 'wipeleft', 'slide': 'slideleft' }
SAMPLE_RATE = 48000
MAX_FPS = Fraction(60)
FULL_QUALITY = [ '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p' ]
BROWSER_EXTENSIONS = { '.mp4', '.m4v', '.webm' }
BROWSER_VIDEO = { 'h264', 'av1', 'vp8', 'vp9' }
BROWSER_AUDIO = { 'aac', 'mp3', 'opus', 'vorbis' }
PIECES_VERSION = 1
FONT_SIZES = (12, 400)      # a text's size: pixels at a frame height of 1080


def probe(path : Path) -> dict:
	"""Size, timing and codecs of a video. width/height are the upright picture in stored pixels (a phone video
	recorded sideways is turned like players turn it); sar is the pixel shape (old DV video has wide pixels)."""
	info = json.loads(subprocess.run([ 'ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path) ], capture_output = True, text = True, check = True).stdout)
	video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video' and not stream.get('disposition', {}).get('attached_pic'))
	audio = next((stream for stream in info['streams'] if stream['codec_type'] == 'audio'), None)
	# the nominal rate, like upscale.probe: files with skipped time slots have a lower average rate than they were recorded at
	rate = video.get('r_frame_rate', '0/0')
	if rate in ('0/0', '0') or Fraction(rate) > 120:
		rate = video.get('avg_frame_rate', '0/0')
	fps = Fraction(rate).limit_denominator(1001) if rate not in ('0/0', '0') else Fraction(25)
	rotation = 0
	for side in video.get('side_data_list', []):
		if 'rotation' in side:
			rotation = int(round(float(side['rotation'])))
	rotation = rotation or int(video.get('tags', {}).get('rotate', 0) or 0)
	turned = abs(rotation) % 180 == 90
	width, height = (int(video['height']), int(video['width'])) if turned else (int(video['width']), int(video['height']))
	try:
		sar = Fraction(video.get('sample_aspect_ratio', '1:1').replace(':', '/'))
	except (ValueError, ZeroDivisionError):
		sar = Fraction(1)
	if sar <= 0:
		sar = Fraction(1)
	if turned:
		sar = 1 / sar
	duration = float(info['format'].get('duration') or video.get('duration') or 0)
	playable = Path(path).suffix.lower() in BROWSER_EXTENSIONS and video.get('codec_name') in BROWSER_VIDEO and video.get('pix_fmt') in ('yuv420p', 'yuvj420p')\
		and (audio is None or audio.get('codec_name') in BROWSER_AUDIO)
	return\
	{
		'width': width, 'height': height, 'sar': float(sar), 'fps': float(fps), 'rate': '{}/{}'.format(fps.numerator, fps.denominator),
		'duration': duration, 'has_audio': audio is not None, 'playable': playable
	}


# ---------------------------------------------------------------- copies for the page

def make_proxy(source : Path, info : dict, target : Path, on_progress = None) -> None:
	"""A browser-playable copy of a video browsers cannot play (old AVI/MOV codecs, H.265): H.264 at most 720 lines
	high, with a keyframe every half second so jumping around is quick. Made on the graphics card when it can,
	otherwise by the processor. on_progress(fraction) is called while it is made."""
	temp = target.with_name('tmp_' + target.name)
	audio = [ '-map', '0:a:0', '-c:a', 'aac', '-b:a', '128k', '-ac', '2' ] if info['has_audio'] else [ '-an' ]
	gop = str(max(int(round(info['fps'] / 2)), 1))
	frames = max(info['duration'] * info['fps'], 1)
	attempts =\
	[
		([ '-hwaccel', 'auto' ], [ '-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'vbr', '-cq', '23', '-b:v', '0', '-g', gop ]),
		([], [ '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-g', gop ])
	]
	for number, (decode, encode) in enumerate(attempts):
		try:
			run_ffmpeg([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', '-y', *decode, '-i', str(source), '-map', '0:v:0', *audio,
				'-vf', "scale=-2:'2*trunc(min(ih,720)/2)',format=yuv420p", *encode, '-movflags', '+faststart', str(temp) ],
				lambda done: on_progress and on_progress(min(done / frames, 1.0)))
			break
		except RuntimeError:
			temp.unlink(missing_ok = True)
			if number == len(attempts) - 1:
				raise
	temp.replace(target)


def make_speed_preview(source : Path, info : dict, start : float, end : float, speed : float, target : Path) -> None:
	"""The part start–end already at the speed, without sound: browsers play a video at most 16× as fast."""
	temp = target.with_name('tmp_' + target.name)
	subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', '{:.3f}'.format(start), '-t', '{:.3f}'.format(end - start), '-i', str(source), '-an',
		'-vf', "setpts=(PTS-STARTPTS)/{},fps={},scale=-2:'2*trunc(min(ih,540)/2)',format=yuv420p".format(speed, info['rate']),
		'-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24', '-g', str(max(int(round(info['fps'] / 2)), 1)), '-movflags', '+faststart', str(temp) ], check = True)
	temp.replace(target)


# ---------------------------------------------------------------- the plan of an export

def even(value : float) -> int:
	return max(int(round(value / 2)) * 2, 2)


def shown_size(clip : dict, info : dict) -> tuple:
	"""Width and height of the clip's picture as it is seen (after the crop, with square pixels)."""
	crop = clip.get('crop')
	width, height = (crop['w'], crop['h']) if crop else (info['width'], info['height'])
	return width * info['sar'], height


def plan(project : dict, infos : dict) -> dict:
	"""Frame size, frame rate, the length of every clip and transition in frames, and the pieces to make.

	The frame has the shape that most of the edit's time has, at the size of the largest clip of that shape; other clips
	are fitted in with black bars. The frame rate is the highest of the clips (at most 60): lower rates repeat frames.
	A transition takes at most half of each clip it joins."""
	clips = project['clips']
	if not clips:
		raise ValueError('the edit has no clips')
	fps = min(max(Fraction(infos[clip['path']]['rate']) for clip in clips), MAX_FPS)
	lengths = [ max(int(round((clip['out'] - clip['in']) / clip['speed'] * fps)), 1) for clip in clips ]
	# shapes within 6% of each other count as one (a crop is rarely exactly 4:3)
	shapes = []
	for clip, length in zip(clips, lengths):
		width, height = shown_size(clip, infos[clip['path']])
		shape = next((shape for shape in shapes if abs(width / height / shape['aspect'] - 1) < 0.06), None)
		if not shape:
			shape = { 'aspect': width / height, 'weight': 0, 'sizes': [] }
			shapes.append(shape)
		shape['weight'] += length
		shape['sizes'].append((width, height))
	width, height = max(max(shapes, key = lambda shape: shape['weight'])['sizes'], key = lambda size: size[1])
	size = (even(width), even(height))

	count = len(clips)
	overlap = [ 0 ] * (count + 1)     # frames clip i shares with the clip before it
	kinds = [ 'cut' ] * count
	fade_in = [ 0 ] * count           # frames at the start of clip i that fade in from black
	fade_out = [ 0 ] * count
	for index, clip in enumerate(clips):
		if index == 0:
			fade_in[0] = min(int(round(float(project.get('fade_in') or 0) * fps)), lengths[0] // 2)
			continue
		transition = clip.get('transition') or {}
		kind, seconds = transition.get('type', 'cut'), float(transition.get('duration') or 1.0)
		room = min(lengths[index - 1], lengths[index]) // 2
		if kind in XFADE:
			frames = min(int(round(seconds * fps)), room)
			if frames >= 2:
				overlap[index], kinds[index] = frames, kind
		elif kind == 'black':
			half = min(int(round(seconds / 2 * fps)), room)
			if half >= 1:
				fade_out[index - 1] = fade_in[index] = half
				kinds[index] = kind
	fade_out[-1] = min(int(round(float(project.get('fade_out') or 0) * fps)), lengths[-1] // 2)
	starts = []
	position = 0
	for index, length in enumerate(lengths):
		position -= overlap[index]
		starts.append(position)
		position += length
	total = position

	pieces = []
	for index in range(count):
		if overlap[index]:
			pieces.append({ 'kind': 'transition', 'clips': [ index - 1, index ], 'start': starts[index], 'frames': overlap[index], 'xfade': XFADE[kinds[index]] })
		# a fade to or from black is never next to an overlap, so it lies at the start or end of the middle piece
		first, last = overlap[index], lengths[index] - overlap[index + 1]
		if last > first:
			pieces.append({ 'kind': 'clip', 'clips': [ index ], 'start': starts[index] + first, 'first': first, 'frames': last - first, 'fade_in': fade_in[index], 'fade_out': fade_out[index] })
	return { 'fps': fps, 'size': size, 'lengths': lengths, 'starts': starts, 'overlap': overlap, 'fade_in': fade_in, 'fade_out': fade_out, 'total': total, 'pieces': pieces }


# ---------------------------------------------------------------- making the pieces

def picture_filters(clip : dict, info : dict, size : tuple, fps : Fraction) -> list:
	"""Speed, frame rate, crop, brightness (the upscaler's eq filter) and fitting into the frame."""
	filters = [ 'setpts=(PTS-STARTPTS)/{}'.format(clip['speed']), 'fps={}/{}'.format(fps.numerator, fps.denominator) ]
	crop = clip.get('crop')
	if crop:
		filters.append('crop={w}:{h}:{x}:{y}'.format(**crop))
	if abs(clip.get('gamma', 1.0) - 1.0) > 1e-3:
		filters.append('eq=gamma={:.2f}'.format(clip['gamma']))
	width, height = shown_size(clip, info)
	scale = min(size[0] / width, size[1] / height)
	fit_width, fit_height = min(even(width * scale), size[0]), min(even(height * scale), size[1])
	filters += [ 'scale={}:{}:flags=bicubic'.format(fit_width, fit_height), 'setsar=1', 'pad={}:{}:(ow-iw)/2:(oh-ih)/2:black'.format(*size), 'format=yuv420p',
		# a part that ends a frame early still gives every frame the piece needs
		'tpad=stop_mode=clone:stop_duration=1' ]
	return filters


def input_args(clip : dict, first : int, frames : int, fps : Fraction) -> list:
	start = clip['in'] + float(first / fps) * clip['speed']
	seconds = float((frames + 2) / fps) * clip['speed']
	return [ '-ss', '{:.6f}'.format(start), '-t', '{:.6f}'.format(seconds), '-i', clip['path'] ]


def file_identity(path : str) -> list:
	stat = Path(path).stat()
	return [ str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size ]


def piece_texts(piece : dict, the_plan : dict, texts : list) -> list:
	"""The texts that show during a piece, with their first frame counted from the piece's start (negative: the
	text started in an earlier piece) and their length in frames."""
	fps, found = the_plan['fps'], []
	for text in texts:
		start, end = int(round(text['start'] * fps)), min(int(round(text['end'] * fps)), the_plan['total'])
		if end - start >= 1 and end > piece['start'] and start < piece['start'] + piece['frames']:
			found.append(dict(text, begin = start - piece['start'], length = end - start))
	return found


def piece_key(piece : dict, clips : list, the_plan : dict, video_args : list) -> str:
	# the piece's place in the edit is left out, so a piece that only moved is reused; its texts are counted from it
	parts = [ dict(file = file_identity(clips[index]['path']), **{ key: clips[index].get(key) for key in ('in', 'speed', 'crop', 'gamma') }) for index in piece['clips'] ]
	texts = [ dict({ key: text[key] for key in ('x', 'y', 'frame', 'fade', 'begin', 'length') }, picture = Path(text['file']).name) for text in piece.get('texts', []) ]
	description = { 'version': PIECES_VERSION, 'piece': { key: value for key, value in piece.items() if key not in ('clips', 'start', 'texts') }, 'clips': parts,
		'texts': texts, 'size': the_plan['size'], 'fps': str(the_plan['fps']), 'encoder': video_args }
	return hashlib.sha1(json.dumps(description, sort_keys = True).encode()).hexdigest()[:20]


def run_ffmpeg(command : list, on_frames) -> None:
	"""Runs ffmpeg with -progress on stdout; on_frames(frames done) may raise to stop it."""
	process = subprocess.Popen(command, stdout = subprocess.PIPE, stderr = subprocess.PIPE, text = True)
	try:
		for line in process.stdout:
			if line.startswith('frame=') and line[6:].strip().isdigit():
				on_frames(int(line[6:].strip()))
	except BaseException:
		process.kill()
		process.wait()
		raise
	error = process.stderr.read()
	if process.wait() != 0:
		raise RuntimeError(error.strip()[-600:] or 'ffmpeg failed')


def make_piece(piece : dict, clips : list, infos : dict, the_plan : dict, video_args : list, target : Path, on_frames) -> None:
	fps, size = the_plan['fps'], the_plan['size']
	if piece['kind'] == 'clip':
		clip = clips[piece['clips'][0]]
		filters = picture_filters(clip, infos[clip['path']], size, fps)
		if piece['fade_in']:
			filters.append('fade=t=in:s=0:n={}'.format(piece['fade_in']))
		if piece['fade_out']:
			filters.append('fade=t=out:s={}:n={}'.format(piece['frames'] - piece['fade_out'], piece['fade_out']))
		inputs = input_args(clip, piece['first'], piece['frames'], fps)
		graph = '[0:v]{}[v]'.format(','.join(filters))
	else:
		before, after = (clips[index] for index in piece['clips'])
		length_before = the_plan['lengths'][piece['clips'][0]]
		inputs = input_args(before, length_before - piece['frames'], piece['frames'], fps) + input_args(after, 0, piece['frames'], fps)
		graph = '[0:v]{}[a];[1:v]{}[b];[a][b]xfade=transition={}:duration={:.6f}:offset=0[v]'.format(
			','.join(picture_filters(before, infos[before['path']], size, fps)), ','.join(picture_filters(after, infos[after['path']], size, fps)),
			piece['xfade'], float(piece['frames'] / fps))
	if piece.get('texts'):
		inputs, graph = add_texts(piece['texts'], inputs, graph, fps, size)
	temp = target.with_name('tmp_' + target.name)
	run_ffmpeg([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', '-y', *inputs, '-filter_complex', graph, '-map', '[v]',
		'-frames:v', str(piece['frames']), '-an', *video_args, '-video_track_timescale', '90000', str(temp) ], on_frames)
	temp.replace(target)


def add_texts(texts : list, inputs : list, graph : str, fps : Fraction, size : tuple) -> tuple:
	"""Lays the texts' pictures (drawn by the page, see edTextPictures in ui/editor.js) over the piece's picture,
	each from its first to its last frame, fading in and out like the preview."""
	first_input = inputs.count('-i')
	rate = '{}/{}'.format(fps.numerator, fps.denominator)
	graph = graph[:-len('[v]')] + '[t0]'
	for number, text in enumerate(texts):
		seconds = float(text['length'] / fps)
		inputs = inputs + [ '-loop', '1', '-framerate', rate, '-t', '{:.6f}'.format(seconds), '-i', text['file'] ]
		filters = [ 'format=rgba' ]
		# drawn for a frame of another size (the page and this plan disagree): scaled to fit
		scale = size[0] / text['frame'][0] if text['frame'][0] else 1.0
		if abs(scale - 1) > 1e-3:
			filters.append('scale=iw*{0:.6f}:ih*{0:.6f}'.format(scale))
		fade = min(float(text['fade']), seconds / 2)
		if fade > 0.01:
			filters += [ 'fade=t=in:st=0:d={:.6f}:alpha=1'.format(fade), 'fade=t=out:st={:.6f}:d={:.6f}:alpha=1'.format(seconds - fade, fade) ]
		# a text that started in an earlier piece goes on where it was; a later one waits for its first frame
		if text['begin'] < 0:
			filters.append('trim=start_frame={}'.format(-text['begin']))
		filters.append('setpts=PTS-STARTPTS+{}/({})/TB'.format(max(text['begin'], 0), rate))
		graph += ';[{}:v]{}[p{}];[t{}][p{}]overlay=x={}:y={}:eof_action=pass:format=auto[t{}]'.format(
			first_input + number, ','.join(filters), number, number, number, int(round(text['x'] * scale)), int(round(text['y'] * scale)), number + 1)
	graph += ';[t{}]format=yuv420p[v]'.format(len(texts))
	return inputs, graph


# ---------------------------------------------------------------- sound

def mix_sound(clips : list, infos : dict, the_plan : dict, target : Path, on_fraction) -> bool:
	"""The sound of the whole edit as raw 48 kHz stereo float samples: every clip's sound at its speed (pitch kept),
	cross-faded where clips overlap and faded where the picture fades to or from black. False when there is no sound."""
	fps = the_plan['fps']

	def sample(frame : int) -> int:
		return int(round(Fraction(frame) * SAMPLE_RATE / fps))

	audible = [ index for index, clip in enumerate(clips) if clip.get('sound', True) and infos[clip['path']]['has_audio'] ]
	if not audible:
		return False
	mix = numpy.memmap(target, dtype = numpy.float32, mode = 'w+', shape = (max(sample(the_plan['total']), 1), 2))
	starts, lengths, overlap = the_plan['starts'], the_plan['lengths'], the_plan['overlap']
	for done, index in enumerate(audible):
		clip = clips[index]
		first, last = sample(starts[index]), sample(starts[index] + lengths[index])
		count = last - first
		# gain ramps (in samples): fading in from the clip before or from black, fading out to the next clip or to black
		ramp_in = sample(starts[index] + max(overlap[index], the_plan['fade_in'][index])) - first
		ramp_out = last - sample(starts[index] + lengths[index] - max(overlap[index + 1], the_plan['fade_out'][index]))
		tempo = [ '-af', 'atempo={}'.format(clip['speed']) ] if clip['speed'] != 1 else []
		process = subprocess.Popen([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', '{:.6f}'.format(clip['in']), '-t', '{:.6f}'.format(clip['out'] - clip['in']),
			'-i', clip['path'], '-map', '0:a:0', '-vn', *tempo, '-ac', '2', '-ar', str(SAMPLE_RATE), '-f', 'f32le', 'pipe:1' ], stdout = subprocess.PIPE, stderr = subprocess.DEVNULL)
		position = 0
		try:
			while position < count:
				data = process.stdout.read(SAMPLE_RATE * 8)
				if not data:
					break
				chunk = numpy.frombuffer(data[:len(data) // 8 * 8], dtype = numpy.float32).reshape(-1, 2)[:count - position]
				where = numpy.arange(position, position + len(chunk), dtype = numpy.float64)
				gain = numpy.ones(len(chunk))
				if ramp_in:
					gain *= numpy.clip(where / ramp_in, 0, 1)
				if ramp_out:
					gain *= numpy.clip((count - where) / ramp_out, 0, 1)
				mix[first + position:first + position + len(chunk)] += chunk * gain[:, None].astype(numpy.float32)
				position += len(chunk)
				on_fraction((done + position / max(count, 1)) / len(audible))
		finally:
			process.kill()
			process.wait()
	mix.flush()
	del mix
	return True


# ---------------------------------------------------------------- export

def export_edit(project : dict, output : Path, work : Path, size : str = 'original', video_format : str = 'av1', audio : bool = True, on_progress = None, texts : list = None) -> dict:
	"""Makes the edit as one video. size/video_format: 'original' is full quality, otherwise a shrink.py level and
	format. work keeps the finished pieces of this edit for the next export. on_progress(fraction, detail) may raise
	to stop. texts: pictures of the texts to lay over the video, the front-most last, each { file, x, y (where the
	picture goes in the frame), frame (the frame size it was drawn for), start, end (seconds), fade }.
	Returns the frame size, frame rate, length, file size and how many pieces were reused."""
	report = on_progress or (lambda fraction, detail: None)
	clips = project['clips']
	for clip in clips:
		if not Path(clip['path']).is_file():
			raise ValueError('{} is missing'.format(clip['path']))
	infos = { path: probe(Path(path)) for path in { clip['path'] for clip in clips } }
	the_plan = plan(project, infos)
	if size == 'original':
		video_args, audio_bitrate = FULL_QUALITY, '192k'
	else:
		video_args, audio_bitrate = shrink.video_args(size, video_format), '{}k'.format(shrink.AUDIO_KBPS[size])
	work.mkdir(parents = True, exist_ok = True)
	for old in work.glob('tmp_*'):
		old.unlink(missing_ok = True)

	for piece in the_plan['pieces']:
		piece['texts'] = piece_texts(piece, the_plan, texts or [])
	# the pieces: 90% of the progress, by frames to make
	pieces = [ (piece, work / (piece_key(piece, clips, the_plan, video_args) + '.mp4')) for piece in the_plan['pieces'] ]
	to_make = sum(piece['frames'] for piece, path in pieces if not path.is_file()) or 1
	made = 0
	reused = 0
	for number, (piece, path) in enumerate(pieces, 1):
		if path.is_file():
			reused += 1
			continue
		detail = 'part {} of {}'.format(number, len(pieces))
		report(0.9 * made / to_make, detail)
		make_piece(piece, clips, infos, the_plan, video_args, path, lambda frames, before = made, text = detail: report(0.9 * min(before + frames, to_make) / to_make, text))
		made += piece['frames']

	sound = work / 'sound.f32'
	has_sound = audio and mix_sound(clips, infos, the_plan, sound, lambda fraction: report(0.9 + 0.07 * fraction, 'sound'))
	report(0.97, 'joining')
	listing = work / 'pieces.txt'
	listing.write_text(''.join("file '{}'\n".format(path.as_posix()) for _, path in pieces), encoding = 'utf-8')
	temp = output.with_name(output.stem + '.part.mp4')
	sound_args = [ '-f', 'f32le', '-ar', str(SAMPLE_RATE), '-ac', '2', '-i', str(sound) ] if has_sound else []
	output.parent.mkdir(parents = True, exist_ok = True)
	result = subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), *sound_args,
		'-map', '0:v:0', *([ '-map', '1:a:0', '-c:a', 'aac', '-b:a', audio_bitrate ] if has_sound else [ '-an' ]),
		'-c:v', 'copy', '-movflags', '+faststart', str(temp) ], capture_output = True, text = True)
	sound.unlink(missing_ok = True)
	if result.returncode != 0:
		temp.unlink(missing_ok = True)
		raise RuntimeError('joining failed: ' + result.stderr.strip()[-500:])
	temp.replace(output)
	# keep only this edit's pieces and text pictures, for the next export
	used = { path.name for _, path in pieces } | { Path(text['file']).name for text in texts or [] }
	for old in [ *work.glob('*.mp4'), *work.glob('text-*.png') ]:
		if old.name not in used:
			old.unlink(missing_ok = True)
	report(1.0, 'done')
	fps = the_plan['fps']
	return { 'output': str(output), 'width': the_plan['size'][0], 'height': the_plan['size'][1], 'fps': float(fps),
		'duration': float(the_plan['total'] / fps), 'size': output.stat().st_size, 'pieces': len(pieces), 'reused': reused }
