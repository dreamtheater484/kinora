"""Run the SeedVR2 standalone CLI with all network access disabled (unless VU_ONLINE=1)."""
import os
import runpy
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SEEDVR2_DIR = os.path.join(TOOLS_DIR, 'seedvr2')

sys.stdout.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.stderr.reconfigure(encoding = 'utf-8', errors = 'replace')
sys.path.insert(0, TOOLS_DIR)
import offline_guard  # noqa: E402,F401

sys.path.insert(0, SEEDVR2_DIR)
sys.argv = [ os.path.join(SEEDVR2_DIR, 'inference_cli.py') ] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name = '__main__')
