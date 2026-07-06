# Donor Typing to HML v2.0 - Windows Installation Guide

Version: 2.0  
Last updated: 2026-07-06  
Example ET numbers in this document are synthetic and anonymized, for example `734219`.

![Main window](docs_v2_assets/v2_main_window.png)

## What this guide installs

Donor Typing to HML v2.0 converts HLA typing from Eurotransplant Donordata into HML 1.0.1 files. Version 2.0 adds imputation using HLA allele and haplotype frequency tables, genotype/haplotype GL string display, imputed HML output, and a soft country/registration-center prior.

There are two Windows installation paths:

1. **Recommended: use the single executable.** No Python installation is required.
2. **Developer/source mode: run the Python script.** Use this if you want to inspect or modify the source.

## Recommended installation: single executable

### Requirements

- Windows 10 or Windows 11.
- Microsoft Edge or Google Chrome installed.
- Access to the Eurotransplant Donordata website.
- Your normal Donordata login and two-factor authentication method.
- Permission to write files in your chosen output folder.

The executable contains:

- the v2.0 GUI;
- the v3 browser/data extraction base script;
- the bundled `hla_frequency_tables.xlsx` frequency workbook;
- the application icon.

The executable does not contain a browser and does not bypass Donordata authentication. It uses your installed browser for the official login workflow.

### Step 1 - Create an application folder

Create a folder such as:

```powershell
C:\DonorTyping
```

Place the executable in that folder:

```text
C:\DonorTyping\Donor_Typing_To_hml_v2_0.exe
```

### Step 2 - Start the application

Double-click:

```text
Donor_Typing_To_hml_v2_0.exe
```

On first launch, Windows SmartScreen may warn that the application is from an unknown publisher. Choose **More info** and then **Run anyway** only if the file came from your trusted release source.

### Step 3 - Choose the output folder

Use the **Browse** button next to **Output folder**. The recommended layout is:

```text
C:\DonorTyping
```

The program will create:

```text
C:\DonorTyping\hml_files
```

Every generated HML file is copied there. Donor-specific audit folders are also created in the selected output folder.

### Step 4 - Log in to Donordata

Open **Settings** and press **Login**. Complete the normal Donordata login and two-factor authentication in the external browser.

![Settings](docs_v2_assets/v2_settings.png)

Important:

- Do not enter your password into this app.
- The app does not store your password or 2FA secret.
- If Donordata redirects to the login page, the app treats the session as not logged in.
- If a donor report can be accessed, the app treats the session as active.

### Step 5 - Add donor ET numbers

You can add donors in three ways:

- Type one ET number, for example `734219`, and press **Enter**.
- Type one ET number and press **Add**.
- Press **Add ET** and select a CSV file with ET numbers in the first column, one donor per row.

Example CSV:

```csv
734219
682504
591873
```

### Step 6 - Run conversion

Use the checkbox column in the donor queue to control which donors are included.

- **Run selected** processes only highlighted/selected queue rows.
- **Run all** processes all checked rows.
- **Stop** requests a stop after the current donor operation.

Successful rows become bold with a light green background. Failed rows become italic with a light red background.

### Step 7 - Review output

The **Selected Donor** panel shows metadata, original GL string, and generated files. The **Output Files** panel lists created artifacts.

![Output files](docs_v2_assets/v2_output_files.png)

## Source-mode installation on Windows

Use source mode when you want to inspect, edit, or debug the Python scripts.

### Files required

Keep these files in the same folder:

```text
Donor_Typing_To_hml_full_gui_v2_0.py
Donor_Typing_To_hml_full_gui_v3.py
hla_frequency_tables.xlsx
Donor_Typing_To_hml_icon.ico
```

### Install Python

Install Python 3.11 or 3.12 from:

```text
https://www.python.org/downloads/windows/
```

During installation, tick **Add python.exe to PATH**.

### Create a virtual environment

Open PowerShell in the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install PySide6
```

### Start the app from source

```powershell
.\.venv\Scripts\python.exe .\Donor_Typing_To_hml_full_gui_v2_0.py
```

## Updating the frequency workbook

Open **Settings**, choose the Excel supplement, and confirm. The program copies the selected workbook to:

```text
hla_frequency_tables.xlsx
```

next to the script or executable. On the next launch, this file is loaded automatically.

## Troubleshooting

### The app opens but no donor conversion happens

- Check that you are logged in to Donordata.
- Open Settings and press Login again.
- Confirm that the browser shows the donor report and not the login page.

### A row is marked failed

Open the donor-specific output folder. It usually contains a failure report or raw text file. Common causes:

- donor number not accessible for your account;
- Donordata timeout;
- typing table not loaded or not expanded;
- no CIWD genotype present;
- HML file could not be written to the selected output folder.

### Frequency table does not load

- Open Settings.
- Select the Excel frequency workbook again.
- Confirm that `hla_frequency_tables.xlsx` appears next to the executable or script.

### Antivirus or SmartScreen warning

This can happen with onefile PyInstaller executables. If the file came from your own build or trusted GitHub Release, allow it. If not, do not run it.
