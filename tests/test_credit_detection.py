from pathlib import Path
import importlib.util

MODULE_PATH = Path(__file__).resolve().parents[1] / 'fh6_gui.py'
spec = importlib.util.spec_from_file_location('fh6_gui', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_detect_credit_change_from_text():
    assert module.detect_credit_change_from_text('You earned 12,500 credits') == 12500
    assert module.detect_credit_change_from_text('You earned 12.5k credits') == 12500
    assert module.detect_credit_change_from_text('You spent 1,200 credits') == -1200
    assert module.detect_credit_change_from_text('Credits: 507,324') == 507324
