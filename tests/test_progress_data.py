import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'fh6_gui.py'
spec = importlib.util.spec_from_file_location('fh6_gui', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_progress_data_excludes_owned_cars():
    master_db = {'Car A': 1000, 'Car B': 2000, 'Car C': 3000}
    owned = ['Car B']
    missing, owned_cars, total_cost = module.build_progress_data(master_db, owned)
    assert missing == [('Car A', 1000), ('Car C', 3000)]
    assert owned_cars == [('Car B', 2000)]
    assert total_cost == 4000


def test_progress_data_filters_by_manufacturer_and_year():
    master_db = {
        '2023 Acura Integra A-Spec': 1000,
        '2022 Acura NSX Type S': 2000,
        '1995 BMW M5': 3000,
    }
    missing, owned_cars, total_cost = module.build_progress_data(master_db, [], manufacturer='Acura', year='2023')
    assert missing == [('2023 Acura Integra A-Spec', 1000)]
    assert owned_cars == []
    assert total_cost == 1000
