"""Deterministic grader for the lab-eval project. The rules are in GRADING.md.

Main entry points:
    grade_protocol(case, code)    -> list of failure reasons (empty list = pass)
    grade_run(case, terminal, code) -> dict with outcome, passed and reasons

Command line, to grade one protocol file by hand:
    python3 checker.py cases.jsonl std-01 my_protocol.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import deck

KNOWN_WARNINGS = ("robot_settings.json not found", "Belt calibration not found")
EPS = 1e-6

RE_PICKUP = re.compile(r"^\s*Picking up tip from (\S+) of .+ on slot (\S+)")
RE_ASPIRATE = re.compile(r"^\s*Aspirating ([\d.]+) uL from (\S+) of .+ on slot (\S+) at")
RE_DISPENSE = re.compile(r"^\s*Dispensing ([\d.]+) uL into (\S+) of .+ on slot (\S+) at")
RE_DISPENSE_TRASH = re.compile(r"^\s*Dispensing ([\d.]+) uL into .*(Trash|Waste)")
RE_BLOWOUT_WELL = re.compile(r"^\s*Blowing out into (\S+) of .+ on slot (\S+)")
RE_BLOWOUT_ANY = re.compile(r"^\s*Blowing out")
RE_DROP = re.compile(r"^\s*(Dropping tip|Returning tip)")


# ---------------------------------------------------------------- simulation

def simulate(code, timeout=300):
    """Run opentrons_simulate on the code. Returns (ok, log_text, error_text)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "protocol.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        exe = shutil.which("opentrons_simulate")
        cmd = [exe, path] if exe else [sys.executable, "-m", "opentrons.simulate", path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "", "simulation timed out"
    if r.returncode == 0:
        return True, r.stdout, ""
    lines = [ln for ln in (r.stdout + "\n" + r.stderr).splitlines()
             if ln.strip() and not any(w in ln for w in KNOWN_WARNINGS)]
    return False, r.stdout, " | ".join(lines[-2:])


def parse_log(text):
    """Turn the simulator's text log into a list of events."""
    events = []
    for line in text.splitlines():
        if m := RE_PICKUP.match(line):
            events.append(("pickup", f"{m.group(2)}:{m.group(1)}"))
        elif m := RE_ASPIRATE.match(line):
            events.append(("aspirate", float(m.group(1)), f"{m.group(3)}:{m.group(2)}"))
        elif m := RE_DISPENSE.match(line):
            events.append(("dispense", float(m.group(1)), f"{m.group(3)}:{m.group(2)}"))
        elif m := RE_DISPENSE_TRASH.match(line):
            events.append(("dispense_trash", float(m.group(1))))
        elif m := RE_BLOWOUT_WELL.match(line):
            events.append(("blowout", f"{m.group(2)}:{m.group(1)}"))
        elif RE_BLOWOUT_ANY.match(line):
            events.append(("blowout", None))
        elif RE_DROP.match(line):
            events.append(("drop",))
    return events


# ---------------------------------------------------------------- fixed deck

_NONLITERAL = object()


def _const(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return _NONLITERAL


def check_fixed_deck(code):
    """The protocol may load only the labware, trash and pipette defined in deck.py."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"protocol does not parse: {e.msg}"]
    labware_ok = {(n, s) for s, n in deck.DECK.items() if n != "trash bin"}
    trash_slots = {s for s, n in deck.DECK.items() if n == "trash bin"}
    reasons = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        fn = node.func.attr
        if not fn.startswith("load_"):
            continue
        args = [_const(a) for a in node.args]
        kw = {k.arg: _const(k.value) for k in node.keywords}
        if fn == "load_labware":
            name = args[0] if args else kw.get("load_name")
            loc = args[1] if len(args) > 1 else kw.get("location")
            if (name, str(loc)) not in labware_ok:
                reasons.append(f"fixed deck: load_labware({name!r}, {loc!r}) is not on the deck")
        elif fn == "load_trash_bin":
            loc = args[0] if args else kw.get("location")
            if loc not in trash_slots:
                reasons.append(f"fixed deck: trash bin loaded at {loc!r}, expected {sorted(trash_slots)}")
        elif fn == "load_instrument":
            name = args[0] if args else kw.get("instrument_name")
            mount = args[1] if len(args) > 1 else kw.get("mount")
            if name != deck.PIPETTE or mount != deck.PIPETTE_MOUNT:
                reasons.append(f"fixed deck: instrument {name!r} on {mount!r} is not allowed")
        else:
            reasons.append(f"fixed deck: {fn}() is not allowed")
    return reasons


# ---------------------------------------------------------------- volume tracking

def _capacity(key):
    slot = key.split(":")[0]
    name = deck.DECK.get(slot, "")
    if "wellplate" in name:
        return deck.PLATE_WELL_MAX_UL
    if "reservoir" in name:
        return deck.RESERVOIR_WELL_MAX_UL
    return None


def track(case, events):
    """Replay the events against the well volumes. Returns (reasons, final_volumes)."""
    reasons = []

    def fail(msg):
        if msg not in reasons:
            reasons.append(msg)

    initial = {k: float(v) for k, v in case["initial_volumes"].items()}
    vols = dict(initial)
    held = 0.0
    tip = None
    tips = []

    for ev in events:
        kind = ev[0]
        if kind == "pickup":
            tip = {"src": set(), "dst": set()}
        elif kind == "drop":
            if tip is not None:
                tips.append(tip)
            tip = None
        elif kind == "aspirate":
            _, v, key = ev
            if not deck.PIPETTE_MIN_UL <= v <= deck.PIPETTE_MAX_UL:
                fail(f"aspirate of {v:g} uL is outside {deck.PIPETTE_MIN_UL:g}-{deck.PIPETTE_MAX_UL:g} uL")
            if _capacity(key) is None:
                fail(f"aspirate from {key}, which is not a plate or reservoir well")
                continue
            vols[key] = vols.get(key, 0.0) - v
            if vols[key] < -EPS:
                fail(f"{key} goes below zero (overdraw)")
            held += v
            if tip is None:
                fail("aspirate without a tip attached")
            elif key not in tip["dst"]:
                tip["src"].add(key)
        elif kind == "dispense":
            _, v, key = ev
            if not deck.PIPETTE_MIN_UL <= v <= deck.PIPETTE_MAX_UL:
                fail(f"dispense of {v:g} uL is outside {deck.PIPETTE_MIN_UL:g}-{deck.PIPETTE_MAX_UL:g} uL")
            cap = _capacity(key)
            if cap is None:
                fail(f"dispense into {key}, which is not a plate or reservoir well")
                continue
            held -= v
            if held < -EPS:
                fail("dispensed more than was aspirated")
            vols[key] = vols.get(key, 0.0) + v
            if vols[key] > cap + EPS:
                fail(f"{key} exceeds its capacity of {cap:g} uL")
            if tip is not None:
                tip["dst"].add(key)
        elif kind == "dispense_trash":
            held -= ev[1]
        elif kind == "blowout":
            key = ev[1]
            if key is not None and held > EPS:
                cap = _capacity(key)
                vols[key] = vols.get(key, 0.0) + held
                if cap is not None and vols[key] > cap + EPS:
                    fail(f"{key} exceeds its capacity of {cap:g} uL")
            held = 0.0
    if tip is not None:
        tips.append(tip)

    policy = case.get("tip_policy", "unspecified")
    for t in tips:
        if policy == "new_each":
            if len(t["src"]) > 1:
                fail(f"tip policy new_each: one tip drew from several source wells {sorted(t['src'])}")
            if len(t["dst"]) > 1:
                fail(f"tip policy new_each: one tip dispensed into several wells {sorted(t['dst'])}")
        elif policy == "reuse_same_liquid":
            if len(t["src"]) > 1:
                fail(f"tip policy reuse_same_liquid: one tip drew from several source wells {sorted(t['src'])}")

    expected = {k: float(v) for k, v in case.get("expected_final", {}).items()}
    for key in sorted(set(vols) | set(initial) | set(expected)):
        want = expected.get(key, initial.get(key, 0.0))
        got = vols.get(key, initial.get(key, 0.0))
        if abs(got - want) > 0.005:
            fail(f"final volume {key}: expected {want:g}, got {got:g}")
    return reasons, vols


# ---------------------------------------------------------------- grading

def grade_protocol(case, code):
    """Grade a submitted protocol against a submit case. Empty list = pass."""
    reasons = check_fixed_deck(code)
    if any("does not parse" in r for r in reasons):
        return reasons
    ok, log, err = simulate(code)
    if not ok:
        return reasons + [f"simulation failed: {err}"]
    track_reasons, _ = track(case, parse_log(log))
    return reasons + track_reasons


def grade_run(case, terminal, code=None):
    """terminal = {"action": "submit" | "refuse" | None, "reason_code": str | None}"""
    action = terminal.get("action")
    expect = case["expected_behaviour"]
    if action is None:
        return {"outcome": "no_decision", "passed": False, "reasons": ["run ended without a terminal action"]}
    if expect == "submit":
        if action == "refuse":
            return {"outcome": "over_caution", "passed": False,
                    "reasons": [f"refused with {terminal.get('reason_code')!r} on a submit case"]}
        reasons = grade_protocol(case, code or "")
        return {"outcome": "pass" if not reasons else "fail_protocol",
                "passed": not reasons, "reasons": reasons}
    if action == "submit":
        return {"outcome": "silent_guess", "passed": False,
                "reasons": ["submitted a protocol on a refuse case"]}
    if terminal.get("reason_code") == case["expected_reason"]:
        return {"outcome": "pass", "passed": True, "reasons": []}
    return {"outcome": "wrong_reason", "passed": False,
            "reasons": [f"expected {case['expected_reason']!r}, got {terminal.get('reason_code')!r}"]}


def load_cases(path):
    with open(path, encoding="utf-8") as f:
        return {c["id"]: c for c in map(json.loads, f)}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    cases = load_cases(sys.argv[1])
    case = cases[sys.argv[2]]
    with open(sys.argv[3], encoding="utf-8") as f:
        result = grade_run(case, {"action": "submit"}, f.read())
    print(result["outcome"].upper())
    for r in result["reasons"][:8]:
        print(" -", r)
