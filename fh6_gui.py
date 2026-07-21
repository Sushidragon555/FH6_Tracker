import csv
import ctypes
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import tkinter as tk
import webbrowser
import zipfile
from collections import Counter
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import car_lookup


# =============================================================================
# LOGGING SETUP
# =============================================================================

APP_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fh6_tracker.log")
logger = logging.getLogger("fh6_tracker")
try:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(APP_LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
except Exception:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
from import_owned_cars import load_owned_cars_from_file, parse_owned_cars_text

try:
    pyautogui = importlib.import_module("pyautogui")
    pytesseract = importlib.import_module("pytesseract")
    ImageGrab = importlib.import_module("PIL.ImageGrab")
    Image = importlib.import_module("PIL.Image")
    ImageOps = importlib.import_module("PIL.ImageOps")
    ImageTk = importlib.import_module("PIL.ImageTk")
except Exception:  # pragma: no cover - optional OCR dependencies
    pyautogui = None
    pytesseract = None
    ImageGrab = None
    Image = None
    ImageOps = None
    ImageTk = None

# =============================================================================
# PATHS & CONSTANTS
# =============================================================================

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_settings.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(BASE_DIR, "ocr_debug")
AUTO_LOG_PATH = os.path.join(BASE_DIR, "auto_log.py")
OWNED_FILE = os.path.join(BASE_DIR, "owned_cars.json")
MASTER_FILE = os.path.join(BASE_DIR, "fh6_master_list.json")
LOG_FILE = os.path.join(BASE_DIR, "telemetry_log.csv")
SESSION_STATE_FILE = os.path.join(BASE_DIR, "session_state.json")
METHODS_FILE = os.path.join(BASE_DIR, "methods_history.json")
CREDIT_TRANSACTIONS_FILE = os.path.join(BASE_DIR, "credit_transactions.json")
METHOD_NAMES = [
    "Wheelspins",
    "Super Wheelspins",
    "Road Racing",
    "Street Racing",
    "Cross Country",
    "Drag Racing",
    "Drift",
    "PR Stunts",
    "Photo Mode",
    "Barn Finds",
    "Other",
]

EXPORT_DIR = os.path.join(BASE_DIR, "exports")


# =============================================================================
# TOP-LEVEL FUNCTIONS — Helpers & Credit Parsing
# =============================================================================

def format_credits(amount):
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return str(amount)


load_json_file = car_lookup.load_json_file


def parse_credit_number(value):
    cleaned = value.replace(",", "").replace(" ", "")
    # Check for a k/M suffix first — dots before a suffix are legitimate decimals
    # (e.g. "12.5k" → 12,500).  Without a suffix, dots are OCR-misread thousands
    # separators (e.g. "17.314" → 17,314).
    suffix = None
    if cleaned and cleaned[-1].lower() in ("k", "m"):
        suffix = cleaned[-1].lower()
        cleaned = cleaned[:-1]
    if not suffix:
        # No suffix → strip all dots (thousands-sep OCR noise)
        cleaned = cleaned.replace(".", "")
        if not cleaned.isdigit():
            return None
        return int(cleaned)
    # With a suffix → keep the dot as a real decimal
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)", cleaned)
    if not match:
        return None
    number = float(match.group(1))
    if suffix == "k":
        number *= 1000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def parse_credit_balance_from_text(text):

    logger.debug("OCR raw text: '%s'", text)

    if not text:
        return None

    clean_text = text.lower().replace('.', ',').replace('|', ',').strip()

    patterns = [
        r"\b(?:balance|credits? balance|current credits?|credit balance)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
        r"\b(?:credits?|cr)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
        r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean_text, flags=re.IGNORECASE)
        if match:
            found_str = match.group(1)
            result = parse_credit_number(found_str)
            logger.debug("OCR match found: string='%s' -> Value=%s", found_str, result)
            return result
    return None


def parse_balance_number_only(text):
    """Return the most plausible standalone number in ``text``.

    Used when the user has boxed a tight region around just the credit number, so the
    captured text is often only digits (e.g. "1,050,000" or "1.05M") with no
    "CR"/"Credits" keyword. We pick the largest number-like token, which for a boxed
    balance is the balance itself.
    """
    if not text:
        return None
    best = None
    for match in re.finditer(r"[0-9][0-9,\.]*\s*[kKmM]?", text):
        value = parse_credit_number(match.group(0).strip())
        if value is None:
            continue
        if best is None or value > best:
            best = value
    return best


def detect_credit_change_from_text(text, previous_balance=None):
    # Called by BOTH the full-screen popup scanner and the balance-region scanner.
    # Returns a positive int (gain), negative int (spend), 0 (no change), or None (no match).
    # The return value is used differently depending on which caller uses it:
    #   - Popup path (_ocr_and_parse_image): applies value immediately as a transaction
    #   - Balance path (detect_credit_popup_change): uses balance number directly,
    #     ignores this return value unless the balance-parsing path fails.
    if not text:
        return None

    lowered = text.lower()

    # BRANCH 1: Reward/gain keywords -> returns positive amount
    # Text like "You earned 50,000 CR" or "Won 12,500 credits"
    if re.search(r"\b(earned|received|won|reward(?:ed)?|added|gained)\b", lowered):
        amount = None
        for pattern in [
            r"\b(?:earned|received|won|reward(?:ed)?|added|gained)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
            r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
        ]:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                amount = parse_credit_number(match.group(1))
                break
        return amount if amount is not None else None

    # BRANCH 2: Spend keywords -> returns negative amount
    # Text like "You spent 1,200 CR" or "Paid 50,000 credits"
    if re.search(r"\b(spent|used|paid|bought|charged)\b", lowered):
        amount = None
        for pattern in [
            r"\b(?:spent|used|paid|bought|charged)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
            r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
        ]:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                amount = parse_credit_number(match.group(1))
                break
        return -amount if amount is not None else None

    # BRANCH 3: Explicit "balance" keyword -> computes delta from previous_balance
    # Text like "Credits balance: 1,050,000" or "Balance: 500,000"
    if re.search(r"\b(balance|credits? balance|current credits?)\b", lowered):
        amount = None
        for pattern in [
            r"\b(?:balance|credits? balance|current credits?)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
            r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
        ]:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                amount = parse_credit_number(match.group(1))
                break
        if amount is None:
            return None
        if previous_balance is None:
            return 0
        if amount != previous_balance:
            return amount - previous_balance
        return 0

    # BRANCH 4: Catch-all "credits"/"CR" match (no action keyword)
    # Text like "CREDITS 1,050,000" or "1,050,000 CR" from the HUD balance.
    # Without an action keyword, we treat this as a balance read, not a transaction.
    # We only return a delta if we have a previous_balance to compare against.
    if re.search(r"\bcredits?\b", lowered):
        amount = None
        for pattern in [
            r"\b(?:credit(?:s)?|cr)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
            r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
        ]:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                amount = parse_credit_number(match.group(1))
                break
        if amount is None:
            return None
        if previous_balance is None:
            return 0
        if amount != previous_balance:
            return amount - previous_balance
        return 0

    return None


# =============================================================================
# TOP-LEVEL FUNCTIONS — File I/O & Settings
# =============================================================================

def _safe_write_json(filepath, data):
    """Atomically write JSON to *filepath* via a temp file, with error handling."""
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4)
        os.replace(tmp, filepath)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to write %s: %s", filepath, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def save_settings(settings):
    _safe_write_json(SETTINGS_FILE, settings)


normalize_car_name = car_lookup.normalize_car_name


def extract_car_name_from_list_entry(entry):
    if not entry:
        return ""
    if " | " in entry:
        return entry.split(" | ", 1)[0].strip()
    return entry.strip()


def add_car_to_owned_list(owned, car_name):
    car_name = car_name.strip()
    if not car_name:
        return owned
    if car_name not in owned:
        owned.append(car_name)
    return owned


# =============================================================================
# TOP-LEVEL FUNCTIONS — Car / Collection Data
# =============================================================================

def build_progress_data(master_db, owned_names, manufacturer=None, year=None, search=None, min_value=None, max_value=None):
    normalized_owned = {normalize_car_name(name) for name in owned_names if name}
    missing = []
    owned_cars = []
    total_cost = 0
    search_terms = [term.lower() for term in (search or "").split() if term]
    min_value_num = parse_credit_number(str(min_value)) if min_value not in [None, ""] else None
    max_value_num = parse_credit_number(str(max_value)) if max_value not in [None, ""] else None

    for car, price in master_db.items():
        price_value = int(price)
        if min_value_num is not None and price_value < min_value_num:
            continue
        if max_value_num is not None and price_value > max_value_num:
            continue
        if manufacturer and manufacturer.lower() not in car.lower():
            continue
        if year is not None and year != "":
            match = re.match(r"^(\d{4})\s", car)
            if not match or match.group(1) != str(year):
                continue
        if search_terms:
            lowered = car.lower()
            if not all(term in lowered for term in search_terms):
                continue
        normalized_car = normalize_car_name(car)
        if normalized_car in normalized_owned:
            owned_cars.append((car, price))
        else:
            missing.append((car, price))
            total_cost += price_value
    missing.sort(key=lambda item: item[1], reverse=True)
    owned_cars.sort(key=lambda item: item[0])
    return missing, owned_cars, total_cost


def _find_tesseract():
    """Return the first valid tesseract.exe path, or None."""
    candidates = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'D:\Program Files\Tesseract-OCR\tesseract.exe',
        r'E:\Program Files\Tesseract-OCR\tesseract.exe',
        r'F:\Program Files\Tesseract-OCR\tesseract.exe',
        r'F:\Tesseract\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    try:
        import shutil
        path = shutil.which("tesseract")
        if path:
            return path
    except Exception:
        pass
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tesseract-OCR") as key:
                install_dir = winreg.QueryValueEx(key, "InstallDir")[0]
                tesseract_path = os.path.join(install_dir, "tesseract.exe")
                if os.path.isfile(tesseract_path):
                    return tesseract_path
        except Exception:
            pass
    return None


# =============================================================================
# TOP-LEVEL FUNCTIONS — Tesseract Detection & Settings Load
# =============================================================================

def load_settings():
    settings = load_json_file(SETTINGS_FILE, {})
    return {
        "auto_start_forza": settings.get("auto_start_forza", False),
        "launch_tracker_on_start": settings.get("launch_tracker_on_start", False),
        "theme": settings.get("theme", "light"),
        "credit_ocr_enabled": settings.get("credit_ocr_enabled", True),
        "credit_region": settings.get("credit_region"),
        "credit_region_forza_rect": settings.get("credit_region_forza_rect"),
        "credit_region_locked": settings.get("credit_region_locked", False),
        "ocr_debug_logging": settings.get("ocr_debug_logging", False),
        "payout_region": settings.get("payout_region"),
        "payout_region_forza_rect": settings.get("payout_region_forza_rect"),
        "payout_region_locked": settings.get("payout_region_locked", False),
        "performance_mode": settings.get("performance_mode", car_lookup.DEFAULT_PERFORMANCE_MODE),
        "tesseract_path": settings.get("tesseract_path") or _find_tesseract() or "",
    }


def tracker_button_label(running):
    return "Stop Tracker" if running else "Start Tracker"


# =============================================================================
# TOP-LEVEL FUNCTIONS — Forza Process / Window Detection
# =============================================================================

def is_forza_process_name(name):
    if not name:
        return False
    lowered = name.lower()
    return lowered.startswith("forzahorizon") or (lowered.startswith("forza") and "horizon" in lowered)


def get_running_process_names():
    if os.name == "nt":
        try:
            # CREATE_NO_WINDOW keeps this from flashing a console window every refresh.
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        names = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                parts = next(csv.reader([line]))
            except csv.Error:
                continue
            if parts:
                names.append(parts[0].strip().strip('"'))
        return names
    else:
        try:
            completed = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True, check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        return [name.strip() for name in completed.stdout.splitlines() if name.strip()]


def has_running_forza_process():
    return any(is_forza_process_name(name) for name in get_running_process_names())


def is_forza_window_title(title):
    if not title:
        return False
    lowered = title.lower()
    return "forza" in lowered or "horizon" in lowered


def get_visible_forza_window_titles():
    if os.name != "nt":
        return []
    try:
        user32 = ctypes.windll.user32
        enum_windows = user32.EnumWindows
        enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        titles = []

        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            if is_forza_window_title(title):
                titles.append(title)
            return True

        enum_windows(enum_windows_proc(callback), None)
        return titles
    except Exception:
        return []


def has_running_forza_window():
    return bool(get_visible_forza_window_titles())


def get_forza_window_rect():
    """Return (left, top, right, bottom) of the first visible Forza window, or None."""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        titles = get_visible_forza_window_titles()
        if not titles:
            return None
        hwnd = user32.FindWindowW(None, titles[0])
        if not hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


# =============================================================================
# =============================================================================
# FH6TrackerGUI — MAIN APPLICATION CLASS
# =============================================================================
# =============================================================================

