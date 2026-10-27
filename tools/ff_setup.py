"""Download the FaceFusion models for the given processor options (run by download_models.py with VU_ONLINE=1).

Takes the same face/processor options as a headless-run, so exactly the models the
pipeline uses are fetched, instead of FaceFusion's much larger force-download set.
"""
import os
import sys

# runs on FaceFusion's own Python, tools/facefusion/.venv in the data folder: the checkout is the folder around it
FACEFUSION_DIR = os.path.dirname(sys.prefix)

sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.stderr.reconfigure(encoding = 'utf-8', errors = 'replace')
os.chdir(FACEFUSION_DIR)
sys.path.insert(0, FACEFUSION_DIR)

import onnxruntime  # noqa: E402

onnxruntime.preload_dlls()

from facefusion import logger, state_manager  # noqa: E402
from facefusion.args import apply_args  # noqa: E402
from facefusion.core import processors_pre_check  # noqa: E402
from facefusion.program import create_program  # noqa: E402

args = vars(create_program().parse_args([ 'headless-run' ] + sys.argv[1:]))
apply_args(args, state_manager.init_item)
logger.init('info')
sys.exit(0 if processors_pre_check() else 1)
