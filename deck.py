"""Fixed deck for the eval. Numbers verified with opentrons_simulate (see verify_deck.py)."""

ROBOT_TYPE = "Flex"
API_LEVEL = "2.19"

DECK = {
    "A3": "trash bin",
    "D1": "opentrons_flex_96_tiprack_1000ul",
    "D2": "corning_96_wellplate_360ul_flat",   # source plate
    "D3": "corning_96_wellplate_360ul_flat",   # destination plate
    "C2": "nest_12_reservoir_15ml",            # diluent reservoir
}
PIPETTE = "flex_1channel_1000"
PIPETTE_MOUNT = "right"

PIPETTE_MIN_UL = 5.0
PIPETTE_MAX_UL = 1000.0
PLATE_WELL_MAX_UL = 360.0
RESERVOIR_WELL_MAX_UL = 15000.0
PIPETTE_MIN_UL = 5.0
PIPETTE_MAX_UL = 1000.0
PLATE_WELL_MAX_UL = 360.0
RESERVOIR_WELL_MAX_UL = 15000.0
