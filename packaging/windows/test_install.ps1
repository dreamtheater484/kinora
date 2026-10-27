# End-to-end test of the Windows installer on this PC, done the way a user does it. Run it before every release; a
# version is only tagged when this ends with PASSED:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\test_install.ps1
#
# 1. builds the test installer: kinora.iss with /DTestLaunch, identical to the release one except that a silent install
#    also runs the "Start Kinora" of the last page (with a test port and no browser)
# 2. installs it for the current user, silently; the installer then starts Kinora itself, like its last page does
# 3. checks that this Kinora is a normal 64-bit process, without the installer's Redirection Guard
# 4. waits for the complete first start (install.ps1, about 25 GB the first time; its log is install.log in the data
#    folder) and for the app to answer with the version in VERSION
# 5. upscales a 3-second test clip on the GPU with the installed copy (720p, 50 fps: SeedVR2, PyTorch, RIFE, ffmpeg)
#    and checks that FaceFusion's Python finds the GPU
# It installs into the normal places: %LOCALAPPDATA%\Programs\Kinora and the data folder %LOCALAPPDATA%\Kinora. The test
# clip and its results are removed afterwards; the test Kinora is stopped. Needs Inno Setup 6 and an NVIDIA GPU.
param([int] $Port = 8791, [int] $TimeoutMinutes = 240)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$version = (Get-Content (Join-Path $repo 'VERSION') -Raw).Trim()
$program = Join-Path $env:LOCALAPPDATA 'Programs\Kinora'
$data = Join-Path $env:LOCALAPPDATA 'Kinora'
$log = Join-Path $data 'install.log'
$clipName = '_kinora_install_test'

