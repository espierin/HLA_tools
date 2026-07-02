# Donor Typing to HML

Donor Typing to HML converts Eurotransplant Donordata HLA typing information into HML 1.0.1 files.

The current main version is a Windows-focused Python GUI application that:

- Opens Donordata login in Microsoft Edge or Google Chrome.
- Uses the normal Donordata login and two-factor authentication workflow.
- Reads donor HLA typing data from the authenticated browser session.
- Selects the best usable CIWD genotype typing block when multiple typings are present.
- Builds a GL string with ambiguity information retained.
- Writes a minimal HML 1.0.1 file.
- Copies each generated HML into a shared `hml_files` folder.

## Important Privacy Notice

This tool processes donor data and HLA typing information. Use it only in an approved clinical, laboratory, or research environment.

Do not commit real donor output files, screenshots, browser profiles, logs, or diagnostic folders to GitHub.

Recommended `.gitignore` entries:

```gitignore
hml_files/
ET*_HML_output_*/
ET*_HML_failed_*/
donor_browser_profile*/
*.hml
*.csv
raw_donor_page_text.txt
normalised_donor_page_text.txt
```

## Repository Contents

Core application:

- `Donor_Typing_To_hml_full_gui_v3.py`  
  Main Python GUI script.

- `Donor_Typing_To_hml_full_gui_v3.exe`  
  Windows single-file executable build.

- `Donor_Typing_To_hml_icon.ico` / `Donor_Typing_To_hml_icon.png`  
  Application icon assets.

Documentation:

- `INSTALL_Windows_Donor_Typing_To_HML.md`
- `INSTALL_macOS_Donor_Typing_To_HML.md`
- `INSTALL_Ubuntu_Donor_Typing_To_HML.md`
- `Donor_Typing_To_HML_Windows_Executable_User_Manual.docx`

## Features

### Donordata Session Handling

The application does not ask for or store your Donordata password. Authentication happens in Edge or Chrome using the regular Donordata login and two-factor authentication process.

After login, keep the browser window open. The application reads donor records through that authenticated browser session.

### Batch Conversion

You can add donor ET numbers manually or import a CSV file.

CSV import expects ET numbers in the first column:

```csv
999135
999131
999125
```

Additional columns are ignored.

### CIWD Genotype Selection

If a donor has multiple HLA Typing entries, the application attempts to choose the most useful typing block:

1. It requires usable CIWD genotype allele rows.
2. If multiple entries are usable, it prefers the GL string with fewer `/` ambiguity separators.
3. It favors typings with more two-field allele results, for example `A*02:01` rather than `A*02`.

### Output

For each donor conversion, the application creates:

- A donor-specific output folder.
- A final `.hml` file.
- A copy of the `.hml` file in the configured `hml_files` folder.
- Diagnostic text/CSV files for troubleshooting.

Typical output files include:

- `raw_donor_page_text.txt`
- `normalised_donor_page_text.txt`
- `*_metadata.csv`
- `*_hla_row_candidates.csv`
- `*_selected_hla_rows.csv`
- `*_parsed_hla_typing.csv`
- `*_gl_string.txt`
- `*_donor_typing.hml`

## Quick Start: Windows Executable

1. Download or copy `Donor_Typing_To_hml_full_gui_v3.exe`.
2. Place it in a folder where the user has write access.
3. Double-click the executable.
4. Open `Settings`.
5. Click `Login`.
6. Complete Donordata login and 2FA in Edge or Chrome.
7. Keep the browser window open.
8. Add ET donor numbers manually or import a CSV file.
9. Click `Run selected` or `Run all`.
10. Retrieve HML files from the configured HML folder.

For detailed instructions, see:

- `Donor_Typing_To_HML_Windows_Executable_User_Manual.docx`

## Running From Python

### Requirements

- Python 3.10 or newer.
- Microsoft Edge or Google Chrome.
- Donordata access with two-factor authentication.
- Python package: `PySide6`.

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install PySide6
python .\Donor_Typing_To_hml_full_gui_v3.py
```

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
python Donor_Typing_To_hml_full_gui_v3.py
```

### Ubuntu

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip libgl1 libegl1 libxcb-cursor0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
python Donor_Typing_To_hml_full_gui_v3.py
```

For fuller platform-specific setup instructions, see:

- `INSTALL_Windows_Donor_Typing_To_HML.md`
- `INSTALL_macOS_Donor_Typing_To_HML.md`
- `INSTALL_Ubuntu_Donor_Typing_To_HML.md`

## Building a Windows Executable

Install PyInstaller in the same virtual environment:

```powershell
python -m pip install pyinstaller
```

Build:

```powershell
python -m PyInstaller --clean --onefile --windowed --name Donor_Typing_To_hml_full_gui_v3 --icon Donor_Typing_To_hml_icon.ico Donor_Typing_To_hml_full_gui_v3.py
```

The executable will be created in:

```text
dist\Donor_Typing_To_hml_full_gui_v3.exe
```

Note: PySide6 single-file executables are relatively large. This is expected.

## Status Colors in the App

| Status | Meaning |
|---|---|
| Waiting | Donor is queued. |
| Loading donor page | Donor record is being accessed. |
| Writing HML | Output files are being generated. |
| Done | HML was successfully written. The row is bold and light green. |
| Failed | Donor access, parsing, or HML generation failed. The row is italic and light red. |
| Login needed | The Donordata session is not currently accessible. |

## Troubleshooting

### Login Is Not Detected

Use `Settings` > `Login` from inside the app. Complete login and 2FA in the browser window opened by the app, and keep that window open.

### Rows Fail After Login

Possible causes:

- Donordata session expired.
- Browser window was closed.
- Donor record is not accessible to the logged-in account.
- HLA Typing / CIWD Genotype is missing or unreadable.
- HML output could not be written.

Try logging in again and rerunning the failed donors.

### No HML File Appears

Check:

- The selected output folder.
- The configured `hml_files` folder.
- The Activity Log in the app.
- Any `ET*_HML_failed_*` folder.

### PySide6 Is Missing

Install it in the active virtual environment:

```bash
python -m pip install PySide6
```

## Platform Notes

The current v3 version has been developed and tested primarily on Windows.

macOS and Ubuntu instructions are provided for Python setup, but browser-session detection may require small adaptations depending on local Chrome/Edge installation paths and browser security settings.

## Security and Governance

This tool should be treated as a local operational tool for approved users.

Recommended practices:

- Do not store output in public folders.
- Do not commit generated donor files to Git.
- Keep browser profile folders out of version control.
- Use approved storage locations for HML and diagnostic files.
- Confirm local institutional rules before sharing output files.

## License / Copyright

Copyright (C) 2026 Eric Spierings, UMC Utrecht.

No license has been assigned yet. Until a license is added, all rights are reserved by the copyright holder.