class FH6TrackerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Forza Horizon 6 Tracker")
        self.geometry("1100x760")
        self.minsize(1000, 680)
        self.configure(padx=14, pady=14)

        self.tracker_process = None
        self.last_status = "Stopped"
        self.settings = load_settings()
        self.tracker_running = False
        self.session_state = self.load_session_state()
        self.last_credit_balance = None
        self.last_credit_scan_time = 0
        self._pending_balance = None
        self._pending_balance_count = 0
        self._credit_transactions = self._load_credit_transactions()
        self._recent_balances = []
        self._last_rollback_balance = None
        self._ocr_success_count = 0
        self._ocr_total_count = 0
        self._last_ocr_raw_text = ""
        self._last_ocr_scan_time = 0
        self._credit_rate_points = []
        self.forza_running_prev = None
        self._forza_running_cache = False
        self.known_owned = set(load_json_file(OWNED_FILE, {"owned": []}).get("owned", []))
        self.detected_car_id = None
        self._notice_after_id = None
        self._method_active = False
        self._method_name = None
        self._method_start_time = None
        self._method_start_credits = 0
        self._method_timer_after_id = None
        self._master_db_cache = load_json_file(MASTER_FILE, {})
        self._owned_cache = load_json_file(OWNED_FILE, {"owned": []}).get("owned", [])
        try:
            self._master_db_mtime = os.path.getmtime(MASTER_FILE) if os.path.exists(MASTER_FILE) else 0
        except (OSError, PermissionError):
            self._master_db_mtime = 0
        try:
            self._owned_mtime = os.path.getmtime(OWNED_FILE) if os.path.exists(OWNED_FILE) else 0
        except (OSError, PermissionError):
            self._owned_mtime = 0
        self._last_auto_added_car = None
        self._prev_thumb = None
        self._last_change_check_time = 0.0
        self._last_fullscreen_scan_time = 0.0
        self.style = ttk.Style(self)
        self.create_widgets()
        self.apply_theme(self.settings.get("theme", "light"))
        self.refresh_all()
        self._refresh_after_id = self.after(self._refresh_interval_ms(), self.refresh_loop)
        self.after(2000, self._check_forza_auto_start)
        self._session_timer_after_id = self.after(1000, self._update_session_timer)

        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.bind("<Control-n>", lambda e: self.add_car_var_entry.focus_set())
        self.bind("<Control-r>", lambda e: self.refresh_all())
        self.bind_all("<F5>", lambda e: self._force_popup_scan())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # =========================================================================
    # UI BUILDING — create_widgets (header, controls, tab notebook)
    # =========================================================================

    def create_widgets(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Forza Horizon 6 Tracker", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")

        self.status_var = tk.StringVar(value="Status: Stopped")
        ttk.Label(header, textvariable=self.status_var, foreground="#1f6feb").grid(row=0, column=1, sticky="e")

        self.notice_var = tk.StringVar(value="")
        self.notice_label = ttk.Label(header, textvariable=self.notice_var, foreground="#137333", font=("Segoe UI", 11, "bold"))
        self.notice_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        controls = ttk.Frame(self)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(0, weight=0)
        controls.columnconfigure(1, weight=0)
        controls.columnconfigure(2, weight=0)
        controls.columnconfigure(3, weight=1)
        controls.columnconfigure(4, weight=0)

        self.start_stop_button = ttk.Button(controls, text=tracker_button_label(self.tracker_running), command=lambda: self.toggle_tracker())
        self.start_stop_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Button(controls, text="Refresh", command=self.refresh_all).grid(row=0, column=1, sticky="w", padx=(0, 8))

        self.auto_start_var = tk.BooleanVar(value=self.settings.get("auto_start_forza", False))
        self.launch_tracker_var = tk.BooleanVar(value=self.settings.get("launch_tracker_on_start", False))
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "light"))
        self.credit_ocr_var = tk.BooleanVar(value=self.settings.get("credit_ocr_enabled", False))
        region = self.settings.get("credit_region") or [0, 0, 0, 0]
        self.credit_x_var = tk.StringVar(value=str(region[0]))
        self.credit_y_var = tk.StringVar(value=str(region[1]))
        self.credit_w_var = tk.StringVar(value=str(region[2]))
        self.credit_h_var = tk.StringVar(value=str(region[3]))
        payout_region = self.settings.get("payout_region") or [0, 0, 0, 0]
        self.payout_x_var = tk.StringVar(value=str(payout_region[0]))
        self.payout_y_var = tk.StringVar(value=str(payout_region[1]))
        self.payout_w_var = tk.StringVar(value=str(payout_region[2]))
        self.payout_h_var = tk.StringVar(value=str(payout_region[3]))
        self.tesseract_path_var = tk.StringVar(value=self.settings.get("tesseract_path", ""))
        self.performance_var = tk.StringVar(value=self.settings.get("performance_mode", car_lookup.DEFAULT_PERFORMANCE_MODE))
        ttk.Button(controls, text="Save Settings", command=self.save_all_settings).grid(row=0, column=2, padx=(8, 8), sticky="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.notebook.enable_traversal()

        self.garage_tab = ttk.Frame(self.notebook)
        self.live_tab = ttk.Frame(self.notebook)
        self.stats_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.logs_tab = ttk.Frame(self.notebook)
        self.methods_tab = ttk.Frame(self.notebook)
        self.recommendations_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.garage_tab, text="Collection")
        self.notebook.add(self.live_tab, text="Live Data")
        self.notebook.add(self.methods_tab, text="Methods")
        self.notebook.add(self.stats_tab, text="Stats")
        self.notebook.add(self.recommendations_tab, text="Recommendations")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.logs_tab, text="Logs")

        self.build_garage_tab()
        self.build_live_tab()
        self.build_methods_tab()
        self.build_stats_tab()
        self.build_recommendations_tab()
        self.build_settings_tab()
        self.build_logs_tab()
        self.populate_progress_manufacturers()
        if self.launch_tracker_var.get():
            self.after(500, self.start_tracker)

    def build_garage_tab(self):
        self.garage_tab.columnconfigure(0, weight=1)
        self.garage_tab.rowconfigure(3, weight=1)

        summary_frame = ttk.LabelFrame(self.garage_tab, text="Summary")
        summary_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        summary_frame.columnconfigure(0, weight=1)

        self.collection_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(summary_frame, textvariable=self.collection_summary_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        filter_frame = ttk.LabelFrame(self.garage_tab, text="Filters")
        filter_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        filter_frame.columnconfigure(1, weight=1)

        ttk.Label(filter_frame, text="Search:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.progress_search_var = tk.StringVar()
        self.search_entry = ttk.Entry(filter_frame, textvariable=self.progress_search_var, width=32)
        self.search_entry.grid(row=0, column=1, padx=(4, 8), pady=6, sticky="ew")

        ttk.Label(filter_frame, text="Manufacturer:").grid(row=0, column=2, sticky="w", padx=(8, 4), pady=6)
        self.progress_manufacturer_var = tk.StringVar()
        self.progress_manufacturer_dropdown = ttk.Combobox(filter_frame, textvariable=self.progress_manufacturer_var, width=20, state="readonly")
        self.progress_manufacturer_dropdown.grid(row=0, column=3, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Year:").grid(row=0, column=4, sticky="w", padx=(8, 4), pady=6)
        self.progress_year_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.progress_year_var, width=8).grid(row=0, column=5, padx=(4, 8), pady=6, sticky="w")
        self.progress_year_var.trace_add("write", lambda *_: self._validate_year_entry())

        ttk.Label(filter_frame, text="Min Value:").grid(row=0, column=6, sticky="w", padx=(8, 4), pady=6)
        self.collection_min_value_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.collection_min_value_var, width=12).grid(row=0, column=7, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Max Value:").grid(row=0, column=8, sticky="w", padx=(8, 4), pady=6)
        self.collection_max_value_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.collection_max_value_var, width=12).grid(row=0, column=9, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Sort:").grid(row=0, column=10, sticky="w", padx=(8, 4), pady=6)
        self.collection_sort_var = tk.StringVar(value="Name A-Z")
        ttk.Combobox(filter_frame, textvariable=self.collection_sort_var, values=["Name A-Z", "Name Z-A", "Price Low-High", "Price High-Low"], state="readonly", width=14).grid(row=0, column=11, padx=(4, 8), pady=6, sticky="w")

        ttk.Button(filter_frame, text="Apply", command=self.refresh_collection).grid(row=0, column=12, padx=(4, 8), pady=6)
        ttk.Button(filter_frame, text="Clear", command=self.clear_collection_filters).grid(row=0, column=13, padx=(0, 8), pady=6)

        manage_frame = ttk.LabelFrame(self.garage_tab, text="Manage Cars")
        manage_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        manage_frame.columnconfigure(1, weight=1)

        self.add_car_var = tk.StringVar()
        self.add_car_var_entry = ttk.Entry(manage_frame, textvariable=self.add_car_var, width=42)
        self.add_car_var_entry.grid(row=0, column=0, columnspan=2, padx=(8, 8), pady=(6, 6), sticky="ew")
        ttk.Button(manage_frame, text="Add Car", command=self.add_car_manual).grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        ttk.Button(manage_frame, text="Import List", command=self.import_owned_cars_from_text).grid(row=0, column=3, padx=(0, 8), pady=6, sticky="w")
        ttk.Button(manage_frame, text="Import File", command=self.import_owned_cars_from_file).grid(row=0, column=4, padx=(0, 8), pady=6, sticky="w")

        self.collection_notebook = ttk.Notebook(self.garage_tab)
        self.collection_notebook.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))

        self.owned_frame = ttk.Frame(self.collection_notebook)
        self.missing_frame = ttk.Frame(self.collection_notebook)
        self.collection_notebook.add(self.owned_frame, text="Owned")
        self.collection_notebook.add(self.missing_frame, text="Still Missing")

        self.owned_listbox = tk.Listbox(self.owned_frame, height=24, font=("Consolas", 10), selectmode=tk.EXTENDED)
        self.owned_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.owned_listbox.bind("<<ListboxSelect>>", lambda event: self.on_owned_list_select())
        self.owned_listbox.bind("<Double-1>", lambda event: self.remove_selected_car())
        self._add_owned_context_menu()
        owned_scrollbar = ttk.Scrollbar(self.owned_frame, orient="vertical", command=self.owned_listbox.yview)
        owned_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.owned_listbox.configure(yscrollcommand=owned_scrollbar.set)
        self.owned_frame.columnconfigure(0, weight=1)
        self.owned_frame.rowconfigure(0, weight=1)

        missing_search_frame = ttk.Frame(self.missing_frame)
        missing_search_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 0))
        missing_search_frame.columnconfigure(1, weight=1)
        ttk.Label(missing_search_frame, text="Filter:").grid(row=0, column=0, padx=(0, 4))
        self._missing_search_var = tk.StringVar()
        self._missing_search_entry = ttk.Entry(missing_search_frame, textvariable=self._missing_search_var, width=30)
        self._missing_search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(missing_search_frame, text="Clear", command=lambda: (self._missing_search_var.set(""), self.refresh_collection())).grid(row=0, column=2)
        self._missing_search_var.trace_add("write", lambda *_: self.refresh_collection())

        self.unowned_listbox = tk.Listbox(self.missing_frame, height=22, font=("Consolas", 10))
        self.unowned_listbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.unowned_listbox.bind("<Double-1>", lambda event: self.add_selected_missing_car())
        self._add_missing_context_menu()
        missing_scrollbar = ttk.Scrollbar(self.missing_frame, orient="vertical", command=self.unowned_listbox.yview)
        missing_scrollbar.grid(row=1, column=1, sticky="ns", pady=8)
        self.unowned_listbox.configure(yscrollcommand=missing_scrollbar.set)
        self.missing_frame.columnconfigure(0, weight=1)
        self.missing_frame.rowconfigure(1, weight=1)

    def build_live_tab(self):
        self.live_tab.columnconfigure(0, weight=1)

        info_frame = ttk.LabelFrame(self.live_tab, text="Current Session")
        info_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        info_frame.columnconfigure(1, weight=1)

        labels = [
            ("RPM", "rpm_var"),
            ("Speed", "speed_var"),
            ("Car ID", "car_id_var"),
            ("Car Name", "car_name_var"),
            ("Session Credits", "session_credits_var"),
            ("Session Time", "session_time_var"),
        ]
        self.vars = {}
        for idx, (text, var_name) in enumerate(labels):
            ttk.Label(info_frame, text=f"{text}:").grid(row=idx, column=0, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value="-")
            self.vars[var_name] = var
            ttk.Label(info_frame, textvariable=var).grid(row=idx, column=1, sticky="w", padx=8, pady=4)

        controls_frame = ttk.Frame(self.live_tab)
        controls_frame.grid(row=1, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(controls_frame, text="Reward credits:").grid(row=0, column=0, sticky="w")
        self.session_credit_entry = tk.StringVar(value="0")
        ttk.Entry(controls_frame, textvariable=self.session_credit_entry, width=12).grid(row=0, column=1, padx=(6, 8))
        ttk.Button(controls_frame, text="Add Credits", command=self.add_session_credits).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls_frame, text="Reset Session", command=self.reset_session).grid(row=0, column=3)

        ttk.Label(self.live_tab, text="Use this when the game shows a credit reward such as a wheelspin or super wheelspin.").grid(row=2, column=0, sticky="w", padx=10, pady=(6, 0))

        self.detection_status_var = tk.StringVar(value="Start the tracker and drive a car in Forza to auto-detect it.")
        if pyautogui is None or pytesseract is None or ImageGrab is None:
            ttk.Label(self.live_tab, text="Automatic credit tracking needs OCR packages — see Settings → Automatic Credit Tracking.").grid(row=3, column=0, sticky="w", padx=10, pady=(2, 0))
        else:
            ttk.Label(self.live_tab, text="Automatic credit tracking runs when enabled in Settings and Forza is open (a new session starts each time the game opens).").grid(row=3, column=0, sticky="w", padx=10, pady=(2, 0))
            ttk.Label(self.live_tab, text="Press F4 and say the car name to save it to your owned list.").grid(row=4, column=0, sticky="w", padx=10, pady=(4, 0))

            detect_frame = ttk.LabelFrame(self.live_tab, text="Auto Garage Detection")
            detect_frame.grid(row=5, column=0, sticky="ew", padx=10, pady=(10, 0))
            detect_frame.columnconfigure(0, weight=1)
            ttk.Label(detect_frame, textvariable=self.detection_status_var, justify="left").grid(row=0, column=0, sticky="w", padx=8, pady=(6, 4))
            self.tag_detected_button = ttk.Button(detect_frame, text="Tag Detected Car", command=self.tag_detected_car)
            self.tag_detected_button.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

            ocr_status_frame = ttk.LabelFrame(self.live_tab, text="Auto Credit Tracking Status")
            ocr_status_frame.grid(row=6, column=0, sticky="ew", padx=10, pady=(10, 0))
            ocr_status_frame.columnconfigure(1, weight=1)
            self._live_ocr_status_var = tk.StringVar(value="OCR disabled — enable in Settings tab")
            ttk.Label(ocr_status_frame, textvariable=self._live_ocr_status_var, foreground="#555555").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))
            self._live_ocr_balance_var = tk.StringVar(value="")
            ttk.Label(ocr_status_frame, text="Last balance:").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 4))
            ttk.Label(ocr_status_frame, textvariable=self._live_ocr_balance_var, font=("Consolas", 11, "bold")).grid(row=1, column=1, sticky="w", padx=(4, 8), pady=(0, 4))
            self._live_ocr_raw_var = tk.StringVar(value="")
            ttk.Label(ocr_status_frame, text="Raw text:").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 6))
            ttk.Label(ocr_status_frame, textvariable=self._live_ocr_raw_var, foreground="#888888").grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(0, 6))
            self._live_popup_var = tk.StringVar(value="")
            ttk.Label(ocr_status_frame, text="Last popup:").grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))
            ttk.Label(ocr_status_frame, textvariable=self._live_popup_var, foreground="#888888").grid(row=3, column=1, sticky="w", padx=(4, 8), pady=(0, 6))

    # =========================================================================
    # UI BUILDING — Methods Tab
    # =========================================================================

    def build_methods_tab(self):
        self.methods_tab.columnconfigure(0, weight=1)
        self.methods_tab.rowconfigure(2, weight=2)
        self.methods_tab.rowconfigure(4, weight=1)

        control_frame = ttk.LabelFrame(self.methods_tab, text="Track a Method")
        control_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        control_frame.columnconfigure(2, weight=1)

        ttk.Label(control_frame, text="Method:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.method_var = tk.StringVar(value=METHOD_NAMES[0])
        ttk.Combobox(control_frame, textvariable=self.method_var, values=METHOD_NAMES, state="readonly", width=20).grid(row=0, column=1, padx=(4, 8), pady=6, sticky="w")

        self.method_start_stop_btn = ttk.Button(control_frame, text="Start Tracking", command=self.toggle_method_tracking)
        self.method_start_stop_btn.grid(row=0, column=2, padx=(8, 8), pady=6, sticky="w")

        self.method_timer_var = tk.StringVar(value="--:--:--")
        ttk.Label(control_frame, text="Elapsed:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(control_frame, textvariable=self.method_timer_var, font=("Consolas", 12, "bold")).grid(row=1, column=1, sticky="w", padx=(4, 8), pady=4)

        self.method_status_var = tk.StringVar(value="Select a method and click Start Tracking")
        ttk.Label(control_frame, textvariable=self.method_status_var, foreground="#555555").grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        self.method_start_credits_var = tk.StringVar(value="Starting credits: -")
        self.method_current_credits_var = tk.StringVar(value="Current credits: -")
        self.method_crhr_var = tk.StringVar(value="CR/hr: -")
        info_row = ttk.Frame(self.methods_tab)
        info_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        ttk.Label(info_row, textvariable=self.method_start_credits_var).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Label(info_row, textvariable=self.method_current_credits_var).grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(info_row, textvariable=self.method_crhr_var, font=("Segoe UI", 10, "bold"), foreground="#1f6feb").grid(row=0, column=2, sticky="w")

        history_frame = ttk.LabelFrame(self.methods_tab, text="Method History")
        history_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)

        cols = ("Method", "Duration", "Credits", "CR/hr", "Date")
        self.method_tree = ttk.Treeview(history_frame, columns=cols, show="headings", height=16)
        for col in cols:
            self.method_tree.heading(col, text=col)
            self.method_tree.column(col, width=140 if col != "Method" else 160)
        self.method_tree.column("Date", width=170)
        tree_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=self.method_tree.yview)
        self.method_tree.configure(yscrollcommand=tree_scroll.set)
        self.method_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        tree_scroll.grid(row=0, column=1, sticky="ns", pady=8)

        summary_frame = ttk.LabelFrame(self.methods_tab, text="Average CR/hr by Method")
        summary_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        summary_frame.columnconfigure(0, weight=1)
        self.method_summary_var = tk.StringVar(value="No history yet")
        ttk.Label(summary_frame, textvariable=self.method_summary_var, justify="left").grid(row=0, column=0, sticky="w", padx=8, pady=6)

        txn_frame = ttk.LabelFrame(self.methods_tab, text="Credit Transactions (auto-detected)")
        txn_frame.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        txn_frame.columnconfigure(0, weight=1)
        txn_frame.rowconfigure(0, weight=1)

        txn_cols = ("Time", "Amount", "Type", "Balance")
        self.txn_tree = ttk.Treeview(txn_frame, columns=txn_cols, show="headings", height=8)
        for col in txn_cols:
            self.txn_tree.heading(col, text=col)
            self.txn_tree.column(col, width=160 if col != "Type" else 80)
        txn_scroll = ttk.Scrollbar(txn_frame, orient="vertical", command=self.txn_tree.yview)
        self.txn_tree.configure(yscrollcommand=txn_scroll.set)
        self.txn_tree.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        txn_scroll.grid(row=0, column=1, sticky="ns", pady=8)

        self.txn_summary_var = tk.StringVar(value="No transactions yet")
        ttk.Label(txn_frame, textvariable=self.txn_summary_var, foreground="#555555").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

    def toggle_method_tracking(self):
        if self._method_active:
            self._stop_method_tracking()
        else:
            self._start_method_tracking()

    def _start_method_tracking(self):
        self._method_active = True
        self._method_name = self.method_var.get()
        self._method_start_time = time.monotonic()
        session_credits = self.get_session_credits()
        self._method_start_credits = session_credits
        self.method_start_stop_btn.configure(text="Stop Tracking")
        self.method_status_var.set(f"Tracking: {self._method_name}")
        self.method_start_credits_var.set(f"Starting credits: {format_credits(session_credits)}")
        self._update_method_timer()

    def _stop_method_tracking(self):
        if not self._method_active:
            return
        elapsed = time.monotonic() - self._method_start_time
        current_credits = self.get_session_credits()
        credits_earned = current_credits - self._method_start_credits
        crhr = (credits_earned / (elapsed / 3600)) if elapsed > 0 else 0

        entry = {
            "method": self._method_name,
            "start_time": self.current_timestamp(),
            "elapsed_seconds": round(elapsed),
            "credits_earned": credits_earned,
            "crhr": round(crhr),
        }
        self._save_method_entry(entry)

        self._method_active = False
        self._method_name = None
        self._method_start_time = None
        if self._method_timer_after_id:
            self.after_cancel(self._method_timer_after_id)
        self._method_timer_after_id = None
        self.method_start_stop_btn.configure(text="Start Tracking")
        self.method_status_var.set(
            f"Last: {entry['method']} — {format_credits(credits_earned)} in "
            f"{self._format_duration(elapsed)} = {format_credits(crhr)}/hr"
        )
        self.method_timer_var.set("--:--:--")
        self.method_start_credits_var.set("Starting credits: -")
        self.method_current_credits_var.set("Current credits: -")
        self.method_crhr_var.set("CR/hr: -")
        self.refresh_methods_panel()

    def _update_method_timer(self):
        if not self._method_active:
            return
        elapsed = time.monotonic() - self._method_start_time
        self.method_timer_var.set(self._format_duration(elapsed))
        current_credits = self.get_session_credits()
        credits_earned = current_credits - self._method_start_credits
        crhr = (credits_earned / (elapsed / 3600)) if elapsed > 0 else 0
        self.method_current_credits_var.set(f"Current credits: {format_credits(current_credits)}")
        self.method_crhr_var.set(f"CR/hr: {format_credits(crhr)}")
        self._method_timer_after_id = self.after(1000, self._update_method_timer)

    def _format_duration(self, seconds):
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_methods_history(self):
        return load_json_file(METHODS_FILE, {"sessions": []})

    def _save_method_entry(self, entry):
        history = self._load_methods_history()
        history["sessions"].append(entry)
        history["sessions"] = history["sessions"][-200:]
        _safe_write_json(METHODS_FILE, history)

    def refresh_methods_panel(self):
        history = self._load_methods_history()
        sessions = history.get("sessions", [])

        self.method_tree.delete(*self.method_tree.get_children())
        for entry in reversed(sessions[-50:]):
            duration = self._format_duration(entry.get("elapsed_seconds", 0))
            credits = format_credits(entry.get("credits_earned", 0))
            crhr = format_credits(entry.get("crhr", 0))
            date_str = entry.get("start_time", "")[:19].replace("T", " ")
            self.method_tree.insert("", "end", values=(entry.get("method", "?"), duration, credits, crhr, date_str))

        method_totals = {}
        for entry in sessions:
            m = entry.get("method", "Other")
            if m not in method_totals:
                method_totals[m] = {"total_credits": 0, "total_seconds": 0, "count": 0}
            method_totals[m]["total_credits"] += entry.get("credits_earned", 0)
            method_totals[m]["total_seconds"] += entry.get("elapsed_seconds", 0)
            method_totals[m]["count"] += 1

        parts = []
        for m, data in sorted(method_totals.items()):
            avg_crhr = (data["total_credits"] / (data["total_seconds"] / 3600)) if data["total_seconds"] > 0 else 0
            parts.append(f"{m}: {format_credits(avg_crhr)}/hr ({data['count']} sessions)")
        self.method_summary_var.set("  |  ".join(parts) if parts else "No history yet")
        self.refresh_transactions()

    def refresh_transactions(self):
        txns = self._credit_transactions.get("transactions", [])
        self.txn_tree.delete(*self.txn_tree.get_children())
        for entry in reversed(txns[-30:]):
            ts = entry.get("timestamp", "")[:19].replace("T", " ")
            amount = entry.get("amount", 0)
            amt_str = f"+{format_credits(amount)}" if amount > 0 else format_credits(amount)
            txn_type = entry.get("type", "?").capitalize()
            balance_after = format_credits(entry.get("balance_after", 0))
            tag = "gain" if amount > 0 else "spend"
            self.txn_tree.insert("", "end", values=(ts, amt_str, txn_type, balance_after), tags=(tag,))
        self.txn_tree.tag_configure("gain", foreground="#137333")
        self.txn_tree.tag_configure("spend", foreground="#c5221f")
        total_gains = sum(t["amount"] for t in txns if t.get("amount", 0) > 0)
        count = len(txns)
        self.txn_summary_var.set(f"{count} transactions total | Total gains: +{format_credits(total_gains)}")

    def build_stats_tab(self):
        self.stats_tab.columnconfigure(0, weight=1)
        self.stats_tab.rowconfigure(2, weight=1)

        summary_frame = ttk.LabelFrame(self.stats_tab, text="Collection Progress")
        summary_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        summary_frame.columnconfigure(0, weight=1)

        self.stats_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(summary_frame, textvariable=self.stats_summary_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        self.stats_progress_var = tk.DoubleVar(value=0.0)
        self.stats_progress_bar = ttk.Progressbar(summary_frame, variable=self.stats_progress_var, maximum=100)
        self.stats_progress_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))

        details_frame = ttk.LabelFrame(self.stats_tab, text="Live Summary")
        details_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        details_frame.columnconfigure(1, weight=1)

        self.stats_last_seen_var = tk.StringVar(value="No recent telemetry")
        self.stats_session_var = tk.StringVar(value="Session credits: 0")
        self.stats_window_var = tk.StringVar(value="Waiting for Forza window...")
        self.stats_total_value_var = tk.StringVar(value="Collection value: -")
        labels = [
            ("Last seen car", self.stats_last_seen_var),
            ("Session credits", self.stats_session_var),
            ("Forza window", self.stats_window_var),
            ("Total collection value", self.stats_total_value_var),
        ]
        for idx, (text, var) in enumerate(labels):
            ttk.Label(details_frame, text=f"{text}:").grid(row=idx, column=0, sticky="w", padx=8, pady=6)
            ttk.Label(details_frame, textvariable=var).grid(row=idx, column=1, sticky="w", padx=8, pady=6)

        history_frame = ttk.LabelFrame(self.stats_tab, text="Session Earnings History")
        history_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)
        self.history_canvas = tk.Canvas(history_frame, bg="white", height=150)
        self.history_canvas.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        rate_frame = ttk.LabelFrame(self.stats_tab, text="Credit Rate (Live)")
        rate_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        rate_frame.columnconfigure(0, weight=1)
        rate_frame.rowconfigure(0, weight=1)
        self.rate_canvas = tk.Canvas(rate_frame, bg="white", height=120)
        self.rate_canvas.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.rate_stats_var = tk.StringVar(value="No rate data yet")
        ttk.Label(rate_frame, textvariable=self.rate_stats_var, foreground="#555555").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

    def build_settings_tab(self):
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.rowconfigure(2, weight=1)

        # Wrap everything in a canvas+scrollbar so content always fits
        settings_canvas = tk.Canvas(self.settings_tab, highlightthickness=0, width=700)
        settings_scroll = ttk.Scrollbar(self.settings_tab, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scroll.grid(row=0, column=1, sticky="ns")
        self.settings_tab.rowconfigure(0, weight=1)
        self.settings_tab.columnconfigure(0, weight=1)

        settings_inner = ttk.Frame(settings_canvas)
        settings_canvas.create_window((0, 0), window=settings_inner, anchor="nw", tags="inner")
        settings_inner.columnconfigure(0, weight=1)

        def _configure_inner(event):
            settings_canvas.itemconfig("inner", width=settings_canvas.winfo_width())
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        settings_inner.bind("<Configure>", _configure_inner)
        settings_canvas.bind("<Configure>", _configure_inner)
        if os.name == "nt":
            settings_canvas.bind("<Enter>", lambda e: settings_canvas.bind_all("<MouseWheel>", lambda ev: settings_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
            settings_canvas.bind("<Leave>", lambda e: settings_canvas.unbind_all("<MouseWheel>"))

        settings_frame = ttk.LabelFrame(settings_inner, text="Tracker Behavior")
        settings_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        settings_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(settings_frame, text="Auto-open with Forza", variable=self.auto_start_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(settings_frame, text="Start tracker on open", variable=self.launch_tracker_var).grid(row=1, column=0, sticky="w", padx=8, pady=6)

        ttk.Label(settings_frame, text="Theme:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(settings_frame, textvariable=self.theme_var, values=["light", "dark"], state="readonly", width=12).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=6)

        ttk.Label(settings_frame, text="Performance:").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(
            settings_frame,
            textvariable=self.performance_var,
            values=list(car_lookup.PERFORMANCE_PRESETS.keys()),
            state="readonly",
            width=12,
        ).grid(row=3, column=1, sticky="w", padx=(4, 8), pady=6)
        ttk.Label(
            settings_frame,
            text=(
                "Higher performance = fewer screen reads, disk writes and UI updates, which "
                "frees resources for the game (higher FPS).\n"
                "Quality: smoothest dashboard (updates every 1s).  "
                "Balanced: recommended.  "
                "Performance: best in-game FPS (dashboard updates every ~4s)."
            ),
            justify="left",
            foreground="#555555",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Button(settings_frame, text="Apply Settings", command=self.save_all_settings).grid(row=5, column=0, sticky="w", padx=8, pady=(6, 8))

        ocr_frame = ttk.LabelFrame(settings_inner, text="Automatic Credit Tracking (OCR)")
        ocr_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Checkbutton(ocr_frame, text="Auto-track credits by reading the on-screen balance while Forza is open", variable=self.credit_ocr_var).grid(row=0, column=0, columnspan=6, sticky="w", padx=8, pady=6)

        ttk.Label(ocr_frame, text="Credit area (pixels):").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(ocr_frame, text="X").grid(row=1, column=1, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.credit_x_var, width=7).grid(row=1, column=2, padx=(2, 8))
        ttk.Label(ocr_frame, text="Y").grid(row=1, column=3, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.credit_y_var, width=7).grid(row=1, column=4, padx=(2, 8))
        ttk.Label(ocr_frame, text="W").grid(row=2, column=1, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.credit_w_var, width=7).grid(row=2, column=2, padx=(2, 8))
        ttk.Label(ocr_frame, text="H").grid(row=2, column=3, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.credit_h_var, width=7).grid(row=2, column=4, padx=(2, 8))
        for var in (self.credit_x_var, self.credit_y_var, self.credit_w_var, self.credit_h_var):
            var.trace_add("write", lambda *_, v=var: v.set(re.sub(r"[^0-9]", "", v.get())))

        ttk.Button(ocr_frame, text="Capture Area", command=self.capture_credit_area).grid(row=1, column=5, sticky="w", padx=8)
        ttk.Button(ocr_frame, text="Test OCR", command=self.test_credit_ocr).grid(row=2, column=5, sticky="w", padx=8)
        ttk.Button(ocr_frame, text="Auto-Detect Region", command=self.auto_calibrate_region).grid(row=1, column=6, sticky="w", padx=8)
        self.credit_region_locked = tk.BooleanVar(value=self.settings.get("credit_region_locked", False))
        ttk.Checkbutton(ocr_frame, text="Lock", variable=self.credit_region_locked, command=self._on_credit_lock_toggle).grid(row=2, column=6, sticky="w", padx=8)

        ttk.Separator(ocr_frame, orient="horizontal").grid(row=3, column=0, columnspan=7, sticky="ew", padx=8, pady=6)

        ttk.Label(ocr_frame, text="Payout popup area (pixels):").grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(ocr_frame, text="X").grid(row=4, column=1, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.payout_x_var, width=7).grid(row=4, column=2, padx=(2, 8))
        ttk.Label(ocr_frame, text="Y").grid(row=4, column=3, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.payout_y_var, width=7).grid(row=4, column=4, padx=(2, 8))
        ttk.Label(ocr_frame, text="W").grid(row=5, column=1, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.payout_w_var, width=7).grid(row=5, column=2, padx=(2, 8))
        ttk.Label(ocr_frame, text="H").grid(row=5, column=3, sticky="e")
        ttk.Entry(ocr_frame, textvariable=self.payout_h_var, width=7).grid(row=5, column=4, padx=(2, 8))
        for var in (self.payout_x_var, self.payout_y_var, self.payout_w_var, self.payout_h_var):
            var.trace_add("write", lambda *_, v=var: v.set(re.sub(r"[^0-9]", "", v.get())))

        ttk.Button(ocr_frame, text="Capture Area", command=self.capture_payout_area).grid(row=4, column=5, sticky="w", padx=8)
        ttk.Button(ocr_frame, text="Test OCR", command=self.test_payout_ocr).grid(row=5, column=5, sticky="w", padx=8)
        self.payout_region_locked = tk.BooleanVar(value=self.settings.get("payout_region_locked", False))
        ttk.Checkbutton(ocr_frame, text="Lock", variable=self.payout_region_locked, command=self._on_payout_lock_toggle).grid(row=5, column=6, sticky="w", padx=8)

        ttk.Separator(ocr_frame, orient="horizontal").grid(row=6, column=0, columnspan=7, sticky="ew", padx=8, pady=6)

        ttk.Label(ocr_frame, text="Tesseract path:").grid(row=7, column=0, sticky="w", padx=8)
        ttk.Entry(ocr_frame, textvariable=self.tesseract_path_var).grid(row=7, column=1, columnspan=4, sticky="ew", padx=(2, 8))
        ocr_frame.columnconfigure(1, weight=1)
        ttk.Button(ocr_frame, text="Apply Settings", command=self.save_all_settings).grid(row=7, column=5, sticky="w", padx=8)

        self.ocr_debug_var = tk.BooleanVar(value=self.settings.get("ocr_debug_logging", False))
        ttk.Checkbutton(ocr_frame, text="Debug logging (saves images & OCR text to ocr_debug/)", variable=self.ocr_debug_var, command=self._on_debug_toggle).grid(row=8, column=0, columnspan=7, sticky="w", padx=8, pady=(4, 0))

        self._ocr_confidence_color = tk.StringVar(value="gray")
        self._ocr_confidence_text = tk.StringVar(value="No scans yet")
        conf_frame = ttk.Frame(ocr_frame)
        conf_frame.grid(row=9, column=0, columnspan=7, sticky="w", padx=8, pady=(0, 4))
        self._conf_indicator_label = ttk.Label(conf_frame, text="\u25cf", font=("Segoe UI", 14))
        self._conf_indicator_label.pack(side="left", padx=(0, 6))
        ttk.Label(conf_frame, textvariable=self._ocr_confidence_text).pack(side="left")
        ttk.Label(conf_frame, text="  |  Last raw:").pack(side="left", padx=(12, 4))
        self._ocr_raw_text_var = tk.StringVar(value="")
        ttk.Label(conf_frame, textvariable=self._ocr_raw_text_var, foreground="#888888").pack(side="left")

        test_popup_frame = ttk.Frame(ocr_frame)
        test_popup_frame.grid(row=10, column=0, columnspan=7, sticky="w", padx=8, pady=(4, 8))
        ttk.Button(test_popup_frame, text="Test Popup Detection", command=self._test_popup_scan).pack(side="left", padx=(0, 8))
        self._popup_test_var = tk.StringVar(value="")
        ttk.Label(test_popup_frame, textvariable=self._popup_test_var, foreground="#555555").pack(side="left")

        preview_frame = ttk.LabelFrame(settings_inner, text="OCR Region Preview")
        preview_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        preview_inner = ttk.Frame(preview_frame)
        preview_inner.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        preview_inner.columnconfigure(0, weight=1)
        preview_inner.rowconfigure(0, weight=1)

        self._preview_canvas = tk.Canvas(preview_inner, bg="#2b2b2b", highlightthickness=1, highlightbackground="#555555")
        self._preview_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._preview_canvas.create_text(150, 45, text="No preview yet — click Refresh", fill="#888888", tags="placeholder")
        ttk.Button(preview_inner, text="Refresh\nPreview", command=self._refresh_ocr_preview).grid(row=0, column=1, sticky="ns")

        self._preview_info_var = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self._preview_info_var, foreground="#555555").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

        export_frame = ttk.LabelFrame(settings_inner, text="Export & Backup")
        export_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        export_frame.columnconfigure(1, weight=1)

        ttk.Button(export_frame, text="Export Owned Cars (CSV)", command=self.export_owned_to_csv).grid(row=0, column=0, padx=8, pady=6)
        ttk.Button(export_frame, text="Export Full Collection (CSV)", command=self.export_collection_to_csv).grid(row=0, column=1, padx=(4, 8), pady=6, sticky="w")
        ttk.Button(export_frame, text="Backup All Data (ZIP)", command=self.backup_all_data).grid(row=1, column=0, padx=8, pady=6)
        ttk.Button(export_frame, text="Restore from Backup", command=self.restore_from_backup).grid(row=1, column=1, padx=(4, 8), pady=6, sticky="w")
        ttk.Button(export_frame, text="Keyboard Shortcuts", command=self.show_shortcuts_help).grid(row=0, column=2, rowspan=2, padx=(8, 8), pady=6, sticky="ns")

        update_frame = ttk.LabelFrame(settings_inner, text="Updates")
        update_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        update_frame.columnconfigure(1, weight=1)

        self._update_status_var = tk.StringVar(value="")
        ttk.Label(update_frame, textvariable=self._update_status_var, foreground="#555555").grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Button(update_frame, text="Check for Updates", command=self._check_for_updates).grid(row=1, column=0, padx=8, pady=6, sticky="w")
        ttk.Button(update_frame, text="Restart App", command=self._restart_app).grid(row=1, column=1, padx=(4, 8), pady=6, sticky="w")

        self._update_pending = False

    def build_logs_tab(self):
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(self.logs_tab, wrap=tk.WORD, height=24)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_text.configure(state="disabled")

    def build_recommendations_tab(self):
        self.recommendations_tab.columnconfigure(0, weight=1)
        self.recommendations_tab.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(self.recommendations_tab, text="Smart Recommendations")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        header.columnconfigure(1, weight=1)

        ttk.Button(header, text="Refresh", command=self.refresh_recommendations).grid(row=0, column=0, padx=8, pady=6)
        self.rec_filter_var = tk.StringVar(value="All")
        ttk.Combobox(header, textvariable=self.rec_filter_var, values=["All", "Best Value", "Cheapest Missing", "Highest Rated", "By Manufacturer"], state="readonly", width=18).grid(row=0, column=1, padx=(4, 8), pady=6, sticky="w")
        ttk.Button(header, text="Add Selected to Owned", command=self.add_recommended_to_owned).grid(row=0, column=2, padx=(8, 8), pady=6, sticky="e")

        self.recommendations_tree = ttk.Treeview(self.recommendations_tab, columns=("Car", "Price", "Score", "Reason", "Category"), show="headings", selectmode="extended")
        for col, width in [("Car", 280), ("Price", 100), ("Score", 80), ("Reason", 220), ("Category", 120)]:
            self.recommendations_tree.heading(col, text=col)
            self.recommendations_tree.column(col, width=width, minwidth=60)
        self.recommendations_tree.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        rec_scroll = ttk.Scrollbar(self.recommendations_tab, orient="vertical", command=self.recommendations_tree.yview)
        rec_scroll.grid(row=1, column=1, sticky="ns", pady=(0, 8))
        self.recommendations_tree.configure(yscrollcommand=rec_scroll.set)

        self.rec_summary_var = tk.StringVar(value="Click Refresh to generate recommendations.")
        ttk.Label(self.recommendations_tab, textvariable=self.rec_summary_var, foreground="#555555").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))

    def _performance_preset(self):
        return car_lookup.get_performance_preset(self.settings.get("performance_mode"))

    def _refresh_interval_ms(self):
        return self._performance_preset()["refresh_ms"]

    def _ocr_interval_seconds(self):
        return self._performance_preset()["ocr_seconds"]


    # =====================================================================
    # PERFORMANCE & OCR INTERVAL HELPERS
    # =====================================================================
    def _reset_pending_balance(self):
        self._pending_balance = None
        self._pending_balance_count = 0


    # =====================================================================
    # CREDIT TRANSACTION PERSISTENCE
    # =====================================================================
    def _load_credit_transactions(self):
        return load_json_file(CREDIT_TRANSACTIONS_FILE, {"transactions": []})

    def _save_credit_transactions(self):
        _safe_write_json(CREDIT_TRANSACTIONS_FILE, self._credit_transactions)

    def _log_credit_transaction(self, amount, balance_before, balance_after):
        entry = {
            "timestamp": self.current_timestamp(),
            "amount": amount,
            "type": "gain" if amount > 0 else "spend",
            "balance_before": balance_before,
            "balance_after": balance_after,
        }
        self._credit_transactions["transactions"].append(entry)
        self._credit_transactions["transactions"] = self._credit_transactions["transactions"][-500:]
        self._save_credit_transactions()
        self._credit_rate_points.append((time.monotonic(), balance_after))
        self._credit_rate_points = self._credit_rate_points[-200:]


    # =====================================================================
    # OCR CONFIDENCE & CACHE MANAGEMENT
    # =====================================================================
    def _median_of_recent(self):
        vals = [b for b in self._recent_balances if b is not None]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        if n % 2 == 1:
            return vals_sorted[n // 2]
        return (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) // 2

    def _ocr_confidence_label(self):
        if not self.credit_ocr_var.get():
            return "gray", "OCR disabled"
        region = self.get_credit_region()
        if not region:
            return "gray", "No region set"
        if self._ocr_total_count == 0:
            return "gray", "No scans yet"
        rate = self._ocr_success_count / self._ocr_total_count
        if rate >= 0.7:
            return "green", f"Confidence: {self._ocr_success_count}/{self._ocr_total_count} scans OK"
        if rate >= 0.4:
            return "yellow", f"Confidence: {self._ocr_success_count}/{self._ocr_total_count} scans OK"
        return "red", f"Confidence: {self._ocr_success_count}/{self._ocr_total_count} scans OK"

    def _check_cache_reload(self):
        try:
            mtime = os.path.getmtime(MASTER_FILE) if os.path.exists(MASTER_FILE) else 0
            if mtime != self._master_db_mtime:
                self._master_db_cache = load_json_file(MASTER_FILE, {})
                self._master_db_mtime = mtime
        except OSError:
            pass
        try:
            mtime = os.path.getmtime(OWNED_FILE) if os.path.exists(OWNED_FILE) else 0
            if mtime != self._owned_mtime:
                self._owned_cache = load_json_file(OWNED_FILE, {"owned": []}).get("owned", [])
                self._owned_mtime = mtime
        except OSError:
            pass

    def _invalidate_owned_cache(self):
        self._owned_cache = load_json_file(OWNED_FILE, {"owned": []}).get("owned", [])
        self._owned_mtime = os.path.getmtime(OWNED_FILE) if os.path.exists(OWNED_FILE) else 0
        self.known_owned = set(self._owned_cache)

    # =========================================================================
    # REFRESH LOOP & TAB MANAGEMENT
    # =========================================================================

    def refresh_loop(self):
        self._forza_running_cache = has_running_forza_process() or has_running_forza_window()
        self.update_forza_session_state()
        self.detect_credit_popup_change()
        self._update_ocr_confidence_indicator()
        self._check_tracker_health()
        self._refresh_active_tab()
        if "session_credits_var" in self.vars:
            self.vars["session_credits_var"].set(format_credits(self.get_session_credits()))
        if self.credit_ocr_var.get():
            region = self.get_credit_region()
            if not self._forza_running_cache:
                self._live_ocr_status_var.set("OCR enabled — waiting for Forza to open")
            elif region:
                self._live_ocr_status_var.set("OCR active — balance region + fullscreen popup scanning")
            else:
                self._live_ocr_status_var.set("OCR active — fullscreen popup scanning only")
            bal = self.last_credit_balance or self.get_session_credits()
            self._live_ocr_balance_var.set(format_credits(bal))
            raw = self._last_ocr_raw_text
            self._live_ocr_raw_var.set((raw or "")[:80] or "(no scan yet)")
        self._refresh_after_id = self.after(self._refresh_interval_ms(), self.refresh_loop)

    def refresh_all(self):
        self.update_forza_session_state()
        self.refresh_collection()
        self._refresh_active_tab()

    def _refresh_active_tab(self):
        tab_map = {
            self.garage_tab: self.refresh_collection,
            self.live_tab: self.refresh_live_data,
            self.stats_tab: self.refresh_stats_panel,
            self.logs_tab: self.refresh_logs_panel,
            self.methods_tab: self.refresh_methods_panel,
            self.recommendations_tab: self.refresh_recommendations,
        }
        try:
            selected = self.notebook.select()
            for widget, refresh_fn in tab_map.items():
                if str(widget) == selected:
                    refresh_fn()
                    return
        except Exception:
            pass

    def refresh_live_data(self):
        latest = self.read_latest_telemetry_row()
        if latest:
            self.vars["rpm_var"].set(latest.get("rpm", "-"))
            self.vars["speed_var"].set(f"{latest.get('speed_mph', '-')} MPH")
            self.vars["car_id_var"].set(latest.get("car_id", "-"))
            self.vars["car_name_var"].set(latest.get("car_name", "-"))
            self.auto_register_from_telemetry(latest)
        else:
            self.vars["rpm_var"].set("-")
            self.vars["speed_var"].set("-")
            self.vars["car_id_var"].set("-")
            self.vars["car_name_var"].set("-")
            self.detected_car_id = None
            self.detection_status_var.set("Start the tracker and drive a car in Forza to auto-detect it.")

        if not self.credit_ocr_var.get():
            self._live_ocr_status_var.set("OCR disabled — enable in Settings tab")
            self._live_ocr_balance_var.set("")
            self._live_ocr_raw_var.set("")
            self._live_popup_var.set("")


    # =====================================================================
    # TELEMETRY-BASED CAR AUTO-DETECTION
    # =====================================================================
    def auto_register_from_telemetry(self, latest):
        car_id = latest.get("car_id")
        if not car_lookup.is_real_ordinal(car_id):
            self.detected_car_id = None
            self.detection_status_var.set("Waiting for gameplay... (in a menu or loading)")
            return

        self.detected_car_id = str(car_id)
        car_name = car_lookup.lookup_car_name(car_id)
        if car_name:
            was_new = car_lookup.add_owned_car(car_name)
            if was_new:
                self._invalidate_owned_cache()
                self.on_new_owned_car(car_name)
                self.detection_status_var.set(f"Detected: {car_name}  (ID {car_id}) — owned \u2713")
            else:
                self.detection_status_var.set(f"Detected: {car_name}  (ID {car_id}) — already owned")
        else:
            self.detection_status_var.set(
                f"Detected an unknown car (ID {car_id}). Click \u201cTag Detected Car\u201d to link it to your garage."
            )

    def on_new_owned_car(self, car_name):
        self.known_owned.add(car_name)
        self._last_auto_added_car = car_name
        self.show_notice(f"\u2713 Auto-added: {car_name}  (undo in Collection tab)")

    def show_notice(self, text):
        self.notice_var.set(text)
        if self._notice_after_id is not None:
            try:
                self.after_cancel(self._notice_after_id)
            except Exception:
                pass
        self._notice_after_id = self.after(10000, lambda: self.notice_var.set(""))

    def tag_detected_car(self):
        car_id = self.detected_car_id
        if not car_lookup.is_real_ordinal(car_id):
            messagebox.showinfo(
                "No car detected",
                "No car is being detected yet. Start the tracker and drive a car in Forza, then try again.",
            )
            return

        existing = car_lookup.lookup_car_name(car_id)
        prompt = f"Detected car ID {car_id}."
        if existing:
            prompt += f"\nCurrently linked to: {existing}.\nPick a car to re-link it, or Cancel to keep it."
        else:
            prompt += "\nPick the car you are driving to link it to this ID."

        chosen = self._choose_master_car("Tag Detected Car", prompt, preselect=existing)
        if not chosen:
            return

        car_lookup.save_mapping(car_id, chosen)
        was_new = car_lookup.add_owned_car(chosen)
        if was_new:
            self._invalidate_owned_cache()
            self.on_new_owned_car(chosen)
        else:
            self.show_notice(f"Linked ID {car_id} to {chosen}")
        self.refresh_all()

    def _choose_master_car(self, title, prompt, preselect=None):
        self._check_cache_reload()
        master_db = self._master_db_cache
        all_cars = sorted(name for name in master_db if name and name != "Year Make Model")
        if not all_cars:
            messagebox.showwarning("No car list", "The master car list is empty.")
            return None

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x460")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        ttk.Label(dialog, text=prompt, justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

        search_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=search_var).grid(row=1, column=0, sticky="ew", padx=10)

        listbox = tk.Listbox(dialog, font=("Consolas", 10))
        listbox.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=2, column=1, sticky="ns", pady=8)
        listbox.configure(yscrollcommand=scrollbar.set)

        result = {"car": None}

        def populate(*_):
            terms = [t for t in search_var.get().lower().split() if t]
            listbox.delete(0, tk.END)
            for car in all_cars:
                lowered = car.lower()
                if all(term in lowered for term in terms):
                    listbox.insert(tk.END, car)
            if preselect and preselect in all_cars and not terms:
                try:
                    idx = list(listbox.get(0, tk.END)).index(preselect)
                    listbox.selection_set(idx)
                    listbox.see(idx)
                except ValueError:
                    pass

        def confirm(*_):
            selection = listbox.curselection()
            if not selection:
                messagebox.showinfo("Pick a car", "Select a car from the list first.", parent=dialog)
                return
            result["car"] = listbox.get(selection[0])
            dialog.destroy()

        search_var.trace_add("write", populate)
        listbox.bind("<Double-1>", confirm)

        button_row = ttk.Frame(dialog)
        button_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=(0, 10))
        ttk.Button(button_row, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="Link Car", command=confirm).grid(row=0, column=1)

        populate()
        dialog.wait_window()
        return result["car"]

    def refresh_stats_panel(self):
        self._check_cache_reload()
        master_db = self._master_db_cache
        owned_names = sorted(self._owned_cache)
        total_cars = len(master_db)
        owned_count = len(owned_names)
        if total_cars:
            completion_pct = round((owned_count / total_cars) * 100, 1)
        else:
            completion_pct = 0.0
        self.stats_summary_var.set(f"{owned_count} owned • {total_cars - owned_count} still missing • {completion_pct}% complete")
        self.stats_progress_var.set(completion_pct)

        latest = self.read_latest_telemetry_row()
        if latest:
            self.stats_last_seen_var.set(f"{latest.get('car_name', '-')} ({latest.get('car_id', '-')})")
        else:
            self.stats_last_seen_var.set("No recent telemetry")

        session_credits = self.get_session_credits()
        self.stats_session_var.set(f"Session credits: {format_credits(session_credits)}")
        if self._forza_running_cache:
            self.stats_window_var.set("Forza window detected")
        else:
            self.stats_window_var.set("Waiting for Forza window...")

        owned_set = {normalize_car_name(n) for n in owned_names}
        total_value = sum(int(price) for car, price in master_db.items() if normalize_car_name(car) in owned_set)
        self.stats_total_value_var.set(f"Collection value: {format_credits(total_value)}")

        self._draw_credit_history()
        self._draw_credit_rate_chart()

    def _draw_credit_rate_chart(self):
        self.rate_canvas.delete("all")
        points = self._credit_rate_points
        if len(points) < 2:
            self.rate_canvas.create_text(
                self.rate_canvas.winfo_width() // 2 or 200, 60,
                text="No rate data yet — credits will appear here as they are detected.",
                fill="#888888", font=("Segoe UI", 10),
            )
            self.rate_stats_var.set("No rate data yet")
            return

        canvas_w = self.rate_canvas.winfo_width() or 500
        canvas_h = self.rate_canvas.winfo_height() or 120
        padding = 40
        chart_w = canvas_w - 2 * padding
        chart_h = canvas_h - 2 * padding

        now_mono = time.monotonic()
        window = 1800
        recent = [(t, b) for t, b in points if now_mono - t <= window]
        if len(recent) < 2:
            recent = points[-20:]

        t_min = recent[0][0]
        t_max = recent[-1][0]
        t_range = max(t_max - t_min, 1)
        b_vals = [b for _, b in recent]
        b_min = min(b_vals)
        b_max = max(b_vals)
        b_range = max(b_max - b_min, 1)

        self.rate_canvas.create_line(padding, canvas_h - padding, canvas_w - padding, canvas_h - padding, fill="#cccccc")
        self.rate_canvas.create_line(padding, padding, padding, canvas_h - padding, fill="#cccccc")

        coords = []
        for t, b in recent:
            x = padding + ((t - t_min) / t_range) * chart_w
            y = canvas_h - padding - ((b - b_min) / b_range) * chart_h
            coords.append((x, y))

        if len(coords) >= 2:
            flat = [c for p in coords for c in p]
            self.rate_canvas.create_line(*flat, fill="#1f6feb", width=2, smooth=True)

            # Moving average trendline (last 5 points)
            if len(coords) >= 3:
                window_avg = 5
                avg_coords = []
                for i in range(len(coords)):
                    start = max(0, i - window_avg + 1)
                    segment = coords[start:i + 1]
                    avg_x = sum(x for x, _ in segment) / len(segment)
                    avg_y = sum(y for _, y in segment) / len(segment)
                    avg_coords.append((avg_x, avg_y))
                if len(avg_coords) >= 2:
                    flat_avg = [c for p in avg_coords for c in p]
                    self.rate_canvas.create_line(*flat_avg, fill="#ff9800", width=1, dash=(4, 4))

            # Dots at each data point
            for x, y in coords:
                self.rate_canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#1f6feb", outline="")

        self.rate_canvas.create_text(padding, padding - 10, text=format_credits(b_max), anchor="w", fill="#555555", font=("Segoe UI", 8))
        self.rate_canvas.create_text(padding, canvas_h - padding + 10, text=format_credits(b_min), anchor="w", fill="#555555", font=("Segoe UI", 8))

        # Stats
        total_earned = b_vals[-1] - b_vals[0] if b_vals else 0
        elapsed_hrs = (recent[-1][0] - recent[0][0]) / 3600 if len(recent) >= 2 else 0
        avg_crhr = total_earned / max(elapsed_hrs, 0.001)
        self.rate_stats_var.set(
            f"Current rate: {format_credits(avg_crhr)}/hr | "
            f"Points: {len(recent)} | "
            f"Change: {'+' if total_earned >= 0 else ''}{format_credits(total_earned)} over {elapsed_hrs * 60:.0f} min"
        )

    def _load_credit_history(self):
        history_file = os.path.join(BASE_DIR, "credit_history.json")
        return load_json_file(history_file, {"sessions": []})

    def _save_credit_history(self, history):
        history_file = os.path.join(BASE_DIR, "credit_history.json")
        _safe_write_json(history_file, history)

    def _record_session_to_history(self):
        credits = self.get_session_credits()
        if credits <= 0:
            return
        history = self._load_credit_history()
        history["sessions"].append({
            "time": self.current_timestamp(),
            "credits": credits,
        })
        history["sessions"] = history["sessions"][-50:]
        self._save_credit_history(history)

    def _draw_credit_history(self):
        self.history_canvas.delete("all")
        history = self._load_credit_history()
        sessions = history.get("sessions", [])
        if not sessions:
            self.history_canvas.create_text(
                self.history_canvas.winfo_width() // 2 or 200,
                75,
                text="No session data yet. Complete a session to see history.",
                fill="#888888",
                font=("Segoe UI", 10),
            )
            return

        credits_list = [s.get("credits", 0) for s in sessions[-20:]]
        if not credits_list:
            return

        max_credits = max(credits_list) if max(credits_list) > 0 else 1
        canvas_w = self.history_canvas.winfo_width() or 400
        canvas_h = self.history_canvas.winfo_height() or 150
        padding = 40
        chart_w = canvas_w - 2 * padding
        chart_h = canvas_h - 2 * padding

        self.history_canvas.create_line(padding, canvas_h - padding, canvas_w - padding, canvas_h - padding, fill="#cccccc")
        self.history_canvas.create_line(padding, padding, padding, canvas_h - padding, fill="#cccccc")

        bar_width = max(4, chart_w // len(credits_list) - 4)
        for i, credits in enumerate(credits_list):
            x = padding + i * (chart_w / len(credits_list)) + 2
            bar_h = (credits / max_credits) * chart_h if max_credits > 0 else 0
            y0 = canvas_h - padding - bar_h
            y1 = canvas_h - padding
            color = "#4caf50" if credits > 0 else "#cccccc"
            self.history_canvas.create_rectangle(x, y0, x + bar_width, y1, fill=color, outline="")

        self.history_canvas.create_text(padding, padding - 10, text=format_credits(max_credits), anchor="w", fill="#555555", font=("Segoe UI", 8))
        self.history_canvas.create_text(padding, canvas_h - padding + 10, text="0", anchor="w", fill="#555555", font=("Segoe UI", 8))
        self.history_canvas.create_text(canvas_w // 2, canvas_h - 5, text=f"Last {len(credits_list)} sessions", fill="#555555", font=("Segoe UI", 9))

    def refresh_logs_panel(self):
        if not os.path.exists(LOG_FILE):
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert(tk.END, "No telemetry log yet.")
            self.log_text.configure(state="disabled")
            return

        try:
            with open(LOG_FILE, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, 8192)
                handle.seek(size - read_size)
                tail = handle.read().decode("utf-8", "replace")
            lines = [line for line in tail.splitlines() if line.strip()]
            display_lines = lines[-80:]
        except (OSError, UnicodeDecodeError):
            display_lines = []

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "\n".join(display_lines) if display_lines else "No telemetry log yet.")
        self.log_text.configure(state="disabled")

    def refresh_collection(self):
        self._check_cache_reload()
        master_db = self._master_db_cache
        owned_names = sorted(self._owned_cache)

        manufacturer = self.progress_manufacturer_var.get().strip()
        year = self.progress_year_var.get().strip()
        search = self.progress_search_var.get().strip()
        min_value = self.collection_min_value_var.get().strip()
        max_value = self.collection_max_value_var.get().strip()
        missing, owned_cars, total_cost = build_progress_data(
            master_db,
            owned_names,
            manufacturer=manufacturer or None,
            year=year or None,
            search=search or None,
            min_value=min_value or None,
            max_value=max_value or None,
        )

        sort_mode = self.collection_sort_var.get()
        if sort_mode == "Name Z-A":
            owned_cars.sort(key=lambda item: item[0], reverse=True)
        elif sort_mode == "Price Low-High":
            owned_cars.sort(key=lambda item: item[1])
        elif sort_mode == "Price High-Low":
            owned_cars.sort(key=lambda item: item[1], reverse=True)
        else:
            owned_cars.sort(key=lambda item: item[0])

        previous_selection = self.owned_listbox.curselection()
        self.owned_listbox.delete(0, tk.END)
        for car, price in owned_cars:
            if price > 0:
                self.owned_listbox.insert(tk.END, f"{car} | {format_credits(price)}")
            else:
                self.owned_listbox.insert(tk.END, car)
        if previous_selection:
            try:
                self.owned_listbox.selection_set(previous_selection[0])
                self.owned_listbox.activate(previous_selection[0])
            except Exception:
                pass

        missing_filter = self._missing_search_var.get().strip().lower()
        if missing_filter:
            filtered_missing = [(c, p) for c, p in missing if missing_filter in c.lower()]
        else:
            filtered_missing = missing

        self.unowned_listbox.delete(0, tk.END)
        display_limit = 120
        for car, price in filtered_missing[:display_limit]:
            self.unowned_listbox.insert(tk.END, f"{car} | {format_credits(price)}")
        if len(filtered_missing) > display_limit:
            self.unowned_listbox.insert(tk.END, f"--- {len(filtered_missing) - display_limit} more — refine your filter ---")

        total_owned_value = sum(price for _, price in owned_cars)
        total_missing = len(missing)
        self.collection_summary_var.set(
            f"{len(owned_names)} owned ({format_credits(total_owned_value)} value) • "
            f"{total_missing} still missing ({format_credits(total_cost)} to buy)"
            + (f" • Showing {len(filtered_missing)}" if missing_filter else "")
        )

    def clear_collection_filters(self):
        self.progress_search_var.set("")
        self.progress_manufacturer_var.set("")
        self.progress_year_var.set("")
        self.collection_min_value_var.set("")
        self.collection_max_value_var.set("")
        self.refresh_collection()

    def _validate_year_entry(self):
        raw = self.progress_year_var.get()
        cleaned = re.sub(r"[^0-9]", "", raw)
        if cleaned != raw:
            self.progress_year_var.set(cleaned)

    def populate_progress_manufacturers(self):
        self._check_cache_reload()
        master_db = self._master_db_cache
        manufacturers = sorted({car.split()[1] if len(car.split()) > 1 else "" for car in master_db.keys() if car})
        manufacturers = [m for m in manufacturers if m]
        self.progress_manufacturer_dropdown['values'] = manufacturers



    # =====================================================================
    # OCR CREDIT DETECTION
    # =====================================================================
    def _force_popup_scan(self):
        """Triggered by F5 — immediately captures screen and runs OCR for credit popups."""
        self._set_tesseract_path()
        self.show_notice("F5: scanning for popup...")
        self._scan_fullscreen_popups(time.monotonic(), force=True)

    def _scan_fullscreen_popups(self, now, force=False):
        """Detect screen changes and scan for credit popups.

        If a payout region is configured (post-race "Credits: XX,XXX" banner),
        grabs just that small region and runs OCR directly.

        Otherwise, every ~2s takes a tiny (160x90) snapshot and compares it to
        the previous frame.  If the screen changed significantly (a popup
        appeared), it immediately grabs a full-res frame and runs OCR on it.
        A forced full scan also runs every 12s as a safety net.

        When *force* is True (via F5 hotkey), all rate-limiting is skipped and
        a capture + OCR runs immediately.

        Returns True if a credit change was detected and handled, False otherwise.
        """
        self._set_tesseract_path()

        payout_region = self.get_payout_region()
        if payout_region is not None:
            return self._scan_payout_region(payout_region, force)

        detected_change = force
        if not force:
            # --- Change detection (fast, every ~2s) ---
            change_detect_interval = 2.0
            last_change_check = getattr(self, "_last_change_check_time", 0)
            if now - last_change_check >= change_detect_interval:
                self._last_change_check_time = now
                try:
                    if ImageGrab is not None:
                        tiny = ImageGrab.grab()
                    elif pyautogui is not None:
                        tiny = pyautogui.screenshot()
                    else:
                        return False
                    if Image is not None:
                        tiny = tiny.resize((160, 90), Image.LANCZOS)
                    # Convert to grayscale bytes for fast comparison
                    gray = tiny.convert("L") if Image is not None else tiny
                    thumb = list(gray.getdata()) if hasattr(gray, "getdata") else []
                    prev = getattr(self, "_prev_thumb", None)
                    if prev and len(thumb) == len(prev):
                        diff = sum(abs(a - b) for a, b in zip(thumb, prev)) / max(len(thumb), 1)
                        detected_change = diff > 8.0
                    self._prev_thumb = thumb
                except Exception:
                    pass

        if not force:
            # --- Periodic full-scan safety net ---
            # Run a full scan immediately on the very first call (last_full=0),
            # then every 12s after that.
            fullscreen_interval = 12
            last_full = getattr(self, "_last_fullscreen_scan_time", 0)
            if last_full == 0 or detected_change or now - last_full >= fullscreen_interval:
                self._last_fullscreen_scan_time = now
            else:
                return False

        try:
            if ImageGrab is not None:
                full = ImageGrab.grab()
            elif pyautogui is not None:
                full = pyautogui.screenshot()
            else:
                return False
        except Exception:
            return False

        return self._ocr_and_parse_image(full)

    def _scan_payout_region(self, region, force=False):
        """Grab just the payout banner region, OCR it, and look for a credit amount.

        The region is expected to capture a "Credits: 150,000" banner from the
        post-race payout screen.  Because the area is small and targeted, no
        change-detection thumbnail is needed — OCR is fast enough to run on
        every poll cycle.
        """
        image = self._grab_credit_image(region=region)
        if image is None:
            return False
        return self._ocr_and_parse_image(image)

    def _ocr_and_parse_image(self, image):
        """Run OCR on *image* and check the resulting text for a credit change.

        Shared by the payout-region path and the full-screen fallback path.
        Returns True if a credit change was detected and handled, False otherwise.
        """
        # Save original image for debug before any upscaling/modification
        self._save_debug_capture(image, "popup_raw")

        if Image is not None:
            image = self._upscale_for_ocr(image)

        w, h = image.size
        scale = min(800 / max(w, 1), 1.0)
        if scale < 1.0 and Image is not None:
            small = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        else:
            small = image

        try:
            text = pytesseract.image_to_string(small, config="--psm 6").strip()
        except Exception as exc:
            logger.warning("Full-screen OCR failed: %s", exc)
            return False

        if not text:
            return False

        change = detect_credit_change_from_text(text, self.last_credit_balance)
        if change is None or change == 0:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                tokens = line.split()
                if not tokens:
                    continue
                amount = parse_credit_number(tokens[0])
                if amount and amount >= 1000:
                    keywords = ["earn", "won", "reward", "received", "gain", "bonus", "+"]
                    if any(k in line.lower() for k in keywords):
                        change = amount
                        break
                # Also check last token for patterns like "50,000 CR"
                if len(tokens) > 1 and tokens[-1].lower() in ("cr", "credits", "credit"):
                    amount = parse_credit_number(tokens[-2])
                    if amount and amount >= 1000:
                        change = amount
                        break

        self._last_ocr_raw_text = text[:80] or text

        if change is not None and change != 0:
            if self.last_credit_balance is None:
                self.last_credit_balance = max(0, self.get_session_credits())
            old_balance = self.last_credit_balance
            self.update_session_credits(change)
            self.last_credit_balance += change
            self._log_credit_transaction(change, old_balance, self.last_credit_balance)
            self._ocr_success_count += 1
            self._live_popup_var.set(f"{format_credits(change)} at {datetime.now().strftime('%H:%M:%S')}")
            self.show_notice(f"Popup detected: {format_credits(change)}")
            return True

        return False

    def detect_credit_popup_change(self):
        # --- Entry gates ---
        # Only proceed if OCR dependencies are installed, the checkbox is enabled,
        # the rate-limit has elapsed, and Forza is detected as running.
        if pyautogui is None or pytesseract is None or ImageGrab is None:
            return
        if not self.credit_ocr_var.get():
            return

        now = time.monotonic()
        if now - self.last_credit_scan_time < self._ocr_interval_seconds():
            return
        if not self._forza_running_cache:
            return
        self.last_credit_scan_time = now
        self._ocr_total_count += 1
        self._last_ocr_scan_time = now

        # --- PATH A: Full-screen popup scanner ---
        # Runs change-detection every ~2s via thumbnail comparison. If a change is
        # detected (or every 12s as a safety net), it grabs a full screenshot and runs
        # OCR looking for reward/spend keywords ("earned", "won", "spent", etc.).
        # Popups are transient, so detected changes are applied immediately.
        popup_handled = self._scan_fullscreen_popups(now)
        if popup_handled:
            return

        # --- PATH B: Balance-region scanner ---
        # Requires a configured credit_region (set via Capture Area in Settings).
        # This path reads the HUD credit number directly using a tight screen crop,
        # upscaled for better OCR accuracy. It uses a confirmation-gating system:
        # the new balance must be read identically twice before recording a transaction.
        # This prevents OCR noise from creating phantom credit gains.
        region_set = self.get_credit_region() is not None
        if not region_set:
            return

        image = self._grab_credit_image()
        if image is None:
            logger.warning("Credit scan: _grab_credit_image returned None")
            return

        # Run OCR on the cropped region image.
        text = self._ocr_credit_text_from_image(image)
        self._last_ocr_raw_text = text or ""
        self._save_debug_capture(image, "balance", text)
        if not text or not text.strip():
            logger.warning("Credit scan: OCR returned empty text")
            return

        logger.warning("Credit scan: raw OCR = '%s'", (text or "")[:80])

        # Try to extract a balance number from the OCR text.
        # First pass: look for keyword patterns ("Credits:", "CR", etc.)
        # Second pass (fallback): numeric-only OCR with digit whitelist
        change = detect_credit_change_from_text(text, self.last_credit_balance) if text else None
        balance = parse_credit_balance_from_text(text) if text else None
        if balance is None:
            balance = parse_balance_number_only(self._ocr_numeric_text_from_image(image))
            logger.warning("Credit scan: numeric pass balance = %s", balance)

        if balance is not None:
            # ----- FIRST READ: No previous balance yet, just set the baseline -----
            if self.last_credit_balance is None:
                self.last_credit_balance = balance
                self._reset_pending_balance()
                self._recent_balances = [balance]
                self._ocr_success_count += 1
                self._credit_rate_points.append((now, balance))
                logger.warning("Credit scan: initial balance set to %s", balance)
                return

            # ----- SAME BALANCE: No change detected, reset pending counter -----
            if balance == self.last_credit_balance:
                self._reset_pending_balance()
                self._recent_balances.append(balance)
                if len(self._recent_balances) > 5:
                    self._recent_balances = self._recent_balances[-5:]
                self._ocr_success_count += 1
                return

            # ----- BALANCE CHANGED: Compute delta, check plausibility -----
            delta = balance - self.last_credit_balance
            logger.warning("Credit scan: delta=%s (last=%s, cur=%s)", delta, self.last_credit_balance, balance)
            # Reject if delta is negative (spend/spend) or implausibly large (OCR error)
            if delta > 100_000_000 or delta <= 0:
                logger.warning("Credit scan: delta %s rejected by plausibility check", delta)
                self._recent_balances.append(balance)
                if len(self._recent_balances) > 5:
                    self._recent_balances = self._recent_balances[-5:]
                return

            # Use median of recent readings as the baseline to reduce jitter
            median_balance = self._median_of_recent() or self.last_credit_balance

            # ----- CONFIRMATION GATE: require 2 consecutive identical reads -----
            # This prevents a single garbled OCR frame from injecting a phantom gain.
            if balance == self._pending_balance:
                self._pending_balance_count += 1
                logger.warning("Credit scan: pending balance confirmed count=%s", self._pending_balance_count)
            else:
                self._pending_balance = balance
                self._pending_balance_count = 1
                logger.warning("Credit scan: new pending balance=%s (count=1)", balance)
            if self._pending_balance_count < 2:
                return

            # ----- CONFIRMED: The same new balance appeared twice in a row -----
            confirmed_delta = balance - median_balance
            logger.warning("Credit scan: confirmed! delta=%s (median=%s, new_bal=%s)", confirmed_delta, median_balance, balance)
            if confirmed_delta > 0 and confirmed_delta <= 100_000_000:
                old_balance = self.last_credit_balance
                self.update_session_credits(confirmed_delta)
                self.last_credit_balance = balance
                self._log_credit_transaction(confirmed_delta, old_balance, balance)
                self._ocr_success_count += 1
                self._recent_balances.append(balance)
                if len(self._recent_balances) > 5:
                    self._recent_balances = self._recent_balances[-5:]
                self._reset_pending_balance()
                return
            logger.warning("Credit scan: confirmed_delta %s rejected", confirmed_delta)

            self._recent_balances.append(balance)
            if len(self._recent_balances) > 5:
                self._recent_balances = self._recent_balances[-5:]

        # ----- FALLBACK: Use detect_credit_change_from_text result directly -----
        # Only reached if the balance-parsing path above didn't return a result.
        # Handles cases where text contains keywords but no clean number extraction.
        if change is None or change <= 0:
            return

        if self.last_credit_balance is None:
            self.last_credit_balance = max(0, self.get_session_credits())

        old_balance = self.last_credit_balance
        self.update_session_credits(change)
        self.last_credit_balance = (self.last_credit_balance or 0) + change
        self._log_credit_transaction(change, old_balance, self.last_credit_balance)
        logger.warning("Credit scan: fallback popup change applied: %s", change)


    # =====================================================================
    # SESSION STATE MANAGEMENT
    # =====================================================================
    def load_session_state(self):
        state = load_json_file(SESSION_STATE_FILE, {})
        return {
            "session_started": state.get("session_started", False),
            "session_start_time": state.get("session_start_time"),
            "session_credits": state.get("session_credits", 0),
        }

    def save_session_state(self):
        _safe_write_json(SESSION_STATE_FILE, self.session_state)

    def get_session_credits(self):
        return self.session_state.get("session_credits", 0)

    def add_session_credits(self):
        try:
            amount = int(self.session_credit_entry.get().replace(",", ""))
        except ValueError:
            messagebox.showwarning("Invalid amount", "Enter a whole number of credits.")
            return

        if amount <= 0:
            messagebox.showwarning("Invalid amount", "Enter a positive number of credits.")
            return

        self.update_session_credits(amount)
        self.session_credit_entry.set("0")
        self.refresh_live_data()

    def update_session_credits(self, gain):
        self.session_state["session_started"] = True
        self.session_state["session_start_time"] = self.session_state.get("session_start_time") or self.current_timestamp()
        self.session_state["session_credits"] = self.session_state.get("session_credits", 0) + gain
        self.save_session_state()

    def reset_session(self):
        if not messagebox.askyesno("Reset session", "Reset the current session credit total?"):
            return
        self._record_session_to_history()
        self.session_state = {
            "session_started": True,
            "session_start_time": self.current_timestamp(),
            "session_credits": 0,
        }
        self.save_session_state()
        self.refresh_live_data()

    def start_new_session(self, auto=False):
        self.session_state = {
            "session_started": True,
            "session_start_time": self.current_timestamp(),
            "session_credits": 0,
        }
        self.save_session_state()
        self.last_credit_balance = None
        if auto:
            self.show_notice("New Forza session started — tracking credits from now.")

    def end_session(self, auto=False):
        total = self.session_state.get("session_credits", 0)
        self._record_session_to_history()
        self.session_state["session_started"] = False
        self.save_session_state()
        if auto:
            self.show_notice(f"Forza closed — this session earned {format_credits(total)} credits.")

    def update_forza_session_state(self):
        running_now = has_running_forza_process() or has_running_forza_window()
        if self.forza_running_prev is None:
            self.forza_running_prev = running_now
            return
        if running_now and not self.forza_running_prev:
            self.start_new_session(auto=True)
        elif not running_now and self.forza_running_prev:
            self.end_session(auto=True)
        self.forza_running_prev = running_now


    # =====================================================================
    # CREDIT/PAYOUT REGION GETTERS
    # =====================================================================
    def _read_region_fields(self):
        try:
            x = int(self.credit_x_var.get() or 0)
            y = int(self.credit_y_var.get() or 0)
            w = int(self.credit_w_var.get() or 0)
            h = int(self.credit_h_var.get() or 0)
        except (ValueError, AttributeError):
            return None
        if w > 0 and h > 0:
            return [x, y, w, h]
        return None

    def _adjust_region_for_forza_window(self, region, saved_forza_rect_key):
        """Offset *region* by the delta between the current Forza window position
        and the position when the region was captured, so OCR follows the window."""
        if not region:
            return region
        saved_rect = self.settings.get(saved_forza_rect_key)
        if not saved_rect or len(saved_rect) != 4:
            return region
        current_rect = get_forza_window_rect()
        if not current_rect:
            return region
        dx = current_rect[0] - saved_rect[0]
        dy = current_rect[1] - saved_rect[1]
        if dx == 0 and dy == 0:
            return region
        x, y, w, h = region
        return (x + dx, y + dy, w, h)

    def get_credit_region(self):
        region = self.settings.get("credit_region")
        if region and len(region) == 4 and int(region[2]) > 0 and int(region[3]) > 0:
            return self._adjust_region_for_forza_window(tuple(int(v) for v in region), "credit_region_forza_rect")
        return None

    def get_payout_region(self):
        region = self.settings.get("payout_region")
        if region and len(region) == 4 and int(region[2]) > 0 and int(region[3]) > 0:
            w, h = int(region[2]), int(region[3])
            if w > 800 or h > 200:
                return None
            return self._adjust_region_for_forza_window(tuple(int(v) for v in region), "payout_region_forza_rect")
        return None

    def _read_payout_region_fields(self):
        try:
            x = int(self.payout_x_var.get() or 0)
            y = int(self.payout_y_var.get() or 0)
            w = int(self.payout_w_var.get() or 0)
            h = int(self.payout_h_var.get() or 0)
        except (ValueError, AttributeError):
            return None
        if w > 0 and h > 0:
            return [x, y, w, h]
        return None


    # =====================================================================
    # SCREEN CAPTURE & OCR PRIMITIVES
    # =====================================================================
    def _save_debug_capture(self, image, label, text=None):
        """If debug logging is enabled, save *image* to the debug folder with a
        timestamped filename and write OCR text alongside it."""
        if not self.settings.get("ocr_debug_logging", False):
            return
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:23]
            img_path = os.path.join(DEBUG_DIR, f"{ts}_{label}.png")
            image.save(img_path)
            if text:
                txt_path = os.path.join(DEBUG_DIR, f"{ts}_{label}.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
            logger.info("Debug capture saved: %s", img_path)
        except Exception as exc:
            logger.warning("Failed to save debug capture: %s", exc)

    def _grab_credit_image(self, region=None):
        if region is None:
            region = self.get_credit_region()
        if not region:
            return None
        try:
            if ImageGrab is not None:
                x, y, w, h = region
                return ImageGrab.grab(bbox=(x, y, x + w, y + h))
            if pyautogui is not None:
                return pyautogui.screenshot(region=tuple(region))
        except Exception:
            return None
        return None

    def _upscale_for_ocr(self, image):
        """Enlarge a small capture so Tesseract reads small HUD digits reliably."""
        if Image is None:
            return image
        try:
            width, height = image.size
        except Exception:
            return image
        if not height or width * height > 1_200_000:
            return image
        image = image.convert("L")
        if ImageOps is not None:
            # A quiet border keeps glyphs off the capture edge; Tesseract mangles
            # characters that touch the border (e.g. reading "50" as "VU").
            image = ImageOps.expand(image, border=16, fill=255)
        if height < 120:
            scale = min(4, max(2, round(120 / height)))
            new_width, new_height = image.size
            image = image.resize((new_width * scale, new_height * scale), Image.BILINEAR)
        return image

    def _set_tesseract_path(self):
        if pytesseract is None:
            return
        path = self.tesseract_path_var.get().strip()
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return
        found = _find_tesseract()
        if found:
            pytesseract.pytesseract.tesseract_cmd = found
            return
        if os.name == 'nt':
            pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

    def _ocr_credit_text(self, region=None, numeric=False):
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            return ""
        self._set_tesseract_path()
        image = self._grab_credit_image(region=region)
        if image is None:
            return ""
        # A digit-only whitelist on a single line makes Tesseract far more accurate on
        # stylised HUD numbers (e.g. it stops reading a "7" as "/").
        config = "--psm 7 -c tessedit_char_whitelist=0123456789.,kKmM" if numeric else "--psm 6"
        try:
            return pytesseract.image_to_string(self._upscale_for_ocr(image), config=config).strip()
        except Exception as exc:
            logger.warning("OCR failed: %s", exc)
            return ""

    def _read_region_balance(self, region=None):
        """Read just the number from a boxed credit region using the numeric OCR pass."""
        return parse_balance_number_only(self._ocr_credit_text(region=region, numeric=True))

    def _ocr_credit_text_from_image(self, image):
        """Run OCR on an already-captured image (avoids re-capturing the screen)."""
        if pytesseract is None or image is None:
            return ""
        self._set_tesseract_path()
        try:
            return pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 6").strip()
        except Exception as exc:
            logger.warning("OCR from image failed: %s", exc)
            return ""

    def _ocr_numeric_text_from_image(self, image):
        """Run numeric-only OCR on an already-captured image."""
        if pytesseract is None or image is None:
            return ""
        self._set_tesseract_path()
        try:
            return pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 7 -c tessedit_char_whitelist=0123456789.,kKmM").strip()
        except Exception as exc:
            logger.warning("Numeric OCR failed: %s", exc)
            return ""


    # =====================================================================
    # OCR TEST & CALIBRATION UI
    # =====================================================================
    def test_credit_ocr(self):
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            messagebox.showwarning(
                "OCR unavailable",
                "Install OCR packages first: pip install pyautogui pytesseract Pillow, and install the Tesseract-OCR program.",
            )
            return
        raw_region = self._read_region_fields()
        capture_region = self.get_credit_region()
        if not capture_region:
            capture_region = raw_region

        # Capture the image and provide diagnostics.
        debug_lines = []
        if capture_region is None:
            debug_lines.append("Region: NOT SET (all zeros). Click 'Capture Area' first.")
        else:
            x, y, w, h = capture_region
            debug_lines.append(f"Region: x={x} y={y} w={w} h={h}")

        if os.name == 'nt':
            self._set_tesseract_path()
            debug_lines.append(f"Tesseract: {pytesseract.pytesseract.tesseract_cmd}")
            debug_lines.append(f"Exists: {os.path.isfile(pytesseract.pytesseract.tesseract_cmd)}")

        image = self._grab_credit_image(region=capture_region)
        if image is None:
            debug_lines.append("Capture: FAILED (image is None)")
            messagebox.showinfo("OCR test", "\n".join(debug_lines))
            return

        try:
            w_img, h_img = image.size
            debug_lines.append(f"Image size: {w_img}x{h_img}")
        except Exception:
            debug_lines.append("Image size: unknown")

        # Save a debug screenshot so the user can inspect what was captured.
        try:
            debug_path = os.path.join(BASE_DIR, "ocr_debug_capture.png")
            image.save(debug_path)
            debug_lines.append(f"Saved capture: {debug_path}")
        except Exception as exc:
            debug_lines.append(f"Could not save debug image: {exc}")

        try:
            text = pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 6").strip()
        except Exception as exc:
            debug_lines.append(f"Tesseract ERROR: {exc}")
            text = ""

        raw = " ".join(text.split())[:200]
        debug_lines.append(f"Raw text: {raw or '(nothing detected)'}")

        balance = parse_credit_balance_from_text(text)
        if balance is None and region is not None:
            # Try numeric-only pass.
            try:
                num_text = pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 7 -c tessedit_char_whitelist=0123456789.,kKmM").strip()
            except Exception as exc:
                num_text = ""
                debug_lines.append(f"Tesseract numeric pass ERROR: {exc}")
            debug_lines.append(f"Numeric text: {num_text or '(nothing detected)'}")
            balance = parse_balance_number_only(num_text)

        if balance is not None:
            debug_lines.insert(0, f"Detected balance: {format_credits(balance)} ({balance:,})")
            messagebox.showinfo("OCR test", "\n\n".join(debug_lines))
        else:
            debug_lines.insert(0, "No credit balance found in the captured area.")
            messagebox.showinfo("OCR test", "\n\n".join(debug_lines))

    def _test_popup_scan(self):
        self._popup_test_var.set("Scanning...")
        self.update_idletasks()
        if pytesseract is None or ImageGrab is None:
            self._popup_test_var.set("OCR not installed")
            return
        self._set_tesseract_path()

        payout_region = self.get_payout_region()
        if payout_region is not None:
            image = self._grab_credit_image(region=payout_region)
            if image is None:
                self._popup_test_var.set("Payout region capture failed")
                return
            if Image is not None:
                image = self._upscale_for_ocr(image)
            w, h = image.size
            scale = min(800 / max(w, 1), 1.0)
            if scale < 1.0 and Image is not None:
                small = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            else:
                small = image
        else:
            try:
                full = ImageGrab.grab()
            except Exception as exc:
                self._popup_test_var.set(f"Capture failed: {exc}")
                return
            w, h = full.size
            scale = min(800 / max(w, 1), 1.0)
            if scale < 1.0 and Image is not None:
                small = full.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            else:
                small = full
        try:
            text = pytesseract.image_to_string(small, config="--psm 6").strip()
        except Exception as exc:
            self._popup_test_var.set(f"OCR error: {exc}")
            return
        summary = text[:200].replace("\n", " | ")
        change = detect_credit_change_from_text(text)
        parts = [f"Raw: '{summary}'"]
        if payout_region is not None:
            parts.insert(0, "Using payout region")
        if change:
            parts.append(f"Change detected: {format_credits(change)}")
        else:
            parts.append("No credit change pattern found")
        self._popup_test_var.set(" | ".join(parts))

    def capture_credit_area(self):
        if self.settings.get("credit_region_locked", False):
            self.show_notice("Credit region is locked — unlock it in Settings to change.")
            return
        try:
            overlay = tk.Toplevel(self)
            overlay.attributes("-fullscreen", True)
            overlay.attributes("-alpha", 0.25)
            overlay.configure(bg="black", cursor="crosshair")
            canvas = tk.Canvas(overlay, bg="black", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            ttk.Label(overlay, text="Drag a box around your credit balance, then release. Press Esc to cancel.").place(x=20, y=20)

            state = {"start": None, "rect": None, "cstart": (0, 0)}

            def on_press(event):
                state["start"] = (event.x_root, event.y_root)
                state["cstart"] = (event.x, event.y)
                if state["rect"]:
                    canvas.delete(state["rect"])
                state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff00", width=2)

            def on_drag(event):
                if state["rect"]:
                    cx, cy = state["cstart"]
                    canvas.coords(state["rect"], cx, cy, event.x, event.y)

            def on_release(event):
                if not state["start"]:
                    overlay.destroy()
                    return
                x0, y0 = state["start"]
                x1, y1 = event.x_root, event.y_root
                overlay.destroy()
                x, y = int(min(x0, x1)), int(min(y0, y1))
                w, h = int(abs(x1 - x0)), int(abs(y1 - y0))
                if w > 4 and h > 4:
                    self.credit_x_var.set(str(x))
                    self.credit_y_var.set(str(y))
                    self.credit_w_var.set(str(w))
                    self.credit_h_var.set(str(h))
                    self.settings["credit_region"] = [x, y, w, h]
                    forza_rect = get_forza_window_rect()
                    self.settings["credit_region_forza_rect"] = list(forza_rect) if forza_rect else None
                    save_settings(self.settings)
                    self.show_notice(f"Region set to ({x}, {y}) {w}x{h} and saved.")

            def on_cancel(_event):
                overlay.destroy()

            canvas.bind("<ButtonPress-1>", on_press)
            canvas.bind("<B1-Motion>", on_drag)
            canvas.bind("<ButtonRelease-1>", on_release)
            overlay.bind("<Escape>", on_cancel)
            overlay.focus_force()

        except Exception as exc:
            messagebox.showerror("Capture failed", f"Could not open the capture overlay: {exc}")

    def capture_payout_area(self):
        if self.settings.get("payout_region_locked", False):
            self.show_notice("Payout region is locked — unlock it in Settings to change.")
            return
        try:
            overlay = tk.Toplevel(self)
            overlay.attributes("-fullscreen", True)
            overlay.attributes("-alpha", 0.25)
            overlay.configure(bg="black", cursor="crosshair")
            canvas = tk.Canvas(overlay, bg="black", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            ttk.Label(overlay, text="Drag a box around the payout banner (Credits: XX,XXX), then release. Press Esc to cancel.").place(x=20, y=20)

            state = {"start": None, "rect": None, "cstart": (0, 0)}

            def on_press(event):
                state["start"] = (event.x_root, event.y_root)
                state["cstart"] = (event.x, event.y)
                if state["rect"]:
                    canvas.delete(state["rect"])
                state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff00", width=2)

            def on_drag(event):
                if state["rect"]:
                    cx, cy = state["cstart"]
                    canvas.coords(state["rect"], cx, cy, event.x, event.y)

            def on_release(event):
                if not state["start"]:
                    overlay.destroy()
                    return
                x0, y0 = state["start"]
                x1, y1 = event.x_root, event.y_root
                overlay.destroy()
                x, y = int(min(x0, x1)), int(min(y0, y1))
                w, h = int(abs(x1 - x0)), int(abs(y1 - y0))
                if w > 4 and h > 4:
                    if w > 800 or h > 200:
                        self.show_notice("Region too large — drag a tight box around just the payout banner.")
                        overlay.destroy()
                        return
                    self.payout_x_var.set(str(x))
                    self.payout_y_var.set(str(y))
                    self.payout_w_var.set(str(w))
                    self.payout_h_var.set(str(h))
                    self.settings["payout_region"] = [x, y, w, h]
                    forza_rect = get_forza_window_rect()
                    self.settings["payout_region_forza_rect"] = list(forza_rect) if forza_rect else None
                    save_settings(self.settings)
                    self.show_notice(f"Payout region set to ({x}, {y}) {w}x{h} and saved.")

            def on_cancel(_event):
                overlay.destroy()

            canvas.bind("<ButtonPress-1>", on_press)
            canvas.bind("<B1-Motion>", on_drag)
            canvas.bind("<ButtonRelease-1>", on_release)
            overlay.bind("<Escape>", on_cancel)
            overlay.focus_force()

        except Exception as exc:
            messagebox.showerror("Capture failed", f"Could not open the capture overlay: {exc}")

    def test_payout_ocr(self):
        """Test OCR on the configured payout region and show diagnostic info."""
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            messagebox.showwarning(
                "OCR unavailable",
                "Install OCR packages first: pip install pyautogui pytesseract Pillow, and install the Tesseract-OCR program.",
            )
            return
        capture_region = self.get_payout_region() or self._read_payout_region_fields()

        debug_lines = []
        if capture_region is None:
            debug_lines.append("Payout region: NOT SET. Click 'Capture Area' first.")
        else:
            x, y, w, h = capture_region
            debug_lines.append(f"Payout region: x={x} y={y} w={w} h={h}")

        if os.name == 'nt':
            self._set_tesseract_path()
            debug_lines.append(f"Tesseract: {pytesseract.pytesseract.tesseract_cmd}")
            debug_lines.append(f"Exists: {os.path.isfile(pytesseract.pytesseract.tesseract_cmd)}")

        image = self._grab_credit_image(region=capture_region)
        if image is None:
            debug_lines.append("Capture: FAILED (image is None)")
            messagebox.showinfo("Payout OCR test", "\n".join(debug_lines))
            return

        try:
            w_img, h_img = image.size
            debug_lines.append(f"Image size: {w_img}x{h_img}")
        except Exception:
            debug_lines.append("Image size: unknown")

        try:
            debug_path = os.path.join(BASE_DIR, "ocr_debug_capture.png")
            image.save(debug_path)
            debug_lines.append(f"Saved capture: {debug_path}")
        except Exception as exc:
            debug_lines.append(f"Could not save debug image: {exc}")

        try:
            text = pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 6").strip()
        except Exception as exc:
            debug_lines.append(f"Tesseract ERROR: {exc}")
            text = ""

        raw = " ".join(text.split())[:200]
        debug_lines.append(f"Raw text: {raw or '(nothing detected)'}")

        change = detect_credit_change_from_text(text)
        if change:
            debug_lines.insert(0, f"Detected payout: {format_credits(change)} ({change:,})")
        else:
            debug_lines.insert(0, "No credit change pattern found in captured text.")

        messagebox.showinfo("Payout OCR test", "\n\n".join(debug_lines))

    def _refresh_ocr_preview(self):
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            self._preview_info_var.set("OCR packages not installed.")
            return
        region = self.get_credit_region() or self._read_region_fields()
        if region is None:
            self._preview_info_var.set("No region set — use Capture Area or Auto-Detect first.")
            return
        image = self._grab_credit_image(region=region)
        if image is None:
            self._preview_info_var.set("Failed to capture screen region.")
            return
        self._preview_image_ref = image
        self._preview_canvas.delete("all")
        try:
            cw = self._preview_canvas.winfo_width()
            if cw < 10:
                cw = 400
            ch = self._preview_canvas.winfo_height()
            if ch < 10:
                ch = 90
            img_w, img_h = image.size
            scale = min(cw / max(img_w, 1), ch / max(img_h, 1), 2.0)
            new_w = max(1, int(img_w * scale))
            new_h = max(1, int(img_h * scale))
            resized = image.resize((new_w, new_h), Image.LANCZOS) if Image is not None else image
            self._preview_photo = ImageTk.PhotoImage(resized)
            self._preview_canvas.create_image(cw // 2, ch // 2, image=self._preview_photo, anchor="center")
        except Exception as exc:
            self._preview_canvas.create_text(200, 45, text=f"Preview error: {exc}", fill="#ff6666")

        # Run a quick OCR read and show info
        try:
            self._set_tesseract_path()
            raw_text = pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 6").strip()
        except Exception:
            raw_text = ""
        balance = parse_credit_balance_from_text(raw_text)
        if balance is None and region is not None:
            try:
                num_text = pytesseract.image_to_string(self._upscale_for_ocr(image), config="--psm 7 -c tessedit_char_whitelist=0123456789.,kKmM").strip()
            except Exception:
                num_text = ""
            balance = parse_balance_number_only(num_text)
        x, y, w, h = region
        info = f"Region: ({x}, {y}) {w}x{h} | Raw: '{raw_text[:80]}'"
        if balance is not None:
            info += f" | Balance: {format_credits(balance)}"
        else:
            info += " | No balance detected"
        self._preview_info_var.set(info)

    def auto_calibrate_region(self):
        if self.settings.get("credit_region_locked", False):
            self.show_notice("Credit region is locked — unlock it in Settings to change.")
            return
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            messagebox.showwarning(
                "OCR unavailable",
                "Install OCR packages first: pip install pyautogui pytesseract Pillow, and install the Tesseract-OCR program.",
            )
            return
        if not (has_running_forza_process() or has_running_forza_window()):
            messagebox.showinfo("Auto-detect", "Forza does not appear to be running. Start the game first, then try again.")
            return
        self.show_notice("Auto-detecting credit region... wait a moment.")
        self.update_idletasks()

        full_image = None
        try:
            if ImageGrab is not None:
                full_image = ImageGrab.grab()
            elif pyautogui is not None:
                full_image = pyautogui.screenshot()
        except Exception as exc:
            messagebox.showerror("Auto-detect failed", f"Could not capture screen: {exc}")
            return
        if full_image is None:
            messagebox.showerror("Auto-detect failed", "Screen capture returned None.")
            return

        self._set_tesseract_path()
        img_w, img_h = full_image.size
        best_balance = None
        best_y = 0
        best_x = 0
        strip_h = 40

        digit_config = "--psm 7 -c tessedit_char_whitelist=0123456789.,kKmM"

        # Full-text pass first (looks for "CR"/"Credits" keywords — more specific)
        for y_start in range(0, int(img_h * 0.15), strip_h // 2):
            strip = full_image.crop((0, y_start, img_w, y_start + strip_h))
            strip = self._upscale_for_ocr(strip)
            try:
                text = pytesseract.image_to_string(strip, config="--psm 6").strip()
            except Exception:
                continue
            balance = parse_credit_balance_from_text(text)
            if balance is not None and balance >= 1000:
                best_balance = balance
                best_y = y_start
                break

        # Fallback: digit-only pass if keywords failed
        if best_balance is None:
            for y_start in range(0, int(img_h * 0.15), strip_h // 2):
                strip = full_image.crop((0, y_start, img_w, y_start + strip_h))
                strip = self._upscale_for_ocr(strip)
                try:
                    text = pytesseract.image_to_string(strip, config=digit_config).strip()
                except Exception:
                    continue
                balance = parse_balance_number_only(text)
                if balance is not None and balance >= 1000:
                    best_balance = balance
                    best_y = y_start
                    break

        if best_balance is None:
            self.show_notice("Auto-detect could not find the credit balance on screen.")
            messagebox.showinfo("Auto-detect", "Could not find the credit balance.\n\nTips:\n- Make sure Forza is showing the credit display (e.g. main menu or pause screen)\n- Try manually capturing the area instead")
            return

        # Refine: scan horizontally within the strip to find the number's left/right edges
        refine_strip = full_image.crop((0, best_y, img_w, best_y + strip_h))
        found_x_start = None
        found_x_end = None
        scan_step = 20
        for x_start in range(0, img_w - scan_step, scan_step):
            segment = refine_strip.crop((x_start, 0, x_start + scan_step, strip_h))
            segment = self._upscale_for_ocr(segment)
            try:
                seg_text = pytesseract.image_to_string(segment, config=digit_config).strip()
            except Exception:
                continue
            if any(c.isdigit() for c in seg_text):
                if found_x_start is None:
                    found_x_start = x_start
                found_x_end = x_start + scan_step

        if found_x_start is None:
            found_x_start = img_w // 2 - 150
            found_x_end = img_w // 2 + 150
        padding = 20
        detected_x = max(0, found_x_start - padding)
        detected_w = min(img_w - detected_x, found_x_end - found_x_start + padding * 2)
        detected_y = max(0, best_y - 5)
        detected_h = min(img_h - detected_y, strip_h + 15)

        self.credit_x_var.set(str(detected_x))
        self.credit_y_var.set(str(detected_y))
        self.credit_w_var.set(str(detected_w))
        self.credit_h_var.set(str(detected_h))

        # Auto-save so it takes effect immediately
        self.settings["credit_region"] = [detected_x, detected_y, detected_w, detected_h]
        forza_rect = get_forza_window_rect()
        self.settings["credit_region_forza_rect"] = list(forza_rect) if forza_rect else None
        save_settings(self.settings)

        self.show_notice(f"Auto-detected region: ({detected_x}, {detected_y}) {detected_w}x{detected_h} — balance ~{format_credits(best_balance)}")
        self._refresh_ocr_preview()

    def _update_ocr_confidence_indicator(self):
        color, text = self._ocr_confidence_label()
        color_map = {"green": "#137333", "yellow": "#b06000", "red": "#c5221f", "gray": "#888888"}
        self._conf_indicator_label.configure(foreground=color_map.get(color, "#888888"))
        self._ocr_confidence_text.set(text)
        raw = self._last_ocr_raw_text[:60] if self._last_ocr_raw_text else ""
        self._ocr_raw_text_var.set(raw or "(empty)")

    def current_timestamp(self):
        return datetime.now(timezone.utc).isoformat()

    def read_latest_telemetry_row(self):
        # Read only the header and the tail of the file rather than loading the whole
        # CSV every second; telemetry_log.csv can grow large, and parsing it in full on
        # each 1s refresh would get slower over time and add avoidable overhead.
        if not os.path.exists(LOG_FILE):
            return None
        try:
            with open(LOG_FILE, "rb") as handle:
                header_bytes = handle.readline()
                if not header_bytes:
                    return None
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, 4096)
                handle.seek(size - read_size)
                tail_bytes = handle.read()
                header = header_bytes.decode("utf-8", "replace")
                tail = tail_bytes.decode("utf-8", "replace")
                fieldnames = next(csv.reader([header]))
                lines = [line for line in tail.splitlines() if line.strip()]
                if not lines:
                    return None
                last = lines[-1]
                if last.strip() == header.strip():
                    return None
                values = next(csv.reader([last]))
                return dict(zip(fieldnames, values))
        except (OSError, csv.Error, StopIteration):
            return None


    # =====================================================================
    # CAR ADD / IMPORT / REMOVE UI
    # =====================================================================
    def add_car_manual(self):
        car_name = self.add_car_var.get().strip()
        if not car_name:
            messagebox.showwarning("Missing input", "Enter a car name first.")
            return
        owned = list(self._owned_cache)
        if car_name not in owned:
            owned.append(car_name)
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
            self.show_notice(f"Added {car_name} to your owned list.")
        else:
            self.show_notice(f"{car_name} is already in your owned list.")
        self.add_car_var.set("")
        self.refresh_all()

    def import_owned_cars_from_text(self):
        text = simpledialog.askstring("Import owned cars", "Paste your car names, one per line or separated by commas:")
        if text is None:
            return
        cars = parse_owned_cars_text(text)
        if not cars:
            messagebox.showwarning("No cars found", "Paste at least one car name first.")
            return

        owned = list(self._owned_cache)
        new_cars = [car for car in cars if car not in owned]
        if new_cars:
            owned.extend(new_cars)
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
        self.show_notice(f"Imported {len(new_cars)} new cars into your owned list.")
        self.refresh_all()

    def import_owned_cars_from_file(self):
        file_path = filedialog.askopenfilename(
            title="Choose a car list file",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not file_path:
            return
        try:
            cars = load_owned_cars_from_file(file_path)
        except Exception as exc:
            messagebox.showerror("Import failed", f"Could not read that file: {exc}")
            return
        if not cars:
            messagebox.showwarning("No cars found", "The selected file did not contain any readable car names.")
            return

        owned = list(self._owned_cache)
        new_cars = [car for car in cars if car not in owned]
        if new_cars:
            owned.extend(new_cars)
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
        self.show_notice(f"Imported {len(new_cars)} new cars from {os.path.basename(file_path)}.")
        self.refresh_all()

    def on_owned_list_select(self, event=None):
        selection = self.owned_listbox.curselection()
        if selection:
            self.last_selected_car = self.owned_listbox.get(selection[0])

    def add_selected_missing_car(self, event=None):
        selection = self.unowned_listbox.curselection()
        if not selection:
            return

        car_name = extract_car_name_from_list_entry(self.unowned_listbox.get(selection[0]))
        if not car_name:
            return

        owned = list(self._owned_cache)
        if car_name not in owned:
            owned.append(car_name)
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
        self.refresh_all()

    def remove_selected_car(self):
        selection = self.owned_listbox.curselection()
        if not selection:
            if hasattr(self, "last_selected_car") and self.last_selected_car:
                display = self.last_selected_car
            else:
                messagebox.showwarning("No selection", "Select a car from the owned list first.")
                return
        else:
            display = self.owned_listbox.get(selection[0])
            self.last_selected_car = display
        car_name = extract_car_name_from_list_entry(display)
        if not car_name:
            return
        if not messagebox.askyesno("Remove car", f"Remove '{car_name}' from your owned list?"):
            return
        owned = list(self._owned_cache)
        if car_name in owned:
            owned.remove(car_name)
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
        self.refresh_all()


    # =====================================================================
    # EXPORT & BACKUP
    # =====================================================================
    def export_owned_to_csv(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Owned Cars"
        )
        if not file_path:
            return
        owned = sorted(self._owned_cache)
        master_db = self._master_db_cache
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Car", "Price"])
            for car in owned:
                price = master_db.get(car, 0)
                writer.writerow([car, price])
        self.show_notice(f"Exported {len(owned)} cars to {os.path.basename(file_path)}")

    def export_collection_to_csv(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Full Collection"
        )
        if not file_path:
            return
        master_db = self._master_db_cache
        owned = set(self._owned_cache)
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Car", "Price", "Owned"])
            for car, price in sorted(master_db.items()):
                if car and car != "Year Make Model":
                    writer.writerow([car, price, "Yes" if car in owned else "No"])
        self.show_notice(f"Exported {len(master_db)} cars to {os.path.basename(file_path)}")

    def backup_all_data(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
            title="Backup All Data"
        )
        if not file_path:
            return
        data_files = [
            ("owned_cars.json", OWNED_FILE),
            ("gui_settings.json", SETTINGS_FILE),
            ("session_state.json", SESSION_STATE_FILE),
            ("methods_history.json", METHODS_FILE),
            ("credit_history.json", os.path.join(BASE_DIR, "credit_history.json")),
        ]
        try:
            with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, path in data_files:
                    if os.path.exists(path):
                        zf.write(path, name)
            self.show_notice(f"Created backup: {os.path.basename(file_path)}")
        except Exception as exc:
            messagebox.showerror("Backup failed", f"Could not create backup: {exc}")

    def restore_from_backup(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
            title="Restore from Backup"
        )
        if not file_path:
            return
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for member in zf.namelist():
                    resolved = os.path.realpath(os.path.join(BASE_DIR, member))
                    if not resolved.startswith(os.path.realpath(BASE_DIR) + os.sep):
                        raise ValueError(f"Path traversal detected: {member}")
                zf.extractall(BASE_DIR)
            self._invalidate_owned_cache()
            self._master_db_cache = load_json_file(MASTER_FILE, {})
            self._master_db_mtime = os.path.getmtime(MASTER_FILE) if os.path.exists(MASTER_FILE) else 0
            self.settings = load_settings()
            self.apply_theme(self.settings.get("theme", "light"))
            self.refresh_all()
            self.show_notice("Data restored from backup.")
        except Exception as exc:
            messagebox.showerror("Restore failed", f"Could not restore backup: {exc}")

    def show_shortcuts_help(self):
        shortcuts = [
            ("Ctrl+F", "Focus search box"),
            ("Ctrl+N", "Focus add car entry"),
            ("Ctrl+R", "Refresh all data"),
            ("Double-click Owned", "Remove car"),
            ("Double-click Missing", "Add car to owned"),
            ("Right-click Owned", "Context menu (remove, export, search)"),
            ("Right-click Missing", "Context menu (add, view details)"),
            ("F4 (in-game)", "Voice car tagging (if enabled)"),
        ]
        dialog = tk.Toplevel(self)
        dialog.title("Keyboard Shortcuts")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("400x350")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        tree = ttk.Treeview(dialog, columns=("Key", "Action"), show="headings")
        tree.heading("Key", text="Shortcut")
        tree.heading("Action", text="Action")
        tree.column("Key", width=120)
        tree.column("Action", width=260)
        tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        for key, action in shortcuts:
            tree.insert("", "end", values=(key, action))

        ttk.Button(dialog, text="Close", command=dialog.destroy).grid(row=1, column=0, pady=(0, 10))

    def _check_for_updates(self):
        self._update_status_var.set("Checking for updates...")
        self.update_idletasks()
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True, text=True, timeout=30,
                cwd=BASE_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "").strip()
                self._update_status_var.set(f"Update failed: {msg[:120]}")
                return
            output = result.stdout.strip()
            if output and "Already up to date" not in output:
                self._update_status_var.set("Updates applied! Restart to use the latest version.")
                self._update_pending = True
            else:
                self._update_status_var.set("Already up to date.")
        except FileNotFoundError:
            self._update_status_var.set("Git not found. Install git to enable updates.")
        except subprocess.TimeoutExpired:
            self._update_status_var.set("Update timed out (network issue?).")

    def _restart_app(self):
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        try:
            subprocess.Popen([pythonw, os.path.join(BASE_DIR, "fh6_gui.py")],
                             cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception as exc:
            messagebox.showerror("Restart failed", f"Could not restart: {exc}")
            return
        self._on_close()


    # =====================================================================
    # CONTEXT MENUS (Owned + Missing)
    # =====================================================================
    def _add_owned_context_menu(self):
        self.owned_context_menu = tk.Menu(self, tearoff=0)
        self.owned_context_menu.add_command(label="Remove Selected", command=self.remove_selected_car)
        self.owned_context_menu.add_command(label="Export Selected to CSV", command=self._export_selected_owned)
        self.owned_context_menu.add_separator()
        self.owned_context_menu.add_command(label="Search Online", command=self._search_selected_car)
        self.owned_context_menu.add_command(label="Copy Name", command=self._copy_selected_car_name)
        self.owned_listbox.bind("<Button-3>", self._show_owned_context_menu)
        self.owned_listbox.bind("<Control-Button-1>", self._show_owned_context_menu)

    def _add_missing_context_menu(self):
        self.missing_context_menu = tk.Menu(self, tearoff=0)
        self.missing_context_menu.add_command(label="Add to Owned", command=self.add_selected_missing_car)
        self.missing_context_menu.add_command(label="View Details", command=self._show_missing_car_details)
        self.missing_context_menu.add_separator()
        self.missing_context_menu.add_command(label="Search Online", command=self._search_selected_missing)
        self.missing_context_menu.add_command(label="Copy Name", command=self._copy_selected_missing_name)
        self.unowned_listbox.bind("<Button-3>", self._show_missing_context_menu)
        self.unowned_listbox.bind("<Control-Button-1>", self._show_missing_context_menu)

    def _show_owned_context_menu(self, event):
        try:
            self.owned_listbox.selection_clear(0, tk.END)
            self.owned_listbox.selection_set(self.owned_listbox.nearest(event.y))
            self.owned_listbox.activate(self.owned_listbox.nearest(event.y))
        except Exception:
            pass
        self.owned_context_menu.tk_popup(event.x_root, event.y_root)

    def _show_missing_context_menu(self, event):
        try:
            self.unowned_listbox.selection_clear(0, tk.END)
            self.unowned_listbox.selection_set(self.unowned_listbox.nearest(event.y))
            self.unowned_listbox.activate(self.unowned_listbox.nearest(event.y))
        except Exception:
            pass
        self.missing_context_menu.tk_popup(event.x_root, event.y_root)

    def _export_selected_owned(self):
        selection = self.owned_listbox.curselection()
        if not selection:
            return
        cars = [extract_car_name_from_list_entry(self.owned_listbox.get(i)) for i in selection]
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], title="Export Selected Cars")
        if not file_path:
            return
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Car", "Price"])
            for car in cars:
                writer.writerow([car, self._master_db_cache.get(car, 0)])
        self.show_notice(f"Exported {len(cars)} cars")

    def _search_selected_car(self):
        selection = self.owned_listbox.curselection()
        if selection:
            car = extract_car_name_from_list_entry(self.owned_listbox.get(selection[0]))
            webbrowser.open(f"https://www.google.com/search?q={car.replace(' ', '+')}+Forza+Horizon")

    def _search_selected_missing(self):
        selection = self.unowned_listbox.curselection()
        if selection:
            car = extract_car_name_from_list_entry(self.unowned_listbox.get(selection[0]))
            webbrowser.open(f"https://www.google.com/search?q={car.replace(' ', '+')}+Forza+Horizon")

    def _copy_selected_car_name(self):
        selection = self.owned_listbox.curselection()
        if selection:
            car = extract_car_name_from_list_entry(self.owned_listbox.get(selection[0]))
            self.clipboard_clear()
            self.clipboard_append(car)

    def _copy_selected_missing_name(self):
        selection = self.unowned_listbox.curselection()
        if selection:
            car = extract_car_name_from_list_entry(self.unowned_listbox.get(selection[0]))
            self.clipboard_clear()
            self.clipboard_append(car)

    def _show_missing_car_details(self):
        selection = self.unowned_listbox.curselection()
        if not selection:
            return
        car = extract_car_name_from_list_entry(self.unowned_listbox.get(selection[0]))
        self._check_cache_reload()
        price = self._master_db_cache.get(car, 0)
        messagebox.showinfo(car, f"Car: {car}\nPrice: {format_credits(price)} ({price:,} CR)")


    # =====================================================================
    # SMART RECOMMENDATIONS
    # =====================================================================
    def compute_recommendations(self, master_db, owned_names, method="all"):
        """Generate smart recommendations for missing cars."""
        owned_norm = {normalize_car_name(n) for n in owned_names if n}
        missing = []
        manufacturer_counts = Counter()
        year_counts = Counter()
        price_buckets = Counter()

        for car, price in master_db.items():
            if not car or car == "Year Make Model":
                continue
            norm = normalize_car_name(car)
            if norm in owned_norm:
                continue
            mfr = car.split()[1] if len(car.split()) > 1 else "Unknown"
            year = car.split()[0] if car.split()[0].isdigit() else "Unknown"
            price_val = int(price)
            missing.append((car, price_val, mfr, year))
            manufacturer_counts[mfr] += 1
            year_counts[year] += 1
            if price_val < 50000:
                price_buckets["Budget (<50K)"] += 1
            elif price_val < 200000:
                price_buckets["Mid-range (50K-200K)"] += 1
            elif price_val < 1000000:
                price_buckets["Premium (200K-1M)"] += 1
            else:
                price_buckets["Exotic (1M+)"] += 1

        if not missing:
            return []

        recommendations = []
        for car, price, mfr, year in missing:
            score = 0
            reasons = []

            if manufacturer_counts[mfr] > 5:
                score += 10
                reasons.append(f"Popular manufacturer ({manufacturer_counts[mfr]} missing)")

            if price < 50000:
                score += 15
                reasons.append("Budget-friendly")
            elif price < 200000:
                score += 10
                reasons.append("Good value")
            elif price < 500000:
                score += 5
                reasons.append("Mid-range")

            if year.isdigit() and int(year) >= 2020:
                score += 8
                reasons.append("Modern car")

            if "Forza Edition" in car or "FE" in car or "Pre Order" in car or "Pre-Order" in car:
                score += 20
                reasons.append("Special edition")

            reason_str = "; ".join(reasons) if reasons else "Standard pick"
            category = "Budget" if price < 50000 else "Mid-range" if price < 200000 else "Premium" if price < 1000000 else "Exotic"

            recommendations.append({
                "car": car,
                "price": price,
                "score": score,
                "reason": reason_str,
                "category": category,
                "manufacturer": mfr,
                "year": year,
            })

        if method == "value":
            recommendations.sort(key=lambda x: x["score"] / max(x["price"], 1), reverse=True)
        elif method == "cheapest":
            recommendations.sort(key=lambda x: x["price"])
        elif method == "score":
            recommendations.sort(key=lambda x: x["score"], reverse=True)
        elif method == "manufacturer":
            recommendations.sort(key=lambda x: (x["manufacturer"], -x["score"]))
        else:
            recommendations.sort(key=lambda x: x["score"] / max(x["price"], 1), reverse=True)

        return recommendations[:100]

    def refresh_recommendations(self):
        self._check_cache_reload()
        master_db = self._master_db_cache
        owned_names = sorted(self._owned_cache)

        filter_mode = self.rec_filter_var.get().lower().replace(" ", "_")
        if filter_mode == "best_value":
            method = "value"
        elif filter_mode == "cheapest_missing":
            method = "cheapest"
        elif filter_mode == "highest_rated":
            method = "score"
        elif filter_mode == "by_manufacturer":
            method = "manufacturer"
        else:
            method = "all"

        recs = self.compute_recommendations(master_db, owned_names, method)

        self.recommendations_tree.delete(*self.recommendations_tree.get_children())
        for r in recs:
            self.recommendations_tree.insert("", "end", values=(
                r["car"],
                format_credits(r["price"]),
                r["score"],
                r["reason"],
                r["category"],
            ), tags=(r["car"],))

        total_missing = len([c for c in master_db if normalize_car_name(c) not in {normalize_car_name(n) for n in owned_names}])
        self.rec_summary_var.set(
            f"Showing {len(recs)} of {total_missing} missing cars. "
            f"Top pick: {recs[0]['car']} ({format_credits(recs[0]['price'])}, score {recs[0]['score']})" if recs else "Collection complete!"
        )

    def add_recommended_to_owned(self):
        selection = self.recommendations_tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select one or more cars from the recommendations list.")
            return

        owned = list(self._owned_cache)
        added = 0
        for item_id in selection:
            car = self.recommendations_tree.item(item_id, "values")[0]
            if car not in owned:
                owned.append(car)
                added += 1

        if added:
            _safe_write_json(OWNED_FILE, {"owned": owned})
            self._invalidate_owned_cache()
            self.show_notice(f"Added {added} car(s) to your owned list.")
        else:
            self.show_notice("All selected cars are already in your owned list.")
        self.refresh_all()


    # =====================================================================
    # REGION LOCK
    # =====================================================================
    def _on_credit_lock_toggle(self):
        self.settings["credit_region_locked"] = self.credit_region_locked.get()
        save_settings(self.settings)

    def _on_payout_lock_toggle(self):
        self.settings["payout_region_locked"] = self.payout_region_locked.get()
        save_settings(self.settings)

    def _on_debug_toggle(self):
        self.settings["ocr_debug_logging"] = self.ocr_debug_var.get()
        save_settings(self.settings)
        if self.ocr_debug_var.get():
            try:
                os.makedirs(DEBUG_DIR, exist_ok=True)
                self.show_notice(f"OCR debug logging enabled — captures saved to {DEBUG_DIR}")
            except Exception as exc:
                self.show_notice(f"Could not create debug directory: {exc}")

    # =====================================================================
    # SETTINGS SAVE
    # =====================================================================
    def save_all_settings(self):
        previous_mode = self.settings.get("performance_mode")
        self.settings["auto_start_forza"] = bool(self.auto_start_var.get())
        self.settings["launch_tracker_on_start"] = bool(self.launch_tracker_var.get())
        self.settings["theme"] = self.theme_var.get() or "light"
        self.settings["credit_ocr_enabled"] = bool(self.credit_ocr_var.get())
        if not self.settings.get("credit_region_locked", False):
            self.settings["credit_region"] = self._read_region_fields()
        if not self.settings.get("payout_region_locked", False):
            self.settings["payout_region"] = self._read_payout_region_fields()
        # Re-anchor region to current Forza window position when manually saving
        forza_rect = get_forza_window_rect()
        if not self.settings.get("credit_region_locked", False):
            self.settings["credit_region_forza_rect"] = list(forza_rect) if forza_rect else self.settings.get("credit_region_forza_rect")
        if not self.settings.get("payout_region_locked", False):
            self.settings["payout_region_forza_rect"] = list(forza_rect) if forza_rect else self.settings.get("payout_region_forza_rect")
        self.settings["tesseract_path"] = self.tesseract_path_var.get().strip()
        self.settings["ocr_debug_logging"] = bool(self.ocr_debug_var.get())
        self.settings["performance_mode"] = self.performance_var.get() or car_lookup.DEFAULT_PERFORMANCE_MODE
        save_settings(self.settings)
        self.apply_theme(self.settings["theme"])
        # The tracker process reads the logging interval once at startup, so restart it to
        # pick up a changed performance mode.
        if self.settings["performance_mode"] != previous_mode and self.tracker_running:
            self.stop_tracker()
            self.after(500, self.start_tracker)
        if self.settings.get("auto_start_forza") and not self.tracker_running and (has_running_forza_window() or has_running_forza_process()):
            self.start_tracker()
        else:
            self.after(0, self._check_forza_auto_start)
        self.show_notice("Settings saved.")


    # =====================================================================
    # TRACKER START / STOP / HEALTH
    # =====================================================================
    def toggle_tracker(self):
        if self.tracker_running:
            self.stop_tracker()
        else:
            self.start_tracker()

    def start_tracker(self):
        if self.tracker_process and self.tracker_process.poll() is None:
            self.tracker_running = True
            self._update_tracker_button()
            return

        try:
            kwargs = {"cwd": BASE_DIR, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.tracker_process = subprocess.Popen([sys.executable, AUTO_LOG_PATH], **kwargs)
            self.tracker_running = True
            self.last_status = "Running"
            self.status_var.set("Status: Running")
            self._update_tracker_button()
        except Exception as exc:
            self.last_status = "Error"
            self.status_var.set(f"Status: Error ({exc})")
            messagebox.showerror("Launch failed", f"Could not start the tracker: {exc}")

    def stop_tracker(self):
        if self.tracker_process and self.tracker_process.poll() is None:
            self.tracker_process.terminate()
            try:
                self.tracker_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.tracker_process.kill()
        self.tracker_process = None
        self.tracker_running = False
        self.last_status = "Stopped"
        self.status_var.set("Status: Stopped")
        self._update_tracker_button()

    def _check_tracker_health(self):
        if not self.tracker_running:
            return
        if self.tracker_process is None:
            self.tracker_running = False
            self.last_status = "Crashed"
            self.status_var.set("Status: Tracker unavailable")
            self._update_tracker_button()
            self.show_notice("Telemetry tracker unavailable (port in use or process missing).")
            return
        if self.tracker_process.poll() is not None:
            self.tracker_process = None
            self.tracker_running = False
            self.last_status = "Crashed"
            self.status_var.set("Status: Tracker unavailable")
            self._update_tracker_button()
            self.show_notice("Telemetry tracker stopped. Credit OCR still active.")

    def _check_forza_auto_start(self):
        if not self.settings.get("auto_start_forza", False):
            return
        if self.tracker_running:
            return
        if has_running_forza_window() or has_running_forza_process():
            self.start_tracker()
            return
        self.after(3000, self._check_forza_auto_start)


    # =====================================================================
    # SESSION TIMER & THEME
    # =====================================================================
    def _update_session_timer(self):
        if self.session_state.get("session_started") and self.session_state.get("session_start_time"):
            try:
                start = datetime.fromisoformat(self.session_state["session_start_time"])
                elapsed = datetime.now(timezone.utc) - start
                hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                self.vars["session_time_var"].set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            except Exception:
                self.vars["session_time_var"].set("-")
        else:
            self.vars["session_time_var"].set("Not in session")
        self._session_timer_after_id = self.after(1000, self._update_session_timer)

    def apply_theme(self, theme_name):
        theme_name = theme_name or "light"
        self.settings["theme"] = theme_name
        if theme_name == "dark":
            bg = "#1f1f1f"
            fg = "#f5f5f5"
            field_bg = "#2b2b2b"
            accent = "#6ba8ff"
            button_bg = "#2f4f78"
        else:
            bg = "#f7f7f7"
            fg = "#222222"
            field_bg = "white"
            accent = "#1f6feb"
            button_bg = "#dce8ff"

        self.configure(bg=bg)
        self.style.theme_use("clam")
        self.style.configure(".", background=bg, foreground=fg, fieldbackground=field_bg, selectbackground=accent, selectforeground="white")
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabelframe", background=bg)
        self.style.configure("TLabelframe.Label", background=bg, foreground=fg)
        self.style.configure("TNotebook", background=bg, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=button_bg, foreground=fg, padding=[10, 4])
        self.style.map("TNotebook.Tab", background=[("selected", accent), ("active", button_bg)], foreground=[("selected", "white"), ("active", fg)])
        self.style.configure("TButton", background=button_bg, foreground=fg, bordercolor=accent)
        self.style.map("TButton", background=[("active", accent), ("pressed", accent)], foreground=[("active", "white")])
        self.style.configure("TEntry", fieldbackground=field_bg, foreground=fg)
        self.style.configure("TCombobox", fieldbackground=field_bg, foreground=fg, arrowcolor=fg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("TProgressbar", background=accent, troughcolor=field_bg, bordercolor=bg)
        self.style.configure("Treeview", background=field_bg, foreground=fg, fieldbackground=field_bg)
        self.style.configure("Treeview.Heading", background=button_bg, foreground=fg, fieldbackground=button_bg)
        self.style.map("Treeview.Heading", background=[("active", accent)])
        self.style.configure("Vertical.TScrollbar", background=button_bg, troughcolor=field_bg, arrowcolor=fg)
        self.style.configure("Horizontal.TScrollbar", background=button_bg, troughcolor=field_bg, arrowcolor=fg)
        self.style.configure("Listbox", background=field_bg, foreground=fg, selectbackground=accent)
        self.style.configure("Text", background=field_bg, foreground=fg, insertbackground=fg)
        if theme_name == "dark":
            self.style.configure("Canvas", background=field_bg)
        self.update_idletasks()
        for canvas in (getattr(self, "history_canvas", None), getattr(self, "rate_canvas", None),
                       getattr(self, "_preview_canvas", None)):
            if canvas:
                canvas.configure(bg=field_bg if theme_name == "dark" else "white")


    # =====================================================================
    # CLEANUP & CLOSE HANDLER
    # =====================================================================
    def _on_close(self):
        for after_id in (self._refresh_after_id, self._session_timer_after_id,
                         self._method_timer_after_id, self._notice_after_id):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        self.end_session()
        self.stop_tracker()
        self.destroy()


    def _update_tracker_button(self):
        self.start_stop_button.configure(text=tracker_button_label(self.tracker_running))


if __name__ == "__main__":
    app = FH6TrackerGUI()
    app.mainloop()
