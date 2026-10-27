"""Make videos smaller for sharing, and measure what that costs in quality.

Three levels times three formats. The settings were chosen with VMAF (Netflix's perceptual
quality score, 0-100) on a 27 s upscaled 1080p home video; EXPECTED lists the measured results,
relative to the pipeline's full-quality output (H.264 CRF 16, ~9 Mbit/s).

usage: shrink.bat [--quality near|balanced|small] [--format av1|h265|h264] [--no-measure] video [video ...]
       writes <name>_<quality>-<format>.mp4 next to each video
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUALITIES = { 'near': 'Near lossless', 'balanced': 'Balanced', 'small': 'Small' }
FORMATS =\
{
	'av1': { 'label': 'AV1', 'crf': { 'near': 30, 'balanced': 36, 'small': 42 } },
	'h265': { 'label': 'H.265', 'crf': { 'near': 24, 'balanced': 28, 'small': 31 } },
	'h264': { 'label': 'H.264', 'crf': { 'near': 21, 'balanced': 25, 'small': 28 } }
}
AUDIO_KBPS = { 'near': 128, 'balanced': 96, 'small': 96 }
# (size reduction %, mean VMAF) measured on the test video
EXPECTED =\
{
	('near', 'av1'): (84, 98.2), ('balanced', 'av1'): (89, 94.9), ('small', 'av1'): (92, 90.1),
	('near', 'h265'): (79, 98.5), ('balanced', 'h265'): (87, 94.7), ('small', 'h265'): (91, 89.4),
	('near', 'h264'): (54, 98.8), ('balanced', 'h264'): (75, 94.7), ('small', 'h264'): (83, 89.0)
}
# relative encoding time per frame versus the pipeline's own H.264 encode
ENCODE_SPEED = { 'av1': 1.3, 'h265': 4.5, 'h264': 1.0 }


def video_args(quality : str, video_format : str) -> list:
	crf = str(FORMATS[video_format]['crf'][quality])
	if video_format == 'av1':
		return [ '-c:v', 'libsvtav1', '-preset', '5', '-crf', crf, '-svtav1-params', 'tune=0', '-pix_fmt', 'yuv420p' ]
	if video_format == 'h265':
		return [ '-c:v', 'libx265', '-preset', 'slow', '-crf', crf, '-tag:v', 'hvc1', '-x265-params', 'log-level=error', '-pix_fmt', 'yuv420p' ]
	return [ '-c:v', 'libx264', '-preset', 'slow', '-crf', crf, '-pix_fmt', 'yuv420p' ]


def audio_args(quality : str) -> list:
	return [ '-c:a', 'aac', '-b:a', '{}k'.format(AUDIO_KBPS[quality]) ]


def describe(vmaf : float) -> str:
	if vmaf >= 97:
		return 'no visible difference'
	if vmaf >= 93:
		return 'only visible side by side'
	if vmaf >= 88:
		return 'slightly softer up close'
	return 'visible loss'


def output_name(source : Path, quality : str, video_format : str) -> Path:
	return source.with_name('{}_{}-{}.mp4'.format(source.stem, quality, video_format))


def probe(path : Path) -> dict:
	info = json.loads(subprocess.run([ 'ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path) ], capture_output = True, text = True, check = True).stdout)
	video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
	frames = int(video.get('nb_frames') or 0)
	duration = float(info['format'].get('duration') or 0)
	return { 'duration': duration, 'frames': frames, 'width': int(video['width']), 'height': int(video['height']) }


def measure_vmaf(distorted : Path, reference : Path, width : int, height : int, frames : int) -> tuple:
	"""Mean VMAF and the score of the worst 5% of frames; long videos are sampled to stay quick."""
	subsample = max(1, frames // 900)
	with tempfile.TemporaryDirectory() as folder:
		graph = '[0:v]scale={w}:{h}:flags=bicubic,setpts=PTS-STARTPTS,format=yuv420p[d];[1:v]setpts=PTS-STARTPTS,format=yuv420p[r];[d][r]libvmaf=n_threads=16:n_subsample={s}:log_fmt=json:log_path=vmaf.json'.format(w = width, h = height, s = subsample)
		subprocess.run([ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(distorted.resolve()), '-i', str(reference.resolve()), '-lavfi', graph, '-f', 'null', '-' ], check = True, cwd = folder)
		scores = sorted(frame['metrics']['vmaf'] for frame in json.loads((Path(folder) / 'vmaf.json').read_text())['frames'])
	return sum(scores) / len(scores), scores[max(int(len(scores) * 0.05) - 1, 0)]


def shrink_video(source : Path, output : Path, quality : str, video_format : str, on_progress = None, measure : bool = True) -> dict:
	"""Encode source at the given level and format; returns sizes, reduction and (optionally) measured VMAF."""
	source, output = Path(source), Path(output)
	info = probe(source)
	started = time.time()
	temp = output.with_name(output.stem + '.part.mp4')
	command = [ 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', '-y', '-i', str(source),
		'-map', '0:v:0', '-map', '0:a?', '-map_metadata', '0', '-map_chapters', '0',
		*video_args(quality, video_format), *audio_args(quality), '-movflags', '+faststart', str(temp) ]
	process = subprocess.Popen(command, stdout = subprocess.PIPE, text = True)
	try:
		for line in process.stdout:
			value = line.split('=', 1)[1].strip() if line.startswith('out_time_us=') else ''
			if value.lstrip('-').isdigit() and on_progress and info['duration']:
				on_progress(min(int(value) / 1e6 / info['duration'], 1.0) * (0.8 if measure else 1.0), 'encoding')
	except BaseException:
		process.kill()
		temp.unlink(missing_ok = True)
		raise
	if process.wait() != 0:
		temp.unlink(missing_ok = True)
		raise RuntimeError('encoding {} failed'.format(source.name))
	temp.replace(output)
	result = { 'source': str(source), 'output': str(output), 'quality': quality, 'format': video_format,
		'source_size': source.stat().st_size, 'size': output.stat().st_size, 'seconds': None, 'vmaf': None, 'vmaf_low': None }
	result['reduction'] = round(100 * (1 - result['size'] / result['source_size']), 1)
	if measure:
		if on_progress:
			on_progress(0.82, 'measuring quality')
		frames = info['frames'] or int(info['duration'] * 30)
		mean, low = measure_vmaf(output, source, info['width'], info['height'], frames)
		result.update(vmaf = round(mean, 1), vmaf_low = round(low, 1))
	result['seconds'] = round(time.time() - started, 1)
	if on_progress:
		on_progress(1.0, 'done')
	return result


def main() -> None:
	program = argparse.ArgumentParser(description = 'Make videos smaller for sharing.')
	program.add_argument('videos', nargs = '+', type = Path)
	program.add_argument('--quality', default = 'balanced', choices = QUALITIES.keys())
	program.add_argument('--format', default = 'av1', choices = FORMATS.keys())
	program.add_argument('--no-measure', dest = 'measure', action = 'store_false', help = 'skip the VMAF quality measurement')
	args = program.parse_args()
	sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
	for video in args.videos:
		output = output_name(video, args.quality, args.format)
		result = shrink_video(video, output, args.quality, args.format, on_progress = lambda f, d: print('\r{:5.1f}% {}   '.format(f * 100, d), end = '', flush = True), measure = args.measure)
		quality = ' · VMAF {} ({})'.format(result['vmaf'], describe(result['vmaf'])) if result['vmaf'] is not None else ''
		print('\r{}: {:.1f} MB -> {:.1f} MB ({}% smaller){}'.format(video.name, result['source_size'] / 1e6, result['size'] / 1e6, result['reduction'], quality))


if __name__ == '__main__':
	main()
