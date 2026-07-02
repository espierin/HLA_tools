# Donor Typing to HML - Ubuntu Linux Installation Guide

This guide describes how to install and run the Python GUI version of `Donor_Typing_To_hml_full_gui_v3.py` on Ubuntu.

Important: the current v3 script was primarily developed and tested on Windows. The GUI and HML parser are portable, but browser-session discovery and automated browser launching may need minor adjustment on Linux depending on your Chrome/Edge installation. Test the GUI first, then test Donordata login/session behavior.

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

- Ubuntu 22.04 or newer recommended.
- Python 3.10 or newer.
- Google Chrome, Microsoft Edge, or a Chromium-compatible browser.
- Access to Donordata and working two-factor authentication.
- The script file:
  - `Donor_Typing_To_hml_full_gui_v3.py`

Python dependency:

- `PySide6`

No Tesseract/OCR is needed.

## 3. Install System Packages

Open Terminal.

Update package lists:

```bash
sudo apt update
```

Install Python, virtual environment support, and common Qt runtime libraries:

```bash
sudo apt install -y python3 python3-venv python3-pip libgl1 libegl1 libxcb-cursor0
```

Check Python:

```bash
python3 --version
```

## 4. Install Chrome or Edge

### Option A: Google Chrome

Download Chrome from:

<https://www.google.com/chrome/>

Then install the `.deb` file, for example:

```bash
sudo apt install ./google-chrome-stable_current_amd64.deb
```

Check:

```bash
google-chrome --version
```

### Option B: Microsoft Edge

Download Edge from:

<https://www.microsoft.com/edge>

Install the `.deb` file:

```bash
sudo apt install ./microsoft-edge-stable_*_amd64.deb
```

Check:

```bash
microsoft-edge --version
```

## 5. Create a Program Folder

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

Your prompt should start with:

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

## 9. Browser Login Workflow

1. In the app, open `Settings`.
2. Click `Login`.
3. Complete Donordata login and 2FA in Chrome or Edge.
4. Keep the browser window open.
5. Return to the app.
6. Add ET numbers and run conversion.

## 10. CSV Import Format

The CSV file should contain ET donor numbers in the first column.

Example:

```csv
199135
199131
199125
```

Additional columns are ignored.

## 11. Output Files

The app writes:

- One donor-specific output folder per conversion.
- A copy of each `.hml` file into the configured HML folder.
- By default, this HML folder is named `hml_files`.

## 12. Optional: Build a Linux Executable

Install PyInstaller:

```bash
python -m pip install pyinstaller
```

Build:

```bash
python -m PyInstaller --clean --onefile --windowed --name Donor_Typing_To_hml_full_gui_v3 Donor_Typing_To_hml_full_gui_v3.py
```

The executable appears in:

```text
dist/Donor_Typing_To_hml_full_gui_v3
```

Make it executable if needed:

```bash
chmod +x dist/Donor_Typing_To_hml_full_gui_v3
```

## 13. Ubuntu Caveat: Browser Automation

If the GUI opens but login/conversion cannot find Chrome or Edge, the script may need a small Linux browser path update.

Typical browser commands are:

```text
google-chrome
google-chrome-stable
microsoft-edge
microsoft-edge-stable
chromium
chromium-browser
```

If necessary, update the script's browser detection function to include these commands.

## 14. Troubleshooting

### `No module named PySide6`

Run:

```bash
source .venv/bin/activate
python -m pip install PySide6
```

### Qt / xcb Error

If you see an error mentioning `xcb`, install extra Qt dependencies:

```bash
sudo apt install -y libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0
```

Then run again:

```bash
python Donor_Typing_To_hml_full_gui_v3.py
```

### GUI Does Not Open On a Server

This app needs a graphical desktop session.

It will not run directly in a headless SSH-only terminal unless X11 forwarding or a desktop environment is configured.

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

