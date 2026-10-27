"""Time-accurate frame interpolation with RIFE.

Old cameras and phones often stored far fewer real pictures than their nominal frame rate and
filled the gaps with repeated copies, at irregular intervals. Interpolating such a file frame by
frame gives stutter: nothing happens between the copies, then everything jumps. Instead, this
takes the moments at which the real pictures were taken and renders every output frame at its
exact time between the two real pictures around it.

usage: interpolate.py INPUT OUTPUT TIMELINE_JSON TARGET_FPS MODEL_DIR GPU_ID
  TIMELINE_JSON  { "times": [ seconds of every input frame ], "unique": [ indices of the real pictures ], "duration": seconds }
Prints 'progress <done> <total>' lines.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_guard  # noqa: E402,F401
from rife_ncnn_vulkan_python import Rife  # noqa: E402

SCENE_CUT = 45.0  # mean absolute difference (0-255) of tiny greyscale thumbnails above which two pictures are not interpolated


def video_size(path : str) -> tuple:
	output = subprocess.run([ 'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path ], capture_output = True, text = True, check = True).stdout
	width, height = output.strip().split(',')[:2]
	return int(width), int(height)


def thumbnail(frame : numpy.ndarray) -> numpy.ndarray:
	return frame[::16, ::16].mean(axis = 2)


def main() -> None:
	input_path, output_path, timeline_path, target_fps, model_dir, gpu = sys.argv[1:7]
	target_fps = float(target_fps)
	timeline = json.loads(Path(timeline_path).read_text())
	times = timeline['times']
	unique = set(timeline['unique']) | { 0 }
	total = int(round(timeline['duration'] * target_fps))
	width, height = video_size(input_path)
	frame_bytes = width * height * 3

	decoder = subprocess.Popen([ 'ffmpeg', '-v', 'error', '-i', input_path, '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-' ], stdout = subprocess.PIPE)
	encoder = subprocess.Popen([ 'ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '{}x{}'.format(width, height), '-r', '{:g}'.format(target_fps), '-i', '-',
		'-c:v', 'libx264', '-crf', '12', '-preset', 'medium', '-pix_fmt', 'yuv420p', output_path ], stdin = subprocess.PIPE)
	rife = Rife(gpuid = int(gpu) if gpu.isdigit() else 0, model = model_dir, width = width, height = height, num_threads = 2)

	def real_pictures():
		for index in range(len(times)):
			data = decoder.stdout.read(frame_bytes)
			if len(data) < frame_bytes:
				return
			if index in unique:
				yield times[index], numpy.frombuffer(data, dtype = numpy.uint8).reshape(height, width, 3)

	def is_cut(picture0, picture1) -> bool:
		return picture1 is not None and numpy.abs(thumbnail(picture0) - thumbnail(picture1)).mean() > SCENE_CUT

	pictures = real_pictures()
	time0, picture0 = next(pictures)
	time1, picture1 = next(pictures, (None, None))
	cut = is_cut(picture0, picture1)
	for index in range(total):
		moment = index / target_fps
		while time1 is not None and moment >= time1:
			time0, picture0 = time1, picture1
			time1, picture1 = next(pictures, (None, None))
			cut = is_cut(picture0, picture1)
		snap = 0.5 / target_fps  # show a real picture instead of a blend when it falls within half an output frame
		if time1 is None or moment - time0 <= snap:
			frame = picture0
		elif time1 - moment < snap:
			frame = picture1
		else:
			step = (moment - time0) / (time1 - time0)
			frame = (picture0 if step < 0.5 else picture1) if cut else rife.process_cv2(picture0, picture1, step)
		encoder.stdin.write(frame.tobytes())
		if index % 5 == 0 or index == total - 1:
			print('progress {} {}'.format(index + 1, total), flush = True)

	encoder.stdin.close()
	decoder.stdout.close()
	decoder.kill()
	if encoder.wait() != 0:
		sys.exit('encoding failed')
	# the RIFE binding's ncnn/Vulkan teardown crashes at process exit (it never releases its GPU instance),
	# after all work is done; this marker tells the pipeline the output is complete
	print('done {}'.format(total), flush = True)
	os._exit(0)


if __name__ == '__main__':
	main()
