@echo off
setlocal
rem A 32-bit program (such as the installer's last page) starts this in 32-bit cmd, where Windows hides
rem the real System32 and nvidia-smi is not found: start again as 64-bit.
if defined PROCESSOR_ARCHITEW6432 if exist "%WINDIR%\Sysnative\cmd.exe" (
	"%WINDIR%\Sysnative\cmd.exe" /c ""%~f0" %*"
	exit /b
)
title Kinora
call "%~dp0tools\data_root.bat"
if not exist "%KINORA_DATA%\tools\.installed" (
	echo First start: installing the tools and downloading the models - about 25 GB, only this once.
	powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
	if errorlevel 1 (
		echo.
		pause
		exit /b 1
	)
)
"%KINORA_DATA%\tools\seedvr2\.venv\Scripts\python.exe" "%~dp0app.py" %*
