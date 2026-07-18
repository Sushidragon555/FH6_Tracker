import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from import_owned_cars import parse_owned_cars_text


def test_parse_owned_cars_input():
    parsed = parse_owned_cars_text('Car One\nCar Two\n,Car Three')
    assert parsed == ['Car One', 'Car Two', 'Car Three']
