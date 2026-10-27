@echo off
setlocal
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
