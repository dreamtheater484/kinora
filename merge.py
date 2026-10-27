"""Join finished videos into one file, in the given order, with a chapter per video.

Videos may differ in size, frame rate and sound: every part is brought to one frame size
(pillar/letterboxed, never stretched), one frame rate and 48 kHz stereo sound (silence where a
part has none), then joined without another re-encode.

usage: merge.bat output.mp4 part1.mp4 part2.mp4 ...
"""
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import shrink
from paths import DATA

WORK = DATA / 'work' / '_merge'


def probe(path : Path) -> dict:
	info = json.loads(subprocess.run([ 'ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path) ], capture_output = True, text = True, check = True).stdout)
	video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
	return\
	{
		'width': int(video['width']),
		'height': int(video['height']),
		'fps': float(Fraction(video.get('avg_frame_rate') or video['r_frame_rate'])),
		'duration': float(info['format']['duration']),
		'has_audio': any(stream['codec_type'] == 'audio' for stream in info['streams'])
	}


def merge_videos(parts : list, output : Path, titles : list = None, fps : float = None, on_progress = None, audio : bool = True, size : str = 'original', video_format : str = 'av1') -> Path:
	"""parts: video paths in order. on_progress(fraction, detail) is called while working. audio=False makes a silent video.
	size/video_format: 'original' keeps full quality, otherwise a shrink.py level and format."""
	if size == 'original':
		video_args = [ '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p' ]
		audio_bitrate = '192k'
	else:
		video_args = shrink.video_args(size, video_format)
		audio_bitrate = '{}k'.format(shrink.AUDIO_KBPS[size])
	parts = [ Path(part) for part in parts ]
	infos = [ probe(part) for part in parts ]
	height = max(info['height'] for info in infos)
	width = max(int(round(info['width'] * height / info['height'] / 2)) * 2 for info in infos)
	fps = fps or min(max(info['fps'] for info in infos), 60.0)
	total = sum(info['duration'] for info in infos) or 1.0
	WORK.mkdir(parents = True, exist_ok = True)
	for old in WORK.glob('*'):
		old.unlink()

	done = 0.0
	normalized = []
	for index, (part, info) in enumerate(zip(parts, infos)):
		target = WORK / '{:04d}.mp4'.format(index)
		video_filter = 'scale=-2:{h},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={f},format=yuv420p'.format(w = width, h = height, f = fps)
		if audio:
			audio_input = [] if info['has_audio'] else [ '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo' ]
			audio_args = [ '-map', '0:a:0' if info['has_audio'] else '1:a:0', '-c:a', 'aac', '-b:a', audio_bitrate, '-ar', '48000', '-ac', '2' ]
		else:
			audio_input, audio_args = [], [ '-an' ]
		command = [ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', '-y', '-i', str(part), *audio_input,
			'-map', '0:v:0', '-t', '{:.3f}'.format(info['duration']),
			'-vf', video_filter, *video_args, '-video_track_timescale', '90000',
			*audio_args, str(target) ]
		process = subprocess.Popen(command, stdout = subprocess.PIPE, text = True)
		try:
			for line in process.stdout:
				value = line.split('=', 1)[1].strip() if line.startswith('out_time_us=') else ''
				if value.lstrip('-').isdigit() and on_progress:
					seconds = max(int(value), 0) / 1e6
					on_progress(min((done + seconds) / total, 1.0) * 0.97, 'part {} of {}'.format(index + 1, len(parts)))
		except BaseException:
			# on_progress may raise to cancel
			process.kill()
			raise
		if process.wait() != 0:
			raise RuntimeError('could not prepare {} for merging'.format(part.name))
		normalized.append(target)
		done += info['duration']

	# chapters, one per part
	chapters = [ ';FFMETADATA1' ]
	start = 0
	for index, (part, info) in enumerate(zip(parts, infos)):
		end = start + int(info['duration'] * 1000)
		title = (titles[index] if titles else part.stem).replace('=', '\\=').replace(';', '\\;').replace('#', '\\#')
		chapters += [ '[CHAPTER]', 'TIMEBASE=1/1000', 'START={}'.format(start), 'END={}'.format(end), 'title={}'.format(title) ]
		start = end
	(WORK / 'chapters.txt').write_text('\n'.join(chapters) + '\n', encoding = 'utf-8')
	(WORK / 'list.txt').write_text(''.join("file '{}'\n".format(path.as_posix()) for path in normalized), encoding = 'utf-8')

	if on_progress:
		on_progress(0.98, 'joining')
	output.parent.mkdir(parents = True, exist_ok = True)
	result = subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(WORK / 'list.txt'), '-i', str(WORK / 'chapters.txt'),
		'-map', '0', '-map_metadata', '1', '-map_chapters', '1', '-c', 'copy', '-movflags', '+faststart', str(output) ], capture_output = True, text = True)
	if result.returncode != 0:
		raise RuntimeError('joining failed: ' + result.stderr.strip()[-500:])
	for path in normalized:
		path.unlink(missing_ok = True)
	if on_progress:
		on_progress(1.0, 'done')
	return output


if __name__ == '__main__':
	if len(sys.argv) < 3:
		sys.exit(__doc__)
	merge_videos(sys.argv[2:], Path(sys.argv[1]), on_progress = lambda fraction, detail: print('\r{:5.1f}% {}'.format(fraction * 100, detail), end = '', flush = True))
	print('\nwritten: ' + sys.argv[1])
