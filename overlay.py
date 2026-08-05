"""Standalone live overlay for FH6 Tracker.

Shows only the transparent, click-through telemetry overlay over the Forza
window — no GUI window. Launches the telemetry tracker (auto_log.py) as a
subprocess, polls the live snapshot it writes, and follows the game window.

Launch:
    pythonw overlay.py        (no console)
    python  overlay.py        (console, for debugging)

Quit:
    Press Ctrl+Alt+Q anywhere.
    F6 still toggles race recording (handled by the tracker subprocess).
"""
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

from fh6_gui import _get_virtual_screen, _user32, get_forza_window_rect, get_monitor_rect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RACES_DIR = os.path.join(BASE_DIR, "races")
LIVE_RACE_FILE = os.path.join(RACES_DIR, ".live_race.json")
AUTO_LOG_PATH = os.path.join(BASE_DIR, "auto_log.py")
OVERLAY_BG = "#ff00fe"  # chroma-key color: every pixel of this color is transparent on Windows
W, H, MARGIN = 330, 226, 8

# Quit hotkey: Ctrl+Alt+Q
WM_HOTKEY = 0x0312
HOTKEY_ID_QUIT = 1
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
VK_Q = 0x51

CORNER_FN = {
    "TL": lambda l, t, r, b, w, h, m: (l + m, t + m),
    "TR": lambda l, t, r, b, w, h, m: (r - w - m, t + m),
    "ML": lambda l, t, r, b, w, h, m: (l + m, t + int((b - t) * 0.40) - h // 2),
    "BL": lambda l, t, r, b, w, h, m: (l + m, b - h - m),
    "BR": lambda l, t, r, b, w, h, m: (r - w - m, b - h - m),
}

# 64-bit pointer-size correctness for the Win32 calls made here (the same
# lesson as fh6_gui/auto_log): without argtypes ctypes truncates pointers to
# 32 bits, which crashes the hotkey thread / click-through styling on x64.
_user32.RegisterHotKey.restype = ctypes.c_int
_user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_user32.GetMessageW.restype = ctypes.c_int
_user32.GetMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]


