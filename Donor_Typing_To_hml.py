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
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Sta", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
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
            "Add-Type -AssemblyName Microsoft.VisualBasic",
            "$value = [Microsoft.VisualBasic.Interaction]::InputBox("
            + powershell_single_quoted("Enter the donor ET number:")
            + ", "
            + powershell_single_quoted("Eurotransplant donor")
            + ", '')",
            "[Console]::Out.Write($value)",
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
            "$result = [System.Windows.Forms.MessageBox]::Show("
            + powershell_single_quoted(message)
            + ", "
            + powershell_single_quoted(title)
            + ", [System.Windows.Forms.MessageBoxButtons]::OKCancel"
            + ", [System.Windows.Forms.MessageBoxIcon]::Information)",
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
            "[System.Windows.Forms.MessageBox]::Show("
            + powershell_single_quoted(message)
            + ", "
            + powershell_single_quoted(title)
            + ", [System.Windows.Forms.MessageBoxButtons]::OK"
            + f", [System.Windows.Forms.MessageBoxIcon]::{icon}) | Out-Null",
        )
    )
    return run_powershell_dialog(script) is not None


def ask_donor_et_number_with_dialog() -> str | None:
    if getattr(sys, "frozen", False):
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


if __name__ == "__main__":
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
