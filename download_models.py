"""One-time model download. This is the ONLY step that uses the internet.

  download_models.bat               SeedVR2 7B + all FaceFusion models the pipeline uses
  download_models.bat 7b 7b-sharp   also fetch other SeedVR2 variants (7b, 7b-sharp, 3b)

After this, upscale.bat runs fully offline: both tools are started with a guard that
refuses every non-local network connection.
"""
import os
import subprocess
import sys

from upscale import FACE_ENHANCER_MODELS, FACEFUSION_PYTHON, SEEDVR2_MODEL_DIR, SEEDVR2_MODELS, SEEDVR2_PYTHON, TOOLS, facefusion_face_args


def main() -> None:
	model_keys = sys.argv[1:] or [ '7b' ]
	unknown = [ key for key in model_keys if key not in SEEDVR2_MODELS ]
	if unknown:
		sys.exit('unknown model(s): ' + ', '.join(unknown) + ' (choose from ' + ', '.join(SEEDVR2_MODELS) + ')')
	env = dict(os.environ, VU_ONLINE = '1')

	print('=== SeedVR2 models -> ' + str(SEEDVR2_MODEL_DIR), flush = True)
	SEEDVR2_MODEL_DIR.mkdir(parents = True, exist_ok = True)
	subprocess.run([ str(SEEDVR2_PYTHON), str(TOOLS / 'seedvr2_setup.py'), str(SEEDVR2_MODEL_DIR), *[ SEEDVR2_MODELS[key] for key in model_keys ] ], env = env, check = True)

	print('=== FaceFusion models', flush = True)
	for face_enhancer_model in FACE_ENHANCER_MODELS:
		subprocess.run([ str(FACEFUSION_PYTHON), str(TOOLS / 'ff_setup.py'), *facefusion_face_args(face_enhancer_model, expression = True) ], env = env, check = True)

	print('\nAll models downloaded. From now on upscale.bat works without internet.')


if __name__ == '__main__':
	main()
