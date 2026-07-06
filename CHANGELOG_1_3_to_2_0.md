# Changelog - Donor Typing to HML 1.3 to 2.0

Last updated: 2026-07-06

This changelog summarizes the functional changes from the 1.3/1.3.1 GUI generation to version 2.0. Example ET numbers are synthetic and anonymized.

## Summary

Version 1.3 focused on reliable Donordata-to-HML conversion using an external browser session and a compact GUI. Version 2.0 keeps that conversion workflow and adds a new imputation workspace, frequency table handling, genotype/haplotype output control, imputed HML creation, and better audit metadata.

## Added

### HLA imputation workspace

- Added a new lower-panel imputation area.
- Added an **Impute** button under the original GL string panel.
- Added a population/ethnicity dropdown populated from the frequency workbook.
- Added a **Select** button to apply the currently selected ethnicity.
- Added a right-hand **Imputed GL String** panel.
- Added **Copy GL string** and **Create HML** buttons for the imputed result.

### Frequency workbook integration

- Added support for the Excel frequency table `hla_frequency_tables.xlsx`.
- Added startup auto-loading when the workbook is present next to the script or executable.
- Added Settings workflow to select a new frequency table.
- When a new table is selected, the app copies it to `hla_frequency_tables.xlsx` beside the script/executable.
- Added parsing for all 21 population groups present in the workbook:
  - AFB - African
  - AAFA - African American
  - SCAMB - Black South or Central American
  - CARB - Black Caribbean
  - MENAFC - Middle Eastern or North African
  - EURCAU - White European
  - CARHIS - Hispanic Caribbean
  - SCAHIS - Hispanic South or Central American
  - MSWHIS - Mexican
  - ALANAM - Native Alaskan
  - AISC - Native South or Central American
  - CARIBI - Native Caribbean
  - AMIND - Native North American
  - NCHI - Chinese
  - FILII - Filipino
  - HAWI - Hawaiian or Pacific Islander
  - JAPI - Japanese
  - KORI - Korean
  - AINDI - South Asian
  - SCSEAI - Southeast Asian
  - VIET - Vietnamese

### Genotype and haplotype display modes

- Added a Genotype/Haplotype toggle for the imputed GL string.
- The toggle text now shows the available action:
  - when genotype is displayed, the button says **Haplotype**;
  - when haplotype is displayed, the button says **Genotype**.
- The header now changes dynamically:
  - `Imputed GL String`
  - `Imputed GL String (Genotype)`
  - `Imputed GL String (Haplotype)`

### Haplotype analysis

- Added haplotype table parsing from the frequency workbook.
- Added haplotype GL string construction where supported by the selected ethnicity.
- Added fallback direct allele-1/allele-2 phasing when no sufficiently matching haplotype pair is available.
- Added the selected haplotype GL string to the imputation report.

### Geographic soft prior

- Added a soft geographic prior based on:
  - `Country of Citizenship` from Donordata when available;
  - otherwise the registration center.
- Added explicit reporting of:
  - HLA-only best ethnicity;
  - country/registration-center-informed best ethnicity;
  - prior weights used;
  - final selected imputation.
- Added guardrails so geography does not exclude rare populations or overrule much stronger HLA-only evidence.

### Imputed HML output

- Added imputed HML creation.
- Imputed HML filenames contain `imputed`.
- Imputed HML is copied into the general `hml_files` folder.
- Added imputation-specific HML metadata properties, including:
  - imputation status;
  - version;
  - ethnicity;
  - reliability percentage;
  - confidence;
  - genotype/haplotype mode;
  - geographic prior status;
  - geographic prior source/country/center;
  - original GL string.
- The `<sbt-ngs>` method tags are changed to an imputed source marker.

## Changed

- Reworked the bottom activity-log area into a two-panel imputation workspace.
- Moved runtime activity logging into the top-middle panel.
- Improved output file list spacing by removing extra blank visual gaps.
- Adjusted initial panel sizing to approximately:
  - left: 30%;
  - middle: 45%;
  - right: 25%.
- The **Enter ET number** field now adds the donor when Enter/Return is pressed.
- The **Add ET** button imports a CSV file with ET numbers in the first column.

## Fixed

- Fixed living-donor CIWD parsing where comma-separated alleles can represent the two alleles rather than ambiguity.
- Preserved deceased-donor behavior where hyphen separates the two alleles and commas represent ambiguity.
- Added HLA-C handling in the conversion path.
- Improved automatic CIWD expansion/data capture by reading page/source content rather than relying on OCR.
- Improved executable packaging by bundling required dynamic-source imports such as `html`.

## Privacy and audit improvements

- Documentation now uses synthetic ET numbers such as `734219`.
- The app does not store Donordata passwords or two-factor secrets.
- Output folders keep raw text, parsed rows, GL strings, HML files, and imputation reports for traceability.

## Known limitations

- Imputation is probabilistic and must be reviewed before operational use.
- Registration center and country are used only as soft priors; they are not a substitute for donor ancestry.
- Donordata authentication cannot and should not be bypassed.
- Very unusual typings may still require manual review.
- The Windows executable still requires an installed external browser for Donordata login and access.
