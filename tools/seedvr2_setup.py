"""Download and verify SeedVR2 model weights (run by download_models.py with VU_ONLINE=1)."""
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SEEDVR2_DIR = os.path.join(TOOLS_DIR, 'seedvr2')

sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.stderr.reconfigure(encoding = 'utf-8', errors = 'replace')
os.chdir(SEEDVR2_DIR)
sys.path.insert(0, SEEDVR2_DIR)

from src.utils.debug import Debug  # noqa: E402
from src.utils.downloads import download_weight  # noqa: E402
from src.utils.model_registry import DEFAULT_VAE  # noqa: E402

model_dir = sys.argv[1]
dit_models = sys.argv[2:]
debug = Debug(enabled = True)

for dit_model in dit_models:
	if not download_weight(dit_model = dit_model, vae_model = DEFAULT_VAE, model_dir = model_dir, debug = debug):
		sys.exit(1)