def _load_corner():
    try:
        with open(os.path.join(BASE_DIR, "gui_settings.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        corner = data.get("race_overlay_corner", "TL")
        return corner if corner in CORNER_FN else "TL"
    except Exception:
        return "TL"


class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("FH6 Overlay")
        self.corner = _load_corner()
        self.tracker = None
        self._win_rect_cache = None
        self._win_rect_time = 0.0
        self._place_count = 0
        self._create_overlay()
        self._create_control_panel()
        self._start_tracker()
        self._register_quit_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(500, self._loop)

    # ------------------------------------------------------------------ setup
    def _start_tracker(self):
        os.makedirs(RACES_DIR, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            log = open(os.path.join(BASE_DIR, "tracker_overlay.log"), "w", encoding="utf-8", errors="replace")
        except OSError:
            log = subprocess.DEVNULL
        try:
            self.tracker = subprocess.Popen(
                [sys.executable, AUTO_LOG_PATH],
                cwd=BASE_DIR,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:
            self._show_fatal(f"Could not start the tracker:\n{exc}")

    def _register_quit_hotkey(self):
        if os.name != "nt":
            return

        def _listener():
            try:
                _user32.RegisterHotKey(None, HOTKEY_ID_QUIT, MOD_CONTROL | MOD_ALT, VK_Q)
                msg = ctypes.wintypes.MSG()
                while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_QUIT:
                        try:
                            self.root.after(0, self._quit)
                        except Exception:
                            pass
            except Exception:
                pass

        threading.Thread(target=_listener, daemon=True).start()

    def _show_fatal(self, text):
        import tkinter.messagebox as mb
        mb.showerror("FH6 Overlay", text)
        self._quit()

    # -------------------------------------------------------- control panel
    def _create_control_panel(self):
        """Small always-on-top button bar under the overlay.

        The overlay itself is click-through, so this separate panel is the
        clickable escape hatch: reopen the GUI, or quit the overlay.
        """
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.configure(bg="#1a1a1a")
        panel.attributes("-alpha", 0.92)
        tk.Button(
            panel, text="Open GUI", command=self._open_gui,
            bg="#2ea043", fg="white", activebackground="#3fb950", activeforeground="white",
            relief="flat", bd=0, padx=10, pady=4,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(6, 3), pady=4)
        tk.Button(
            panel, text="Quit", command=self._quit,
            bg="#4a4a4a", fg="white", activebackground="#666666", activeforeground="white",
            relief="flat", bd=0, padx=10, pady=4,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(3, 6), pady=4)
        self._panel = panel
        self._position_panel()

    def _position_panel(self):
        try:
            x = self._overlay.winfo_rootx()
            y = self._overlay.winfo_rooty() + H + 6
            self._panel.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _open_gui(self):
        """Stop the overlay, reset overlay-only mode, and relaunch the GUI."""
        try:
            path = os.path.join(BASE_DIR, "gui_settings.json")
            data = {}
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            data["race_overlay_standalone"] = False
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=4)
        except Exception:
            pass
        self._stop_tracker()
        try:
            if os.path.exists(LIVE_RACE_FILE):
                os.remove(LIVE_RACE_FILE)
        except OSError:
            pass
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        gui_script = os.path.join(BASE_DIR, "fh6_gui.py")
        if os.path.exists(gui_script):
            try:
                subprocess.Popen([pythonw, gui_script], cwd=BASE_DIR,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ overlay UI
    def _create_overlay(self):
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg=OVERLAY_BG)
        try:
            overlay.attributes("-transparentcolor", OVERLAY_BG)
        except tk.TclError:
            overlay.attributes("-alpha", 0.75)
        canvas = tk.Canvas(overlay, width=W, height=H, bg=OVERLAY_BG, highlightthickness=0)
        canvas.pack()
        self._overlay = overlay
        self._overlay_canvas = canvas
        self._overlay_items = self._build_overlay_canvas(canvas)
        self._place()
        self._make_click_through(overlay)

    def _ov_text(self, canvas, items, key, x, y, text, font, fill, anchor="nw"):
        shadow = canvas.create_text(x + 1, y + 1, anchor=anchor, text=text, fill="#000000", font=font)
        main = canvas.create_text(x, y, anchor=anchor, text=text, fill=fill, font=font)
        items[key] = (shadow, main)

    def _build_overlay_canvas(self, canvas):
        items = {}
        canvas.create_oval(8, 6, 16, 14, fill="#ff3333", outline="")  # LIVE dot
        self._ov_text(canvas, items, "car", 22, 5, "Waiting for telemetry...", ("Segoe UI", 10, "bold"), "#ffffff")
        self._ov_text(canvas, items, "time", 322, 5, "0:00", ("Segoe UI", 10, "bold"), "#ffffff", anchor="ne")
        self._ov_text(canvas, items, "gear", 322, 24, "-", ("Segoe UI", 30, "bold"), "#58a6ff", anchor="ne")
        self._ov_text(canvas, items, "rpm", 322, 70, "RPM -", ("Segoe UI", 10, "bold"), "#dddddd", anchor="ne")

        bar_y = {}
        bars = [
            ("thr", "THR", "#3fb950"),
            ("brk", "BRK", "#f85149"),
            ("str", "STR", "#58a6ff"),
            ("hbrk", "HBK", "#d29922"),
        ]
        for idx, (key, label, color) in enumerate(bars):
            y = 122 + idx * 28
            bar_y[key] = y
            self._ov_text(canvas, items, key + "_label", 10, y - 15, label, ("Segoe UI", 8, "bold"), color)
            canvas.create_rectangle(10, y, 150, y + 12, fill="#101010", outline="#3f3f3f")
            items[key + "_fill"] = canvas.create_rectangle(10, y, 10, y + 12, fill=color, outline="")
            items[key + "_val"] = canvas.create_text(150, y - 15, anchor="ne", text="", fill="#eeeeee", font=("Segoe UI", 8, "bold"))
        items["bar_y"] = bar_y
        items["spark"] = canvas.create_line(196, 122, 322, 122, fill="#58a6ff", width=2)
        return items

    def _overlay_set(self, key, text):
        ids = self._overlay_items.get(key)
        if not ids:
            return
        if isinstance(ids, int):
            self._overlay_canvas.itemconfig(ids, text=text)
        else:
            for item in ids:
                self._overlay_canvas.itemconfig(item, text=text)

    # ---------------------------------------------------------------- loop
    def _loop(self):
        try:
            self._update_overlay()
        except Exception:
            pass
        self._place_count += 1
        if self._place_count >= 10:  # re-anchor to the game window ~every 5s
            self._place_count = 0
            try:
                self._place()
            except Exception:
                pass
        try:
            self._position_panel()
        except Exception:
            pass
        self.root.after(500, self._loop)

    def _update_overlay(self):
        data = None
        try:
            if os.path.exists(LIVE_RACE_FILE):
                with open(LIVE_RACE_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
        except (OSError, ValueError, TypeError):
            data = None
        if not data or not data.get("sample"):
            self._overlay_set("car", "Waiting for telemetry...")
            self._overlay_set("time", "")
            self._overlay_set("gear", "-")
            self._overlay_set("rpm", "RPM -")
            for key in ("thr", "brk", "str", "hbrk"):
                self._overlay_set(key + "_val", "")
                y = self._overlay_items["bar_y"][key]
                self._overlay_canvas.coords(self._overlay_items[key + "_fill"], 10, y, 10, y + 12)
            self._overlay_canvas.coords(self._overlay_items["spark"], 196, 122, 322, 122)
            return

        sample = data.get("sample") or {}
        recent = data.get("recent") or []
        recording = bool(data.get("recording"))
        self._overlay_set("car", (data.get("car_name") or "Unknown")[:26])
        if recording:
            elapsed = float(data.get("elapsed", 0) or 0)
            m, s = divmod(int(elapsed), 60)
            self._overlay_set("time", f"{m}:{s:02d}")
        else:
            self._overlay_set("time", "LIVE")
        gear = sample.get("gear")
        self._overlay_set("gear", str(gear) if gear is not None else "-")
        self._overlay_set("rpm", "RPM " + f"{int(sample.get('rpm', 0) or 0):,}")

        for key, attr in (("thr", "thr"), ("brk", "brk"), ("str", "str"), ("hbrk", "hbrk")):
            value = max(0.0, min(1.0, float(sample.get(attr, 0) or 0)))
            self._overlay_set(key + "_val", f"{int(round(value * 100))}%")
            y = self._overlay_items["bar_y"][key]
            self._overlay_canvas.coords(self._overlay_items[key + "_fill"], 10, y, 10 + int(value * 140), y + 12)

        speeds = [s.get("spd", 0) or 0 for s in recent]
        if len(speeds) >= 2:
            x0, y0, x1, y1 = 196, 122, 322, 218
            high = max(60.0, max(speeds))
            n = len(speeds)
            pts = []
            for i, spd in enumerate(speeds):
                px = x0 + (x1 - x0) * (i / (n - 1))
                py = y1 - (y1 - y0) * (min(spd, high) / high)
                pts.append(px)
                pts.append(py)
            self._overlay_canvas.coords(self._overlay_items["spark"], *pts)

    # ------------------------------------------------------------- placement
    def _place(self):
        w, h, margin = W, H, MARGIN
        rect = self._forza_rect()
        monitor = None
        if rect:
            monitor = get_monitor_rect((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        if not monitor:
            monitor = _get_virtual_screen()
        if not monitor:
            monitor = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

        left, top, right, bottom = monitor
        x, y = CORNER_FN.get(self.corner, CORNER_FN["TL"])(left, top, right, bottom, w, h, margin)
        x = max(left + margin, min(x, right - w - margin))
        y = max(top + margin, min(y, bottom - h - margin))

        if os.name == "nt":
            self._set_overlay_pos(self._overlay, int(x), int(y), w, h)
        else:
            self._overlay.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _forza_rect(self):
        now = time.monotonic()
        if now - self._win_rect_time >= 5.0:
            self._win_rect_cache = get_forza_window_rect()
            self._win_rect_time = now
        return self._win_rect_cache

    def _set_overlay_pos(self, overlay, x, y, w, h):
        try:
            hwnd = _user32.GetParent(overlay.winfo_id())
            if not hwnd:
                hwnd = overlay.winfo_id()
            # HWND_TOPMOST = -1; SWP_NOACTIVATE | SWP_SHOWWINDOW
            _user32.SetWindowPos(hwnd, -1, x, y, w, h, 0x0010 | 0x0040)
        except Exception:
            try:
                overlay.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                pass

    def _make_click_through(self, window):
        if os.name != "nt":
            return
        try:
            hwnd = _user32.GetParent(window.winfo_id())
            ex_style = _user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            _user32.SetWindowLongW(hwnd, -20, ex_style | 0x00000020 | 0x00080000)  # WS_EX_TRANSPARENT | WS_EX_LAYERED
        except Exception:
            pass

    # ------------------------------------------------------------------ quit
    def _stop_tracker(self):
        if self.tracker and self.tracker.poll() is None:
            try:
                self.tracker.terminate()
            except Exception:
                pass
        self.tracker = None

    def _quit(self):
        self._stop_tracker()
        try:
            if os.path.exists(LIVE_RACE_FILE):
                os.remove(LIVE_RACE_FILE)
        except OSError:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    OverlayApp()
    tk.mainloop()


if __name__ == "__main__":
    main()
