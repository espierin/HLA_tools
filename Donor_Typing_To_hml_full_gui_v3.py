#!/usr/bin/env python3
"""
Convert Eurotransplant Donordata webpage text into:
  1. parsed HLA typing CSV
  2. GL string with ambiguities retained
  3. minimal HML 1.0.1 file

The script opens the donor webpage from an ET donor number in a controlled
browser session. Complete the normal Eurotransplant login and two-factor
authentication, confirm the donor page is loaded, and let this script expand
the HLA Typing details and read the visible webpage text directly to convert it
to HML.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
from xml.dom import minidom
from xml.etree import ElementTree as ET


ALLOWED_LOCI = (
    "A",
    "B",
    "C",
    "DRB1",
    "DRB3",
    "DRB4",
    "DRB5",
    "DQA1",
    "DQB1",
    "DPA1",
    "DPB1",
)

ET_LOCUS_ORDER = ALLOWED_LOCI
LOCUS_ORDER_INDEX = {locus: i for i, locus in enumerate(ET_LOCUS_ORDER)}

HLA_LOCUS_PATTERN = (
    "DRB1|DRB3|DRB4|DRB5|DQA1|DQB1|DPA1|DPB1|A|B|C"
)
HLA_ALLELE_RE = re.compile(
    rf"(?:HLA[- ]?)?({HLA_LOCUS_PATTERN})"
    r"\*"
    r"\d{2,3}"
    r"(?::\d{2,3}){0,3}"
    r"[A-Z]?"
)
ROW_START_RE = re.compile(
    r"^\s*(A|B|C|Cw|DRB1|DRB345|DQB1|DQA1|DPB1|DPA1)\b\s+"
)
DATE_TIME_CODE_RE = re.compile(
    r"(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}\s+([A-Z0-9]{3,})\b"
)
DONOR_DATA_URL_PREFIX = "https://donor-data.etnext.eu/donor/"

LAST_DONOR_ET_NUMBER: str | None = None
LAST_DONOR_PAGE_TEXT: str | None = None
LAST_OUTPUT_ROOT: Path | None = None


@dataclass(slots=True)
class Metadata:
    donor_et_number: str | None
    registration_center: str | None
    tt_lab: str | None
    typing_date: str | None
    donor_registration_date: str | None


@dataclass(slots=True)
class CandidateRow:
    candidate_id: int
    row_label: str
    row_text: str
    expected_allele_count: int
    total_allele_count: int
    character_count: int


@dataclass(slots=True)
class HlaRecord:
    row_label: str
    locus: str
    allele_group_1: str
    allele_group_2: str
    source_row_text: str


@dataclass(slots=True)
class ParsedTyping:
    text: str
    typing_date: str | None
    tt_lab: str | None
    parsed_hla: list[HlaRecord]
    all_candidates: list[CandidateRow]
    selected_candidates: list[CandidateRow]
    slash_count: int
    two_field_count: int
    allele_count: int


@dataclass(slots=True)
class ConversionResult:
    donor_et_number: str
    registration_center: str
    tt_lab: str
    typing_date: str
    loci: list[str]
    gl_string: str
    hml_file: Path
    hml_copy_file: Path | None
    output_dir: Path
    raw_text_file: Path
    normalised_text_file: Path
    metadata_file: Path
    hla_row_candidates_file: Path
    selected_hla_rows_file: Path
    parsed_hla_file: Path
    gl_string_file: Path


def empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in {"", "NA", "N/A", "NULL", "NONE", "NAN"}:
        return None
    return text


def first_present(*values: object) -> str | None:
    for value in values:
        cleaned = empty_to_none(value)
        if cleaned is not None:
            return cleaned
    return None


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def parse_et_date(value: object) -> str | None:
    text = empty_to_none(value)
    if text is None:
        return None
    date_at_start = re.match(r"^(\d{2}[-/]\d{2}[-/]\d{4})\b", text)
    if date_at_start:
        text = date_at_start.group(1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def normalise_text(value: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "`": "'",
        "\u00b4": "'",
        "\u201c": '"',
        "\u201d": '"',
        "|": "I",
        ";": ":",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"\bDRBI\*", "DRB1*", value)
    value = re.sub(r"\bDRBl\*", "DRB1*", value)
    value = re.sub(r"\bDRB\s*1\*", "DRB1*", value)
    value = re.sub(r"\bDQA[Iil]\*", "DQA1*", value)
    value = re.sub(r"\bDQB[Iil]\*", "DQB1*", value)
    value = re.sub(r"\bDPA[Iil]\*", "DPA1*", value)
    value = re.sub(r"\bDPB[Iil]\*", "DPB1*", value)
    value = re.sub(r"\bCw\*", "C*", value)
    value = re.sub(r"\s*\*\s*", "*", value)
    value = re.sub(r"\s*:\s*", ":", value)
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    return value.strip()


def get_ocr_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [normalise_text(line) for line in text.split("\n")]
    return [line for line in lines if line]


def get_following_label_value(lines: Sequence[str], label: str) -> str | None:
    for index, line in enumerate(lines):
        if line.strip().lower() != label.lower():
            continue
        for value in lines[index + 1 : index + 8]:
            cleaned = empty_to_none(value)
            if cleaned is None:
                continue
            return cleaned
    return None


def extract_donor_header_metadata(text: str) -> tuple[str | None, str | None, str | None]:
    text_norm = normalise_text(text)
    lines = get_ocr_lines(text)
    donor_registration_date = get_following_label_value(lines, "Registration Date")
    donor_et_number = get_following_label_value(lines, "Donor Nr")
    registration_center = get_following_label_value(lines, "Registration Center")

    match = re.search(
        r"Registration\s*Date\s+Donor\s*Nr\s+Registration\s*Center.*?"
        r"(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}\s+"
        r"([0-9]{3,})\s+([A-Z0-9]{3,})\b",
        text_norm,
        flags=re.IGNORECASE,
    )
    if match:
        donor_registration_date = first_present(donor_registration_date, match.group(1))
        donor_et_number = first_present(donor_et_number, match.group(2))
        registration_center = first_present(registration_center, match.group(3))

    if donor_et_number is None or registration_center is None:
        for i, line in enumerate(lines):
            if re.search(r"Donor\s*Nr", line, re.I) and re.search(
                r"Registration\s*Center", line, re.I
            ):
                lookahead = " ".join(lines[i : i + 9])
                found = re.search(
                    r"(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}\s+"
                    r"([0-9]{3,})\s+([A-Z0-9]{3,})\b",
                    lookahead,
                )
                if found:
                    donor_registration_date = first_present(
                        donor_registration_date, found.group(1)
                    )
                    donor_et_number = first_present(donor_et_number, found.group(2))
                    registration_center = first_present(
                        registration_center, found.group(3)
                    )
                    break

    if donor_et_number is None or registration_center is None:
        matches = re.findall(
            r"(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}\s+"
            r"([0-9]{3,})\s+([A-Z0-9]{3,})\s+(?:M|F)\s+\d+\s+"
            r"(?:Deceased|Living)",
            text_norm,
        )
        if matches:
            donor_registration_date = first_present(donor_registration_date, matches[0][0])
            donor_et_number = first_present(donor_et_number, matches[0][1])
            registration_center = first_present(registration_center, matches[0][2])

    return (
        parse_et_date(donor_registration_date),
        empty_to_none(donor_et_number),
        empty_to_none(registration_center),
    )


def extract_hla_typing_metadata(text: str) -> tuple[str | None, str | None]:
    text_norm = normalise_text(text)
    lines = get_ocr_lines(text)
    typing_date_raw = tt_lab = None
    hla_indexes = [i for i, line in enumerate(lines) if re.search(r"HLA\s*Typing", line, re.I)]

    if hla_indexes:
        start_idx = hla_indexes[0]
        end_idx = len(lines)
        ciwd_indexes = [
            i for i, line in enumerate(lines) if re.search(r"CIWD\s*Genotype", line, re.I)
        ]
        if ciwd_indexes and ciwd_indexes[0] > start_idx:
            end_idx = ciwd_indexes[0]
        hla_lines = lines[start_idx:end_idx]
        for index, line in enumerate(hla_lines):
            date_match = re.match(r"^(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}$", line)
            if not date_match:
                continue
            typing_date_raw = first_present(typing_date_raw, date_match.group(1))
            for value in hla_lines[index + 1 : index + 5]:
                if re.fullmatch(r"[A-Z0-9]{3,}", value):
                    tt_lab = first_present(tt_lab, value)
                    break
            if typing_date_raw and tt_lab:
                break

    if hla_indexes:
        hla_text = " ".join(lines[hla_indexes[0] :])
        match = re.search(
            r"Entry\s*Date\s+TT\s*Lab.*?"
            r"(\d{2}[-/]\d{2}[-/]\d{4})\s+\d{2}:\d{2}\s+"
            r"([A-Z0-9]{3,})\b",
            hla_text,
            flags=re.IGNORECASE,
        )
        if match:
            typing_date_raw, tt_lab = match.groups()

    if (typing_date_raw is None or tt_lab is None) and hla_indexes:
        start_idx = hla_indexes[0]
        end_idx = len(lines)
        ciw_indexes = [
            i for i, line in enumerate(lines) if re.search(r"CIWD\s*Genotype", line, re.I)
        ]
        if ciw_indexes and ciw_indexes[0] > start_idx:
            end_idx = ciw_indexes[0]
        for line in lines[start_idx:end_idx]:
            match = DATE_TIME_CODE_RE.search(line)
            if match:
                typing_date_raw = first_present(typing_date_raw, match.group(1))
                tt_lab = first_present(tt_lab, match.group(2))
                break

    if typing_date_raw is None or tt_lab is None:
        ciw_indexes = [
            i for i, line in enumerate(lines) if re.search(r"CIWD\s*Genotype", line, re.I)
        ]
        if ciw_indexes:
            start = max(0, ciw_indexes[0] - 25)
            lookback = " ".join(lines[start : ciw_indexes[0] + 1])
            matches = DATE_TIME_CODE_RE.findall(lookback)
            if matches:
                typing_date_raw = first_present(typing_date_raw, matches[-1][0])
                tt_lab = first_present(tt_lab, matches[-1][1])

    if typing_date_raw is None or tt_lab is None:
        matches = DATE_TIME_CODE_RE.findall(text_norm)
        if len(matches) >= 2:
            typing_date_raw = first_present(typing_date_raw, matches[1][0])
            tt_lab = first_present(tt_lab, matches[1][1])
        elif len(matches) == 1:
            typing_date_raw = first_present(typing_date_raw, matches[0][0])
            tt_lab = first_present(tt_lab, matches[0][1])

    return parse_et_date(typing_date_raw), empty_to_none(tt_lab)


def find_hla_typing_section_lines(text: str) -> list[str]:
    lines = get_ocr_lines(text)
    hla_indexes = [
        i for i, line in enumerate(lines) if re.search(r"^HLA\s*Typing$", line, re.I)
    ]
    if not hla_indexes:
        hla_indexes = [
            i for i, line in enumerate(lines) if re.search(r"HLA\s*Typing", line, re.I)
        ]
    if not hla_indexes:
        return lines

    start_idx = hla_indexes[0]
    end_idx = len(lines)
    section_end_re = re.compile(
        r"^(Current Virology|Organs Reported|Other Organs|Expected Explantation Date|"
        r"Donor Information|Clinical Data|Medical History|Laboratory Results|"
        r"Bacteriology & Virology|Radiology & Pathology|Bloodgas & Ventilation)\b",
        re.I,
    )
    for index in range(start_idx + 1, len(lines)):
        if section_end_re.search(lines[index]):
            end_idx = index
            break
    return lines[start_idx:end_idx]


def is_typing_entry_date_line(line: str) -> bool:
    return bool(re.fullmatch(r"\d{2}[-/]\d{2}[-/]\d{4}\s+\d{2}:\d{2}", line.strip()))


def find_tt_lab_after_date(lines: Sequence[str], date_index: int) -> str | None:
    for line in lines[date_index + 1 : date_index + 6]:
        if re.fullmatch(r"[A-Z0-9]{3,}", line.strip()):
            return line.strip()
    return None


def split_hla_typing_blocks(text: str) -> list[tuple[str, str | None, str | None]]:
    section_lines = find_hla_typing_section_lines(text)
    starts: list[int] = []
    for index, line in enumerate(section_lines):
        if is_typing_entry_date_line(line) and find_tt_lab_after_date(section_lines, index):
            starts.append(index)

    if not starts:
        typing_date, tt_lab = extract_hla_typing_metadata(text)
        return [(text, typing_date, tt_lab)]

    blocks: list[tuple[str, str | None, str | None]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(section_lines)
        block_lines = section_lines[start:end]
        typing_date_raw = section_lines[start]
        tt_lab = find_tt_lab_after_date(section_lines, start)
        blocks.append(("\n".join(block_lines), parse_et_date(typing_date_raw), tt_lab))
    return blocks


def two_field_allele_count(parsed_hla: Sequence[HlaRecord]) -> int:
    alleles = extract_alleles_from_text(make_gl_string(parsed_hla))
    return sum(bool(re.fullmatch(r"HLA-[A-Z0-9]+\*\d{2,3}:\d{2,3}[A-Z]?", allele)) for allele in alleles)


def parsed_typing_sort_key(parsed: ParsedTyping) -> tuple[int, int, int, int, int]:
    loci_count = len({record.locus for record in parsed.parsed_hla})
    return (
        1 if loci_count >= 8 else 0,
        -parsed.slash_count,
        parsed.two_field_count,
        loci_count,
        -parsed.allele_count,
    )


def extract_metadata(text: str) -> Metadata:
    donor_registration_date, donor_et_number, registration_center = (
        extract_donor_header_metadata(text)
    )
    typing_date, tt_lab = extract_hla_typing_metadata(text)
    return Metadata(
        donor_et_number=donor_et_number,
        registration_center=registration_center,
        tt_lab=tt_lab,
        typing_date=typing_date,
        donor_registration_date=donor_registration_date,
    )


def extract_alleles_from_text(text: str) -> list[str]:
    text = normalise_text(text)
    alleles: list[str] = []
    seen: set[str] = set()
    for match in HLA_ALLELE_RE.finditer(text):
        allele = match.group(0)
        allele = re.sub(r"^HLA[- ]?", "", allele)
        allele = re.sub(r"\s+", "", allele)
        allele = allele.replace(";", ":")
        allele = re.sub(r"\bDRBI\*", "DRB1*", allele)
        allele = re.sub(r"\bDRBl\*", "DRB1*", allele)
        allele = re.sub(r"\bDQA[Iil]\*", "DQA1*", allele)
        allele = re.sub(r"\bDQB[Iil]\*", "DQB1*", allele)
        allele = re.sub(r"\bDPA[Iil]\*", "DPA1*", allele)
        allele = re.sub(r"\bDPB[Iil]\*", "DPB1*", allele)
        allele = re.sub(r"\bCw\*", "C*", allele)
        allele = f"HLA-{allele}"
        if allele not in seen:
            seen.add(allele)
            alleles.append(allele)
    return alleles


def has_expanded_hla_genotype_text(text: str) -> bool:
    if re.search(r"CIWD\s+Genotype", text, re.I):
        return True
    allele_matches = list(HLA_ALLELE_RE.finditer(text))
    loci = {match.group(1).upper().replace("DRB345", "DRB3") for match in allele_matches}
    return len(allele_matches) >= 6 and len(loci) >= 4


def get_locus_from_allele(allele: str) -> str | None:
    match = re.match(r"^HLA-([A-Z0-9]+)\*", allele)
    return match.group(1) if match else None


def expected_loci_for_row_label(row_label: str) -> tuple[str, ...]:
    if row_label == "Cw":
        return ("C",)
    if row_label == "DRB345":
        return ("DRB3", "DRB4", "DRB5")
    return (row_label,)


def count_expected_alleles(row_text: str, row_label: str) -> int:
    alleles = extract_alleles_from_text(row_text)
    expected = set(expected_loci_for_row_label(row_label))
    return sum(get_locus_from_allele(allele) in expected for allele in alleles)


def extract_locus_row_candidates(text: str) -> list[CandidateRow]:
    lines = get_ocr_lines(text)
    candidates: list[CandidateRow] = []
    current_label: str | None = None
    current_text: list[str] = []
    candidate_id = 0

    def save_current() -> None:
        if current_label is None or not current_text:
            return
        row_text = " ".join(current_text)
        expected_count = count_expected_alleles(row_text, current_label)
        if expected_count == 0:
            return
        candidates.append(
            CandidateRow(
                candidate_id=candidate_id,
                row_label=current_label,
                row_text=row_text,
                expected_allele_count=expected_count,
                total_allele_count=len(extract_alleles_from_text(row_text)),
                character_count=len(row_text),
            )
        )

    for line in lines:
        match = ROW_START_RE.match(line)
        if match:
            save_current()
            candidate_id += 1
            current_label = match.group(1)
            if current_label == "Cw":
                current_label = "C"
            current_text = [line]
        elif current_label is not None and HLA_ALLELE_RE.search(line):
            current_text.append(line)

    save_current()
    if not candidates:
        raise ValueError("No HLA row candidates were detected in the webpage text.")
    return candidates


def select_best_locus_rows(text: str) -> tuple[dict[str, str], list[CandidateRow], list[CandidateRow]]:
    candidates = extract_locus_row_candidates(text)
    best_by_label: dict[str, CandidateRow] = {}
    for candidate in candidates:
        previous = best_by_label.get(candidate.row_label)
        key = (candidate.expected_allele_count, candidate.character_count)
        prev_key = (
            previous.expected_allele_count,
            previous.character_count,
        ) if previous else (-1, -1)
        if key > prev_key:
            best_by_label[candidate.row_label] = candidate
    selected = sorted(
        best_by_label.values(),
        key=lambda row: LOCUS_ORDER_INDEX.get(row.row_label, 999),
    )
    return {row.row_label: row.row_text for row in selected}, candidates, selected


def split_locus_row_into_groups(
    row_text: str, row_label: str, duplicate_single_group_as_homozygous: bool
) -> list[list[str]]:
    text = normalise_text(row_text)
    text = ROW_START_RE.sub("", text, count=1)
    split_re = re.compile(
        rf"(?<=[0-9A-Z])\s*-\s*(?=(?:HLA[- ]?)?(?:{HLA_LOCUS_PATTERN})\*)"
    )
    parts = [part.strip() for part in split_re.split(text) if part.strip()]
    groups = [extract_alleles_from_text(part) for part in parts]
    groups = [group for group in groups if group]
    expected = set(expected_loci_for_row_label(row_label))
    groups = [
        [allele for allele in group if get_locus_from_allele(allele) in expected]
        for group in groups
    ]
    groups = [group for group in groups if group]
    if len(groups) == 1 and duplicate_single_group_as_homozygous:
        groups = [groups[0], list(groups[0])]
    return groups


def make_locus_records(
    rows: dict[str, str], duplicate_single_group_as_homozygous: bool
) -> list[HlaRecord]:
    records: dict[str, HlaRecord] = {}
    for row_label, row_text in rows.items():
        groups = split_locus_row_into_groups(
            row_text, row_label, duplicate_single_group_as_homozygous
        )
        if not groups:
            continue
        allele_loci = []
        for allele in [a for group in groups for a in group]:
            locus = get_locus_from_allele(allele)
            if locus and locus not in allele_loci:
                allele_loci.append(locus)

        expected = set(expected_loci_for_row_label(row_label))
        for locus in [locus for locus in allele_loci if locus in expected]:
            groups_for_locus = [
                [allele for allele in group if get_locus_from_allele(allele) == locus]
                for group in groups
            ]
            groups_for_locus = [group for group in groups_for_locus if group]
            if len(groups_for_locus) == 1 and duplicate_single_group_as_homozygous:
                groups_for_locus = [groups_for_locus[0], list(groups_for_locus[0])]
            if not groups_for_locus:
                continue
            if locus not in ALLOWED_LOCI:
                print(f"Warning: skipping unsupported locus: {locus}", file=sys.stderr)
                continue
            group_1 = "/".join(dict.fromkeys(groups_for_locus[0]))
            group_2_source = groups_for_locus[1] if len(groups_for_locus) >= 2 else groups_for_locus[0]
            group_2 = "/".join(dict.fromkeys(group_2_source))
            records[locus] = HlaRecord(
                row_label=row_label,
                locus=locus,
                allele_group_1=group_1,
                allele_group_2=group_2,
                source_row_text=row_text,
            )

    if not records:
        raise ValueError("No HLA allele records were extracted from the webpage text.")
    return sorted(records.values(), key=lambda row: LOCUS_ORDER_INDEX[row.locus])


def try_parse_hla_from_text(
    donor_page_text: str, duplicate_single_group_as_homozygous: bool
) -> ParsedTyping:
    parsed_options: list[ParsedTyping] = []
    parse_errors: list[str] = []
    for block_text, typing_date, tt_lab in split_hla_typing_blocks(donor_page_text):
        try:
            selected_rows, all_candidates, selected_candidates = select_best_locus_rows(block_text)
            parsed_hla = make_locus_records(
                selected_rows, duplicate_single_group_as_homozygous
            )
        except Exception as exc:  # noqa: BLE001 - try the next HLA Typing card
            parse_errors.append(str(exc))
            continue
        gl_string = make_gl_string(parsed_hla)
        parsed_options.append(
            ParsedTyping(
                text=block_text,
                typing_date=typing_date,
                tt_lab=tt_lab,
                parsed_hla=parsed_hla,
                all_candidates=all_candidates,
                selected_candidates=selected_candidates,
                slash_count=gl_string.count("/"),
                two_field_count=two_field_allele_count(parsed_hla),
                allele_count=len(extract_alleles_from_text(gl_string)),
            )
        )

    if not parsed_options:
        detail = "; ".join(dict.fromkeys(parse_errors))
        raise ValueError(
            "No HLA Typing block with usable CIWD genotype allele rows was found"
            + (f": {detail}" if detail else ".")
        )

    return max(parsed_options, key=parsed_typing_sort_key)


def make_gl_string(parsed_hla: Sequence[HlaRecord]) -> str:
    return "^".join(
        f"{record.allele_group_1}+{record.allele_group_2}" for record in parsed_hla
    )


def validate_et_locus_constraints(parsed_hla: Sequence[HlaRecord]) -> None:
    loci = [record.locus for record in parsed_hla]
    if len(set(loci)) != len(loci):
        raise ValueError("ET HML constraint failed: a locus occurs more than once.")
    if len(loci) < 8:
        raise ValueError(f"ET HML constraint failed: fewer than 8 loci extracted: {len(loci)}")
    if len(loci) > len(ALLOWED_LOCI):
        raise ValueError(
            f"ET HML constraint failed: more than {len(ALLOWED_LOCI)} loci extracted: {len(loci)}"
        )
    unsupported = sorted(set(loci).difference(ALLOWED_LOCI))
    if unsupported:
        raise ValueError(f"Unsupported loci extracted: {', '.join(unsupported)}")


def write_hml(
    parsed_hla: Sequence[HlaRecord],
    metadata: Metadata,
    gl_string: str,
    output_file: Path,
    imgt_hla_version: str,
    hml_project_name: str,
    collection_method: str,
    include_typing_method_placeholders: bool,
    typing_method_placeholder_type: str,
    typing_method_test_id_source: str,
) -> None:
    donor_et_number = metadata.donor_et_number
    reporting_center = metadata.registration_center
    tt_lab = metadata.tt_lab
    typing_date = metadata.typing_date

    if not donor_et_number:
        raise ValueError("Missing donor ET number. HML cannot be written.")
    if not reporting_center:
        raise ValueError("Missing Registration Center / typing lab. HML cannot be written.")
    if not tt_lab:
        raise ValueError("Missing TT-lab. HML cannot be written.")
    if not typing_date:
        raise ValueError("Missing HLA typing entry date. HML cannot be written.")

    ns = "http://schemas.nmdp.org/spec/hml/1.0.1"
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    ET.register_namespace("", ns)
    ET.register_namespace("xsi", xsi)

    root = ET.Element(
        f"{{{ns}}}hml",
        {
            "project-name": hml_project_name,
            "version": "1.0.1",
            f"{{{xsi}}}schemaLocation": (
                "http://schemas.nmdp.org/spec/hml/1.0.1 "
                "http://schemas.nmdp.org/spec/hml/1.0.1/hml-1.0.1.xsd"
            ),
        },
    )
    timestamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    ET.SubElement(
        root,
        f"{{{ns}}}hmlid",
        {
            "root": reporting_center,
            "extension": f"ET{donor_et_number}_{timestamp}",
        },
    )
    ET.SubElement(
        root,
        f"{{{ns}}}reporting-center",
        {
            "reporting-center-id": reporting_center,
            "reporting-center-context": "Eurotransplant",
        },
    )
    sample = ET.SubElement(
        root,
        f"{{{ns}}}sample",
        {"id": donor_et_number, "center-code": reporting_center},
    )
    for name, value in (
        ("person-identifier", donor_et_number),
        ("ET-donor-number", donor_et_number),
        ("registration-center", reporting_center),
        ("TT-lab", tt_lab),
    ):
        ET.SubElement(sample, f"{{{ns}}}property", {"name": name, "value": value})
    ET.SubElement(sample, f"{{{ns}}}collection-method").text = collection_method

    typing = ET.SubElement(
        sample, f"{{{ns}}}typing", {"date": typing_date, "gene-family": "HLA"}
    )
    allele_assignment = ET.SubElement(
        typing,
        f"{{{ns}}}allele-assignment",
        {
            "date": typing_date,
            "allele-db": "IMGT/HLA",
            "allele-version": imgt_hla_version,
        },
    )
    ET.SubElement(allele_assignment, f"{{{ns}}}glstring").text = gl_string
    typing_method = ET.SubElement(typing, f"{{{ns}}}typing-method")

    if include_typing_method_placeholders:
        if typing_method_placeholder_type not in {"sbt-ngs", "sbt-sanger"}:
            raise ValueError(
                f"Unsupported typing method placeholder type: {typing_method_placeholder_type}"
            )
        for record in parsed_hla:
            ET.SubElement(
                typing_method,
                f"{{{ns}}}{typing_method_placeholder_type}",
                {
                    "locus": f"HLA-{record.locus}",
                    "test-id": f"interpreted-{record.locus}",
                    "test-id-source": typing_method_test_id_source,
                },
            )

    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(xml_bytes).toprettyxml(indent="  ", encoding="utf-8")
    output_file.write_bytes(pretty)


def write_csv(path: Path, rows: Sequence[object], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            data = asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row)
            writer.writerow({name: data.get(name) for name in fieldnames})


def require_metadata(metadata: Metadata) -> None:
    missing = []
    if not metadata.donor_et_number:
        missing.append("Donor Nr")
    if not metadata.registration_center:
        missing.append("Registration Center")
    if not metadata.tt_lab:
        missing.append("TT Lab from HLA Typing section")
    if not metadata.typing_date:
        missing.append("HLA Typing Entry Date")
    if missing:
        raise ValueError("Could not extract required metadata: " + "; ".join(missing))


def require_requested_donor_match(metadata: Metadata, requested_donor_et_number: str | None) -> None:
    if requested_donor_et_number is None:
        return
    if not metadata.donor_et_number:
        raise ValueError(
            "Could not find 'Donor Nr' in the webpage text. "
            "Make sure the donor report is loaded before clicking OK."
        )
    if metadata.donor_et_number != requested_donor_et_number:
        raise ValueError(
            "The browser page is for donor "
            f"{metadata.donor_et_number}, but you requested donor {requested_donor_et_number}. "
            "Open the requested donor page before clicking OK."
        )


def find_browser_executable() -> Path:
    candidates = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidates.extend(
            [
                Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ValueError("Could not find Microsoft Edge or Google Chrome on this computer.")


def get_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_json_url(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_devtools(port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            read_json_url(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
            return
        except Exception as exc:  # noqa: BLE001 - browser may still be starting
            last_error = exc
            time.sleep(0.3)
    raise ValueError(f"Browser did not expose DevTools on port {port}: {last_error}")


def is_devtools_alive(port: int) -> bool:
    try:
        read_json_url(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
        return True
    except Exception:
        return False


def get_browser_profile_dir(started_from: Path) -> Path:
    profile_dir = started_from / "donor_browser_profile_active"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def read_saved_devtools_port(profile_dir: Path) -> int | None:
    port_file = profile_dir / "devtools_port.txt"
    try:
        port = int(port_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return port if is_devtools_alive(port) else None


def discover_existing_devtools_port(profile_dir: Path) -> int | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -match '^(msedge|chrome)\\.exe$' -and $_.CommandLine } | "
                    "Select-Object -ExpandProperty CommandLine"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    profile_text = str(profile_dir).lower()
    for command_line in result.stdout.splitlines():
        command_lower = command_line.lower()
        if profile_text not in command_lower:
            continue
        match = re.search(r"--remote-debugging-port=(\d+)", command_line)
        if not match:
            continue
        port = int(match.group(1))
        if is_devtools_alive(port):
            return port
    return None


def save_devtools_port(profile_dir: Path, port: int) -> None:
    (profile_dir / "devtools_port.txt").write_text(str(port), encoding="utf-8")


def launch_controlled_browser(
    donor_url: str, donor_et_number: str, started_from: Path
) -> tuple[subprocess.Popen | None, int]:
    profile_dir = get_browser_profile_dir(started_from)
    saved_port = read_saved_devtools_port(profile_dir) or discover_existing_devtools_port(profile_dir)
    if saved_port is not None:
        save_devtools_port(profile_dir, saved_port)
        navigate_controlled_browser(saved_port, donor_url, donor_et_number)
        return None, saved_port

    browser = find_browser_executable()
    port = get_free_local_port()
    process = subprocess.Popen(
        [
            str(browser),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--new-window",
            donor_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_for_devtools(port)
    save_devtools_port(profile_dir, port)
    return process, port


def find_donor_page_websocket_url(port: int, donor_et_number: str) -> str:
    targets = read_json_url(f"http://127.0.0.1:{port}/json", timeout=2.0)
    fallback = None
    for target in targets:
        if target.get("type") != "page":
            continue
        websocket_url = target.get("webSocketDebuggerUrl")
        if not websocket_url:
            continue
        url = target.get("url", "")
        if donor_et_number in url or "donor-data.etnext.eu" in url:
            return websocket_url
        fallback = fallback or websocket_url
    if fallback:
        return fallback
    raise ValueError("Could not find the controlled donor browser page.")


def parse_ws_url(ws_url: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"ws://([^/:]+):(\d+)(/.*)", ws_url)
    if not match:
        raise ValueError(f"Unsupported DevTools websocket URL: {ws_url}")
    host, port, path = match.groups()
    return host, int(port), path


def websocket_connect(ws_url: str) -> socket.socket:
    host, port, path = parse_ws_url(ws_url)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock = socket.create_connection((host, port), timeout=5)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    if b" 101 " not in response.split(b"\r\n", 1)[0]:
        raise ValueError("Could not connect to browser DevTools websocket.")
    return sock


def websocket_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(0x80 | len(payload))
    elif len(payload) < 65536:
        header.extend([0x80 | 126, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF])
    else:
        header.append(0x80 | 127)
        header.extend(len(payload).to_bytes(8, "big"))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ValueError("Browser DevTools websocket closed unexpectedly.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def websocket_recv_text(sock: socket.socket) -> str:
    while True:
        first_two = recv_exact(sock, 2)
        opcode = first_two[0] & 0x0F
        masked = bool(first_two[1] & 0x80)
        length = first_two[1] & 0x7F
        if length == 126:
            length = int.from_bytes(recv_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(recv_exact(sock, 8), "big")
        mask = recv_exact(sock, 4) if masked else b""
        payload = recv_exact(sock, length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 1:
            return payload.decode("utf-8")
        if opcode == 8:
            raise ValueError("Browser DevTools websocket closed.")


def devtools_evaluate(sock: socket.socket, command_id: int, expression: str, await_promise: bool = False) -> str:
        command = {
            "id": command_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        }
        websocket_send_text(sock, json.dumps(command))
        while True:
            message = json.loads(websocket_recv_text(sock))
            if message.get("id") != command_id:
                continue
            value = (
                message.get("result", {})
                .get("result", {})
                .get("value", "")
            )
            return value or ""


def devtools_command(sock: socket.socket, command_id: int, method: str, params: dict | None = None) -> dict:
    command = {
        "id": command_id,
        "method": method,
        "params": params or {},
    }
    websocket_send_text(sock, json.dumps(command))
    while True:
        message = json.loads(websocket_recv_text(sock))
        if message.get("id") == command_id:
            return message


def navigate_controlled_browser(port: int, donor_url: str, donor_et_number: str) -> None:
    websocket_url = find_donor_page_websocket_url(port, donor_et_number)
    sock = websocket_connect(websocket_url)
    try:
        devtools_command(sock, 100, "Page.enable")
        devtools_command(sock, 101, "Page.navigate", {"url": donor_url})
    finally:
        sock.close()


def read_donor_page_text_from_browser(port: int, donor_et_number: str) -> str:
    websocket_url = find_donor_page_websocket_url(port, donor_et_number)
    sock = websocket_connect(websocket_url)
    try:
        expand_expression = r"""
