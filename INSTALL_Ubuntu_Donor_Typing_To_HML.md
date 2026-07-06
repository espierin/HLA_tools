# Donor Typing to HML v2.0 - Ubuntu Installation Guide

Version: 2.0  
Last updated: 2026-07-06  
Example ET numbers in this document are synthetic and anonymized, for example `734219`.

## Ubuntu support status

The Windows `.exe` is Windows-only. On Ubuntu, run the Python source version. This is best suited for technical users who can manage a Python virtual environment and browser setup.

## Requirements

- Ubuntu 22.04 or 24.04.
- Python 3.11 or 3.12.
- A graphical desktop session.
- Chrome, Chromium, or Microsoft Edge.
- Donordata access and two-factor authentication.

## Step 1 - Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
sudo apt install -y libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1
```

Install a browser if needed:

```bash
sudo apt install -y chromium-browser
```

or install Google Chrome/Edge according to your institutional policy.

## Step 2 - Create the application folder

```bash
mkdir -p ~/DonorTyping
cd ~/DonorTyping
```

Copy these files into the folder:

```text
Donor_Typing_To_hml_full_gui_v2_0.py
Donor_Typing_To_hml_full_gui_v3.py
hla_frequency_tables.xlsx
Donor_Typing_To_hml_icon.ico
```

## Step 3 - Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install PySide6
```

## Step 4 - Start the app

```bash
python Donor_Typing_To_hml_full_gui_v2_0.py
```

## Step 5 - Login and run

1. Open Settings.
2. Press Login.
3. Complete Donordata authentication in the browser.
4. Add donor numbers to the queue.
5. Run selected donors.
6. Review HML files in `hml_files`.

![Main window](docs_v2_assets/v2_main_window.png)

## Output location

Choose a folder where your Linux user has write permission, for example:

```bash
~/DonorTyping/output
```

The app creates:

```text
output/hml_files
output/ET734219_HML_output_<timestamp>
output/ET734219_imputed_HML_<timestamp>
```

## Troubleshooting

### Qt platform plugin errors

Install the Qt/XCB dependencies:

```bash
sudo apt install -y libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1
```

### Browser/session detection fails

- Confirm that Donordata opens in the browser.
- Confirm that you can access a donor page after login.
- Restart the app after completing login.

### No frequency data loaded

Place `hla_frequency_tables.xlsx` next to the v2.0 script or select it in Settings.
