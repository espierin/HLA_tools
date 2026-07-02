@echo off
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\work\donor_exe_venv\Scripts\python.exe"
set "SCRIPT=%~dp0Donor_Typing_To_hml_full_gui_v3.py"

if exist "%PY%" (
  start "" "%PY%" "%SCRIPT%"
  exit /b 0
)

echo Could not find "%PY%".
echo Please run:
echo   work\donor_exe_venv\Scripts\python.exe -m pip install PySide6
pause
