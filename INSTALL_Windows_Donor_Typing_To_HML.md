# Donor Typing to HML - Windows Installation Guide

This guide installs and runs the Python GUI version of `Donor_Typing_To_hml_full_gui_v3.py` on Windows.

## 1. What This Program Does

The program converts Eurotransplant Donordata HLA typing data into HML.

The normal workflow is:

1. Start the Donor Typing to HML app.
2. Use `Settings` / `Login` to open Donordata in Edge or Chrome.
3. Complete normal login and two-factor authentication.
4. Enter or import one or more ET donor numbers.
5. Run selected donors or all donors.
6. The app reads the authenticated Donordata page in the background.
7. HML files are written to the configured output folder and the `hml_files` folder.

## 2. Requirements

You need:

- Windows 10 or Windows 11.
- Python 3.10 or newer. Python 3.12 is recommended.
- Microsoft Edge or Google Chrome.
- Access to Donordata and working two-factor authentication.
- The script file:
  - `Donor_Typing_To_hml_full_gui_v3.py`

Python dependency:

- `PySide6`

No Tesseract/OCR is needed for this version.

## 3. Install Python

1. Go to <https://www.python.org/downloads/windows/>.
2. Download the latest Python 3 installer.
3. Run the installer.
4. Very important: tick `Add python.exe to PATH`.
5. Click `Install Now`.

After installation, open PowerShell and check:

```powershell
python --version
```

You should see something like:

```text
Python 3.12.x
```

If `python` is not recognized, try:

```powershell
py --version
```

## 4. Create a Program Folder

Create a folder for the app, for example:

```powershell
mkdir C:\DonorTypingToHML
```

Copy `Donor_Typing_To_hml_full_gui_v3.py` into that folder.

Then go to that folder:

```powershell
cd C:\DonorTypingToHML
```

## 5. Create a Virtual Environment

A virtual environment keeps the app dependencies separate from other Python software.

```powershell
python -m venv .venv
```

If `python` does not work, use:

```powershell
py -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

When activation works, your prompt will start with:

```text
(.venv)
```

## 6. Install Dependencies

Update `pip`:

```powershell
python -m pip install --upgrade pip
```

Install the GUI dependency:

```powershell
python -m pip install PySide6
```

Check that it installed:

```powershell
python -c "import PySide6; print('PySide6 OK')"
```

## 7. Run the Program

From the program folder:

```powershell
python .\Donor_Typing_To_hml_full_gui_v3.py
```

The GUI should open.

## 8. First Use

1. Click `Settings`.
2. Click `Login`.
3. Edge or Chrome opens.
4. Complete Donordata login and 2FA.
5. Return to the app.
6. The app should detect that the session is active.
7. Add ET numbers manually or import a CSV file.
8. Click `Run selected` or `Run all`.

## 9. CSV Import Format

The CSV file should contain ET donor numbers in the first column.

Example:

```csv
999135
999131
999125
```

Additional columns are ignored.

## 10. Output Files

The app writes:

- One donor-specific output folder per conversion.
- A copy of the `.hml` file into the configured HML folder.
- By default, this folder is named `hml_files`.

Inside the GUI, use:

- `Open HML`
- `Open output folder`
- `Open HML folder`

## 11. Creating a Double-Click Launcher

Create a file named:

```text
Run_Donor_Typing_To_HML.cmd
```

Place it in the same folder as the script.

Put this inside:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python Donor_Typing_To_hml_full_gui_v3.py
pause
```

Double-click this `.cmd` file to start the app.

## 12. Optional: Build a Windows EXE

Install PyInstaller:

```powershell
python -m pip install pyinstaller
```

Build:

```powershell
python -m PyInstaller --clean --onefile --windowed --name Donor_Typing_To_hml_full_gui_v3 Donor_Typing_To_hml_full_gui_v3.py
```

The executable will appear in:

```text
dist\Donor_Typing_To_hml_full_gui_v3.exe
```

Note: a PySide6 one-file EXE is usually large. This is normal.

## 13. Troubleshooting

### `No module named PySide6`

Activate the virtual environment and install PySide6:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install PySide6
```

### Browser Does Not Open

Install or update Microsoft Edge or Google Chrome.

The app looks for:

- Microsoft Edge
- Google Chrome

### Login Is Not Detected

Try:

1. Click `Settings`.
2. Click `Login`.
3. Log in again.
4. Keep the browser window open.
5. Return to the app and run again.

### Records Turn Red

Red rows mean conversion failed. Common reasons:

- Donordata session expired.
- The donor record could not be accessed.
- The HLA Typing / CIWD genotype table could not be read.
- No HML file was generated.

Log in again and rerun the donor.

