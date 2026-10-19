"""Tests for agent.py and run_eval.py using a fake Claude client (no API calls, no cost).

Run:  python3 run_agent_tests.py
"""
import copy
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

import agent
import checker
import run_eval

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = checker.load_cases(os.path.join(HERE, "cases.jsonl"))
RESULTS = []

GOOD = '''requirements = {"robotType": "Flex", "apiLevel": "2.19"}

def run(protocol):
    protocol.load_trash_bin("A3")
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")
    d2 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    d3 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D3")
    res = protocol.load_labware("nest_12_reservoir_15ml", "C2")
    pip = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])
    pip.transfer(100, d2["A1"], d3["A1"])
'''


class FakeClient:
    """messages.create(...) returns scripted replies; `script` is a list of lists of block specs."""

    def __init__(self, script):
        self.script, self.calls, self.n = script, [], 0
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(copy.deepcopy(kw))  # snapshot: the agent keeps appending to messages
        specs = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        blocks = []
        for i, s in enumerate(specs):
            if s[0] == "text":
                blocks.append(NS(type="text", text=s[1]))
            elif s[0] == "thinking":
                blocks.append(NS(type="thinking", thinking=s[1], signature=s[2]))
            else:
                blocks.append(NS(type="tool_use", id=f"tu_{self.n}_{i}", name=s[1], input=s[2]))
        return NS(content=blocks, usage=NS(input_tokens=100, output_tokens=20), stop_reason="tool_use")


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   -> {detail}"))


def grade(case_id, client, **kw):
    case = CASES[case_id]
    r = agent.run_case(client, "fake-model", case, **kw)
    return r, checker.grade_run(case, r["terminal"], r["terminal"].get("code"))


# 1. simulate first, then submit a correct protocol
c = FakeClient([[("tool", "run_simulation", {"code": GOOD})], [("tool", "submit_protocol", {"code": GOOD})]])
r, g = grade("std-01", c)
check("simulate then submit -> pass", g["outcome"] == "pass" and r["turns"] == 2, (g, r["turns"]))
second_user = c.calls[1]["messages"][-1]["content"][0]
check("tool_result is returned with the matching id",
      second_user["type"] == "tool_result" and second_user["tool_use_id"] == "tu_1_0"
      and second_user["content"].startswith("Simulation succeeded"), second_user)
check("system prompt and tools are sent", c.calls[0]["system"] == agent.SYSTEM_PROMPT
      and [t["name"] for t in c.calls[0]["tools"]] == ["run_simulation", "submit_protocol", "refuse"])
check("agent is never shown the expected answer or tip policy", "expected" not in c.calls[0]["messages"][0]["content"]
      and "new_each" not in c.calls[0]["messages"][0]["content"])
check("request and starting volumes are shown", "D2 A1: 200 uL" in c.calls[0]["messages"][0]["content"])

# 1b. thinking blocks are sent back intact (this was a real API error in the first live run)
c = FakeClient([[("thinking", "let me check", "sig123"), ("tool", "run_simulation", {"code": GOOD})],
                [("tool", "submit_protocol", {"code": GOOD})]])
r, g = grade("std-01", c)
sent = c.calls[1]["messages"][1]["content"][0]
check("thinking block is passed back with its text and signature",
      sent == {"type": "thinking", "thinking": "let me check", "signature": "sig123"} and g["outcome"] == "pass", sent)
check("stop reasons are recorded", r["stop_reasons"] == ["tool_use", "tool_use"], r["stop_reasons"])

# 2. failed simulation is reported as an error
c = FakeClient([[("tool", "run_simulation", {"code": "def run(:"})], [("tool", "refuse", {"reason_code": "overdraw", "explanation": "x"})]])
r, g = grade("imp-01", c)
tr = c.calls[1]["messages"][-1]["content"][0]
check("failed simulation -> is_error tool_result", tr["is_error"] and tr["content"].startswith("Simulation failed"), tr)
check("refuse with right code -> pass", g["outcome"] == "pass", g)

