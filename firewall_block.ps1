# Blocks all outbound network traffic for this project's own Python interpreters
# (the ones SeedVR2 and FaceFusion run on). Your other Python installs are not affected.
#
# Run once, AFTER download_models.bat, in an Administrator PowerShell:
#   powershell -ExecutionPolicy Bypass -File firewall_block.ps1
#
# Temporarily allow again (e.g. to download another model):
#   Disable-NetFirewallRule -DisplayName 'video-upscaler*'
#   ...download...
#   Enable-NetFirewallRule -DisplayName 'video-upscaler*'
# Remove completely:
#   Remove-NetFirewallRule -DisplayName 'video-upscaler*'

$root = $PSScriptRoot
$folders = @(Get-ChildItem "$root\tools\python" -Directory | ForEach-Object { $_.FullName }) + @("$root\tools\seedvr2\.venv\Scripts", "$root\tools\facefusion\.venv\Scripts")

$folders += "$root\tools\rife"

foreach ($folder in $folders) {
	foreach ($exe in 'python.exe', 'pythonw.exe', 'rife-ncnn-vulkan.exe') {
		$program = Join-Path $folder $exe
		if (Test-Path $program) {
			$name = 'video-upscaler block ' + $program.Substring($root.Length + 1)
			Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
			New-NetFirewallRule -DisplayName $name -Direction Outbound -Program $program -Action Block -Profile Any | Out-Null
			Write-Host "blocked: $program"
		}
	}
}
