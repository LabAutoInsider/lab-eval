requirements = {"robotType": "Flex", "apiLevel": "2.19"}

def run(protocol):
    protocol.load_trash_bin("A3")
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")
    src = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    dst = protocol.load_labware("corning_96_wellplate_360ul_flat", "D3")
    res = protocol.load_labware("nest_12_reservoir_15ml", "C2")
    pip = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])
    protocol.comment(f"pipette min {pip.min_volume} max {pip.max_volume}")
    protocol.comment(f"plate well max {src['A1'].max_volume}")
    protocol.comment(f"reservoir well max {res['A1'].max_volume}")