# 3. outcomes
c = FakeClient([[("tool", "submit_protocol", {"code": GOOD})]])
check("submit on a refuse case -> silent_guess", grade("imp-01", c)[1]["outcome"] == "silent_guess")
c = FakeClient([[("tool", "refuse", {"reason_code": "invalid_volume", "explanation": "x"})]])
check("wrong reason code -> wrong_reason", grade("imp-01", c)[1]["outcome"] == "wrong_reason")
c = FakeClient([[("tool", "refuse", {"reason_code": "overdraw", "explanation": "x"})]])
check("refuse on a submit case -> over_caution", grade("std-01", c)[1]["outcome"] == "over_caution")

# 4. never finishing
c = FakeClient([[("text", "Let me think about this.")]])
r, g = grade("std-01", c, max_turns=3)
check("text-only replies -> no_decision after the cap", g["outcome"] == "no_decision" and r["turns"] == 3, (g, r["turns"]))
check("model is nudged to finish", any(m["role"] == "user" and isinstance(m["content"], str)
                                       and "must finish" in m["content"] for m in r["transcript"]))
c = FakeClient([[("tool", "run_simulation", {"code": GOOD})]])
r, g = grade("std-01", c, max_turns=4)
check("endless simulating -> no_decision", g["outcome"] == "no_decision" and r["turns"] == 4, (g, r["turns"]))

# 5. a terminal tool wins even if listed after another tool in the same reply
c = FakeClient([[("tool", "run_simulation", {"code": GOOD}), ("tool", "submit_protocol", {"code": GOOD})]])
r, g = grade("std-01", c)
check("terminal call in a mixed reply ends the run", g["outcome"] == "pass" and r["turns"] == 1, (g, r["turns"]))

# 6. token usage is added up
c = FakeClient([[("tool", "run_simulation", {"code": GOOD})], [("tool", "submit_protocol", {"code": GOOD})]])
r, _ = grade("std-01", c)
check("token usage is summed", r["usage"] == {"input_tokens": 200, "output_tokens": 40}, r["usage"])

# 7. run_all: saving, resuming, summary
with tempfile.TemporaryDirectory() as d:
    out = os.path.join(d, "t.jsonl")
    ids = {"std-01", "imp-01", "ambi-01"}
    c = FakeClient([[("tool", "refuse", {"reason_code": "overdraw", "explanation": "x"})]])
    logs = []
    run_eval.run_all(c, "fake-model", CASES, 2, out, ids=ids, log=logs.append)
    lines = [json.loads(x) for x in open(out)]
    check("run_all saves one line per run (3 cases x 2 runs)", len(lines) == 6, len(lines))
    n_calls = len(c.calls)
    run_eval.run_all(c, "fake-model", CASES, 2, out, ids=ids, log=logs.append)
    check("re-running skips saved runs", len(c.calls) == n_calls and len(open(out).readlines()) == 6)
    run_eval.run_all(c, "fake-model", CASES, 3, out, ids=ids, log=logs.append)
    check("asking for more runs only does the missing ones", len(open(out).readlines()) == 9)
    summary = []
    run_eval.summarise(out, CASES, log=summary.append)
    text = "\n".join(summary)
    check("summary reports the headline metrics", "silent guesses" in text and "over-caution" in text
          and "Cases where runs disagree" in text, text[:200])
    check("summary counts are right (std-01 over-cautious 3/3)", "over-caution (refused on a submit case):      3/3" in text, text)

    class Boom:
        messages = NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("network down")))

    out2 = os.path.join(d, "e.jsonl")
    fails = run_eval.run_all(Boom, "m", CASES, 1, out2, ids={"std-01"}, log=logs.append)
    check("API errors are counted and not saved", fails == 1 and not os.path.exists(out2))

print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks behaved as expected")
sys.exit(0 if all(RESULTS) else 1)
