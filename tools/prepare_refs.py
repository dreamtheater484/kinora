"""Turn a folder of reference photos into clean single-face crops of the one person they share.

FaceFusion takes the largest face of every source photo and averages them, so a photo with a
friend in it (or a sunglasses close-up of someone else) would pull the identity off. This finds
the identity that appears across most photos, keeps only that face per photo, blanks out any
other face inside the crop and drops faces that are too small to carry identity detail.

usage: prepare_refs.py OUTPUT_DIR MIN_FACE_PX PHOTO [PHOTO ...]
"""
import json
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
# runs on FaceFusion's own Python, tools/facefusion/.venv in the data folder: the checkout is the folder around it
FACEFUSION_DIR = os.path.dirname(sys.prefix)

sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.path.insert(0, TOOLS_DIR)
import offline_guard  # noqa: E402,F401

ARGV = [ os.path.abspath(sys.argv[1]), sys.argv[2] ] + [ os.path.abspath(path) for path in sys.argv[3:] ]
os.chdir(FACEFUSION_DIR)
sys.path.insert(0, FACEFUSION_DIR)

import onnxruntime  # noqa: E402

onnxruntime.preload_dlls()

import cv2  # noqa: E402
import numpy  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from facefusion import state_manager  # noqa: E402
from facefusion.args import apply_args  # noqa: E402
from facefusion.face_creator import get_many_faces  # noqa: E402
from facefusion.program import create_program  # noqa: E402

SAME_PERSON = 0.35  # cosine similarity of ArcFace embeddings
CROP_MARGIN = 0.8  # extra context around the face box, as a fraction of its size


def load_bgr(path : str) -> numpy.ndarray:
	image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
	return cv2.cvtColor(numpy.asarray(image), cv2.COLOR_RGB2BGR)


def face_size(face) -> float:
	x1, y1, x2, y2 = face.bounding_box
	return min(x2 - x1, y2 - y1)


def similarity(a : numpy.ndarray, b : numpy.ndarray) -> float:
	return float(numpy.dot(a, b) / (numpy.linalg.norm(a) * numpy.linalg.norm(b)))


def main() -> None:
	output_dir, min_face, photo_paths = ARGV[0], int(ARGV[1]), ARGV[2:]
	args = vars(create_program().parse_args([ 'headless-run', '--face-detector-model', 'yolo_face', '--execution-providers', 'cuda' ]))
	apply_args(args, state_manager.init_item)

	photos = []
	for path in photo_paths:
		frame = load_bgr(path)
		faces = [ face for face in get_many_faces([ frame ]) if face_size(face) >= min_face ]
		photos.append({ 'path': path, 'frame': frame, 'faces': faces })

	# anchor = the face that matches a face in the most other photos
	candidates = [ (photo_index, face) for photo_index, photo in enumerate(photos) for face in photo['faces'] ]
	if not candidates:
		sys.exit('no usable faces (>= ' + str(min_face) + ' px) found in the reference photos')

	def support(candidate) -> int:
		photo_index, face = candidate
		return sum(1 for other_index, other in enumerate(photos) if other_index != photo_index and any(similarity(face.embedding_norm, other_face.embedding_norm) > SAME_PERSON for other_face in other['faces']))

	_, anchor = max(candidates, key = support)
	identity = anchor.embedding_norm

	# two passes: pick the best match per photo, re-estimate the identity from the picks
	for _ in range(2):
		picks = []
		for photo in photos:
			scored = sorted(((similarity(identity, face.embedding_norm), face) for face in photo['faces']), key = lambda item: item[0], reverse = True)
			picks.append(scored[0] if scored and scored[0][0] > SAME_PERSON else None)
		kept = [ pick[1].embedding_norm for pick in picks if pick ]
		identity = numpy.mean(kept, axis = 0)

	os.makedirs(output_dir, exist_ok = True)
	for name in os.listdir(output_dir):
		os.remove(os.path.join(output_dir, name))

	report = []
	for photo, pick in zip(photos, picks):
		name = os.path.basename(photo['path'])
		if not pick:
			reason = 'not the same person' if photo['faces'] else 'no face of at least ' + str(min_face) + ' px'
			report.append({ 'photo': name, 'kept': False, 'reason': reason })
			continue
		score, face = pick
		frame = photo['frame'].copy()
		height, width = frame.shape[:2]
		for other in photo['faces']:
			if other is not face:
				ox1, oy1, ox2, oy2 = (int(value) for value in other.bounding_box)
				frame[max(oy1, 0):max(oy2, 0), max(ox1, 0):max(ox2, 0)] = 127
		x1, y1, x2, y2 = face.bounding_box
		margin = CROP_MARGIN * max(x2 - x1, y2 - y1)
		left, top = int(max(x1 - margin, 0)), int(max(y1 - margin, 0))
		right, bottom = int(min(x2 + margin, width)), int(min(y2 + margin, height))
		crop_path = os.path.join(output_dir, os.path.splitext(name)[0] + '.png')
		cv2.imencode('.png', frame[top:bottom, left:right])[1].tofile(crop_path)
		report.append({ 'photo': name, 'kept': True, 'face_px': int(face_size(face)), 'similarity': round(score, 2), 'other_faces_blanked': len(photo['faces']) - 1 })

	for line in report:
		print(json.dumps(line))
	print('kept {} of {} photos'.format(sum(1 for line in report if line['kept']), len(report)))


if __name__ == '__main__':
	main()
