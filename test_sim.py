import io
import opentrons.simulate

protocol_str = '''
from opentrons import protocol_api

requirements = {
    "robotType": "Flex",
    "apiLevel": "2.18"
}

def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")
    res = protocol.load_labware("nest_12_reservoir_15ml", "C2")
    p1 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    p2 = protocol.load_labware("corning_96_wellplate_360ul_flat", "D3")
    pip = protocol.load_instrument("flex_1channel_1000", "left", tip_racks=[tips])

    pip.pick_up_tip()
    pip.aspirate(100, p1["A1"])
    pip.dispense(100, p2["A1"])
    pip.drop_tip()
'''

runlog, _ = opentrons.simulate.simulate(io.StringIO(protocol_str))
for step in runlog:
    print(step["payload"]["text"])
