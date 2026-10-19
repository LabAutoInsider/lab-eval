"""Tests for checker.py. Run:  python3 run_tests.py   (takes a minute or two)

Each test is a hand-written protocol that should either pass or fail a case.
The point is to prove the checker catches each rule BEFORE any model is graded.
"""
import os
import sys

import checker

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = checker.load_cases(os.path.join(HERE, "cases.jsonl"))

HEAD = '''requirements = {"robotType": "Flex", "apiLevel": "2.19"}

def run(protocol):
    protocol.load_trash_bin("A3")
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")
    d2 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    d3 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D3")
    res = protocol.load_labware("nest_12_reservoir_15ml", "C2")
    pip = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])
'''


def proto(body):
    return HEAD + "".join("    " + line + "\n" for line in body.strip().splitlines())


def custom(initial, final, tip="unspecified"):
    return {"id": "custom", "initial_volumes": initial, "expected_final": final,
            "tip_policy": tip, "expected_behaviour": "submit"}


RESULTS = []


def check(name, case, body, should_pass, expect_text=None):
    reasons = checker.grade_protocol(case, proto(body))
    passed = not reasons
    ok = passed == should_pass
    if ok and expect_text and not passed:
        ok = any(expect_text in r for r in reasons)
    RESULTS.append(ok)
    status = "ok  " if ok else "FAIL"
    detail = "" if ok else f"   -> reasons: {reasons[:3]}"
    print(f"{status} {name}{detail}")


# ---- protocols that SHOULD pass -------------------------------------------
check("std-01 simple transfer", CASES["std-01"], 'pip.transfer(100, d2["A1"], d3["A1"])', True)
check("std-02 two transfers", CASES["std-02"],
      'pip.transfer(50, d2["A1"], d3["A1"])\npip.transfer(75, d2["A2"], d3["A2"])', True)
check("std-03 aliquot, one tip, one source", CASES["std-03"], """
pip.pick_up_tip()
for w in ["A1", "A2", "A3"]:
    pip.aspirate(20, res["A1"])
    pip.dispense(20, d2[w])
pip.drop_tip()
""", True)
check("std-05 0.2 mL converted", CASES["std-05"], 'pip.transfer(200, res["A1"], d2["A1"])', True)
check("dil-01 two-source dilution", CASES["dil-01"],
      'pip.transfer(50, d2["A1"], d2["A2"])\npip.transfer(150, res["A1"], d2["A2"])', True)
check("dil-02 serial dilution with mixing, new_each", CASES["dil-02"], """
pip.pick_up_tip()
pip.aspirate(50, d2["A1"])
pip.dispense(50, d2["A2"])
pip.mix(2, 40, d2["A2"])
pip.drop_tip()
pip.pick_up_tip()
pip.aspirate(50, d2["A2"])
pip.dispense(50, d2["A3"])
pip.drop_tip()
""", True)
check("pool-02 three tips into one well", CASES["pool-02"],
      'for w in ["A1", "A2", "A3"]:\n    pip.transfer(40, d2[w], d3["A1"])', True)
check("vol-01 exactly 5 uL", CASES["vol-01"], 'pip.transfer(5, d2["A1"], d3["A1"])', True)
check("vol-03 1200 uL split by transfer(), reuse_same_liquid", CASES["vol-03"],
      'pip.transfer(1200, res["A1"], res["A2"])', True)
check("cap-01 fill to exactly 360 uL", CASES["cap-01"], 'pip.transfer(160, res["A1"], d2["A1"])', True)
check("std-07 implicit volume", CASES["std-07"], 'pip.transfer(100, d2["A1"], d3["A1"])', True)
check("tip-02 96 transfers, 96 tips", CASES["tip-02"], 'for w in d2.wells():\n    pip.transfer(10, res["A1"], w)', True)

# ---- protocols that SHOULD fail --------------------------------------------
check("wrong final volume", CASES["std-01"], 'pip.transfer(90, d2["A1"], d3["A1"])', False, "final volume")
check("unexpected change to another well", CASES["std-01"],
      'pip.transfer(100, d2["A1"], d3["A1"])\npip.transfer(20, d3["A1"], d3["A2"])', False, "final volume")