function Step([string] $text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Fail([string] $text) { Write-Host ''; Write-Host "FAILED: $text" -ForegroundColor Red; Stop-TestKinora; exit 1 }

Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class KinoraTestProcess {
	[DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
	[DllImport("kernel32.dll", SetLastError = true)] static extern bool GetProcessMitigationPolicy(IntPtr process, int policy, out uint flags, IntPtr size);
	[DllImport("kernel32.dll", SetLastError = true)] static extern bool IsWow64Process(IntPtr process, out bool wow64);
	[DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
	// 1 when Windows' Redirection Guard is enforced for the process (ProcessRedirectionTrustPolicy = 16), else 0
	public static int RedirectionGuard(int pid) {
		IntPtr h = OpenProcess(0x1000, false, pid); if (h == IntPtr.Zero) return -1;
		uint flags; bool ok = GetProcessMitigationPolicy(h, 16, out flags, (IntPtr)4); CloseHandle(h);
		return ok ? (int)(flags & 1) : -1;
	}
	public static bool Is32Bit(int pid) {
		IntPtr h = OpenProcess(0x1000, false, pid); if (h == IntPtr.Zero) return false;
		bool wow64; IsWow64Process(h, out wow64); CloseHandle(h); return wow64;
	}
}
'@

# the processes of the installed Kinora (its start.bat window, install.ps1 and app.py)
function KinoraProcesses { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($program) } }
function Stop-TestKinora {
	foreach ($p in @(KinoraProcesses)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Answers { try { Invoke-RestMethod "http://127.0.0.1:$Port/api/state" -TimeoutSec 5 } catch { $null } }

Step "Kinora $version"
if (Answers) { Fail "something already answers on port $Port; stop it or pass -Port" }
if (@(KinoraProcesses).Count) { Fail "the installed Kinora is running; close it first" }
$iscc = @((Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source, "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { Fail 'Inno Setup 6 (ISCC.exe) was not found' }

Step 'Building the test installer'
& $iscc /Q "/DTestLaunch=--port $Port --no-browser" (Join-Path $repo 'packaging\windows\kinora.iss')
if ($LASTEXITCODE -ne 0) { Fail "ISCC failed (exit code $LASTEXITCODE)" }
$setup = Join-Path $repo "dist\Kinora-$version-test-setup.exe"
Write-Host $setup

Step 'Installing, then the installer starts Kinora (as its last page does)'
$logStart = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
# without its marker the first start runs install.ps1 again: every step, the finished ones in seconds
Remove-Item (Join-Path $data 'tools\.installed') -Force -ErrorAction SilentlyContinue
# the installer gets the environment a user's double-click gives it: Windows' own PATH (system, then user), without
# what a developer shell adds (Git Bash puts Git's own tar, find etc. first in PATH)
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
foreach ($name in 'MSYSTEM', 'MSYSTEM_PREFIX', 'MSYSTEM_CARCH', 'MSYSTEM_CHOST', 'MINGW_PREFIX', 'MINGW_CHOST', 'MINGW_PACKAGE_PREFIX', 'MSYS', 'CHERE_INVOKING',
	'SHELL', 'TERM', 'ORIGINAL_PATH', 'ORIGINAL_TEMP', 'ORIGINAL_TMP', 'EXEPATH', 'PLINK_PROTOCOL', 'MANPATH', 'INFOPATH', 'PKG_CONFIG_PATH', 'ACLOCAL_PATH',
	'SHLVL', 'PWD', 'OLDPWD', 'HOSTNAME', 'HOME', '_') { Remove-Item "env:$name" -ErrorAction SilentlyContinue }
# wait for the installer only (Start-Process -Wait would also wait for the Kinora it starts)
$installer = Start-Process $setup -ArgumentList '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER' -PassThru
$installer.WaitForExit()
if ($installer.ExitCode -ne 0) { Fail "the installer ended with exit code $($installer.ExitCode)" }
$installed = (Get-Content (Join-Path $program 'VERSION') -Raw).Trim()
if ($installed -ne $version) { Fail "installed version is $installed, expected $version" }
Write-Host "installed $installed in $program"

Step 'Kinora started by the installer'
$deadline = (Get-Date).AddSeconds(60)
do {
	Start-Sleep -Seconds 1
	$worker = @(KinoraProcesses | Where-Object { $_.Name -in 'powershell.exe', 'python.exe' }) | Select-Object -First 1
} until ($worker -or (Get-Date) -gt $deadline)
if (-not $worker) { Fail 'the installer did not start Kinora (no install.ps1 or app.py within a minute)' }
$guard = [KinoraTestProcess]::RedirectionGuard($worker.ProcessId)
$bits = if ([KinoraTestProcess]::Is32Bit($worker.ProcessId)) { 32 } else { 64 }
Write-Host "$($worker.Name) ($($worker.ProcessId)): $bits-bit, Redirection Guard $(if ($guard -eq 1) { 'ON' } elseif ($guard -eq 0) { 'off' } else { 'unknown' })"
if ($bits -ne 64) { Fail 'Kinora runs as a 32-bit process' }
if ($guard -ne 0) { Fail 'Kinora inherited Redirection Guard from the installer' }

Step "First start (log: $log)"
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$lastStep = ''
while (-not ($state = Answers)) {
	if ((Get-Date) -gt $deadline) { Fail "Kinora did not answer within $TimeoutMinutes minutes" }
	$new = if (Test-Path $log) { $text = Get-Content $log -Raw; if ($text.Length -gt $logStart) { $text.Substring([int]$logStart) } else { '' } } else { '' }
	if ($new -match 'Installation stopped: (.+)') { Write-Host $new.Substring([Math]::Max(0, $new.Length - 3000)); Fail "the first start stopped: $($Matches[1].Trim())" }
	$steps = [regex]::Matches($new, '(?m)^== (.+)$')
	if ($steps.Count -and $steps[$steps.Count - 1].Groups[1].Value -ne $lastStep) { $lastStep = $steps[$steps.Count - 1].Groups[1].Value; Write-Host "$(Get-Date -Format HH:mm)  $lastStep" }
	if (-not @(KinoraProcesses).Count) { Fail 'Kinora stopped before it answered (see the log above)' }
	Start-Sleep -Seconds 10
}
$new = (Get-Content $log -Raw -Encoding UTF8).Substring([int]$logStart)
if ($new -notmatch 'Installation complete') { Fail 'install.ps1 did not say "Installation complete" (see install.log)' }
Write-Host 'install.ps1: Installation complete'
# UTF-8 read as code page 437 (emoji and symbols that SeedVR2 prints): what the first-start window would show
# (written as character codes, so this file stays ASCII: Windows PowerShell reads a script without a BOM as ANSI)
$pairs = @(@(0x2261, 0x0192), @(0x0393, 0x00A3), @(0x0393, 0x00DC), @(0x0393, 0x00A5), @(0x2229, 0x2555)) | ForEach-Object { [regex]::Escape([string][char]$_[0] + [char]$_[1]) }
$garbled = [regex]::Matches($new, ($pairs -join '|'))
if ($garbled.Count) { Fail "the first start shows garbled characters ($($garbled.Count)x, e.g. '$($garbled[0].Value)')" }
Write-Host 'no garbled characters'
if ($state.version -ne $version) { Fail "Kinora answers with version $($state.version), expected $version" }
Write-Host "Kinora $($state.version) answers on http://127.0.0.1:$Port"

Step 'Upscaling a 3-second test clip on the GPU (720p, 50 fps)'
$clip = Join-Path $data "input\$clipName.mp4"
New-Item -ItemType Directory -Force (Split-Path $clip) | Out-Null
& ffmpeg -v error -y -f lavfi -i 'testsrc2=size=320x240:rate=25' -f lavfi -i 'sine=frequency=440' -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac $clip
if ($LASTEXITCODE -ne 0) { Fail 'ffmpeg could not make the test clip' }
& (Join-Path $program 'upscale.bat') $clip --height 720 --fps 50 --no-compare
$upscaleExit = $LASTEXITCODE
$result = Get-ChildItem (Join-Path $data 'output') -Filter "$clipName*.mp4" -ErrorAction SilentlyContinue | Select-Object -First 1
$info = if ($result) { & ffprobe -v error -select_streams v:0 -show_entries stream=height,r_frame_rate -of csv=p=0 $result.FullName } else { '' }
Remove-Item $clip -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $data 'output') -Filter "$clipName*" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $data 'work') -Filter "$clipName*" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
if ($upscaleExit -ne 0 -or -not $result) { Fail "the upscale failed (exit code $upscaleExit)" }
if ($info -notmatch '^720,50/1') { Fail "the result is $info (height,frame rate), expected 720,50/1" }
Write-Host "result: 720p at 50 fps"

Step 'FaceFusion finds the GPU'
$providers = & (Join-Path $data 'tools\facefusion\.venv\Scripts\python.exe') -c 'import onnxruntime; print(onnxruntime.get_available_providers())'
Write-Host $providers
if ($providers -notmatch 'CUDAExecutionProvider') { Fail 'onnxruntime in the FaceFusion environment has no CUDA' }

Stop-TestKinora
Write-Host ''
Write-Host "PASSED: Kinora $version installs, starts from the installer and upscales on the GPU." -ForegroundColor Green
exit 0
