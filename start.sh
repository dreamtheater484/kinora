#!/usr/bin/env bash
# Starts Kinora on Linux, like start.bat on Windows: the first start installs the tools and models
# (install.sh, about 25 GB, only once), then the app runs. Arguments go to app.py (--port, --no-browser).
set -u
app="$(dirname "$(readlink -f "$0")")"

# the data folder (videos, results, tools and models), the same rule as paths.py: next to the program in a
# portable copy, ~/.local/share/kinora in an installed one (the package puts installed.txt next to app.py)
if [ -z "${KINORA_DATA:-}" ]; then
	if [ -f "$app/installed.txt" ]; then
		KINORA_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/kinora"
	else
		KINORA_DATA="$app"
	fi
fi
export KINORA_DATA

if [ ! -f "$KINORA_DATA/tools/.installed" ]; then
	echo 'First start: installing the tools and downloading the models - about 25 GB, only this once.'
	if ! "$app/install.sh"; then
		echo
		read -rp 'Press Enter to close this window. ' _ || true
		exit 1
	fi
fi
exec "$KINORA_DATA/tools/seedvr2/.venv/bin/python" "$app/app.py" "$@"
