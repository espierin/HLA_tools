# Donor Typing to HML

Convert Eurotransplant Donordata HLA typing into HML 1.0.1 files.

Current documented version: **2.0**  
Author: Eric Spierings, UMC Utrecht  
Copyright: (C) 2026

All ET numbers shown in screenshots and examples are synthetic and anonymized. 

![Donor Typing to HML v2.0 main window](docs_v2_assets/v2_main_window.png)

## What the application does

Donor Typing to HML reads HLA typing data from the Eurotransplant Donordata donor page and generates:

- original GL string;
- original HML 1.0.1 file;
- donor-specific audit folder;
- general `hml_files` folder containing all generated HML files;
- optional imputed GL string;
- optional imputed HML file with imputation metadata.

Version 2.0 adds an HLA imputation workspace using allele and haplotype frequency data.

## Main features

- External-browser Donordata authentication with normal login and 2FA.
- No OCR dependency for the v2.0 webpage workflow.
- Queue-based donor processing.
- Manual ET input, Enter-to-add, and CSV import.
- Per-donor output folders and central `hml_files` folder.
- HLA-A, B, C, DRB1, DRB3/4/5, DQA1, DQB1, DPA1, and DPB1 support.
- Living-donor comma-only CIWD parsing safeguards.
- Imputation across all available workbook ethnicities.
- Genotype/haplotype toggle for imputed GL strings.
- Soft country/registration-center prior with guardrails.
- Imputed HML metadata for auditability.

## Screenshots

### Main window

![Main window](docs_v2_assets/v2_main_window.png)

### Imputation workspace

![Imputation workspace](docs_v2_assets/v2_imputation_panel.png)

### Settings

![Settings](docs_v2_assets/v2_settings.png)

### Output files

![Output files](docs_v2_assets/v2_output_files.png)

## Quick start for Windows executable

1. Download `Donor_Typing_To_hml_v2_0.exe` from the GitHub Release.
2. Place it in a folder such as `C:\DonorTyping`.
3. Double-click the executable.
4. Choose an output folder.
5. Open Settings and log in to Donordata.
6. Add donor ET numbers.
7. Run selected donors or run all checked donors.
8. Collect HML files from `hml_files`.

## Python source start

Keep these files in the same folder:

```text
Donor_Typing_To_hml_full_gui_v2_0.py
Donor_Typing_To_hml_full_gui_v3.py
hla_frequency_tables.xlsx
Donor_Typing_To_hml_icon.ico
```

Install dependencies:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install PySide6
```

Run:

```bash
python Donor_Typing_To_hml_full_gui_v2_0.py
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip PySide6
.\.venv\Scripts\python.exe .\Donor_Typing_To_hml_full_gui_v2_0.py
```

## Frequency table

Version 2.0 can load `hla_frequency_tables.xlsx` automatically when it is next to the script or executable. A different frequency workbook can be selected in Settings. The selected file is copied to `hla_frequency_tables.xlsx` for future launches.

## Imputation

The imputation workflow:

1. Select a completed donor.
2. Press **Impute**.
3. Review the ethnicity comparison.
4. Optionally choose another ethnicity and press **Select**.
5. Toggle between genotype and haplotype display.
6. Press **Create HML** to write an imputed HML file.

The imputed HML includes metadata describing the imputation method, selected ethnicity, reliability, confidence, GL string mode, original GL string, and geographic prior status.

## Security and privacy

- The app does not store Donordata passwords or 2FA secrets.
- Authentication is performed in the official Donordata website through an external browser.
- Generated files may contain donor-related data and should be handled according to local policy.
- Documentation screenshots use synthetic ET numbers only.

## Documentation

- [Windows installation guide](INSTALL_Windows_Donor_Typing_To_HML.md)
- [macOS installation guide](INSTALL_macOS_Donor_Typing_To_HML.md)
- [Ubuntu installation guide](INSTALL_Ubuntu_Donor_Typing_To_HML.md)
- [Changelog 1.3 to 2.0](CHANGELOG_1_3_to_2_0.md)
- Word user manual: `Donor_Typing_To_HML_v2_0_Windows_Executable_User_Manual.docx`

## Limitations

- Donordata authentication cannot be bypassed.
- Imputation is probabilistic and requires expert review.
- Registration center/country is used only as a soft prior.
- Browser and Donordata frontend changes may require maintenance.

## License / ownership

Author: Eric Spierings, UMC Utrecht  
Copyright: (C) 2026
