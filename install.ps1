# One-time setup of everything that is not in the repository: SeedVR2, FaceFusion, two local
# Pythons with their packages, RIFE and the model weights (~25 GB download in total).
# start.bat runs this automatically on the first start. Safe to run again: finished steps are
# skipped, so it also repairs a half-finished installation. After this, nothing uses the internet.
#
# Needs: Windows 10/11, an NVIDIA GPU with a current driver, and git, uv and ffmpeg in PATH:
#   winget install Git.Git astral-sh.uv Gyan.FFmpeg

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$tools = Join-Path $root 'tools'

# pinned versions: newer upstream releases may change options this project relies on
$SEEDVR2_REPO = 'https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git'
$SEEDVR2_COMMIT = '4490bd1f482e026674543386bb2a4d176da245b9'
$FACEFUSION_REPO = 'https://github.com/facefusion/facefusion.git'
$FACEFUSION_COMMIT = '72470819a0373be3388b3929c8f8f311f418fc3c'
$PYTHONS = '3.13.14', '3.12.13'
$TORCH = 'torch==2.14.1+cu130', 'torchvision==0.29.1+cu130'
$TORCH_INDEX = 'https://download.pytorch.org/whl/cu130'
$RIFE_URL = 'https://github.com/TNTwise/rife-ncnn-vulkan/releases/download/20250112/windows.zip'
$RIFE_MODEL = 'rife-v4.26'

function Step([string] $text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }

function Native([string] $what, [scriptblock] $command) {
	# Out-Host keeps program output on screen and out of function return values
	& $command | Out-Host
	if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Require([string] $command, [string] $hint) {
	if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "'$command' was not found. $hint" }
}

function Checkout([string] $url, [string] $commit, [string] $target) {
	if ((Test-Path (Join-Path $target '.git')) -and ((git -C $target rev-parse HEAD) -eq $commit)) {
		Write-Host "already at $($commit.Substring(0, 7))"
		return
	}
	if (-not (Test-Path (Join-Path $target '.git'))) {
		Native 'git init' { git init --quiet $target }
		Native 'git remote' { git -C $target remote add origin $url }
	}
	Native "downloading $url" { git -C $target fetch --quiet --depth 1 origin $commit }
	Native 'git checkout' { git -C $target checkout --quiet --force FETCH_HEAD }
}

function Venv([string] $folder, [string] $python) {
	$exe = Join-Path $folder 'Scripts\python.exe'
	if (-not (Test-Path $exe)) { Native "creating $folder" { uv venv --quiet --python $python --managed-python $folder } }
	return $exe
}

try {
	Step 'Checking requirements'
	Require git 'Install it with:  winget install Git.Git'
	Require uv 'Install it with:  winget install astral-sh.uv'
	Require ffmpeg 'Install it with:  winget install Gyan.FFmpeg'
	Require nvidia-smi 'An NVIDIA graphics card with a current driver is required.'
	Write-Host 'git, uv, ffmpeg and an NVIDIA driver found'

	Step 'SeedVR2 (upscaler)'
	Checkout $SEEDVR2_REPO $SEEDVR2_COMMIT (Join-Path $tools 'seedvr2')

	Step 'FaceFusion (face lock)'
	Checkout $FACEFUSION_REPO $FACEFUSION_COMMIT (Join-Path $tools 'facefusion')

	Step 'Python (inside this folder, not system-wide)'
	$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'
	Native 'installing Python' { uv python install --no-bin @PYTHONS }

	Step 'Packages for SeedVR2 and RIFE (includes PyTorch, ~3 GB)'
	$seedvr2Python = Venv (Join-Path $tools 'seedvr2\.venv') $PYTHONS[0]
	Native 'installing PyTorch' { uv pip install --quiet --python $seedvr2Python @TORCH --index-url $TORCH_INDEX }
	Native 'installing packages' { uv pip install --quiet --python $seedvr2Python -r (Join-Path $tools 'requirements\seedvr2.txt') }

	Step 'Packages for FaceFusion'
	$facefusionPython = Venv (Join-Path $tools 'facefusion\.venv') $PYTHONS[1]
	Native 'installing packages' { uv pip install --quiet --python $facefusionPython -r (Join-Path $tools 'requirements\facefusion.txt') }

	Step "RIFE frame interpolation ($RIFE_MODEL)"
	$rife = Join-Path $tools 'rife'
	if ((Test-Path (Join-Path $rife 'rife-ncnn-vulkan.exe')) -and (Test-Path (Join-Path $rife "$RIFE_MODEL\flownet.bin"))) {
		Write-Host 'already installed'
	} else {
		$temp = Join-Path $env:TEMP ('rife-' + [guid]::NewGuid().ToString('N'))
		New-Item -ItemType Directory -Force $temp | Out-Null
		try {
			Native 'downloading RIFE (830 MB)' { curl.exe --location --fail --progress-bar --output (Join-Path $temp 'rife.zip') $RIFE_URL }
			Native 'unpacking RIFE' { tar.exe -xf (Join-Path $temp 'rife.zip') -C $temp }
			$exe = Get-ChildItem $temp -Recurse -Filter 'rife-ncnn-vulkan.exe' | Select-Object -First 1
			New-Item -ItemType Directory -Force $rife | Out-Null
			Copy-Item $exe.FullName $rife -Force
			Get-ChildItem $exe.Directory.FullName -Filter 'LICENSE*' | Copy-Item -Destination $rife -Force
			Copy-Item (Join-Path $exe.Directory.FullName $RIFE_MODEL) $rife -Recurse -Force
		} finally {
			Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
		}
	}

	Step 'Models: SeedVR2 7B (~17 GB) and the FaceFusion face models (~2.5 GB)'
	Native 'downloading models' { & $seedvr2Python (Join-Path $root 'download_models.py') }

	Set-Content -Path (Join-Path $tools '.installed') -Value "installed $(Get-Date -Format s)`nseedvr2 $SEEDVR2_COMMIT`nfacefusion $FACEFUSION_COMMIT" -Encoding utf8
	Write-Host ''
	Write-Host 'Installation complete. From now on the upscaler works without internet.' -ForegroundColor Green
	Write-Host 'Optional hard block: run firewall_block.ps1 once in an Administrator PowerShell.'
	exit 0
} catch {
	Write-Host ''
	Write-Host "Installation stopped: $($_.Exception.Message)" -ForegroundColor Red
	Write-Host 'Fix the problem and start again; finished steps are skipped.'
	exit 1
}
