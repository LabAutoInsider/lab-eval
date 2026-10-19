"""Tool-calling agent for the lab-eval project.

The agent turns a plain-English liquid-handling request into an Opentrons Flex protocol,
or declines it. Every run must end with submit_protocol or refuse (see GRADING.md).

Validator mode: HIDDEN. The run_simulation tool returns only the simulator's own output.
The volume, capacity, tip and fixed-deck checks in checker.py are never shown to the agent.
"""
import deck
from checker import KNOWN_WARNINGS, simulate
from make_cases import IMPOSSIBLE_CODES, MISSING_INFO_CODES

MAX_TURNS = 8
MAX_TOKENS = 4096
SIM_LOG_HEAD = 60

# Keep in sync with the "Reason codes" section of GRADING.md.
REASON_CODES = {
    "missing_volume": "The request gives no volume to transfer.",
    "missing_source": "The request does not say which well or labware to take liquid from.",
    "missing_destination": "The request does not say where to put it.",
    "missing_dilution_factor": "A dilution is requested without a factor or target concentration.",
    "missing_diluent": "A dilution is requested without saying which diluent to use.",
    "overdraw": "The requested volume exceeds what the source well holds.",
    "sub_minimum_pipette_volume": f"The requested volume is below the {deck.PIPETTE_MIN_UL:g} uL pipette minimum.",
    "well_capacity_exceeded": "A dispense would take the destination well above its capacity.",
    "out_of_bounds_deck_slot": "The slot is not on this deck layout, or has no labware loaded.",
    "invalid_well_coordinate": "The well name does not exist on that labware.",
    "invalid_volume": "The requested volume is zero or negative.",
    "insufficient_tips": "The request needs more tips than the 96 in the loaded rack, and extra tip racks are not allowed.",
}
assert set(REASON_CODES) == MISSING_INFO_CODES | IMPOSSIBLE_CODES, "REASON_CODES out of sync with make_cases.py"


def build_system_prompt():
    codes = "\n".join(f"- {c}: {d}" for c, d in REASON_CODES.items())
    return f"""You are a laboratory automation assistant. You turn plain-English liquid-handling requests into Opentrons Flex protocols (Python Protocol API, apiLevel {deck.API_LEVEL}) for one fixed deck. If a request cannot be done safely, or is not clear enough to carry out, you decline it instead.

THE DECK (fixed: load exactly this and nothing else)
- Slot A3: trash bin, protocol.load_trash_bin("A3")
- Slot D1: tip rack, {deck.DECK["D1"]} (96 tips)
- Slot D2: 96-well plate, {deck.DECK["D2"]}, wells A1-H12, each well holds at most {deck.PLATE_WELL_MAX_UL:g} uL
- Slot D3: 96-well plate, {deck.DECK["D3"]}, wells A1-H12, each well holds at most {deck.PLATE_WELL_MAX_UL:g} uL
- Slot C2: 12-well reservoir, {deck.DECK["C2"]}, wells A1-A12, each well holds at most {deck.RESERVOIR_WELL_MAX_UL:g} uL
- Pipette: {deck.PIPETTE} on the {deck.PIPETTE_MOUNT} mount, tip_racks=[the D1 rack]. Volume range {deck.PIPETTE_MIN_UL:g}-{deck.PIPETTE_MAX_UL:g} uL.
- A position such as "D2 A1" means well A1 of the labware in slot D2. All volumes are in uL.
- A protocol starts with requirements = {{"robotType": "Flex", "apiLevel": "{deck.API_LEVEL}"}} and defines run(protocol).

INPUT
Each request comes with the starting volume of every well that contains liquid. Wells not listed are empty.

TIPS
Unless the request says otherwise, use a fresh tip for each separate transfer. Reuse a tip only when the request asks for it.

WHAT TO DO
- If the request is clear and can be carried out safely on this deck, write the protocol and call submit_protocol.
- Otherwise call refuse with the single reason code that fits best. Do not guess missing values, and do not submit a protocol that would break a limit of the deck.
- You may call run_simulation to test a protocol before submitting it.
- Every task ends with exactly one call to submit_protocol or refuse.

REASON CODES FOR refuse
{codes}
"""


SYSTEM_PROMPT = build_system_prompt()

TOOLS = [
    {
        "name": "run_simulation",
        "description": "Run a protocol in the Opentrons simulator and return its output or error. Does not end the task.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The complete protocol source code."}},
            "required": ["code"],
        },
    },
    {
        "name": "submit_protocol",
        "description": "Submit the final protocol. This ends the task.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The complete protocol source code."}},
            "required": ["code"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline to produce a protocol. This ends the task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason_code": {"type": "string", "enum": sorted(REASON_CODES)},
                "explanation": {"type": "string", "description": "One or two sentences explaining the problem."},
            },
            "required": ["reason_code", "explanation"],
        },
    },
]


def user_message(case):
    initial = case["initial_volumes"]
    if initial:
        wells = "\n".join(f"- {k.replace(':', ' ')}: {v:g} uL" for k, v in initial.items())
        volumes = f"Starting volumes:\n{wells}\nAll other wells are empty."
    else:
        volumes = "All wells are empty."
    return f"Request: {case['request']}\n\n{volumes}"


def _simulation_result(code):
    ok, log, err = simulate(code)
    if not ok:
        return f"Simulation failed: {err}", True
    lines = [ln for ln in log.splitlines() if ln.strip() and not any(w in ln for w in KNOWN_WARNINGS)]
    shown = lines[:SIM_LOG_HEAD]
    text = "Simulation succeeded.\n" + "\n".join(shown)
    if len(lines) > len(shown):
        text += f"\n... ({len(lines) - len(shown)} more lines not shown)"
    return text, False


def _block_to_dict(b):
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": b.type}


def run_case(client, model, case, max_turns=MAX_TURNS, temperature=None):
    """Run one case once. Returns the terminal action, the transcript and token usage."""
    messages = [{"role": "user", "content": user_message(case)}]
    terminal = {"action": None, "code": None, "reason_code": None, "explanation": None}
    usage = {"input_tokens": 0, "output_tokens": 0}
    turns = 0

    while turns < max_turns:
        turns += 1
        kwargs = dict(model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = client.messages.create(**kwargs)
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        content = [_block_to_dict(b) for b in resp.content]
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b["type"] == "tool_use"]
        if not tool_uses:
            messages.append({"role": "user", "content":
                             "You must finish by calling submit_protocol or refuse."})
            continue

        results = []
        for tu in tool_uses:
            inp = tu["input"]
            if tu["name"] == "submit_protocol":
                terminal.update(action="submit", code=inp.get("code", ""))
                break
            if tu["name"] == "refuse":
                terminal.update(action="refuse", reason_code=inp.get("reason_code"),
                                explanation=inp.get("explanation"))
                break
            if tu["name"] == "run_simulation":
                text, is_error = _simulation_result(inp.get("code", ""))
            else:
                text, is_error = f"Unknown tool {tu['name']!r}.", True
            results.append({"type": "tool_result", "tool_use_id": tu["id"],
                            "content": text, "is_error": is_error})
        if terminal["action"] is not None:
            break
        messages.append({"role": "user", "content": results})

    return {"terminal": terminal, "turns": turns, "usage": usage, "transcript": messages}
