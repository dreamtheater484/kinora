#!/usr/bin/env bash
# One-time setup of everything that is not in the program: SeedVR2, FaceFusion, two local Pythons with their
# packages, RIFE and the model weights (~25 GB download in total). The Linux version of install.ps1, with the
# same pinned versions; start.sh runs it automatically on the first start. Safe to run again: finished steps
# are skipped, so it also repairs a half-finished installation. After this, nothing uses the internet.
# Everything goes into Kinora's data folder (see paths.py): next to this script in a portable copy,
# ~/.local/share/kinora in an installed one.
#
# Needs: Ubuntu 22.04 or newer on x86_64, an NVIDIA GPU with a current driver, and git, curl and ffmpeg
# (the Kinora package depends on them; for a portable copy: sudo apt install git curl ffmpeg libvulkan1 libgl1).
# uv is used from PATH when it is there; otherwise a pinned copy is downloaded into the data folder.
set -Eeuo pipefail

app="$(dirname "$(readlink -f "$0")")"
# the same rule as paths.py and start.sh (which passes the folder in KINORA_DATA)
if [ -n "${KINORA_DATA:-}" ]; then
	data="$KINORA_DATA"
elif [ -f "$app/installed.txt" ]; then
	data="${XDG_DATA_HOME:-$HOME/.local/share}/kinora"
else
	data="$app"
fi
export KINORA_DATA="$data"  # so download_models.py agrees
tools="$data/tools"

# pinned versions: newer upstream releases may change options this project relies on (the same as install.ps1)
SEEDVR2_REPO='https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git'
SEEDVR2_COMMIT='4490bd1f482e026674543386bb2a4d176da245b9'
FACEFUSION_REPO='https://github.com/facefusion/facefusion.git'
FACEFUSION_COMMIT='72470819a0373be3388b3929c8f8f311f418fc3c'
PYTHONS=(3.13.14 3.12.13)
TORCH=(torch==2.14.1+cu130 torchvision==0.29.1+cu130)
TORCH_INDEX='https://download.pytorch.org/whl/cu130'
RIFE_URL='https://github.com/TNTwise/rife-ncnn-vulkan/releases/download/20250112/ubuntu.zip'
RIFE_MODEL='rife-v4.26'
UV_URL='https://github.com/astral-sh/uv/releases/download/0.11.29/uv-x86_64-unknown-linux-gnu.tar.gz'
UV_SHA256='04f8b82f5d47f0512dcd32c67a4a6f16a0ea27c81537c338fd0ad6b23cebe829'

step() { printf '\n\033[36m== %s\033[0m\n' "$1"; }

fail() {
	printf '\n\033[31mInstallation stopped: %s\033[0m\n' "$1"
	echo 'Fix the problem and start again; finished steps are skipped.'
	exit 1
}

# any other failing command stops the installation with the same message
trap 'fail "$BASH_COMMAND failed"' ERR

native() {
	local what="$1"
	shift
	"$@" || fail "$what failed (exit code $?)"
}

require() {
	command -v "$1" > /dev/null || fail "'$1' was not found. $2"
}

checkout() {
	local url="$1" commit="$2" target="$3"
	if [ -d "$target/.git" ] && [ "$(git -C "$target" rev-parse HEAD 2> /dev/null)" = "$commit" ]; then
		echo "already at ${commit:0:7}"
		return
	fi
	if [ ! -d "$target/.git" ]; then
		native 'git init' git init --quiet "$target"
		native 'git remote' git -C "$target" remote add origin "$url"
	fi
	native "downloading $url" git -C "$target" fetch --quiet --depth 1 origin "$commit"
	native 'git checkout' git -C "$target" checkout --quiet --force FETCH_HEAD
}

make_venv() {
	local folder="$1" python="$2"
	if [ ! -x "$folder/bin/python" ]; then
		native "creating $folder" "$uv" venv --quiet --python "$python" --managed-python "$folder"
	fi
}

temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT

