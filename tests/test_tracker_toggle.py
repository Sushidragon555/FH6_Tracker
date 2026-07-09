import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'fh6_gui.py'
spec = importlib.util.spec_from_file_location('fh6_gui', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TrackerToggleTests(unittest.TestCase):
    def test_tracker_button_label(self):
        self.assertEqual(module.tracker_button_label(False), 'Start Tracker')
        self.assertEqual(module.tracker_button_label(True), 'Stop Tracker')

    def test_forza_process_detection(self):
        self.assertTrue(module.is_forza_process_name('ForzaHorizon6.exe'))
        self.assertTrue(module.is_forza_process_name('forzahorizon5.exe'))
        self.assertTrue(module.is_forza_process_name('forzahorizon4.exe'))
        self.assertFalse(module.is_forza_process_name('notepad.exe'))


if __name__ == '__main__':
    unittest.main()