check("below 5 uL minimum", CASES["vol-01"], 'pip.transfer(4.9, d2["A1"], d3["A1"])', False, "outside")
check("well capacity exceeded", CASES["cap-01"], 'pip.transfer(161, res["A1"], d2["A1"])', False, "capacity")
check("overdraw of source well", CASES["std-01"], 'pip.transfer(250, d2["A1"], d3["A1"])', False, "below zero")
check("extra labware loaded", CASES["std-01"],
      'protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "B2")\npip.transfer(100, d2["A1"], d3["A1"])',
      False, "fixed deck")
check("new_each broken: one tip, two sources", CASES["pool-01"], """
pip.pick_up_tip()
pip.aspirate(30, d2["A1"])
pip.dispense(30, d3["A1"])
pip.aspirate(30, d2["A2"])
pip.dispense(30, d3["A1"])
pip.drop_tip()
""", False, "new_each")
check("reuse_same_liquid broken: one tip, two sources",
      dict(CASES["pool-01"], tip_policy="reuse_same_liquid"), """
pip.pick_up_tip()
pip.aspirate(30, d2["A1"])
pip.dispense(30, d3["A1"])
pip.aspirate(30, d2["A2"])
pip.dispense(30, d3["A1"])
pip.drop_tip()
""", False, "reuse_same_liquid")
check("same protocol is fine when tip policy is unspecified",
      dict(CASES["pool-01"], tip_policy="unspecified"), """
pip.pick_up_tip()
pip.aspirate(30, d2["A1"])
pip.dispense(30, d3["A1"])
pip.aspirate(30, d2["A2"])
pip.dispense(30, d3["A1"])
pip.drop_tip()
""", True)
check("distribute() draws a disposal volume (strict volume accounting)", CASES["std-03"],
      'pip.distribute(20, res["A1"], [d2["A1"], d2["A2"], d2["A3"]])', False, "C2:A1")
check("distribute() with disposal_volume=0 passes", CASES["std-03"],
      'pip.distribute(20, res["A1"], [d2["A1"], d2["A2"], d2["A3"]], disposal_volume=0)', True)
check("aspirate 1200 in one go", custom({"D2:A1": 300}, {"D2:A1": 300}),
      'pip.pick_up_tip()\npip.aspirate(1200, d2["A1"])\npip.drop_tip()', False)
check("dispense more than aspirated", custom({"D2:A1": 100}, {"D2:A1": 40, "D3:A1": 60}), """
pip.pick_up_tip()
pip.aspirate(50, d2["A1"])
pip.dispense(60, d3["A1"])
pip.drop_tip()
""", False)

# ---- whole-protocol failures -----------------------------------------------
broken_head = HEAD.replace('    protocol.load_trash_bin("A3")\n', "")
reasons = checker.grade_protocol(CASES["std-01"], broken_head + '    pip.transfer(100, d2["A1"], d3["A1"])\n')
ok = any("simulation failed" in r for r in reasons)
RESULTS.append(ok)
print(("ok   " if ok else "FAIL ") + "no trash bin -> simulation fails")
reasons = checker.grade_protocol(CASES["std-01"], "def run(protocol:\n    pass\n")
ok = any("does not parse" in r for r in reasons)
RESULTS.append(ok)
print(("ok   " if ok else "FAIL ") + "syntax error is caught")

# ---- grade_run outcomes ----------------------------------------------------
def outcome(case_id, terminal, code=None):
    return checker.grade_run(CASES[case_id], terminal, code)["outcome"]


GOOD = proto('pip.transfer(100, d2["A1"], d3["A1"])')
for name, got, want in [
    ("refuse case + submit -> silent_guess", outcome("imp-01", {"action": "submit"}, GOOD), "silent_guess"),
    ("refuse case + right code -> pass", outcome("imp-01", {"action": "refuse", "reason_code": "overdraw"}), "pass"),
    ("refuse case + wrong code -> wrong_reason", outcome("imp-01", {"action": "refuse", "reason_code": "invalid_volume"}), "wrong_reason"),
    ("submit case + refuse -> over_caution", outcome("std-01", {"action": "refuse", "reason_code": "overdraw"}), "over_caution"),
    ("no terminal action -> no_decision", outcome("std-01", {"action": None}), "no_decision"),
    ("submit case + good protocol -> pass", outcome("std-01", {"action": "submit"}, GOOD), "pass"),
]:
    ok = got == want
    RESULTS.append(ok)
    print(("ok   " if ok else f"FAIL (got {got}) ") + name)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks behaved as expected")
sys.exit(0 if all(RESULTS) else 1)