(async () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const text = el => (el && el.innerText ? el.innerText : '').replace(/\s+/g, ' ').trim();
  const bodyText = () => document.body ? document.body.innerText : '';
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const mouseClick = el => {
    if (!visible(el)) return false;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const rect = el.getBoundingClientRect();
    const x = Math.max(1, Math.floor(rect.left + Math.min(rect.width - 1, rect.width / 2)));
    const y = Math.max(1, Math.floor(rect.top + Math.min(rect.height - 1, rect.height / 2)));
    if (typeof el.click === 'function') {
      try { el.click(); } catch (_) {}
    }
    for (const type of ['pointerover', 'pointerenter', 'mouseover', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      const eventInit = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, buttons: type.endsWith('down') ? 1 : 0};
      try {
        if (typeof PointerEvent !== 'undefined' && type.startsWith('pointer')) {
          el.dispatchEvent(new PointerEvent(type, eventInit));
        } else {
          el.dispatchEvent(new MouseEvent(type, eventInit));
        }
      } catch (_) {
        el.dispatchEvent(new MouseEvent(type.replace(/^pointer/, 'mouse'), eventInit));
      }
    }
    return true;
  };
  const clickTargetsFor = el => {
    const targets = [];
    const preferred = el.closest('button, [role="button"], [aria-expanded], .mat-expansion-panel-header, mat-expansion-panel-header, mat-row, tr, .mat-row, .mat-mdc-row, .cdk-row');
    if (preferred) targets.push(preferred);
    let current = el;
    for (let depth = 0; current && depth < 9; depth++) {
      targets.push(current);
      current = current.parentElement;
    }
    const row = el.closest('tr, mat-row, .mat-row, .mat-mdc-row, .cdk-row');
    if (row) targets.push(row);
    return Array.from(new Set(targets));
  };

  const clickIfUseful = (el) => {
    if (!el) return false;
    if ((el.getAttribute('aria-expanded') || '').toLowerCase() === 'true') return false;
    if (!visible(el)) return false;
    const local = [
      el.innerText || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.getAttribute('aria-expanded') || '',
      text(el.closest('section, article, .card, .accordion, .panel, div'))
    ].join(' ');
    if (!/HLA Typing|CIWD Genotype|Full Phenotype|TT Lab/i.test(local)) return false;
    let clicked = false;
    for (const target of clickTargetsFor(el)) clicked = mouseClick(target) || clicked;
    return clicked;
  };

  const clickHlaExpandIcons = async () => {
    const hlaHeading = Array.from(document.querySelectorAll('body *'))
      .find(el => /^HLA Typing$/i.test(text(el)));
    if (!hlaHeading) return 0;
    const hlaY = hlaHeading.getBoundingClientRect().top;
    const bottomMarker = Array.from(document.querySelectorAll('body *'))
      .filter(el => /^(Current Virology|Organs Reported|Donor Information)$/i.test(text(el)))
      .map(el => el.getBoundingClientRect().top)
      .filter(y => y > hlaY)
      .sort((a, b) => a - b)[0];
    const maxY = Number.isFinite(bottomMarker) ? bottomMarker : hlaY + 700;
    const candidates = Array.from(document.querySelectorAll(
      'mat-icon, .mat-icon, [class*="expand"], [aria-expanded="false"], button, [role="button"], .mat-expansion-panel-header, mat-expansion-panel-header'
    ))
      .filter(el => {
        if (!visible(el)) return false;
        const rect = el.getBoundingClientRect();
        if (rect.top < hlaY - 30 || rect.top > maxY + 30) return false;
        const label = [
          text(el),
          el.getAttribute('aria-label') || '',
          el.getAttribute('title') || '',
          el.getAttribute('aria-expanded') || '',
          text(el.closest('tr, .mat-row, .mat-mdc-row, .cdk-row, mat-expansion-panel, .mat-expansion-panel'))
        ].join(' ');
        return /expand_more|keyboard_arrow_down|HLA Typing|Full Phenotype|TT Lab/i.test(label);
      })
      .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);

    let clicked = 0;
    for (const el of candidates) {
      if ((el.getAttribute('aria-expanded') || '').toLowerCase() === 'true') continue;
      let didClick = false;
      for (const target of clickTargetsFor(el)) didClick = mouseClick(target) || didClick;
      if (didClick) {
        clicked += 1;
        await sleep(1000);
        if (/CIWD Genotype/i.test(bodyText())) break;
      }
    }
    return clicked;
  };

  for (let round = 0; round < 4 && !/CIWD Genotype/i.test(bodyText()); round++) {
    const explicit = Array.from(document.querySelectorAll(
      '[aria-expanded="false"], button, [role="button"], .accordion-button, .mat-expansion-panel-header, mat-expansion-panel-header'
    ));
    for (const el of explicit) {
      if (clickIfUseful(el)) await sleep(900);
      if (/CIWD Genotype/i.test(bodyText())) break;
    }
    if (/CIWD Genotype/i.test(bodyText())) break;
    const clicks = await clickHlaExpandIcons();
    if (!clicks) await sleep(500);
  }

  return bodyText();
})()
"""
        devtools_evaluate(sock, 1, expand_expression, await_promise=True)
        time.sleep(0.5)
        return devtools_evaluate(sock, 2, "document.body ? document.body.innerText : ''")
    finally:
        sock.close()


def powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_powershell_dialog(script: str) -> str | None:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Sta", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - dialog fallback must not crash CLI use
        print(f"Could not run PowerShell dialog: {exc}", file=sys.stderr)
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        print(f"PowerShell dialog failed: {detail}", file=sys.stderr)
        return None
    return completed.stdout


def ask_donor_et_number_with_powershell_dialog() -> str | None:
    script = "\n".join(
        (
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$form = New-Object System.Windows.Forms.Form",
            "$form.Text = " + powershell_single_quoted("Eurotransplant donor"),
            "$form.Size = New-Object System.Drawing.Size(390,165)",
            "$form.StartPosition = 'CenterScreen'",
            "$form.TopMost = $true",
            "$form.FormBorderStyle = 'FixedDialog'",
            "$form.MaximizeBox = $false",
            "$form.MinimizeBox = $false",
            "$label = New-Object System.Windows.Forms.Label",
            "$label.Text = " + powershell_single_quoted("Enter the donor ET number:"),
            "$label.AutoSize = $true",
            "$label.Location = New-Object System.Drawing.Point(16,18)",
            "$textbox = New-Object System.Windows.Forms.TextBox",
            "$textbox.Location = New-Object System.Drawing.Point(18,48)",
            "$textbox.Size = New-Object System.Drawing.Size(335,24)",
            "$ok = New-Object System.Windows.Forms.Button",
            "$ok.Text = 'OK'",
            "$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK",
            "$ok.Location = New-Object System.Drawing.Point(196,88)",
            "$cancel = New-Object System.Windows.Forms.Button",
            "$cancel.Text = 'Cancel'",
            "$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel",
            "$cancel.Location = New-Object System.Drawing.Point(278,88)",
            "$form.AcceptButton = $ok",
            "$form.CancelButton = $cancel",
            "$form.Controls.AddRange(@($label, $textbox, $ok, $cancel))",
            "$form.Add_Shown({$textbox.Focus()})",
            "$result = $form.ShowDialog()",
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($textbox.Text) } else { exit 2 }",
        )
    )
    value = run_powershell_dialog(script)
    if value is None:
        return None
    value = value.strip()
    return value or None


def ask_ok_cancel_with_powershell_dialog(title: str, message: str) -> bool | None:
    script = "\n".join(
        (
            "Add-Type -AssemblyName System.Windows.Forms",
            "$owner = New-Object System.Windows.Forms.Form",
            "$owner.TopMost = $true",
            "$owner.StartPosition = 'CenterScreen'",
            "$owner.Size = New-Object System.Drawing.Size(1,1)",
            "$owner.ShowInTaskbar = $false",
            "$owner.Opacity = 0",
            "$owner.Show()",
            "$owner.Activate()",
            "$result = [System.Windows.Forms.MessageBox]::Show($owner, "
            + powershell_single_quoted(message)
            + ", "
            + powershell_single_quoted(title)
            + ", [System.Windows.Forms.MessageBoxButtons]::OKCancel"
            + ", [System.Windows.Forms.MessageBoxIcon]::Information)",
            "$owner.Close()",
            "[Console]::Out.Write($result)",
        )
    )
    result = run_powershell_dialog(script)
    if result is None:
        return None
    return result.strip().lower() == "ok"


def show_powershell_message(title: str, message: str, error: bool = False) -> bool:
    icon = "Error" if error else "Information"
    script = "\n".join(
        (
            "Add-Type -AssemblyName System.Windows.Forms",
            "$owner = New-Object System.Windows.Forms.Form",
            "$owner.TopMost = $true",
            "$owner.StartPosition = 'CenterScreen'",
            "$owner.Size = New-Object System.Drawing.Size(1,1)",
            "$owner.ShowInTaskbar = $false",
            "$owner.Opacity = 0",
            "$owner.Show()",
            "$owner.Activate()",
            "[System.Windows.Forms.MessageBox]::Show($owner, "
            + powershell_single_quoted(message)
            + ", "
            + powershell_single_quoted(title)
            + ", [System.Windows.Forms.MessageBoxButtons]::OK"
            + f", [System.Windows.Forms.MessageBoxIcon]::{icon}) | Out-Null",
            "$owner.Close()",
        )
    )
    return run_powershell_dialog(script) is not None


def ask_donor_et_number_with_dialog() -> str | None:
    if getattr(sys, "frozen", False):
        value = ask_donor_et_number_with_powershell_dialog()
        if value is None:
            try:
                value = input("Enter the donor ET number and press Enter: ")
            except EOFError:
                return None
        value = value.strip()
        if not re.fullmatch(r"\d{3,}", value):
            raise ValueError("The ET number must contain digits only.")
        return value

    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception as exc:  # noqa: BLE001 - keep CLI usable if Tk is unavailable
        print(f"Could not open ET number dialog: {exc}", file=sys.stderr)
        try:
            value = input("Enter the donor ET number and press Enter: ")
        except EOFError:
            return None
        value = value.strip()
        if not re.fullmatch(r"\d{3,}", value):
            raise ValueError("The ET number must contain digits only.")
        return value

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        value = simpledialog.askstring(
            "Eurotransplant donor",
            "Enter the donor ET number:",
            parent=root,
        )
    finally:
        root.destroy()

    if value is None:
        return None
    value = value.strip()
    if not re.fullmatch(r"\d{3,}", value):
        raise ValueError("The ET number must contain digits only.")
    return value


def wait_for_browser_ready_with_dialog(donor_et_number: str) -> bool:
    if getattr(sys, "frozen", False):
        value = ask_ok_cancel_with_powershell_dialog(
            "Donor page ready?",
            (
                "A controlled browser window has been opened for the donor page.\n\n"
                "Complete the normal Eurotransplant login and two-factor authentication.\n\n"
                f"Make sure donor {donor_et_number} is open.\n\n"
                "Click OK when the donor page is loaded. The program will expand the HLA Typing details "
                "and read the webpage content directly."
            ),
        )
        if value is not None:
            return value
        print("A controlled browser window has been opened for the donor page.")
        print("Complete the normal Eurotransplant login and two-factor authentication.")
        print(f"Make sure donor {donor_et_number} is open.")
        input("After the donor page has loaded, press Enter here to continue.")
        return True

    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:  # noqa: BLE001 - keep CLI usable if Tk is unavailable
        print(f"Could not open browser-ready dialog: {exc}", file=sys.stderr)
        print("A controlled browser window has been opened for the donor page.")
        print("Complete the normal Eurotransplant login and two-factor authentication.")
        print(f"Make sure donor {donor_et_number} is open.")
        input("After the donor page has loaded, press Enter here to continue.")
        return True

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return messagebox.askokcancel(
            "Donor page ready?",
            (
                "A controlled browser window has been opened for the donor page.\n\n"
                "Complete the normal Eurotransplant login and two-factor authentication.\n\n"
                f"Make sure donor {donor_et_number} is open.\n\n"
                "Click OK here when the donor page is loaded. The program will expand the HLA Typing details "
                "and read the webpage content directly."
            ),
            parent=root,
        )
    finally:
        root.destroy()


def default_output_dir_for_run(
    donor_et_number: str | None, output_root: Path, run_timestamp: str
) -> Path:
    donor_id_safe = safe_filename(donor_et_number or "unknown")
    return output_root / f"ET{donor_id_safe}_HML_output_{run_timestamp}"


def donor_et_number_from_argv() -> str | None:
    for index, item in enumerate(sys.argv):
        if item == "--donor-et-number" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if item.startswith("--donor-et-number="):
            return item.split("=", 1)[1]
    return None


def write_failure_log(exc: BaseException) -> Path | None:
    output_root = LAST_OUTPUT_ROOT or Path(__file__).resolve().parent
    donor_et_number = LAST_DONOR_ET_NUMBER or donor_et_number_from_argv() or "unknown"
    run_timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    failure_dir = output_root / f"ET{safe_filename(donor_et_number)}_HML_failed_{run_timestamp}"
    try:
        failure_dir.mkdir(parents=True, exist_ok=True)
        error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if isinstance(exc, SystemExit):
            error_text = f"SystemExit: {exc.code}\n\n{error_text}"
        (failure_dir / "error.txt").write_text(error_text, encoding="utf-8")
        if LAST_DONOR_PAGE_TEXT is not None:
            (failure_dir / "raw_donor_page_text.txt").write_text(
                LAST_DONOR_PAGE_TEXT, encoding="utf-8"
            )
            (failure_dir / "normalised_donor_page_text.txt").write_text(
                normalise_text(LAST_DONOR_PAGE_TEXT), encoding="utf-8"
            )
        print(f"Failure details written to: {failure_dir}", file=sys.stderr)
        return failure_dir
    except Exception as log_exc:  # noqa: BLE001 - never hide the original failure
        print(f"Could not write failure log: {log_exc}", file=sys.stderr)
        return None


def show_dialog_message(title: str, message: str, error: bool = False) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        show_powershell_message(title, message, error=error)
        return

    try:
        root = tk.Tk()
    except Exception:
        show_powershell_message(title, message, error=error)
        return
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if error:
            messagebox.showerror(title, message, parent=root)
        else:
            messagebox.showinfo(title, message, parent=root)
    finally:
        root.destroy()


def convert_donor_page_text_to_hml(
    donor_page_text: str,
    requested_donor_et_number: str,
    output_root: Path,
    output_dir: Path | None = None,
    hml_files_dir: Path | None = None,
    imgt_hla_version: str = "unknown",
    hml_project_name: str = "Eurotransplant_Deceased_Donor_HLA",
    collection_method: str = "unknown",
    copy_to_hml_files: bool = True,
    save_diagnostics: bool = True,
    duplicate_single_group_as_homozygous: bool = True,
    include_typing_method_placeholders: bool = True,
    typing_method_placeholder_type: str = "sbt-ngs",
    typing_method_test_id_source: str = "Eurotransplant-Donordata-interpreted",
) -> ConversionResult:
    output_root = Path(output_root)
    run_timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    selected_typing = try_parse_hla_from_text(
        donor_page_text, duplicate_single_group_as_homozygous
    )
    parsed_hla = selected_typing.parsed_hla
    metadata = extract_metadata(donor_page_text)
    metadata = Metadata(
        donor_et_number=metadata.donor_et_number,
        registration_center=metadata.registration_center,
        tt_lab=first_present(selected_typing.tt_lab, metadata.tt_lab),
        typing_date=first_present(selected_typing.typing_date, metadata.typing_date),
        donor_registration_date=metadata.donor_registration_date,
    )
    require_requested_donor_match(metadata, requested_donor_et_number)
    require_metadata(metadata)

    donor_id_safe = safe_filename(metadata.donor_et_number or requested_donor_et_number)
    file_base = f"ET{donor_id_safe}_donor_typing"
    output_dir = output_dir or default_output_dir_for_run(
        metadata.donor_et_number, output_root, run_timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_page_text_file = output_dir / "raw_donor_page_text.txt"
    normalised_page_text_file = output_dir / "normalised_donor_page_text.txt"
    if save_diagnostics:
        raw_page_text_file.write_text(donor_page_text, encoding="utf-8")
        normalised_page_text_file.write_text(
            normalise_text(donor_page_text), encoding="utf-8"
        )

    metadata_file = output_dir / f"{file_base}_metadata.csv"
    hla_row_candidates_file = output_dir / f"{file_base}_hla_row_candidates.csv"
    selected_hla_rows_file = output_dir / f"{file_base}_selected_hla_rows.csv"
    parsed_hla_file = output_dir / f"{file_base}_parsed_hla_typing.csv"
    gl_string_file = output_dir / f"{file_base}_gl_string.txt"
    hml_file = output_dir / f"{file_base}.hml"

    write_csv(
        metadata_file,
        [metadata],
        (
            "donor_et_number",
            "registration_center",
            "tt_lab",
            "typing_date",
            "donor_registration_date",
        ),
    )
    write_csv(
        hla_row_candidates_file,
        selected_typing.all_candidates,
        (
            "candidate_id",
            "row_label",
            "row_text",
            "expected_allele_count",
            "total_allele_count",
            "character_count",
        ),
    )
    write_csv(
        selected_hla_rows_file,
        selected_typing.selected_candidates,
        (
            "candidate_id",
            "row_label",
            "row_text",
            "expected_allele_count",
            "total_allele_count",
            "character_count",
        ),
    )
    write_csv(
        parsed_hla_file,
        parsed_hla,
        ("row_label", "locus", "allele_group_1", "allele_group_2", "source_row_text"),
    )

    validate_et_locus_constraints(parsed_hla)
    gl_string = make_gl_string(parsed_hla)
    gl_string_file.write_text(gl_string, encoding="utf-8")

    write_hml(
        parsed_hla=parsed_hla,
        metadata=metadata,
        gl_string=gl_string,
        output_file=hml_file,
        imgt_hla_version=imgt_hla_version,
        hml_project_name=hml_project_name,
        collection_method=collection_method,
        include_typing_method_placeholders=include_typing_method_placeholders,
        typing_method_placeholder_type=typing_method_placeholder_type,
        typing_method_test_id_source=typing_method_test_id_source,
    )

    hml_copy_file: Path | None = None
    if copy_to_hml_files:
        hml_files_dir = Path(hml_files_dir) if hml_files_dir is not None else output_root / "hml_files"
        hml_files_dir.mkdir(parents=True, exist_ok=True)
        hml_copy_file = hml_files_dir / f"{hml_file.stem}_{run_timestamp}{hml_file.suffix}"
        shutil.copy2(hml_file, hml_copy_file)

    return ConversionResult(
        donor_et_number=metadata.donor_et_number or requested_donor_et_number,
        registration_center=metadata.registration_center or "",
        tt_lab=metadata.tt_lab or "",
        typing_date=metadata.typing_date or "",
        loci=[record.locus for record in parsed_hla],
        gl_string=gl_string,
        hml_file=hml_file,
        hml_copy_file=hml_copy_file,
        output_dir=output_dir,
        raw_text_file=raw_page_text_file,
        normalised_text_file=normalised_page_text_file,
        metadata_file=metadata_file,
        hla_row_candidates_file=hla_row_candidates_file,
        selected_hla_rows_file=selected_hla_rows_file,
        parsed_hla_file=parsed_hla_file,
        gl_string_file=gl_string_file,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Eurotransplant Donordata webpage text to HLA GL string and HML."
    )
    parser.add_argument("--output-dir", type=Path, help="Output directory.")
    parser.add_argument(
        "--donor-et-number",
        help="Open the Eurotransplant Donordata URL for this ET donor number and read visible webpage text from the controlled browser.",
    )
    parser.add_argument(
        "--web-text-file",
        type=Path,
        help="Use previously saved Eurotransplant donor webpage text. Useful for testing without reopening the website.",
    )
    parser.add_argument("--imgt-hla-version", default="unknown")
    parser.add_argument("--duplicate-single-group-as-homozygous", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--hml-project-name", default="Eurotransplant_Deceased_Donor_HLA")
    parser.add_argument("--collection-method", default="unknown")
    parser.add_argument("--typing-method-placeholder-type", default="sbt-ngs")
    parser.add_argument(
        "--typing-method-test-id-source",
        default="Eurotransplant-Donordata-interpreted",
    )
    parser.add_argument(
        "--no-typing-method-placeholders",
        action="store_true",
        help="Do not include placeholder typing-method children in the HML.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global LAST_DONOR_ET_NUMBER, LAST_DONOR_PAGE_TEXT, LAST_OUTPUT_ROOT

    args = build_arg_parser().parse_args(argv)
    launched_with_dialog = False
    started_from = Path.cwd()
    output_root = started_from
    LAST_OUTPUT_ROOT = output_root

    if args.web_text_file is None and args.donor_et_number is None:
        args.donor_et_number = ask_donor_et_number_with_dialog()
        launched_with_dialog = True
        if args.donor_et_number is None:
            raise SystemExit("No donor ET number selected.")

    if args.donor_et_number is not None:
        args.donor_et_number = args.donor_et_number.strip()
        LAST_DONOR_ET_NUMBER = args.donor_et_number
        if not re.fullmatch(r"\d{3,}", args.donor_et_number):
            raise SystemExit("The ET number must contain digits only.")
    if args.web_text_file is not None and not args.web_text_file.exists():
        raise SystemExit(f"Web text file does not exist: {args.web_text_file}")

    start = dt.datetime.now()
    run_timestamp = start.strftime("%Y%m%d_%H%M%S")
    print(f"Started from: {started_from}")
    print(f"Output root:  {output_root}")

    if args.web_text_file:
        donor_page_text = args.web_text_file.read_text(encoding="utf-8")
        LAST_DONOR_PAGE_TEXT = donor_page_text
    else:
        donor_url = f"{DONOR_DATA_URL_PREFIX}{args.donor_et_number}"
        print(f"Donor URL:    {donor_url}")
        _browser_process, browser_port = launch_controlled_browser(
            donor_url, args.donor_et_number, output_root
        )
        launched_with_dialog = True
        if not wait_for_browser_ready_with_dialog(args.donor_et_number):
            raise SystemExit("Donor webpage was not confirmed as ready.")
        donor_page_text = read_donor_page_text_from_browser(browser_port, args.donor_et_number)
        LAST_DONOR_PAGE_TEXT = donor_page_text
        if empty_to_none(donor_page_text) is None:
            raise SystemExit("No donor webpage text could be read from the browser.")
        if "HLA Typing" not in donor_page_text:
            raise SystemExit(
                "The browser page text does not contain 'HLA Typing'. "
                "Make sure the donor report is loaded before clicking OK."
            )
        if "CIWD Genotype" not in donor_page_text:
            raise SystemExit(
                "The donor page was loaded, but the CIWD Genotype table was not visible after automatic expansion. "
                "Please keep the donor page open and try again."
            )

    selected_typing = try_parse_hla_from_text(
        donor_page_text, args.duplicate_single_group_as_homozygous
    )
    parsed_hla = selected_typing.parsed_hla
    all_candidates = selected_typing.all_candidates
    selected_candidates = selected_typing.selected_candidates

    metadata = extract_metadata(donor_page_text)
    metadata = Metadata(
        donor_et_number=metadata.donor_et_number,
        registration_center=metadata.registration_center,
        tt_lab=first_present(selected_typing.tt_lab, metadata.tt_lab),
        typing_date=first_present(selected_typing.typing_date, metadata.typing_date),
        donor_registration_date=metadata.donor_registration_date,
    )
    require_requested_donor_match(metadata, args.donor_et_number)
    require_metadata(metadata)

    donor_id_safe = safe_filename(metadata.donor_et_number or "unknown")
    file_base = f"ET{donor_id_safe}_donor_typing"
    output_dir = args.output_dir or default_output_dir_for_run(
        metadata.donor_et_number, output_root, run_timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    normalised_page_text = normalise_text(donor_page_text)
    raw_page_text_file = output_dir / "raw_donor_page_text.txt"
    normalised_page_text_file = output_dir / "normalised_donor_page_text.txt"
    raw_page_text_file.write_text(donor_page_text, encoding="utf-8")
    normalised_page_text_file.write_text(
        normalised_page_text, encoding="utf-8"
    )

    print(f"Output dir:   {output_dir}")

    metadata_file = output_dir / f"{file_base}_metadata.csv"
    hla_row_candidates_file = output_dir / f"{file_base}_hla_row_candidates.csv"
    selected_hla_rows_file = output_dir / f"{file_base}_selected_hla_rows.csv"
    parsed_hla_file = output_dir / f"{file_base}_parsed_hla_typing.csv"
    gl_string_file = output_dir / f"{file_base}_gl_string.txt"
    hml_file = output_dir / f"{file_base}.hml"

    write_csv(
        metadata_file,
        [metadata],
        (
            "donor_et_number",
            "registration_center",
            "tt_lab",
            "typing_date",
            "donor_registration_date",
        ),
    )

    write_csv(
        hla_row_candidates_file,
        all_candidates,
        (
            "candidate_id",
            "row_label",
            "row_text",
            "expected_allele_count",
            "total_allele_count",
            "character_count",
        ),
    )
    write_csv(
        selected_hla_rows_file,
        selected_candidates,
        (
            "candidate_id",
            "row_label",
            "row_text",
            "expected_allele_count",
            "total_allele_count",
            "character_count",
        ),
    )
    write_csv(
        parsed_hla_file,
        parsed_hla,
        ("row_label", "locus", "allele_group_1", "allele_group_2", "source_row_text"),
    )

    validate_et_locus_constraints(parsed_hla)
    gl_string = make_gl_string(parsed_hla)
    gl_string_file.write_text(gl_string, encoding="utf-8")

    write_hml(
        parsed_hla=parsed_hla,
        metadata=metadata,
        gl_string=gl_string,
        output_file=hml_file,
        imgt_hla_version=args.imgt_hla_version,
        hml_project_name=args.hml_project_name,
        collection_method=args.collection_method,
        include_typing_method_placeholders=not args.no_typing_method_placeholders,
        typing_method_placeholder_type=args.typing_method_placeholder_type,
        typing_method_test_id_source=args.typing_method_test_id_source,
    )
    hml_files_dir = output_root / "hml_files"
    hml_files_dir.mkdir(parents=True, exist_ok=True)
    hml_copy_file = hml_files_dir / f"{hml_file.stem}_{run_timestamp}{hml_file.suffix}"
    shutil.copy2(hml_file, hml_copy_file)

    elapsed = (dt.datetime.now() - start).total_seconds()
    print("\nMetadata:")
    for key, value in asdict(metadata).items():
        print(f"  {key}: {value}")
    print(f"\nExtracted loci: {', '.join(record.locus for record in parsed_hla)}")
    print(f"\nFinal GL string:\n{gl_string}")
    print("\nOutput files:")
    for path in (
        raw_page_text_file,
        normalised_page_text_file,
        metadata_file,
        hla_row_candidates_file,
        selected_hla_rows_file,
        parsed_hla_file,
        gl_string_file,
        hml_file,
        hml_copy_file,
    ):
        print(f"  {path}")
    print(f"\nTotal runtime: {elapsed:.1f} seconds")

    if launched_with_dialog:
        show_dialog_message(
            "ET donor HML conversion complete",
            (
                "Conversion completed successfully.\n\n"
                f"HML file:\n{hml_file}\n\n"
                f"HML copy:\n{hml_copy_file}\n\n"
                f"Output folder:\n{output_dir}"
            ),
        )
    return 0


def gui_main() -> int:
    try:
        from PySide6.QtCore import QPointF, QSettings, QTimer, QUrl, Qt
        from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
        from PySide6.QtWidgets import (
            QApplication,
            QAbstractItemView,
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QFormLayout,
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QPlainTextEdit,
            QSizePolicy,
            QSplitter,
            QStyle,
            QTableWidget,
            QTableWidgetItem,
            QToolBar,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        script_path = Path(__file__).resolve()
        candidate_venvs = [
            script_path.parent.parent / "work" / "donor_exe_venv" / "Scripts" / "python.exe",
            Path.cwd() / "work" / "donor_exe_venv" / "Scripts" / "python.exe",
        ]
        current_python = Path(sys.executable).resolve()
        for candidate in candidate_venvs:
            if candidate.exists() and candidate.resolve() != current_python:
                subprocess.Popen([str(candidate), str(script_path), *sys.argv[1:]], cwd=str(script_path.parent.parent))
                return 0
        message = (
            "PySide6 is required for the lightweight GUI.\n\n"
            "The script is valid, but this Python environment does not have PySide6 installed yet.\n\n"
            "Tomorrow/next step:\n"
            "  work\\donor_exe_venv\\Scripts\\python.exe -m pip install PySide6\n\n"
            f"Technical detail:\n{exc}"
        )
        print(message, file=sys.stderr)
        show_powershell_message("Donor Typing to HML - missing GUI dependency", message, error=True)
        return 2

    PAGE_STATE_JS = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  return {
    href: location.href,
    title: document.title,
    hasDonorReport: /Donor Report/i.test(text),
    hasHlaTyping: /HLA Typing/i.test(text),
    hasTypingCard: /Full Phenotype|TT Lab|Entry Date|Used In Match|Typing Purpose/i.test(text),
    hasCiwD: /CIWD Genotype/i.test(text),
    text: text
  };
})()
"""

    SOURCE_DEBUG_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const alleleRe = /\b(?:A|B|C|DRB1|DRB3|DRB4|DRB5|DRB345|DQA1|DQB1|DPA1|DPB1)\*\d{2,3}(?::\d{2,3})?/i;
  const rowLabelRe = /^(?:A|B|C|DRB1|DRB345|DQB1|DQA1|DPB1|DPA1|Publics?)\b/i;
  const deepTexts = [];
  const walkText = root => {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node = root.nodeType === Node.ELEMENT_NODE ? root : walker.nextNode();
    while (node) {
      if (node.shadowRoot) walkText(node.shadowRoot);
      const own = clean(node.innerText || node.textContent || '');
      if (own && alleleRe.test(own) && own.length < 1500) deepTexts.push(own);
      node = walker.nextNode();
    }
  };
  try { walkText(document.body); } catch (_) {}
  const rowSelectors = [
    'tr',
    '[role="row"]',
    '.mat-row',
    '.mat-mdc-row',
    '.cdk-row',
    'mat-row',
    'tbody > *',
    '.ng-star-inserted'
  ];
  const hlaRows = [];
  const seenRows = new Set();
  const addRowText = value => {
    value = clean(value);
    if (!value || seenRows.has(value) || !alleleRe.test(value)) return;
    if (!rowLabelRe.test(value)) {
      const match = value.match(/\b(A|B|C|DRB1|DRB345|DQB1|DQA1|DPB1|DPA1)\b\s+/i);
      if (!match) return;
      value = value.slice(match.index);
    }
    seenRows.add(value);
    hlaRows.push(value);
  };
  for (const selector of rowSelectors) {
    for (const el of Array.from(document.querySelectorAll(selector))) {
      const cellTexts = Array.from(el.querySelectorAll('th,td,[role="cell"],.mat-cell,.mat-mdc-cell,.cdk-cell'))
        .map(cell => clean(cell.innerText || cell.textContent || ''))
        .filter(Boolean);
      addRowText(cellTexts.length ? cellTexts.join(' ') : (el.innerText || el.textContent || ''));
    }
  }
  for (const value of deepTexts) addRowText(value);
  const resources = performance.getEntriesByType('resource').map(entry => ({
    name: entry.name,
    initiatorType: entry.initiatorType || '',
    duration: entry.duration || 0
  }));
  return {
    href: location.href,
    title: document.title,
    bodyInnerText: document.body ? document.body.innerText : '',
    documentOuterText: document.documentElement ? document.documentElement.outerText : '',
    documentOuterHTML: document.documentElement ? document.documentElement.outerHTML : '',
    hlaRowsText: hlaRows.join('\n'),
    hlaElementTexts: deepTexts.slice(0, 300),
    localStorageKeys: (() => {
      try { return Object.keys(localStorage || {}); } catch (_) { return []; }
    })(),
    sessionStorageKeys: (() => {
      try { return Object.keys(sessionStorage || {}); } catch (_) { return []; }
    })(),
    resources: resources
  };
})()
"""

    EXPAND_HLA_JS = r"""
