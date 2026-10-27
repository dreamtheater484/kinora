@echo off
setlocal
call "%~dp0tools\data_root.bat"
"%KINORA_DATA%\tools\seedvr2\.venv\Scripts\python.exe" "%~dp0merge.py" %*
