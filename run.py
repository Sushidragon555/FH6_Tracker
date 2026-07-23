"""Entry point for the PyInstaller-built .exe.

When launched normally, runs the GUI.
When launched with --tracker, runs the telemetry tracker subprocess.
"""
import sys
import os


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    if "--tracker" in sys.argv:
        base = _base_dir()
        tracker_path = os.path.join(base, "auto_log.py")
        if os.path.exists(tracker_path):
            import runpy
            sys.argv = [tracker_path]
            os.chdir(base)
            runpy.run_path(tracker_path, run_name="__main__")
        else:
            print(f"Tracker not found: {tracker_path}")
            sys.exit(1)
    else:
        import fh6_gui
        fh6_gui_app = fh6_gui.FH6TrackerGUI()
        fh6_gui_app.mainloop()