(async () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const text = el => (el && el.innerText ? el.innerText : '').replace(/\s+/g, ' ').trim();
  const bodyText = () => document.body ? document.body.innerText : '';
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const mouseClick = el => {
    if (!visible(el)) return false;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
    }
    return true;
  };
  const clickTargetsFor = el => {
    const targets = [
      el.closest('button, [role="button"], [aria-expanded], .mat-expansion-panel-header, mat-expansion-panel-header'),
      el,
      el.parentElement,
      el.closest('tr, .mat-row, .mat-mdc-row, .cdk-row'),
    ].filter(Boolean);
    return Array.from(new Set(targets));
  };
  const clickIfUseful = (el) => {
    if (!el) return false;
    if ((el.getAttribute('aria-expanded') || '').toLowerCase() === 'true') return false;
    if (!visible(el)) return false;
    const local = [
      el.innerText || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.getAttribute('aria-expanded') || '',
      text(el.closest('section, article, .card, .accordion, .panel, div'))
    ].join(' ');
    if (!/HLA Typing|CIWD Genotype|Full Phenotype|TT Lab/i.test(local)) return false;
    let clicked = false;
    for (const target of clickTargetsFor(el)) clicked = mouseClick(target) || clicked;
    return clicked;
  };
  const clickHlaExpandIcons = async () => {
    const hlaHeading = Array.from(document.querySelectorAll('body *'))
      .find(el => /^HLA Typing$/i.test(text(el)));
    const reportHeading = Array.from(document.querySelectorAll('body *'))
      .find(el => /^Donor Report$/i.test(text(el)));
    const hlaY = hlaHeading
      ? hlaHeading.getBoundingClientRect().top
      : (reportHeading ? reportHeading.getBoundingClientRect().top : 0);
    const bottomMarker = Array.from(document.querySelectorAll('body *'))
      .filter(el => /^(Current Virology|Organs Reported|Donor Information)$/i.test(text(el)))
      .map(el => el.getBoundingClientRect().top)
      .filter(y => y > hlaY)
      .sort((a, b) => a - b)[0];
    const maxY = Number.isFinite(bottomMarker) ? bottomMarker : hlaY + Math.max(700, window.innerHeight * 0.9);
    const candidates = Array.from(document.querySelectorAll(
      'mat-icon, .mat-icon, [class*="expand"], [aria-expanded="false"], button, [role="button"], .mat-expansion-panel-header, mat-expansion-panel-header'
    ))
      .filter(el => {
        if (!visible(el)) return false;
        const rect = el.getBoundingClientRect();
        if (rect.top < hlaY - 30 || rect.top > maxY + 30) return false;
        const label = [
          text(el),
          el.getAttribute('aria-label') || '',
          el.getAttribute('title') || '',
          el.getAttribute('aria-expanded') || '',
          text(el.closest('tr, .mat-row, .mat-mdc-row, .cdk-row, mat-expansion-panel, .mat-expansion-panel'))
        ].join(' ');
        return /expand_more|keyboard_arrow_down|HLA Typing|Full Phenotype|TT Lab|Entry Date|Typing Purpose|Used In Match/i.test(label);
      })
      .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    let clicked = 0;
    for (const el of candidates) {
      if ((el.getAttribute('aria-expanded') || '').toLowerCase() === 'true') continue;
      let didClick = false;
      for (const target of clickTargetsFor(el)) didClick = mouseClick(target) || didClick;
      if (didClick) {
        clicked += 1;
        await sleep(1000);
        if (/CIWD Genotype/i.test(bodyText())) break;
      }
    }
    return clicked;
  };
  for (let round = 0; round < 4 && !/CIWD Genotype/i.test(bodyText()); round++) {
    const explicit = Array.from(document.querySelectorAll(
      '[aria-expanded="false"], button, [role="button"], .accordion-button, .mat-expansion-panel-header, mat-expansion-panel-header'
    ));
    for (const el of explicit) {
      if (clickIfUseful(el)) await sleep(900);
      if (/CIWD Genotype/i.test(bodyText())) break;
    }
    if (/CIWD Genotype/i.test(bodyText())) break;
    const clicks = await clickHlaExpandIcons();
    if (!clicks) await sleep(500);
  }
  return bodyText();
})()
"""

    LOGIN_STATE_JS = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const href = location.href || '';
  const appShellLike = /Donordata|Search Donor|Select Report|Go to Section|Extended Allocation/i.test(text);
  const donorPageLike = /Donor Report|Donor:\s+Registration Date|HLA Typing|Current Virology|Organs Reported/i.test(text);
  const loginPageLike = /login|signin|sign-in|authenticate|oauth|keycloak|sso|adfs/i.test(href);
  const loginFormLike = Boolean(
    document.querySelector('input[type="password"], input[name*="password" i], input[id*="password" i]')
  ) || /username|password|one-time|verification code|authenticator|two.factor|2fa/i.test(text);
  const loggedIn = donorPageLike || appShellLike || (/donor-data\.etnext\.eu\/donor\/\d+/i.test(href) && !loginPageLike && !loginFormLike);
  const usernameSelectors = [
    '[data-testid*="user" i]', '[class*="user" i]', '[class*="account" i]',
    '[aria-label*="user" i]', '[aria-label*="account" i]', 'button[title]', '[title*="@"]'
  ];
  let username = '';
  for (const selector of usernameSelectors) {
    for (const el of Array.from(document.querySelectorAll(selector))) {
      const candidate = (el.innerText || el.getAttribute('title') || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
      if (candidate && candidate.length <= 80 && !/search|menu|section|report/i.test(candidate)) {
        username = candidate;
        break;
      }
    }
    if (username) break;
  }
  return {loggedIn, username, href, title: document.title, appShellLike, donorPageLike, loginPageLike, loginFormLike};
})()
"""

    def make_double_play_icon(color: str) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor(color), 2))
        painter.drawPolygon(QPolygonF([QPointF(6, 6), QPointF(6, 26), QPointF(20, 16)]))
        painter.drawPolygon(QPolygonF([QPointF(15, 6), QPointF(15, 26), QPointF(29, 16)]))
        painter.end()
        return QIcon(pixmap)

    def make_play_icon(color: str) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor(color), 2))
        painter.drawPolygon(QPolygonF([QPointF(9, 6), QPointF(9, 26), QPointF(25, 16)]))
        painter.end()
        return QIcon(pixmap)

    def make_stop_icon(color: str) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor(color), 2))
        painter.drawRoundedRect(8, 8, 16, 16, 3, 3)
        painter.end()
        return QIcon(pixmap)

    def make_sliders_icon(color: str) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(8, 6, 8, 26)
        painter.drawLine(16, 6, 16, 26)
        painter.drawLine(24, 6, 24, 26)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(8, 12), 4, 4)
        painter.drawEllipse(QPointF(16, 20), 4, 4)
        painter.drawEllipse(QPointF(24, 14), 4, 4)
        painter.end()
        return QIcon(pixmap)

    def app_icon_path() -> Path | None:
        base_dirs = [
            Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)),
            Path(__file__).resolve().parent,
            Path.cwd() / "outputs",
        ]
        for base_dir in base_dirs:
            candidate = base_dir / "Donor_Typing_To_hml_icon.ico"
            if candidate.exists():
                return candidate
        return None

    class DonorTypingMainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Donor Typing to HML")
            icon_path = app_icon_path()
            if icon_path is not None:
                self.setWindowIcon(QIcon(str(icon_path)))
            self.resize(1500, 900)
            self.settings = QSettings("UMC Utrecht", "Donor Typing to HML")
            self.output_root = Path(str(self.settings.value("output_root", str(Path.cwd()))))
            self.queue: list[dict[str, object]] = []
            self.current_row: int | None = None
            self.running = False
            self.poll_count = 0
            self.max_polls = 15
            self.expand_attempt_count = 0
            self.max_expand_attempts = 3
            self.run_rows: set[int] | None = None
            self.logged_in = False
            self.logged_in_username = ""
            self.hml_files_dir = Path(str(self.settings.value("hml_files_dir", str(self.output_root / "hml_files"))))
            self.browser_process: subprocess.Popen | None = None
            self.browser_port: int | None = None
            self.current_donor_url = ""

            self._build_ui()
            self._apply_style()
            self.update_login_controls()
            self.log("Ready. Version 3 uses an external browser; no webpages are embedded in this program.")

        def _build_ui(self) -> None:
            toolbar = QToolBar("Workflow")
            toolbar.setMovable(False)
            self.addToolBar(toolbar)

            title = QLabel("Donor Typing to HML")
            title.setObjectName("AppTitle")
            toolbar.addWidget(title)
            toolbar.addSeparator()

            toolbar.addWidget(QLabel("Output folder"))
            self.output_folder_edit = QLineEdit(str(self.output_root))
            self.output_folder_edit.setMinimumWidth(360)
            toolbar.addWidget(self.output_folder_edit)

            browse_action = QAction(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
                "Browse",
                self,
            )
            browse_action.triggered.connect(self.browse_output_folder)
            toolbar.addAction(browse_action)
            toolbar.addSeparator()

            self.add_action = QAction(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
                "Add ET",
                self,
            )
            self.add_action.triggered.connect(self.import_et_numbers_from_csv)
            toolbar.addAction(self.add_action)

            self.run_selected_action = QAction(
                make_play_icon("#00843D"),
                "Run selected",
                self,
            )
            self.run_selected_action.triggered.connect(self.run_selected)
            toolbar.addAction(self.run_selected_action)
            toolbar.widgetForAction(self.run_selected_action).setProperty("toolRole", "run")

            self.run_all_action = QAction(make_double_play_icon("#00843D"), "Run all", self)
            self.run_all_action.triggered.connect(self.run_all)
            toolbar.addAction(self.run_all_action)
            toolbar.widgetForAction(self.run_all_action).setProperty("toolRole", "run")

            self.stop_action = QAction(
                make_stop_icon("#C62828"),
                "Stop",
                self,
            )
            self.stop_action.triggered.connect(self.stop_run)
            toolbar.addAction(self.stop_action)
            toolbar.widgetForAction(self.stop_action).setProperty("toolRole", "stop")

            self.settings_action = QAction(make_sliders_icon("#006EB6"), "Settings", self)
            self.settings_action.triggered.connect(self.show_settings)
            toolbar.addAction(self.settings_action)
            toolbar.widgetForAction(self.settings_action).setProperty("toolRole", "settings")

            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QVBoxLayout(central)
            main_layout.setContentsMargins(10, 8, 10, 10)
            main_layout.setSpacing(8)

            top_splitter = QSplitter(Qt.Orientation.Horizontal)
            bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
            vertical_splitter = QSplitter(Qt.Orientation.Vertical)
            vertical_splitter.addWidget(top_splitter)
            vertical_splitter.addWidget(bottom_splitter)
            vertical_splitter.setSizes([650, 220])
            main_layout.addWidget(vertical_splitter)

            top_splitter.addWidget(self._build_queue_panel())
            top_splitter.addWidget(self._build_browser_panel())
            top_splitter.addWidget(self._build_details_panel())
            top_splitter.setSizes([320, 820, 360])

            bottom_splitter.addWidget(self._build_files_panel())
            bottom_splitter.addWidget(self._build_log_panel())
            bottom_splitter.setSizes([520, 980])

        def _build_queue_panel(self) -> QWidget:
            panel = QFrame()
            panel.setObjectName("Panel")
            layout = QVBoxLayout(panel)
            title = QLabel("Donor Queue")
            title.setObjectName("SectionTitle")
            layout.addWidget(title)

            self.single_et_edit = QLineEdit()
            self.single_et_edit.setPlaceholderText("Enter ET number")
            self.batch_et_edit = QPlainTextEdit()
            self.batch_et_edit.setPlaceholderText("Paste multiple ET numbers, one per line")
            self.batch_et_edit.setMaximumHeight(82)

            add_button = QPushButton("Add")
            add_button.clicked.connect(self.add_donors_from_inputs)
            clear_button = QPushButton("Clear")
            clear_button.clicked.connect(self.clear_queue)
            input_row = QHBoxLayout()
            input_row.addWidget(add_button)
            input_row.addWidget(clear_button)

            layout.addWidget(self.single_et_edit)
            layout.addWidget(self.batch_et_edit)
            layout.addLayout(input_row)

            self.queue_table = QTableWidget(0, 5)
            self.queue_table.setHorizontalHeaderLabels(["Run", "ET number", "Status", "TT Lab", "Message"])
            self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.queue_table.itemChanged.connect(self.on_queue_item_changed)
            self.queue_table.itemSelectionChanged.connect(self.on_queue_selection_changed)
            layout.addWidget(self.queue_table)
            return panel

        def _build_browser_panel(self) -> QWidget:
            panel = QFrame()
            panel.setObjectName("Panel")
            layout = QVBoxLayout(panel)
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0%")
            self.progress_bar.setTextVisible(True)
            layout.addWidget(self.progress_bar)
            title = QLabel("External Browser Mode")
            title.setObjectName("SectionTitle")
            layout.addWidget(title)
            self.external_browser_status = QLabel(
                "Donordata opens in Edge/Chrome for login and navigation. "
                "This app reads the authenticated page in the background and does not display webpages."
            )
            self.external_browser_status.setWordWrap(True)
            layout.addWidget(self.external_browser_status)
            layout.addStretch(1)
            return panel

        def _build_details_panel(self) -> QWidget:
            panel = QFrame()
            panel.setObjectName("Panel")
            layout = QVBoxLayout(panel)
            title = QLabel("Selected Donor")
            title.setObjectName("SectionTitle")
            layout.addWidget(title)

            form_box = QGroupBox("Metadata")
            form = QFormLayout(form_box)
            self.et_value = QLabel("-")
            self.center_value = QLabel("-")
            self.tt_lab_value = QLabel("-")
            self.typing_date_value = QLabel("-")
            self.loci_value = QLabel("-")
            for label, widget in (
                ("ET number", self.et_value),
                ("Registration center", self.center_value),
                ("TT Lab", self.tt_lab_value),
                ("Typing date", self.typing_date_value),
                ("Loci", self.loci_value),
            ):
                form.addRow(label, widget)
            layout.addWidget(form_box)

            gl_title = QLabel("GL String")
            gl_title.setObjectName("SubsectionTitle")
            layout.addWidget(gl_title)
            self.gl_text = QPlainTextEdit()
            self.gl_text.setReadOnly(True)
            self.gl_text.setPlaceholderText("GL string will appear here after conversion")
            layout.addWidget(self.gl_text, 1)

            button_row = QHBoxLayout()
            copy_button = QPushButton("Copy GL string")
            copy_button.clicked.connect(self.copy_gl_string)
            open_hml_button = QPushButton("Open HML")
            open_hml_button.clicked.connect(self.open_selected_hml)
            open_folder_button = QPushButton("Open output folder")
            open_folder_button.clicked.connect(self.open_selected_output_folder)
            button_row.addWidget(copy_button)
            button_row.addWidget(open_hml_button)
            button_row.addWidget(open_folder_button)
            layout.addLayout(button_row)
            return panel

        def _build_files_panel(self) -> QWidget:
            panel = QFrame()
            panel.setObjectName("Panel")
            layout = QVBoxLayout(panel)
            header = QHBoxLayout()
            title = QLabel("Output Files")
            title.setObjectName("SectionTitle")
            header.addWidget(title)
            header.addStretch(1)
            open_hml_folder_button = QPushButton(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
                "Open HML folder",
            )
            open_hml_folder_button.clicked.connect(self.open_hml_files_folder)
            header.addWidget(open_hml_folder_button)
            layout.addLayout(header)
            self.files_list = QListWidget()
            self.files_list.itemDoubleClicked.connect(lambda item: self.open_path(Path(item.text())))
            layout.addWidget(self.files_list)
            return panel

        def _build_log_panel(self) -> QWidget:
            panel = QFrame()
            panel.setObjectName("Panel")
            layout = QVBoxLayout(panel)
            title = QLabel("Activity Log")
            title.setObjectName("SectionTitle")
            layout.addWidget(title)
            self.log_text = QPlainTextEdit()
            self.log_text.setReadOnly(True)
            layout.addWidget(self.log_text)
            return panel

        def _build_browser(self) -> None:
            self.set_progress_label("Ready - external browser", 0)

        def _apply_style(self) -> None:
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #f4f8fb; color: #1f2933; font-family: Segoe UI; font-size: 9pt; }
                QToolBar { background: #ffffff; border-bottom: 2px solid #006EB6; spacing: 6px; padding: 5px; }
                QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 4px 8px; background: #ffffff; }
                QToolButton:hover { background: #E8F4FB; border-color: #79BDE3; }
                QToolButton[toolRole="run"] { color: #00843D; font-weight: 650; }
                QToolButton[toolRole="run"]:disabled { color: #8a969e; background: #f1f5f9; }
                QToolButton[toolRole="stop"] { color: #C62828; font-weight: 650; }
                QToolButton[toolRole="stop"]:disabled { color: #8a969e; background: #f1f5f9; }
                QToolButton[toolRole="settings"] { color: #006EB6; font-weight: 650; }
                #AppTitle { font-size: 15pt; font-weight: 650; color: #006EB6; padding-right: 12px; }
                #Panel { background: #ffffff; border: 1px solid #c9dbe7; border-radius: 6px; }
                #SectionTitle { font-size: 12pt; font-weight: 650; color: #006EB6; padding: 2px 0 6px 0; }
                #SubsectionTitle { font-weight: 650; color: #34495e; }
                QProgressBar {
                    background: #eef4fa; border: 1px solid #b8c5d3; border-radius: 4px;
                    height: 18px; text-align: center; color: #1f2933;
                }
                QProgressBar::chunk { background: #006EB6; border-radius: 3px; }
                QLineEdit, QPlainTextEdit, QListWidget, QTableWidget {
                    background: #ffffff; border: 1px solid #cfd8e3; border-radius: 4px; padding: 4px;
                }
                QTableWidget { gridline-color: #e5ebf2; selection-background-color: #dbeafe; selection-color: #102a43; }
                QHeaderView::section { background: #E8F4FB; border: 0; border-right: 1px solid #d9e1ea; padding: 5px; font-weight: 600; color: #004B7A; }
                QPushButton { background: #ffffff; border: 1px solid #b8c5d3; border-radius: 4px; padding: 5px 9px; }
                QPushButton:hover { background: #f1f7ff; border-color: #6aa6d8; }
                QPushButton:pressed { background: #dbeafe; }
                QGroupBox { border: 1px solid #d9e1ea; border-radius: 5px; margin-top: 12px; padding: 8px; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #475569; }
                """
            )
            self.gl_text.setStyleSheet("font-family: Consolas; font-size: 9pt;")
            self.log_text.setStyleSheet("font-family: Consolas; font-size: 9pt;")

        def browse_output_folder(self) -> None:
            folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_folder_edit.text())
            if folder:
                self.output_root = Path(folder)
                self.output_folder_edit.setText(folder)
                self.hml_files_dir = self.output_root / "hml_files"
                self.settings.setValue("output_root", str(self.output_root))
                self.settings.setValue("hml_files_dir", str(self.hml_files_dir))
                self.log(f"Output folder set to {folder}")

        def add_donor_candidates(self, candidates: list[str], source_label: str = "input") -> int:
            added = 0
            known = {str(item["et"]) for item in self.queue}
            for raw in candidates:
                et = raw.strip()
                if not et:
                    continue
                if not re.fullmatch(r"\d{3,}", et):
                    self.log(f"Skipped invalid ET number: {et}")
                    continue
                if et in known:
                    continue
                self.queue.append({"run": True, "et": et, "status": "Waiting", "tt_lab": "", "message": "", "result": None})
                known.add(et)
                added += 1
            self.refresh_queue_table()
            if added:
                self.log(f"Added {added} donor(s) from {source_label}.")
            return added

        def add_donors_from_inputs(self) -> None:
            candidates = [self.single_et_edit.text()]
            candidates.extend(self.batch_et_edit.toPlainText().splitlines())
            added = self.add_donor_candidates(candidates, "manual input")
            self.single_et_edit.clear()
            self.batch_et_edit.clear()

        def import_et_numbers_from_csv(self) -> None:
            file_name, _selected_filter = QFileDialog.getOpenFileName(
                self,
                "Open ET number CSV",
                str(self.output_root),
                "CSV files (*.csv);;Text files (*.txt);;All files (*.*)",
            )
            if not file_name:
                return
            path = Path(file_name)
            candidates: list[str] = []
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    sample = handle.read(4096)
                    handle.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                    except csv.Error:
                        dialect = csv.excel
                    reader = csv.reader(handle, dialect)
                    for row in reader:
                        if row:
                            candidates.append(str(row[0]))
            except UnicodeDecodeError:
                with path.open("r", encoding="cp1252", newline="") as handle:
                    reader = csv.reader(handle)
                    for row in reader:
                        if row:
                            candidates.append(str(row[0]))
            except Exception as exc:  # noqa: BLE001 - surface file import failures in GUI
                QMessageBox.warning(self, "Could not import CSV", str(exc))
                return
            added = self.add_donor_candidates(candidates, path.name)
            if added == 0:
                QMessageBox.information(
                    self,
                    "No ET numbers added",
                    "No new ET numbers were found in the first column of the selected file.",
                )

        def clear_queue(self) -> None:
            if self.running:
                QMessageBox.warning(self, "Run active", "Stop the current run before clearing the queue.")
                return
            self.queue.clear()
            self.run_rows = None
            self.current_row = None
            self.refresh_queue_table()
            self.set_progress_label("Ready", 0)
            self.show_result(None)
            self.files_list.clear()
            self.log("Queue cleared.")

        def refresh_queue_table(self) -> None:
            self.queue_table.blockSignals(True)
            self.queue_table.setRowCount(len(self.queue))
            for row, item in enumerate(self.queue):
                status = str(item.get("status", ""))
                row_background = None
                row_font = QFont()
                if status == "Done":
                    row_background = QColor("#DFF3E5")
                    row_font.setBold(True)
                elif status == "Failed":
                    row_background = QColor("#FDE2E2")
                    row_font.setItalic(True)

                run_item = QTableWidgetItem("")
                run_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                run_item.setCheckState(
                    Qt.CheckState.Checked if bool(item.get("run", True)) else Qt.CheckState.Unchecked
                )
                run_item.setFont(row_font)
                if row_background is not None:
                    run_item.setBackground(row_background)
                self.queue_table.setItem(row, 0, run_item)

                for col, key in enumerate(("et", "status", "tt_lab", "message"), start=1):
                    table_item = QTableWidgetItem(str(item.get(key, "")))
                    table_item.setFont(row_font)
                    if row_background is not None:
                        table_item.setBackground(row_background)
                    if key == "status":
                        if status == "Done":
                            table_item.setForeground(Qt.GlobalColor.darkGreen)
                        elif status == "Failed":
                            table_item.setForeground(Qt.GlobalColor.red)
                        elif status not in {"Waiting", ""}:
                            table_item.setForeground(Qt.GlobalColor.darkBlue)
                    self.queue_table.setItem(row, col, table_item)
            self.queue_table.blockSignals(False)

        def set_row_status(self, row: int, status: str, message: str = "", tt_lab: str | None = None) -> None:
            if row < 0 or row >= len(self.queue):
                return
            self.queue[row]["status"] = status
            self.queue[row]["message"] = message
            if tt_lab is not None:
                self.queue[row]["tt_lab"] = tt_lab
            self.refresh_queue_table()
            self.update_progress(status)

        def on_queue_item_changed(self, item: QTableWidgetItem) -> None:
            if item.column() != 0:
                return
            row = item.row()
            if 0 <= row < len(self.queue):
                self.queue[row]["run"] = item.checkState() == Qt.CheckState.Checked

        def checked_run_rows(self) -> list[int]:
            rows: list[int] = []
            for row, item in enumerate(self.queue):
                table_item = self.queue_table.item(row, 0)
                checked = table_item.checkState() == Qt.CheckState.Checked if table_item else bool(item.get("run", True))
                self.queue[row]["run"] = checked
                if checked:
                    rows.append(row)
            return rows

        def selected_row_indices(self) -> list[int]:
            return sorted({index.row() for index in self.queue_table.selectionModel().selectedRows()})

        def set_progress_label(self, label: str, value: int | None = None) -> None:
            if value is not None:
                self.progress_bar.setValue(max(0, min(100, value)))
            self.progress_bar.setFormat(f"{self.progress_bar.value()}% - {label}")

        def update_progress(self, label: str = "") -> None:
            if not self.run_rows:
                self.set_progress_label(label or "Ready", 0)
                return
            finished = sum(
                1
                for row in self.run_rows
                if 0 <= row < len(self.queue) and str(self.queue[row].get("status")) in {"Done", "Failed"}
            )
            percent = round((finished / len(self.run_rows)) * 100)
            self.set_progress_label(label or "Running", percent)

        def run_selected(self) -> None:
            if not self.queue:
                self.add_donors_from_inputs()
            rows = self.checked_run_rows()
            if not rows:
                rows = self.selected_row_indices()
            if not rows:
                QMessageBox.information(self, "No donor selected", "Tick one or more donor rows first.")
                return
            self.start_run(rows)

        def run_all(self) -> None:
            if not self.queue:
                self.add_donors_from_inputs()
            self.start_run(list(range(len(self.queue))))

        def start_run(self, rows: list[int]) -> None:
            if self.running:
                return
            self.check_login_state()
            if not self.queue:
                self.add_donors_from_inputs()
            if not self.queue:
                QMessageBox.information(self, "No donors", "Add at least one ET number first.")
                return
            rows = sorted({row for row in rows if 0 <= row < len(self.queue)})
            if not rows:
                QMessageBox.information(self, "No donors selected", "Tick one or more donor rows first.")
                return
            self.output_root = Path(self.output_folder_edit.text()).expanduser()
            self.output_root.mkdir(parents=True, exist_ok=True)
            self.run_rows = set(rows)
            self.running = True
            self.current_row = min(rows)
            self.update_login_controls()
            self.set_progress_label("Starting", 0)
            self.process_current_row()

        def stop_run(self) -> None:
            self.running = False
            self.set_progress_label("Stopped")
            self.update_login_controls()
            self.log("Run stopped by user.")

        def process_current_row(self) -> None:
            if not self.running or self.current_row is None:
                return
            while (
                self.current_row < len(self.queue)
                and (
                    self.current_row not in (self.run_rows or set())
                    or self.queue[self.current_row]["status"] == "Done"
                )
            ):
                self.current_row += 1
            if self.current_row >= len(self.queue):
                self.running = False
                self.set_progress_label("Complete", 100)
                self.update_login_controls()
                self.log("Queue complete.")
                return
            et = str(self.queue[self.current_row]["et"])
            self.queue_table.selectRow(self.current_row)
            self.poll_count = 0
            self.expand_attempt_count = 0
            self.set_row_status(self.current_row, "Loading donor page", "Navigating")
            self.log(f"Loading donor {et}.")
            self.external_browser_status.setText(f"Reading donor {et} in the background.")
            try:
                self.browser_port = self.get_existing_browser_port()
                if self.browser_port is None:
                    raise ValueError("No active Donordata browser session. Use Settings > Login first.")
                self.current_donor_url = f"{DONOR_DATA_URL_PREFIX}{et}"
            except Exception as exc:  # noqa: BLE001 - surface browser/session failures in GUI
                self.set_row_status(self.current_row, "Failed", str(exc))
                self.write_gui_failure(et, "", str(exc))
                self.log(f"Donor {et} failed: {exc}")
                self.advance_queue()
                return
            self.poll_count = 0
            QTimer.singleShot(2500, self.read_current_external_donor_page)

        def get_existing_browser_port(self) -> int | None:
            profile_dir = get_browser_profile_dir(Path(self.output_folder_edit.text()).expanduser())
            port = read_saved_devtools_port(profile_dir) or discover_existing_devtools_port(profile_dir)
            if port is not None:
                save_devtools_port(profile_dir, port)
            return port

        def read_external_donor_page_text(self, et: str) -> str:
            if self.browser_port is None:
                raise ValueError("External browser DevTools session is not available.")
            websocket_url = find_donor_page_websocket_url(self.browser_port, et)
            sock = websocket_connect(websocket_url)
            try:
                devtools_command(sock, 200, "Page.enable")
                devtools_command(sock, 201, "Runtime.enable")
                donor_url = self.current_donor_url or f"{DONOR_DATA_URL_PREFIX}{et}"
                background_expression = f"""
(async () => {{
  const donorUrl = {json.dumps(donor_url)};
  const donorEt = {json.dumps(et)};
  const expandExpression = {json.dumps(EXPAND_HLA_JS)};
  const sourceExpression = {json.dumps(SOURCE_DEBUG_JS)};
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let frame = document.getElementById('__donor_hml_background_frame__');
  if (!frame) {{
    frame = document.createElement('iframe');
    frame.id = '__donor_hml_background_frame__';
    frame.setAttribute('aria-hidden', 'true');
    frame.style.position = 'fixed';
    frame.style.left = '-10000px';
    frame.style.top = '-10000px';
    frame.style.width = '1200px';
    frame.style.height = '900px';
    frame.style.opacity = '0';
    frame.style.pointerEvents = 'none';
    document.body.appendChild(frame);
  }}
  if (!frame.src || frame.src.indexOf('/donor/' + donorEt) === -1) {{
    frame.src = donorUrl;
  }}
  const readFrameText = () => {{
    try {{
      const doc = frame.contentDocument || (frame.contentWindow && frame.contentWindow.document);
      return doc && doc.body ? doc.body.innerText || '' : '';
    }} catch (_) {{
      return '';
    }}
  }};
  let text = '';
  for (let round = 0; round < 30; round++) {{
    text = readFrameText();
    if (text.includes(donorEt) && /Donor Report|HLA Typing|Full Phenotype/i.test(text)) break;
    await sleep(500);
  }}
  let expandedText = text;
  let debugData = null;
  try {{
    expandedText = await frame.contentWindow.eval(expandExpression);
  }} catch (error) {{
    expandedText = text || String(error && error.message || error || '');
  }}
  await sleep(500);
  try {{
    debugData = frame.contentWindow.eval(sourceExpression);
  }} catch (_) {{
    debugData = null;
  }}
  return {{
    href: (() => {{ try {{ return frame.contentWindow.location.href; }} catch (_) {{ return ''; }} }})(),
    visibleText: readFrameText(),
    expandedText: expandedText || '',
    debugData: debugData
  }};
}})()
"""
                frame_data = devtools_evaluate(sock, 202, background_expression, await_promise=True)
                if not isinstance(frame_data, dict):
                    frame_data = {"expandedText": str(frame_data or ""), "visibleText": str(frame_data or "")}
                expanded_text = str(frame_data.get("expandedText") or "")
                if has_expanded_hla_genotype_text(expanded_text):
                    return expanded_text
                visible_text = str(frame_data.get("visibleText") or "")
                debug_data = frame_data.get("debugData")
                candidates: list[str] = [expanded_text, visible_text]
                if isinstance(debug_data, dict):
                    hla_rows_text = str(debug_data.get("hlaRowsText") or "")
                    body_text = str(debug_data.get("bodyInnerText") or "")
                    outer_text = str(debug_data.get("documentOuterText") or "")
                    element_texts = "\n".join(str(value) for value in debug_data.get("hlaElementTexts") or [])
                    candidates.extend(
                        [
                            f"{visible_text}\n{hla_rows_text}",
                            f"{body_text}\n{hla_rows_text}",
                            f"{outer_text}\n{hla_rows_text}",
                            f"{visible_text}\n{element_texts}",
                            body_text,
                            outer_text,
                        ]
                    )
                for candidate in candidates:
                    if has_expanded_hla_genotype_text(candidate):
                        return candidate
                return visible_text or expanded_text
            finally:
                sock.close()

        def read_current_external_donor_page(self) -> None:
            if not self.running or self.current_row is None:
                return
            et = str(self.queue[self.current_row]["et"])
            if self.browser_port is None:
                self.set_row_status(self.current_row, "Failed", "External browser is not available")
                self.advance_queue()
                return
            self.poll_count += 1
            try:
                page_text = self.read_external_donor_page_text(et)
            except Exception as exc:  # noqa: BLE001 - page may still be loading/login pending
                if self.poll_count >= self.max_polls:
                    message = f"Timed out reading donor record after 30 seconds: {exc}"
                    self.set_row_status(self.current_row, "Failed", message)
                    self.write_gui_failure(et, "", message)
                    self.log(f"Donor {et} failed: {exc}")
                    self.advance_queue()
                    return
                self.set_row_status(self.current_row, "Waiting", "Waiting for external browser")
                if self.poll_count in {1, 5, 15, 30}:
                    self.log(f"Waiting for donor {et} in external browser: {exc}")
                QTimer.singleShot(2000, self.read_current_external_donor_page)
                return

            has_requested = et in page_text
            has_report = bool(re.search(r"Donor Report", page_text, re.I))
            has_hla = bool(re.search(r"HLA Typing", page_text, re.I))
            has_expanded = has_expanded_hla_genotype_text(page_text)
            if has_requested and has_report and has_hla and has_expanded:
                self.logged_in = True
                self.update_login_controls()
                self.external_browser_status.setText(f"Read donor {et} from external browser.")
                self.write_hml_from_page_text(et, page_text)
                return

            if self.poll_count >= self.max_polls:
                message = "Timed out after 30 seconds waiting for accessible donor HLA typing"
                self.set_row_status(self.current_row, "Failed", message)
                self.write_gui_failure(et, page_text, message)
                self.log(f"Donor {et} failed: {message}")
                self.advance_queue()
                return

            if not has_report and not has_requested:
                self.set_row_status(self.current_row, "Login needed", "Complete login/2FA in external browser")
                self.external_browser_status.setText("Complete login/2FA in the external browser.")
            else:
                self.set_row_status(self.current_row, "Loading donor page", "Waiting for HLA typing")
            QTimer.singleShot(2000, self.read_current_external_donor_page)

        def write_hml_from_page_text(self, et: str, text: str) -> None:
            if self.current_row is None:
                return
            self.set_row_status(self.current_row, "Writing HML", "Parsing and writing output")
            try:
                result = convert_donor_page_text_to_hml(
                    donor_page_text=text,
                    requested_donor_et_number=et,
                    output_root=Path(self.output_folder_edit.text()).expanduser(),
                    hml_files_dir=self.hml_files_dir,
                )
            except Exception as exc:  # noqa: BLE001 - surface donor-specific failures in GUI
                self.set_row_status(self.current_row, "Failed", str(exc))
                self.write_gui_failure(et, text, str(exc))
                self.log(f"Donor {et} failed: {exc}")
                self.advance_queue()
                return
            self.queue[self.current_row]["result"] = result
            if not Path(result.hml_file).exists():
                message = "Conversion finished but no HML file was written"
                self.set_row_status(self.current_row, "Failed", message)
                self.write_gui_failure(et, text, message)
                self.log(f"Donor {et} failed: {message}")
                self.advance_queue()
                return
            self.set_row_status(self.current_row, "Done", str(result.hml_file), result.tt_lab)
            self.show_result(result)
            self.log(f"Donor {et}: HML written to {result.hml_file}")
            self.advance_queue()

        def on_browser_load_finished(self, ok: bool) -> None:
            if self.web_page is not None:
                QTimer.singleShot(800, self.check_login_state)
            if not self.running or self.current_row is None:
                return
            if not ok:
                self.set_row_status(
                    self.current_row,
                    "Checking page",
                    "Browser reported incomplete load; checking content anyway",
                )
                self.log("Browser reported incomplete load; checking visible page content anyway.")
            QTimer.singleShot(1200, self.poll_page_ready)

        def poll_page_ready(self) -> None:
            if not self.running or self.current_row is None:
                return
            self.web_page.toPlainText(self.on_page_plain_text_state)

        def on_page_plain_text_state(self, page_text: str) -> None:
            text = str(page_text or "")
            self.on_page_state(
                {
                    "href": self.browser.url().toString() if self.browser is not None else "",
                    "hasDonorReport": bool(re.search(r"Donor Report", text, re.I)),
                    "hasHlaTyping": bool(re.search(r"HLA Typing", text, re.I)),
                    "hasTypingCard": bool(re.search(r"Full Phenotype|TT Lab|Entry Date|Used In Match|Typing Purpose|CIWD Genotype", text, re.I)),
                    "hasCiwD": has_expanded_hla_genotype_text(text),
                    "text": text,
                }
            )

        def on_page_state(self, state: object) -> None:
            if not self.running or self.current_row is None:
                return
            et = str(self.queue[self.current_row]["et"])
            self.poll_count += 1
            state = state if isinstance(state, dict) else {}
            text = str(state.get("text", ""))
            has_requested = et in text or et in str(state.get("href", ""))
            has_report = bool(state.get("hasDonorReport"))
            has_hla = bool(state.get("hasHlaTyping"))
            has_typing_card = bool(state.get("hasTypingCard"))
            has_ciw_d = bool(state.get("hasCiwD")) or "CIWD Genotype" in text
            if has_requested and has_report and (has_hla or has_typing_card or has_ciw_d):
                if not self.logged_in:
                    self.logged_in = True
                    self.update_login_controls()
                if has_ciw_d:
                    self.set_row_status(self.current_row, "Reading page", "CIWD Genotype already visible")
                    self.log(f"Donor {et}: CIWD Genotype already visible; reading page.")
                    self.on_page_text_read(text)
                    return
                self.expand_attempt_count += 1
                self.set_row_status(
                    self.current_row,
                    "Expanding HLA typing",
                    f"Automatic CIWD expansion attempt {self.expand_attempt_count}",
                )
                self.log(
                    f"Donor {et} page detected. Automatically expanding CIWD typing "
                    f"(attempt {self.expand_attempt_count}/{self.max_expand_attempts})."
                )
                self.web_page.runJavaScript(EXPAND_HLA_JS, self.on_hla_expanded)
                return
            if self.poll_count >= self.max_polls:
                self.set_row_status(self.current_row, "Failed", "Timed out waiting for donor page")
                self.log(f"Timed out waiting for donor {et}.")
                self.advance_queue()
                return
            if self.poll_count in {1, 5, 15, 30}:
                self.log(
                    f"Waiting for donor {et}: requested={has_requested}, "
                    f"report={has_report}, hla={has_hla}, typing_card={has_typing_card}."
                )
            if not has_report and not has_requested:
                self.set_row_status(self.current_row, "Login needed", "Complete login/2FA in browser")
            else:
                self.set_row_status(self.current_row, "Loading donor page", "Waiting for typing content")
            QTimer.singleShot(1000, self.poll_page_ready)

        def on_hla_expanded(self, _value: object) -> None:
            if not self.running or self.current_row is None:
                return
            self.set_row_status(self.current_row, "Reading page", "Reading expanded webpage text")
            QTimer.singleShot(2500, lambda: self.web_page.toPlainText(self.on_page_text_read))

        def on_page_text_read(self, page_text: object) -> None:
            if not self.running or self.current_row is None:
                return
            et = str(self.queue[self.current_row]["et"])
            text = str(page_text or "")
            expanded_hla_visible = has_expanded_hla_genotype_text(text)
            if not expanded_hla_visible:
                if self.expand_attempt_count < self.max_expand_attempts:
                    self.expand_attempt_count += 1
                    self.set_row_status(
                        self.current_row,
                        "Expanding HLA typing",
                        f"CIWD not visible; retry {self.expand_attempt_count}",
                    )
                    self.log(
                        f"Donor {et}: CIWD Genotype not visible yet; retrying automatic expansion "
                        f"({self.expand_attempt_count}/{self.max_expand_attempts})."
                    )
                    self.web_page.runJavaScript(EXPAND_HLA_JS, self.on_hla_expanded)
                    return
                self.set_row_status(self.current_row, "Reading source", "Trying source-level extraction")
                self.log(f"Donor {et}: expanded genotype not found in visible text; trying page source.")
                self.web_page.runJavaScript(
                    SOURCE_DEBUG_JS,
                    lambda data, donor_et=et, visible_text=text: self.on_source_debug_read(
                        donor_et,
                        visible_text,
                        "Expanded HLA genotype allele rows were not visible after expansion.",
                        data,
                    ),
                )
                return
            if re.search(r"CIWD\s+Genotype", text, re.I):
                self.log(f"Donor {et}: CIWD Genotype detected after expansion.")
            else:
                allele_count = len(extract_alleles_from_text(text))
                self.log(
                    f"Donor {et}: expanded HLA genotype allele rows detected "
                    f"({allele_count} alleles); proceeding."
                )
            self.set_row_status(self.current_row, "Writing HML", "Parsing and writing output")
            try:
                result = convert_donor_page_text_to_hml(
                    donor_page_text=text,
                    requested_donor_et_number=et,
                    output_root=Path(self.output_folder_edit.text()).expanduser(),
                    hml_files_dir=self.hml_files_dir,
                )
            except Exception as exc:  # noqa: BLE001 - surface donor-specific failures in GUI
                self.set_row_status(self.current_row, "Failed", str(exc))
                self.write_gui_failure(et, text, str(exc))
                self.log(f"Donor {et} failed: {exc}")
                self.advance_queue()
                return
            self.queue[self.current_row]["result"] = result
            self.set_row_status(self.current_row, "Done", str(result.hml_file), result.tt_lab)
            self.show_result(result)
            self.log(f"Donor {et}: HML written to {result.hml_file}")
            self.advance_queue()

        def write_gui_failure(self, et: str, page_text: str, message: str) -> None:
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            failure_dir = Path(self.output_folder_edit.text()).expanduser() / f"ET{safe_filename(et)}_HML_failed_{timestamp}"
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / "error.txt").write_text(message, encoding="utf-8")
            (failure_dir / "raw_donor_page_text.txt").write_text(page_text, encoding="utf-8")
            (failure_dir / "normalised_donor_page_text.txt").write_text(normalise_text(page_text), encoding="utf-8")
            self.files_list.addItem(str(failure_dir))

        def html_to_text(self, value: str) -> str:
            value = re.sub(r"(?is)<(script|style)\b.*?</\1>", "\n", value)
            value = re.sub(r"(?s)<[^>]+>", "\n", value)
            value = html.unescape(value)
            return normalise_text(value)

        def on_source_debug_read(
            self,
            et: str,
            visible_text: str,
            message: str,
            data: object,
        ) -> None:
            data = data if isinstance(data, dict) else {}
            hla_rows_text = str(data.get("hlaRowsText", ""))
            hla_element_texts = "\n".join(str(item) for item in data.get("hlaElementTexts", []) if item)
            body_inner_text = str(data.get("bodyInnerText", ""))
            document_outer_text = str(data.get("documentOuterText", ""))
            html_text = self.html_to_text(str(data.get("documentOuterHTML", "")))
            source_candidates = [
                "\n".join(part for part in [visible_text, hla_rows_text] if part),
                "\n".join(part for part in [body_inner_text, hla_rows_text] if part),
                "\n".join(part for part in [document_outer_text, hla_rows_text] if part),
                "\n".join(part for part in [visible_text, hla_element_texts] if part),
                hla_rows_text,
                hla_element_texts,
                visible_text,
                body_inner_text,
                document_outer_text,
                html_text,
            ]
            for candidate in source_candidates:
                if not has_expanded_hla_genotype_text(candidate):
                    continue
                try:
                    result = convert_donor_page_text_to_hml(
                        donor_page_text=candidate,
                        requested_donor_et_number=et,
                        output_root=Path(self.output_folder_edit.text()).expanduser(),
                        hml_files_dir=self.hml_files_dir,
                    )
                except Exception as exc:  # noqa: BLE001 - try next source candidate
                    self.log(f"Donor {et}: source candidate contained genotype but conversion failed: {exc}")
                    continue
                if self.current_row is not None and self.current_row < len(self.queue):
                    self.queue[self.current_row]["result"] = result
                    self.set_row_status(self.current_row, "Done", str(result.hml_file), result.tt_lab)
                self.show_result(result)
                self.log(f"Donor {et}: HML written from page source to {result.hml_file}")
                self.advance_queue()
                return

            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            failure_dir = Path(self.output_folder_edit.text()).expanduser() / f"ET{safe_filename(et)}_HML_failed_{timestamp}"
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / "error.txt").write_text(message, encoding="utf-8")
            (failure_dir / "raw_donor_page_text.txt").write_text(visible_text, encoding="utf-8")
            (failure_dir / "normalised_donor_page_text.txt").write_text(normalise_text(visible_text), encoding="utf-8")
            (failure_dir / "page_body_inner_text.txt").write_text(str(data.get("bodyInnerText", "")), encoding="utf-8")
            (failure_dir / "page_document_outer_text.txt").write_text(str(data.get("documentOuterText", "")), encoding="utf-8")
            (failure_dir / "page_hla_rows_text.txt").write_text(str(data.get("hlaRowsText", "")), encoding="utf-8")
            (failure_dir / "page_hla_element_texts.txt").write_text(
                "\n\n--- element ---\n\n".join(str(item) for item in data.get("hlaElementTexts", []) if item),
                encoding="utf-8",
            )
            (failure_dir / "page_source.html").write_text(str(data.get("documentOuterHTML", "")), encoding="utf-8")
            (failure_dir / "resource_entries.json").write_text(
                json.dumps(data.get("resources", []), indent=2),
                encoding="utf-8",
            )
            (failure_dir / "storage_keys.json").write_text(
                json.dumps(
                    {
                        "localStorageKeys": data.get("localStorageKeys", []),
                        "sessionStorageKeys": data.get("sessionStorageKeys", []),
                        "href": data.get("href", ""),
                        "title": data.get("title", ""),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.files_list.addItem(str(failure_dir))
            if self.current_row is not None:
                self.set_row_status(self.current_row, "Failed", "Expanded HLA genotype not visible")
            self.log(f"Donor {et} failed: source-level extraction did not find expanded genotype.")
            self.advance_queue()

        def advance_queue(self) -> None:
            if self.current_row is None:
                return
            self.current_row += 1
            if self.running:
                QTimer.singleShot(400, self.process_current_row)

        def on_queue_selection_changed(self) -> None:
            rows = self.queue_table.selectionModel().selectedRows()
            if not rows:
                return
            item = self.queue[rows[0].row()]
            result = item.get("result")
            self.show_result(result if isinstance(result, ConversionResult) else None)

        def show_result(self, result: ConversionResult | None) -> None:
            self.files_list.clear()
            if result is None:
                self.et_value.setText("-")
                self.center_value.setText("-")
                self.tt_lab_value.setText("-")
                self.typing_date_value.setText("-")
                self.loci_value.setText("-")
                self.gl_text.clear()
                return
            self.et_value.setText(result.donor_et_number)
            self.center_value.setText(result.registration_center)
            self.tt_lab_value.setText(result.tt_lab)
            self.typing_date_value.setText(result.typing_date)
            self.loci_value.setText(", ".join(result.loci))
            self.gl_text.setPlainText(result.gl_string)
            for path in (
                result.output_dir,
                result.hml_file,
                result.hml_copy_file,
                result.gl_string_file,
                result.metadata_file,
                result.raw_text_file,
            ):
                if path is not None:
                    self.files_list.addItem(str(path))

        def copy_gl_string(self) -> None:
            QApplication.clipboard().setText(self.gl_text.toPlainText())
            self.log("GL string copied to clipboard.")

        def selected_result(self) -> ConversionResult | None:
            rows = self.queue_table.selectionModel().selectedRows()
            if not rows:
                return None
            result = self.queue[rows[0].row()].get("result")
            return result if isinstance(result, ConversionResult) else None

        def open_selected_hml(self) -> None:
            result = self.selected_result()
            if result:
                self.open_path(result.hml_file)

        def open_selected_output_folder(self) -> None:
            result = self.selected_result()
            if result:
                self.open_path(result.output_dir)
            else:
                self.open_path(Path(self.output_folder_edit.text()))

        def open_hml_files_folder(self) -> None:
            self.hml_files_dir.mkdir(parents=True, exist_ok=True)
            self.open_path(self.hml_files_dir)

        def open_path(self, path: Path) -> None:
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def update_login_controls(self) -> None:
            enabled = bool(self.logged_in)
            for action in (self.run_selected_action, self.run_all_action, self.stop_action):
                action.setEnabled(True)
            if self.running:
                return
            user_text = self.logged_in_username or "logged in"
            if enabled:
                self.set_progress_label(f"Ready - {user_text}", self.progress_bar.value())
            else:
                self.set_progress_label("Ready - login not confirmed", self.progress_bar.value())

        def check_login_state(self) -> None:
            try:
                profile_dir = get_browser_profile_dir(Path(self.output_folder_edit.text()).expanduser())
                port = read_saved_devtools_port(profile_dir) or discover_existing_devtools_port(profile_dir)
                self.browser_port = port
                logged_in = False
                if port is not None:
                    for target in read_json_url(f"http://127.0.0.1:{port}/json", timeout=1.0):
                        url = str(target.get("url", ""))
                        if "donor-data.etnext.eu" in url and not re.search(
                            r"login|signin|sign-in|authenticate|oauth|keycloak|sso|adfs",
                            url,
                            re.I,
                        ):
                            logged_in = True
                            break
                self.on_login_state_read({"loggedIn": logged_in, "username": ""})
            except Exception:
                self.on_login_state_read({"loggedIn": False, "username": ""})

        def on_login_state_read(self, data: object) -> None:
            data = data if isinstance(data, dict) else {}
            was_logged_in = self.logged_in
            self.logged_in = bool(data.get("loggedIn"))
            self.logged_in_username = str(data.get("username") or "").strip()
            self.update_login_controls()
            if self.logged_in and not was_logged_in:
                self.log(f"Donordata login detected: {self.logged_in_username or 'session active'}.")
            if not self.logged_in and not self.running:
                QTimer.singleShot(3000, self.check_login_state)

        def login_to_donordata(self) -> None:
            try:
                self.browser_process, self.browser_port = launch_controlled_browser(
                    "https://donor-data.etnext.eu/",
                    "login",
                    Path(self.output_folder_edit.text()).expanduser(),
                )
                self.set_progress_label("Complete login/2FA in external browser", 0)
                self.external_browser_status.setText("Complete login/2FA in the external browser.")
                self.log("Opening Donordata login page in external browser.")
                QTimer.singleShot(3000, self.check_login_state)
            except Exception as exc:  # noqa: BLE001 - surface browser launch failures in GUI
                QMessageBox.warning(self, "Could not open browser", str(exc))

        def logout_from_donordata(self) -> None:
            self.logged_in = False
            self.logged_in_username = ""
            self.update_login_controls()
            self.log("Logout is handled in the external browser; local status reset.")

        def choose_hml_files_folder(self, label: QLabel | None = None) -> None:
            folder = QFileDialog.getExistingDirectory(self, "Choose HML output folder", str(self.hml_files_dir))
            if not folder:
                return
            self.hml_files_dir = Path(folder)
            self.hml_files_dir.mkdir(parents=True, exist_ok=True)
            self.settings.setValue("hml_files_dir", str(self.hml_files_dir))
            if label is not None:
                label.setText(str(self.hml_files_dir))
            self.log(f"HML folder set to {self.hml_files_dir}")

        def show_settings(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Settings")
            layout = QVBoxLayout(dialog)

            session_box = QGroupBox("Donordata session")
            session_layout = QVBoxLayout(session_box)
            session_status = QLabel(
                f"Logged in as: {self.logged_in_username or 'session active'}"
                if self.logged_in
                else "Not logged in"
            )
            session_layout.addWidget(session_status)
            session_buttons = QHBoxLayout()
            login_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton), "Login")
            logout_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton), "Logout")
            login_button.clicked.connect(lambda: (self.login_to_donordata(), dialog.accept()))
            logout_button.clicked.connect(lambda: (self.logout_from_donordata(), dialog.accept()))
            session_buttons.addWidget(login_button)
            session_buttons.addWidget(logout_button)
            session_layout.addLayout(session_buttons)
            layout.addWidget(session_box)

            output_box = QGroupBox("HML output")
            output_layout = QVBoxLayout(output_box)
            hml_label = QLabel(str(self.hml_files_dir))
            hml_label.setWordWrap(True)
            folder_buttons = QHBoxLayout()
            choose_button = QPushButton(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
                "Choose HML folder",
            )
            open_button = QPushButton(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirLinkIcon),
                "Open HML folder",
            )
            choose_button.clicked.connect(lambda: self.choose_hml_files_folder(hml_label))
            open_button.clicked.connect(self.open_hml_files_folder)
            folder_buttons.addWidget(choose_button)
            folder_buttons.addWidget(open_button)
            output_layout.addWidget(hml_label)
            output_layout.addLayout(folder_buttons)
            layout.addWidget(output_box)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()

        def log(self, message: str) -> None:
            timestamp = dt.datetime.now().strftime("%H:%M:%S")
            self.log_text.appendPlainText(f"[{timestamp}] {message}")

    app = QApplication(sys.argv)
    app.setApplicationName("Donor Typing to HML")
    icon_path = app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    window = DonorTypingMainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        try:
            raise SystemExit(main())
        except SystemExit as exc:
            if exc.code not in (0, None):
                failure_dir = write_failure_log(exc)
                message = str(exc)
                if failure_dir is not None:
                    message = f"{message}\n\nFailure details:\n{failure_dir}"
                show_dialog_message("ET donor HML conversion failed", message, error=True)
            raise
        except Exception as exc:
            failure_dir = write_failure_log(exc)
            message = str(exc)
            if failure_dir is not None:
                message = f"{message}\n\nFailure details:\n{failure_dir}"
            show_dialog_message("ET donor HML conversion failed", message, error=True)
            raise
    raise SystemExit(gui_main())
