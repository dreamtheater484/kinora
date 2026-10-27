"""Where Kinora keeps its data: input, output, work, projects, refs, models and tools (the installed tools:
SeedVR2, FaceFusion, their Pythons and RIFE). The program itself is the folder this file is in.

  portable copy   a git checkout or an unpacked zip: the data is next to the program, as it always was
  installed copy  the Windows installer and the Ubuntu package put installed.txt next to app.py: the data is in
                  %LOCALAPPDATA%\\Kinora on Windows and ~/.local/share/kinora on Linux ($XDG_DATA_HOME/kinora)

KINORA_DATA overrides both. start.bat (tools/data_root.bat) and start.sh decide the same way and pass the folder
on in KINORA_DATA, so install.ps1 / install.sh and the app always agree.
"""
import os
from pathlib import Path

APP = Path(__file__).resolve().parent
WINDOWS = os.name == 'nt'
INSTALLED_MARKER = APP / 'installed.txt'


def data_root() -> Path:
	if os.environ.get('KINORA_DATA'):
		return Path(os.environ['KINORA_DATA']).resolve()
	if not INSTALLED_MARKER.is_file():
		return APP
	if WINDOWS:
		return Path(os.environ['LOCALAPPDATA']) / 'Kinora'
	return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'kinora'


DATA = data_root()


def venv_python(tool : Path) -> Path:
	"""The Python of a tool's own virtual environment (tools/<tool>/.venv, made by install.ps1 / install.sh)."""
	return tool / '.venv' / ('Scripts/python.exe' if WINDOWS else 'bin/python')