step 'Checking requirements'
[ "$(uname -m)" = x86_64 ] || fail 'Kinora needs a 64-bit Intel or AMD processor (x86_64).'
require git 'Install it with:  sudo apt install git'
require curl 'Install it with:  sudo apt install curl'
require ffmpeg 'Install it with:  sudo apt install ffmpeg'
require nvidia-smi 'An NVIDIA graphics card with a current driver is required (Ubuntu: Software & Updates, Additional Drivers).'
uv="$(command -v uv || true)"
if [ -z "$uv" ]; then
	uv="$tools/uv/uv"
	if [ ! -x "$uv" ]; then
		echo 'uv is not installed: downloading it into the data folder'
		native 'downloading uv' curl --location --fail --silent --show-error --output "$temp/uv.tar.gz" "$UV_URL"
		echo "$UV_SHA256  $temp/uv.tar.gz" | sha256sum --check --quiet || fail 'the downloaded uv is not the expected file'
		mkdir -p "$tools/uv"
		native 'unpacking uv' tar -xzf "$temp/uv.tar.gz" -C "$tools/uv" --strip-components 1
	fi
fi
echo 'git, curl, ffmpeg, uv and an NVIDIA driver found'

step 'SeedVR2 (upscaler)'
checkout "$SEEDVR2_REPO" "$SEEDVR2_COMMIT" "$tools/seedvr2"

step 'FaceFusion (face lock)'
checkout "$FACEFUSION_REPO" "$FACEFUSION_COMMIT" "$tools/facefusion"

step 'Python (inside the data folder, not system-wide)'
export UV_PYTHON_INSTALL_DIR="$tools/python"
native 'installing Python' "$uv" python install --no-bin "${PYTHONS[@]}"

step 'Packages for SeedVR2 and RIFE (includes PyTorch, ~3 GB)'
seedvr2_python="$tools/seedvr2/.venv/bin/python"
make_venv "$tools/seedvr2/.venv" "${PYTHONS[0]}"
native 'installing PyTorch' "$uv" pip install --quiet --python "$seedvr2_python" "${TORCH[@]}" --index-url "$TORCH_INDEX"
native 'installing packages' "$uv" pip install --quiet --python "$seedvr2_python" -r "$app/tools/requirements/seedvr2.txt"

step 'Packages for FaceFusion'
facefusion_python="$tools/facefusion/.venv/bin/python"
make_venv "$tools/facefusion/.venv" "${PYTHONS[1]}"
native 'installing packages' "$uv" pip install --quiet --python "$facefusion_python" -r "$app/tools/requirements/facefusion.txt"

step "RIFE frame interpolation ($RIFE_MODEL)"
rife="$tools/rife"
if [ -x "$rife/rife-ncnn-vulkan" ] && [ -f "$rife/$RIFE_MODEL/flownet.bin" ]; then
	echo 'already installed'
else
	native 'downloading RIFE (830 MB)' curl --location --fail --progress-bar --output "$temp/rife.zip" "$RIFE_URL"
	native 'unpacking RIFE' "$seedvr2_python" -I -m zipfile -e "$temp/rife.zip" "$temp/rife"
	exe="$(find "$temp/rife" -type f -name rife-ncnn-vulkan -print -quit)"
	[ -n "$exe" ] || fail 'rife-ncnn-vulkan is not in the downloaded zip'
	mkdir -p "$rife"
	install -m 755 "$exe" "$rife/"
	cp "$(dirname "$exe")"/LICENSE* "$rife/" 2> /dev/null || true
	rm -rf "${rife:?}/$RIFE_MODEL"
	cp -r "$(dirname "$exe")/$RIFE_MODEL" "$rife/"
fi

step 'Models: SeedVR2 7B (~17 GB) and the FaceFusion face models (~2.5 GB)'
native 'downloading models' "$seedvr2_python" "$app/download_models.py"

printf 'installed %s\nseedvr2 %s\nfacefusion %s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$SEEDVR2_COMMIT" "$FACEFUSION_COMMIT" > "$tools/.installed"
printf '\n\033[32mInstallation complete, in %s. From now on Kinora works without internet.\033[0m\n' "$data"
