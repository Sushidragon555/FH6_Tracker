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


def test_parse_balance_number_only():
    # A tight capture box around just the number has no "CR"/"Credits" keyword.
    assert module.parse_balance_number_only('1,050,000') == 1050000
    assert module.parse_balance_number_only('$1,050,000') == 1050000
    assert module.parse_balance_number_only('1050000') == 1050000
    assert module.parse_balance_number_only('1.05M') == 1050000
    # Keyword parsing still returns None here, proving the number-only fallback is needed.
    assert module.parse_credit_balance_from_text('1,050,000') is None
    assert module.parse_balance_number_only('') is None
    assert module.parse_balance_number_only('no digits here') is None
