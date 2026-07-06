# Donor Typing to HML v2.0 - macOS Installation Guide

Version: 2.0  
Last updated: 2026-07-06  
Example ET numbers in this document are synthetic and anonymized, for example `734219`.

## macOS support status

The Windows `.exe` does not run natively on macOS. On macOS, use the Python source version.

The app uses an external browser for Donordata login and page access. Donordata authentication, including two-factor authentication, must be completed manually in the browser.

## Requirements

- macOS 13 or newer recommended.
- Python 3.11 or 3.12.
- Google Chrome, Microsoft Edge, or another Chromium-compatible browser.
- Donordata access and 2FA.
- The following files in one folder:

```text
Donor_Typing_To_hml_full_gui_v2_0.py
Donor_Typing_To_hml_full_gui_v3.py
hla_frequency_tables.xlsx
Donor_Typing_To_hml_icon.ico
```

## Step 1 - Install Python

Recommended using Homebrew:

```bash
brew install python@3.12
```

Alternatively install Python from:

```text
https://www.python.org/downloads/macos/
```

## Step 2 - Create a project folder

Example:

```bash
mkdir -p ~/DonorTyping
cd ~/DonorTyping
```

Copy the v2.0 Python files and `hla_frequency_tables.xlsx` into this folder.

## Step 3 - Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
```

## Step 4 - Start the app

```bash
python Donor_Typing_To_hml_full_gui_v2_0.py
```

## Step 5 - Log in

Open **Settings** in the app and choose **Login**. Complete Donordata login and 2FA in your browser.

![Settings](docs_v2_assets/v2_settings.png)

## Step 6 - Convert donor typings

1. Choose an output folder.
2. Add anonymized example ET numbers such as `734219` only for testing documentation.
3. Use **Run selected** or **Run all**.
4. Review the generated HML files in `hml_files`.

## Notes for macOS Gatekeeper

If you later package the app yourself, macOS may block unsigned apps. You may need local signing/notarization for wider distribution. For source-mode use, Gatekeeper normally does not block the Python script itself.

## Troubleshooting

### PySide6 install fails

Update pip first:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install PySide6
```

### Browser does not open

Open Donordata manually in Chrome or Edge, log in, and then start the app again.

### Frequency table missing

Make sure `hla_frequency_tables.xlsx` is in the same folder as the v2.0 script, or select it in Settings.
