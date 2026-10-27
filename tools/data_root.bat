@rem Sets KINORA_DATA to Kinora's data folder (videos, results, tools and models), unless it is set already.
@rem The same rule as paths.py: next to the program in a portable copy; %LOCALAPPDATA%\Kinora in an installed
@rem copy (the installer puts installed.txt next to app.py). Used by start.bat and the other .bat files.
@if defined KINORA_DATA exit /b 0
@if exist "%~dp0..\installed.txt" (set "KINORA_DATA=%LOCALAPPDATA%\Kinora") else (for %%I in ("%~dp0..") do set "KINORA_DATA=%%~fI")
