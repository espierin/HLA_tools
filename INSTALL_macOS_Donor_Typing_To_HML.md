# Donor Typing to HML - macOS Installation Guide

This guide describes how to install and run the Python GUI version of `Donor_Typing_To_hml_full_gui_v3.py` on macOS.

Important: the current v3 script was primarily developed and tested on Windows. The GUI and parser are portable, but browser-session discovery and automated browser launching may need minor adjustment on macOS depending on your Chrome/Edge installation. The safest macOS route is to install Python/PySide6 first, test that the GUI opens, and then confirm browser login/session behavior.

## 1. What This Program Does

The program converts Eurotransplant Donordata HLA typing data into HML.

The intended workflow is:

1. Start the Donor Typing to HML app.
2. Open Donordata login through the app.
3. Complete normal login and two-factor authentication in Chrome or Edge.
4. Enter or import ET donor numbers.
5. Run conversions.
6. Save HML files.

## 2. Requirements

You need:

- macOS 12 or newer recommended.
- Python 3.10 or newer. Python 3.12 is recommended.
- Google Chrome or Microsoft Edge.
- Access to Donordata and working two-factor authentication.
- The script file:
  - `Donor_Typing_To_hml_full_gui_v3.py`

Python dependency:

- `PySide6`

No Tesseract/OCR is needed.

## 3. Install Python

### Option A: Install Python From python.org

1. Go to <https://www.python.org/downloads/macos/>.
2. Download the latest Python 3 macOS installer.
3. Run the installer.

Check in Terminal:

```bash
python3 --version
```

### Option B: Install Python With Homebrew

If Homebrew is installed:

```bash
brew install python
```

Check:

```bash
python3 --version
```

## 4. Install Chrome or Edge

Install one of:

- Google Chrome: <https://www.google.com/chrome/>
- Microsoft Edge: <https://www.microsoft.com/edge>

Chrome is usually the easiest option on macOS.

## 5. Create a Program Folder

In Terminal:

```bash
mkdir -p "$HOME/DonorTypingToHML"
```

Copy `Donor_Typing_To_hml_full_gui_v3.py` into that folder.

Then go there:

```bash
cd "$HOME/DonorTypingToHML"
```

## 6. Create a Virtual Environment

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Your prompt should now start with:

```text
(.venv)
```

## 7. Install Dependencies

Update `pip`:

```bash
python -m pip install --upgrade pip
```

Install PySide6:

```bash
python -m pip install PySide6
```

Check:

```bash
python -c "import PySide6; print('PySide6 OK')"
```

## 8. Run the Program

```bash
python Donor_Typing_To_hml_full_gui_v3.py
```

The GUI should open.

## 9. macOS Security Prompt

If macOS blocks the app or Python from opening windows:

1. Open `System Settings`.
2. Go to `Privacy & Security`.
3. Allow the blocked Python/app action.
4. Run the script again.

## 10. Browser Login Workflow

1. In the app, open `Settings`.
2. Click `Login`.
3. Complete Donordata login and 2FA in Chrome or Edge.
4. Keep the browser window open.
5. Return to the app.
6. Add ET numbers and run conversion.

## 11. CSV Import Format

The CSV file should contain ET donor numbers in the first column.

Example:

```csv
199135
199131
199125
```

Additional columns are ignored.

## 12. Output Files

The app writes:

- One donor-specific output folder per conversion.
- A copy of each `.hml` file into the configured HML folder.
- By default, the HML folder is named `hml_files`.

## 13. Optional: Build a macOS App

Install PyInstaller:

```bash
python -m pip install pyinstaller
```

Build a one-file executable:

```bash
python -m PyInstaller --clean --onefile --windowed --name Donor_Typing_To_hml_full_gui_v3 Donor_Typing_To_hml_full_gui_v3.py
```

The result will be in:

```text
dist/
```

For a polished `.app` bundle, PyInstaller can also build app-style output, but signing/notarization may be needed for distribution outside your own machine.

## 14. macOS Caveat: Browser Automation

If the GUI opens but login/conversion cannot find Chrome or Edge, the script may need a small macOS browser path update.

Typical browser paths are:

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
```

If necessary, update the script's browser detection function to include these paths.

## 15. Troubleshooting

### `No module named PySide6`

Run:

```bash
source .venv/bin/activate
python -m pip install PySide6
```

### GUI Does Not Open

Check Python and PySide6:

```bash
python --version
python -c "import PySide6; print('PySide6 OK')"
```

### Login Is Not Detected

Try:

1. Close old Chrome/Edge windows opened by the app.
2. Start the app again.
3. Click `Settings` / `Login`.
4. Complete login and keep the browser open.

### Rows Turn Red

Red rows indicate failure:

- Donordata session expired.
- Donor record was inaccessible.
- HLA Typing / CIWD genotype could not be read.
- HML was not generated.

Log in again and rerun the donor.

