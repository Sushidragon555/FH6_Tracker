import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'fh6_gui.py'
spec = importlib.util.spec_from_file_location('fh6_gui', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_parse_owned_cars_input():
    parsed = module.parse_owned_cars_input('Car One\nCar Two\n,Car Three')
    assert parsed == ['Car One', 'Car Two', 'Car Three']
