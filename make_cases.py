"""Convert a plain-text notes file of eval cases into cases.jsonl, with validation.

Usage:  python make_cases.py cases_notes.txt [cases.jsonl]

Notes format: one block per case, blocks separated by a line containing only ---
Lines starting with # are comments. Leave a field blank when it does not apply.

    id: std-01
    category: standard
    request: Move 100 uL from D2 A1 to D3 A1.
    initial: D2:A1=200
    tip_policy: new_each
    expect: submit
    final: D2:A1=100, D3:A1=100
    reason:

Fields: id, category, request, initial, tip_policy, expect, final, reason.
expect is submit or refuse. Ambiguous and impossible requests both expect refuse.
Volumes are written SLOT:WELL=UL, comma-separated. A rectangle of wells can be written
as SLOT:FIRST-LAST=UL, for example D2:A1-H12=10 for every well of a 96-well plate.
This script checks structure only. It cannot tell you whether a case is a good one.
"""
import json
import re
import sys
from collections import Counter

import deck

CATEGORIES = {"standard", "edge", "ambiguous", "impossible"}
EXPECTS = {"submit", "refuse"}
TIP_POLICIES = {"new_each", "reuse_same_liquid", "unspecified"}

# Keep in sync with the "Reason codes" section of GRADING.md.
MISSING_INFO_CODES = {
    "missing_volume", "missing_source", "missing_destination",
    "missing_dilution_factor", "missing_diluent",
}
IMPOSSIBLE_CODES = {
    "overdraw", "sub_minimum_pipette_volume", "well_capacity_exceeded",
    "out_of_bounds_deck_slot", "invalid_well_coordinate",
    "invalid_volume", "insufficient_tips",
}

# standard / ambiguous / impossible have one fixed expected behaviour; edge cases can be either.
CATEGORY_EXPECT = {"standard": "submit", "ambiguous": "refuse", "impossible": "refuse"}
CATEGORY_CODES = {"ambiguous": MISSING_INFO_CODES, "impossible": IMPOSSIBLE_CODES}

FIELDS = {"id", "category", "request", "initial", "tip_policy", "expect", "final", "reason"}

# slot -> (valid rows, number of columns, capacity in uL), built from deck.py
LABWARE = {}
for _slot, _name in deck.DECK.items():
    if "wellplate" in _name:
        LABWARE[_slot] = ("ABCDEFGH", 12, deck.PLATE_WELL_MAX_UL)
    elif "reservoir" in _name:
        LABWARE[_slot] = ("A", 12, deck.RESERVOIR_WELL_MAX_UL)


def read_blocks(path):
    blocks, current = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip() == "---":
                if current:
                    blocks.append(current)
                current = []
            elif line.strip() and not line.lstrip().startswith("#"):
                current.append(line.rstrip("\n"))
    if current:
        blocks.append(current)
    return blocks


def expand_range(w1, w2):
    r1, r2 = sorted([w1[0], w2[0]])
    c1, c2 = sorted([int(w1[1:]), int(w2[1:])])
    return [f"{chr(r)}{c}" for r in range(ord(r1), ord(r2) + 1) for c in range(c1, c2 + 1)]


def parse_volumes(text, field, errors):
    out = {}
    if not text.strip():
        return out
    for item in text.split(","):
        item = item.strip()
        m = re.fullmatch(
            r"([A-Za-z]\d):([A-Za-z]\d+)(?:-([A-Za-z]\d+))?\s*=\s*(\d+(?:\.\d+)?)", item
        )
        if not m:
            errors.append(f"{field}: cannot parse '{item}' (expected SLOT:WELL=VOLUME)")
            continue
        slot, first, last, vol = m.group(1).upper(), m.group(2).upper(), m.group(3), float(m.group(4))
        wells = expand_range(first, last.upper()) if last else [first]
        vol = int(vol) if vol == int(vol) else vol
        for well in wells:
            key = f"{slot}:{well}"
            if slot not in LABWARE:
                errors.append(f"{field}: {key} - slot {slot} has no plate or reservoir on the deck")
            else:
                rows, cols, cap = LABWARE[slot]
                if well[0] not in rows or not 1 <= int(well[1:]) <= cols:
                    errors.append(f"{field}: {key} - well does not exist on that labware")
                if vol > cap:
                    errors.append(f"{field}: {key}={vol:g} exceeds well capacity {cap:g}")
            if key in out:
                errors.append(f"{field}: {key} listed twice")
            out[key] = vol
    return out


def parse_block(lines):
    errors, fields = [], {}
    for line in lines:
        if ":" not in line:
            errors.append(f"line without a 'field:' prefix: '{line}'")
            continue
        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.strip()
        if key not in FIELDS:
            errors.append(f"unknown field '{key}'")
        elif key in fields:
            errors.append(f"field '{key}' appears twice")
        else:
            fields[key] = value

    for req in ("id", "category", "request", "expect"):
        if not fields.get(req):
            errors.append(f"missing required field '{req}'")

    category, expect = fields.get("category", ""), fields.get("expect", "")
    if category and category not in CATEGORIES:
        errors.append(f"category '{category}' not in {sorted(CATEGORIES)}")
    if expect and expect not in EXPECTS:
        hint = " (ask and refuse are merged: use refuse)" if expect == "ask" else ""
        errors.append(f"expect '{expect}' not in {sorted(EXPECTS)}{hint}")
    if category in CATEGORY_EXPECT and expect and expect != CATEGORY_EXPECT[category]:
        errors.append(f"{category} cases must expect '{CATEGORY_EXPECT[category]}', not '{expect}'")

    tip = fields.get("tip_policy") or "unspecified"
    if tip not in TIP_POLICIES:
        errors.append(f"tip_policy '{tip}' not in {sorted(TIP_POLICIES)}")

    initial = parse_volumes(fields.get("initial", ""), "initial", errors)
    final = parse_volumes(fields.get("final", ""), "final", errors)
    reason = fields.get("reason", "")

    if expect == "submit":
        if not final:
            errors.append("a submit case needs a 'final' line")
        if reason:
            errors.append("a submit case must leave 'reason' blank")
    elif expect == "refuse":
        if final:
            errors.append("'final' must be blank for refuse cases")
        valid = CATEGORY_CODES.get(category, MISSING_INFO_CODES | IMPOSSIBLE_CODES)
        if reason not in valid:
            errors.append(f"reason '{reason}' not valid for {category or 'refuse'} cases; use one of {sorted(valid)}")

    case = {
        "id": fields.get("id", ""),
        "category": category,
        "request": fields.get("request", ""),
        "initial_volumes": initial,
        "tip_policy": tip,
        "expected_behaviour": expect,
    }
    if expect == "submit":
        case["expected_final"] = final
    else:
        case["expected_reason"] = reason
    return case, errors


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    notes = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "cases.jsonl"

    cases, problems, seen = [], [], set()
    for n, block in enumerate(read_blocks(notes), start=1):
        case, errors = parse_block(block)
        label = case["id"] or f"block {n}"
        if case["id"] in seen:
            errors.append("duplicate id")
        seen.add(case["id"])
        problems.extend(f"{label}: {e}" for e in errors)
        cases.append(case)

    if problems:
        print(f"{len(problems)} problem(s) found, nothing written:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Wrote {len(cases)} cases to {out_path}")
    print("By category:", dict(Counter(c["category"] for c in cases)))
    print("By expected behaviour:", dict(Counter(c["expected_behaviour"] for c in cases)))


if __name__ == "__main__":
    main()
