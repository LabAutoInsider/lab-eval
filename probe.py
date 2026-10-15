import subprocess

HEAD = '''requirements = {"robotType": "Flex", "apiLevel": "2.19"}
def run(protocol):
    protocol.load_trash_bin("A3")
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")
    src = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    dst = protocol.load_labware("corning_96_wellplate_360ul_flat", "D3")
    pip = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])
'''

PROBES = {
    "below_min_2ul": 'pip.transfer(2, src["A1"], dst["A1"])',
    "above_well_400ul": 'pip.pick_up_tip(); pip.aspirate(400, src["A1"]); pip.dispense(400, dst["A1"]); pip.drop_tip()',
    "above_pipette_1200ul": 'pip.pick_up_tip(); pip.aspirate(1200, src["A1"]); pip.drop_tip()',
    "no_tip": 'pip.aspirate(50, src["A1"])',
    "bad_well_A13": 'pip.transfer(50, src["A13"], dst["A1"])',
    "overdraw_empty": 'pip.pick_up_tip(); pip.aspirate(300, src["A1"]); pip.aspirate(300, src["A1"]); pip.drop_tip()',
}

for name, body in PROBES.items():
    path = f"/tmp/{name}.py"
    open(path, "w").write(HEAD + "    " + body + "\n")
    r = subprocess.run(["opentrons_simulate", path], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    last = out[-1] if out else ""
    rejected = r.returncode != 0 or "Error" in (r.stdout + r.stderr)
    print(f"{name}: {'REJECTED' if rejected else 'ACCEPTED'} | {last[:110]}")
