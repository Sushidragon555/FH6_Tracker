import csv
import ctypes
import importlib
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import car_lookup
from import_owned_cars import load_owned_cars_from_file, parse_owned_cars_text, save_owned_cars

try:
    pyautogui = importlib.import_module("pyautogui")
    pytesseract = importlib.import_module("pytesseract")
    ImageGrab = importlib.import_module("PIL.ImageGrab")
except Exception:  # pragma: no cover - optional OCR dependencies
    pyautogui = None
    pytesseract = None
    ImageGrab = None

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_settings.json")


def format_credits(amount):
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return str(amount)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_LOG_PATH = os.path.join(BASE_DIR, "auto_log.py")
OWNED_FILE = os.path.join(BASE_DIR, "owned_cars.json")
MASTER_FILE = os.path.join(BASE_DIR, "fh6_master_list.json")
LOG_FILE = os.path.join(BASE_DIR, "telemetry_log.csv")
SESSION_STATE_FILE = os.path.join(BASE_DIR, "session_state.json")


def load_json_file(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return default
    return default


def parse_credit_number(value):
    cleaned = value.replace(",", "").replace(" ", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kKmM])?", cleaned)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix and suffix.lower() == "k":
        number *= 1000
    elif suffix and suffix.lower() == "m":
        number *= 1_000_000
    return int(number)


def parse_credit_balance_from_text(text):
    if not text:
        return None

    patterns = [
        r"\b(?:balance|credits? balance|current credits?|credit balance)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
        r"\b(?:credits?|cr)\b[^0-9]{0,20}([0-9][0-9,\.kKmM]*)",
        r"\b([0-9][0-9,\.kKmM]*)\s*(?:credits?|cr)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return parse_credit_number(match.group(1))
    return None


def detect_credit_change_from_text(text, previous_balance=None):
    if not text:
        return None

    lowered = text.lower()
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
        return amount if amount is not None else None

    return None


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=4)


def normalize_car_name(name):
    if not name:
        return ""
    lowered = name.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def parse_owned_cars_input(text):
    return parse_owned_cars_text(text)


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
            car_year = None
            match = re.match(r"^(\d{4})\s", car)
            if match:
                car_year = match.group(1)
            if car_year != str(year):
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


def load_settings():
    settings = load_json_file(SETTINGS_FILE, {})
    return {
        "auto_start_forza": settings.get("auto_start_forza", False),
        "launch_tracker_on_start": settings.get("launch_tracker_on_start", False),
        "theme": settings.get("theme", "light"),
        "credit_ocr_enabled": settings.get("credit_ocr_enabled", False),
        "credit_region": settings.get("credit_region"),
    }


def tracker_button_label(running):
    return "Stop Tracker" if running else "Start Tracker"


def is_forza_process_name(name):
    if not name:
        return False
    lowered = name.lower()
    return lowered.startswith("forzahorizon") or (lowered.startswith("forza") and "horizon" in lowered)


def get_running_process_names():
    if os.name == "nt":
        try:
            completed = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=False, timeout=10)
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
        self.forza_running_prev = None
        self.known_owned = set(load_json_file(OWNED_FILE, {"owned": []}).get("owned", []))
        self.detected_car_id = None
        self._notice_after_id = None
        self.style = ttk.Style(self)
        self.create_widgets()
        self.apply_theme(self.settings.get("theme", "light"))
        self.refresh_all()
        self.after(1000, self.refresh_loop)
        self.after(2000, self._check_forza_auto_start)

    def create_widgets(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

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
        controls.columnconfigure(3, weight=0)
        controls.columnconfigure(4, weight=1)

        self.start_stop_button = ttk.Button(controls, text=tracker_button_label(self.tracker_running), command=self.toggle_tracker)
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
        ttk.Button(controls, text="Save Settings", command=self.save_auto_start_setting).grid(row=0, column=2, padx=(8, 8), sticky="w")

        self.add_car_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.add_car_var, width=42).grid(row=1, column=0, columnspan=4, padx=(0, 8), pady=(8, 0), sticky="ew")
        ttk.Button(controls, text="Add Car", command=self.add_car_manual).grid(row=1, column=4, padx=(0, 8), pady=(8, 0), sticky="w")
        ttk.Button(controls, text="Import List", command=self.import_owned_cars_from_text).grid(row=1, column=5, padx=(0, 8), pady=(8, 0), sticky="w")
        ttk.Button(controls, text="Import File", command=self.import_owned_cars_from_file).grid(row=1, column=6, padx=(0, 8), pady=(8, 0), sticky="w")
        ttk.Button(controls, text="Remove Selected", command=self.remove_selected_car).grid(row=1, column=7, pady=(8, 0), sticky="w")

        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew")
        notebook.enable_traversal()

        self.garage_tab = ttk.Frame(notebook)
        self.live_tab = ttk.Frame(notebook)
        self.stats_tab = ttk.Frame(notebook)
        self.settings_tab = ttk.Frame(notebook)
        self.logs_tab = ttk.Frame(notebook)
        notebook.add(self.garage_tab, text="Collection")
        notebook.add(self.live_tab, text="Live Data")
        notebook.add(self.stats_tab, text="Stats")
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.logs_tab, text="Logs")

        self.build_garage_tab()
        self.build_live_tab()
        self.build_stats_tab()
        self.build_settings_tab()
        self.build_logs_tab()
        self.populate_progress_manufacturers()
        if self.launch_tracker_var.get():
            self.after(500, self.start_tracker)

    def build_garage_tab(self):
        self.garage_tab.columnconfigure(0, weight=1)
        self.garage_tab.rowconfigure(2, weight=1)

        summary_frame = ttk.LabelFrame(self.garage_tab, text="Summary")
        summary_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        summary_frame.columnconfigure(0, weight=1)

        self.collection_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(summary_frame, textvariable=self.collection_summary_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        filter_frame = ttk.LabelFrame(self.garage_tab, text="Filters")
        filter_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        filter_frame.columnconfigure(5, weight=1)

        ttk.Label(filter_frame, text="Search:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.progress_search_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.progress_search_var, width=32).grid(row=0, column=1, padx=(4, 8), pady=6, sticky="ew")

        ttk.Label(filter_frame, text="Manufacturer:").grid(row=0, column=2, sticky="w", padx=(8, 4), pady=6)
        self.progress_manufacturer_var = tk.StringVar()
        self.progress_manufacturer_dropdown = ttk.Combobox(filter_frame, textvariable=self.progress_manufacturer_var, width=20, state="readonly")
        self.progress_manufacturer_dropdown.grid(row=0, column=3, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Year:").grid(row=0, column=4, sticky="w", padx=(8, 4), pady=6)
        self.progress_year_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.progress_year_var, width=8).grid(row=0, column=5, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Min Value:").grid(row=0, column=6, sticky="w", padx=(8, 4), pady=6)
        self.collection_min_value_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.collection_min_value_var, width=12).grid(row=0, column=7, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Max Value:").grid(row=0, column=8, sticky="w", padx=(8, 4), pady=6)
        self.collection_max_value_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.collection_max_value_var, width=12).grid(row=0, column=9, padx=(4, 8), pady=6, sticky="w")

        ttk.Button(filter_frame, text="Apply", command=self.refresh_collection).grid(row=0, column=10, padx=(4, 8), pady=6)
        ttk.Button(filter_frame, text="Clear", command=self.clear_collection_filters).grid(row=0, column=11, padx=(0, 8), pady=6)

        self.collection_notebook = ttk.Notebook(self.garage_tab)
        self.collection_notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        self.owned_frame = ttk.Frame(self.collection_notebook)
        self.missing_frame = ttk.Frame(self.collection_notebook)
        self.collection_notebook.add(self.owned_frame, text="Owned")
        self.collection_notebook.add(self.missing_frame, text="Still Missing")

        self.owned_listbox = tk.Listbox(self.owned_frame, height=24, font=("Consolas", 10), selectmode=tk.EXTENDED)
        self.owned_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.owned_listbox.bind("<<ListboxSelect>>", self.on_owned_list_select)
        self.owned_listbox.bind("<Double-1>", lambda event: self.remove_selected_car())
        owned_scrollbar = ttk.Scrollbar(self.owned_frame, orient="vertical", command=self.owned_listbox.yview)
        owned_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.owned_listbox.configure(yscrollcommand=owned_scrollbar.set)
        self.owned_frame.columnconfigure(0, weight=1)
        self.owned_frame.rowconfigure(0, weight=1)

        self.unowned_listbox = tk.Listbox(self.missing_frame, height=24, font=("Consolas", 10))
        self.unowned_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.unowned_listbox.bind("<Double-1>", self.add_selected_missing_car)
        missing_scrollbar = ttk.Scrollbar(self.missing_frame, orient="vertical", command=self.unowned_listbox.yview)
        missing_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.unowned_listbox.configure(yscrollcommand=missing_scrollbar.set)
        self.missing_frame.columnconfigure(0, weight=1)
        self.missing_frame.rowconfigure(0, weight=1)

    def build_live_tab(self):
        self.live_tab.columnconfigure(0, weight=1)
        self.live_tab.rowconfigure(1, weight=1)

        info_frame = ttk.LabelFrame(self.live_tab, text="Current Session")
        info_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        info_frame.columnconfigure(1, weight=1)

        labels = [
            ("RPM", "rpm_var"),
            ("Speed", "speed_var"),
            ("Car ID", "car_id_var"),
            ("Car Name", "car_name_var"),
            ("Session Credits", "session_credits_var"),
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
        if pyautogui is None or pytesseract is None or ImageGrab is None:
            ttk.Label(self.live_tab, text="Automatic credit tracking needs OCR packages — see Settings → Automatic Credit Tracking.").grid(row=3, column=0, sticky="w", padx=10, pady=(2, 0))
        else:
            ttk.Label(self.live_tab, text="Automatic credit tracking runs when enabled in Settings and Forza is open (a new session starts each time the game opens).").grid(row=3, column=0, sticky="w", padx=10, pady=(2, 0))
        ttk.Label(self.live_tab, text="Press F4 and say the car name to save it to your owned list.").grid(row=4, column=0, sticky="w", padx=10, pady=(4, 0))

        detect_frame = ttk.LabelFrame(self.live_tab, text="Auto Garage Detection")
        detect_frame.grid(row=5, column=0, sticky="ew", padx=10, pady=(10, 0))
        detect_frame.columnconfigure(0, weight=1)
        self.detection_status_var = tk.StringVar(value="Start the tracker and drive a car in Forza to auto-detect it.")
        ttk.Label(detect_frame, textvariable=self.detection_status_var, wraplength=760, justify="left").grid(row=0, column=0, sticky="w", padx=8, pady=(6, 4))
        self.tag_detected_button = ttk.Button(detect_frame, text="Tag Detected Car", command=self.tag_detected_car)
        self.tag_detected_button.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

    def build_stats_tab(self):
        self.stats_tab.columnconfigure(0, weight=1)
        self.stats_tab.rowconfigure(1, weight=1)

        summary_frame = ttk.LabelFrame(self.stats_tab, text="Collection Progress")
        summary_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        summary_frame.columnconfigure(0, weight=1)

        self.stats_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(summary_frame, textvariable=self.stats_summary_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        self.stats_progress_var = tk.DoubleVar(value=0.0)
        self.stats_progress_bar = ttk.Progressbar(summary_frame, variable=self.stats_progress_var, maximum=100)
        self.stats_progress_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))

        details_frame = ttk.LabelFrame(self.stats_tab, text="Live Summary")
        details_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        details_frame.columnconfigure(1, weight=1)

        self.stats_last_seen_var = tk.StringVar(value="No recent telemetry")
        self.stats_session_var = tk.StringVar(value="Session credits: 0")
        self.stats_window_var = tk.StringVar(value="Waiting for Forza window...")
        labels = [
            ("Last seen car", self.stats_last_seen_var),
            ("Session credits", self.stats_session_var),
            ("Forza window", self.stats_window_var),
        ]
        for idx, (text, var) in enumerate(labels):
            ttk.Label(details_frame, text=f"{text}:").grid(row=idx, column=0, sticky="w", padx=8, pady=6)
            ttk.Label(details_frame, textvariable=var).grid(row=idx, column=1, sticky="w", padx=8, pady=6)

    def build_settings_tab(self):
        self.settings_tab.columnconfigure(0, weight=1)

        settings_frame = ttk.LabelFrame(self.settings_tab, text="Tracker Behavior")
        settings_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        settings_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(settings_frame, text="Auto-open with Forza", variable=self.auto_start_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(settings_frame, text="Start tracker on open", variable=self.launch_tracker_var).grid(row=1, column=0, sticky="w", padx=8, pady=6)

        ttk.Label(settings_frame, text="Theme:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(settings_frame, textvariable=self.theme_var, values=["light", "dark"], state="readonly", width=12).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=6)
        ttk.Button(settings_frame, text="Apply Settings", command=self.save_auto_start_setting).grid(row=3, column=0, sticky="w", padx=8, pady=(6, 8))

        ocr_frame = ttk.LabelFrame(self.settings_tab, text="Automatic Credit Tracking (OCR)")
        ocr_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ocr_frame.columnconfigure(5, weight=1)

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

        ttk.Button(ocr_frame, text="Capture Area", command=self.capture_credit_area).grid(row=1, column=5, sticky="w", padx=8)
        ttk.Button(ocr_frame, text="Test OCR", command=self.test_credit_ocr).grid(row=2, column=5, sticky="w", padx=8)
        ttk.Button(ocr_frame, text="Apply Settings", command=self.save_auto_start_setting).grid(row=3, column=0, sticky="w", padx=8, pady=(6, 8))

        if pyautogui is None or pytesseract is None or ImageGrab is None:
            ocr_status = "OCR packages not installed. Run: pip install pyautogui pytesseract Pillow, and install the Tesseract-OCR program."
        else:
            ocr_status = "OCR ready. Leave W/H at 0 to scan the full screen, or use Capture Area to box the credit number for faster, more reliable reads."
        self.ocr_status_var = tk.StringVar(value=ocr_status)
        ttk.Label(ocr_frame, textvariable=self.ocr_status_var, wraplength=760, justify="left", foreground="#8a6d00").grid(row=4, column=0, columnspan=6, sticky="w", padx=8, pady=(0, 8))

    def build_logs_tab(self):
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(self.logs_tab, wrap=tk.WORD, height=24)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_text.configure(state="disabled")

    def build_progress_tab(self):
        self.progress_tab.columnconfigure(0, weight=1)
        self.progress_tab.rowconfigure(2, weight=1)

        summary_frame = ttk.LabelFrame(self.progress_tab, text="Summary")
        summary_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        summary_frame.columnconfigure(1, weight=1)

        self.progress_var = tk.StringVar(value="Loading...")
        ttk.Label(summary_frame, textvariable=self.progress_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        filter_frame = ttk.LabelFrame(self.progress_tab, text="Filters")
        filter_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        filter_frame.columnconfigure(3, weight=1)

        ttk.Label(filter_frame, text="Search:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.progress_search_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.progress_search_var, width=32).grid(row=0, column=1, padx=(4, 8), pady=6, sticky="ew")

        ttk.Label(filter_frame, text="Manufacturer:").grid(row=0, column=2, sticky="w", padx=(8, 4), pady=6)
        self.progress_manufacturer_var = tk.StringVar()
        self.progress_manufacturer_dropdown = ttk.Combobox(filter_frame, textvariable=self.progress_manufacturer_var, width=20, state="readonly")
        self.progress_manufacturer_dropdown.grid(row=0, column=3, padx=(4, 8), pady=6, sticky="w")

        ttk.Label(filter_frame, text="Year:").grid(row=0, column=4, sticky="w", padx=(8, 4), pady=6)
        self.progress_year_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.progress_year_var, width=8).grid(row=0, column=5, padx=(4, 8), pady=6, sticky="w")

        ttk.Button(filter_frame, text="Apply", command=self.refresh_progress).grid(row=0, column=6, padx=(4, 8), pady=6)
        ttk.Button(filter_frame, text="Clear", command=self.clear_progress_filters).grid(row=0, column=7, padx=(0, 8), pady=6)

        self.progress_notebook = ttk.Notebook(self.progress_tab)
        self.progress_notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        self.missing_frame = ttk.Frame(self.progress_notebook)
        self.owned_frame = ttk.Frame(self.progress_notebook)
        self.progress_notebook.add(self.missing_frame, text="Still Missing")
        self.progress_notebook.add(self.owned_frame, text="Owned")

        self.unowned_listbox = tk.Listbox(self.missing_frame, height=22, font=("Consolas", 10))
        self.unowned_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        missing_scrollbar = ttk.Scrollbar(self.missing_frame, orient="vertical", command=self.unowned_listbox.yview)
        missing_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.unowned_listbox.configure(yscrollcommand=missing_scrollbar.set)
        self.missing_frame.columnconfigure(0, weight=1)
        self.missing_frame.rowconfigure(0, weight=1)

        self.owned_progress_listbox = tk.Listbox(self.owned_frame, height=22, font=("Consolas", 10))
        self.owned_progress_listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        owned_scrollbar = ttk.Scrollbar(self.owned_frame, orient="vertical", command=self.owned_progress_listbox.yview)
        owned_scrollbar.grid(row=0, column=1, sticky="ns", pady=8)
        self.owned_progress_listbox.configure(yscrollcommand=owned_scrollbar.set)
        self.owned_frame.columnconfigure(0, weight=1)
        self.owned_frame.rowconfigure(0, weight=1)

    def refresh_loop(self):
        self.refresh_all()
        self.after(1000, self.refresh_loop)

    def refresh_all(self):
        self.update_forza_session_state()
        self.refresh_collection()
        self.refresh_live_data()
        self.refresh_stats_panel()
        self.refresh_logs_panel()

    def refresh_owned_cars(self):
        self.refresh_collection()

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

        self.detect_credit_popup_change()

        session_credits = self.get_session_credits()
        self.vars["session_credits_var"].set(format_credits(session_credits))

    def auto_register_from_telemetry(self, latest):
        car_id = latest.get("car_id")
        if not car_lookup.is_real_ordinal(car_id):
            self.detected_car_id = None
            self.detection_status_var.set("Waiting for gameplay... (in a menu or loading)")
            return

        self.detected_car_id = str(car_id)
        car_name = car_lookup.lookup_car_name(car_id)
        if car_name:
            car_lookup.add_owned_car(car_name)
            if car_name not in self.known_owned:
                self.on_new_owned_car(car_name)
            self.detection_status_var.set(f"Detected: {car_name}  (ID {car_id}) — owned \u2713")
        else:
            self.detection_status_var.set(
                f"Detected an unknown car (ID {car_id}). Click \u201cTag Detected Car\u201d to link it to your garage."
            )

    def on_new_owned_car(self, car_name):
        self.known_owned.add(car_name)
        self.show_notice(f"\u2713 You now own: {car_name}")

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
        car_lookup.add_owned_car(chosen)
        if chosen not in self.known_owned:
            self.on_new_owned_car(chosen)
        else:
            self.show_notice(f"Linked ID {car_id} to {chosen}")
        self.refresh_all()

    def _choose_master_car(self, title, prompt, preselect=None):
        master_db = load_json_file(MASTER_FILE, {})
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

        ttk.Label(dialog, text=prompt, wraplength=490, justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

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
        master_db = load_json_file(MASTER_FILE, {})
        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned_names = sorted(owned_data.get("owned", []))
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
        if has_running_forza_window() or has_running_forza_process():
            self.stats_window_var.set("Forza window detected")
        else:
            self.stats_window_var.set("Waiting for Forza window...")

    def refresh_logs_panel(self):
        if not os.path.exists(LOG_FILE):
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert(tk.END, "No telemetry log yet.")
            self.log_text.configure(state="disabled")
            return

        with open(LOG_FILE, "r", newline="", encoding="utf-8") as handle:
            lines = handle.readlines()
        display_lines = lines[-80:] if len(lines) > 80 else lines
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "".join(display_lines))
        self.log_text.configure(state="disabled")

    def refresh_collection(self):
        master_db = load_json_file(MASTER_FILE, {})
        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned_names = sorted(owned_data.get("owned", []))

        manufacturer = self.progress_manufacturer_var.get().strip()
        year = self.progress_year_var.get().strip()
        search = self.progress_search_var.get().strip()
        min_value = self.collection_min_value_var.get().strip()
        max_value = self.collection_max_value_var.get().strip()
        missing, _, total_cost = build_progress_data(
            master_db,
            owned_names,
            manufacturer=manufacturer or None,
            year=year or None,
            search=search or None,
            min_value=min_value or None,
            max_value=max_value or None,
        )

        previous_selection = self.owned_listbox.curselection()
        self.owned_listbox.delete(0, tk.END)
        for car in owned_names:
            self.owned_listbox.insert(tk.END, car)
        if previous_selection:
            try:
                self.owned_listbox.selection_set(previous_selection[0])
                self.owned_listbox.activate(previous_selection[0])
            except Exception:
                pass

        self.unowned_listbox.delete(0, tk.END)
        for car, price in missing[:120]:
            self.unowned_listbox.insert(tk.END, f"{car} | {format_credits(price)}")

        total_missing = len(missing)
        self.collection_summary_var.set(f"{len(owned_names)} owned • {total_missing} still missing • {format_credits(total_cost)} remaining value")

    def clear_collection_filters(self):
        self.progress_search_var.set("")
        self.progress_manufacturer_var.set("")
        self.progress_year_var.set("")
        self.collection_min_value_var.set("")
        self.collection_max_value_var.set("")
        self.refresh_collection()

    def populate_progress_manufacturers(self):
        master_db = load_json_file(MASTER_FILE, {})
        manufacturers = sorted({car.split()[1] if len(car.split()) > 1 else "" for car in master_db.keys() if car})
        manufacturers = [m for m in manufacturers if m]
        self.progress_manufacturer_dropdown['values'] = manufacturers

    def detect_credit_popup_change(self):
        if pyautogui is None or pytesseract is None or ImageGrab is None:
            return
        if not self.settings.get("credit_ocr_enabled", False):
            return

        now = time.monotonic()
        if now - self.last_credit_scan_time < 3:
            return
        if not (has_running_forza_process() or has_running_forza_window()):
            return
        self.last_credit_scan_time = now

        text = self._ocr_credit_text()
        if not text or not text.strip():
            return

        change = detect_credit_change_from_text(text, self.last_credit_balance)
        balance = parse_credit_balance_from_text(text)

        if balance is not None:
            if self.last_credit_balance is None:
                self.last_credit_balance = balance
                return
            if balance > self.last_credit_balance:
                self.update_session_credits(balance - self.last_credit_balance)
            self.last_credit_balance = balance
            return

        if change is None or change <= 0:
            return

        if self.last_credit_balance is None:
            self.last_credit_balance = max(0, self.get_session_credits())

        self.update_session_credits(change)
        self.last_credit_balance = (self.last_credit_balance or 0) + change

    def load_session_state(self):
        state = load_json_file(SESSION_STATE_FILE, {})
        return {
            "session_started": state.get("session_started", False),
            "session_start_time": state.get("session_start_time"),
            "session_credits": state.get("session_credits", 0),
        }

    def save_session_state(self):
        with open(SESSION_STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(self.session_state, handle, indent=4)

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

    def get_credit_region(self):
        region = self.settings.get("credit_region")
        if region and len(region) == 4 and int(region[2]) > 0 and int(region[3]) > 0:
            return tuple(int(v) for v in region)
        return None

    def _grab_credit_image(self, region=None):
        if region is None:
            region = self.get_credit_region()
        try:
            if ImageGrab is not None:
                if region:
                    x, y, w, h = region
                    return ImageGrab.grab(bbox=(x, y, x + w, y + h))
                return ImageGrab.grab()
            if pyautogui is not None:
                if region:
                    return pyautogui.screenshot(region=tuple(region))
                return pyautogui.screenshot()
        except Exception:
            return None
        return None

    def _ocr_credit_text(self, region=None):
        image = self._grab_credit_image(region=region)
        if image is None:
            return ""
        try:
            return pytesseract.image_to_string(image)
        except Exception:
            return ""

    def test_credit_ocr(self):
        if pytesseract is None or (ImageGrab is None and pyautogui is None):
            messagebox.showwarning(
                "OCR unavailable",
                "Install OCR packages first: pip install pyautogui pytesseract Pillow, and install the Tesseract-OCR program.",
            )
            return
        region = self._read_region_fields()
        text = self._ocr_credit_text(region=region)
        raw = " ".join(text.split())[:200]
        balance = parse_credit_balance_from_text(text)
        if balance is not None:
            messagebox.showinfo("OCR test", f"Detected balance: {format_credits(balance)} ({balance:,})\n\nRaw text: {raw}")
        else:
            messagebox.showinfo("OCR test", f"No credit balance found in the captured area.\n\nRaw text: {raw or '(nothing detected)'}")

    def capture_credit_area(self):
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

            def on_cancel(_event):
                overlay.destroy()

            canvas.bind("<ButtonPress-1>", on_press)
            canvas.bind("<B1-Motion>", on_drag)
            canvas.bind("<ButtonRelease-1>", on_release)
            overlay.bind("<Escape>", on_cancel)
            overlay.focus_force()
        except Exception as exc:
            messagebox.showerror("Capture failed", f"Could not open the capture overlay: {exc}\nEnter the X/Y/W/H values manually instead.")

    def current_timestamp(self):
        from datetime import datetime, timezone
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

    def add_car_manual(self):
        car_name = self.add_car_var.get().strip()
        if not car_name:
            messagebox.showwarning("Missing input", "Enter a car name first.")
            return
        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned = owned_data.get("owned", [])
        if car_name not in owned:
            owned.append(car_name)
            with open(OWNED_FILE, "w", encoding="utf-8") as handle:
                json.dump({"owned": owned}, handle, indent=4)
            messagebox.showinfo("Car added", f"Added {car_name} to your owned list.")
        else:
            messagebox.showinfo("Already present", f"{car_name} is already in your owned list.")
        self.add_car_var.set("")
        self.refresh_all()

    def import_owned_cars_from_text(self):
        text = simpledialog.askstring("Import owned cars", "Paste your car names, one per line or separated by commas:")
        if text is None:
            return
        cars = parse_owned_cars_input(text)
        if not cars:
            messagebox.showwarning("No cars found", "Paste at least one car name first.")
            return

        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned = owned_data.get("owned", [])
        for car in cars:
            if car not in owned:
                owned.append(car)
        save_owned_cars(owned, OWNED_FILE)
        messagebox.showinfo("Import complete", f"Imported {len(cars)} cars into your owned list.")
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

        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned = owned_data.get("owned", [])
        for car in cars:
            if car not in owned:
                owned.append(car)
        save_owned_cars(owned, OWNED_FILE)
        messagebox.showinfo("Import complete", f"Imported {len(cars)} cars from {os.path.basename(file_path)}.")
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

        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned = owned_data.get("owned", [])
        owned = add_car_to_owned_list(owned, car_name)
        save_owned_cars(owned, OWNED_FILE)
        self.refresh_all()

    def remove_selected_car(self):
        selection = self.owned_listbox.curselection()
        if not selection:
            if hasattr(self, "last_selected_car") and self.last_selected_car:
                car_name = self.last_selected_car
            else:
                messagebox.showwarning("No selection", "Select a car from the owned list first.")
                return
        else:
            car_name = self.owned_listbox.get(selection[0])
            self.last_selected_car = car_name
        if not messagebox.askyesno("Remove car", f"Remove '{car_name}' from your owned list?"):
            return

        owned_data = load_json_file(OWNED_FILE, {"owned": []})
        owned = owned_data.get("owned", [])
        if car_name in owned:
            owned.remove(car_name)
            with open(OWNED_FILE, "w", encoding="utf-8") as handle:
                json.dump({"owned": owned}, handle, indent=4)
        self.refresh_all()

    def save_auto_start_setting(self):
        self.settings["auto_start_forza"] = bool(self.auto_start_var.get())
        self.settings["launch_tracker_on_start"] = bool(self.launch_tracker_var.get())
        self.settings["theme"] = self.theme_var.get() or "light"
        self.settings["credit_ocr_enabled"] = bool(self.credit_ocr_var.get())
        self.settings["credit_region"] = self._read_region_fields()
        save_settings(self.settings)
        self.apply_theme(self.settings["theme"])
        if self.settings.get("auto_start_forza") and not self.tracker_running and (has_running_forza_window() or has_running_forza_process()):
            self.start_tracker()
        else:
            self.after(0, self._check_forza_auto_start)
        messagebox.showinfo("Settings saved", "Tracker launch settings updated.")

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

    def _check_forza_auto_start(self):
        if not self.settings.get("auto_start_forza", False):
            return
        if self.tracker_running:
            return
        if has_running_forza_window() or has_running_forza_process():
            self.start_tracker()
            return
        self.after(3000, self._check_forza_auto_start)

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
        self.style.configure(".", background=bg, foreground=fg, fieldbackground=field_bg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabelframe", background=bg)
        self.style.configure("TLabelframe.Label", background=bg, foreground=fg)
        self.style.configure("TNotebook", background=bg, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=button_bg, foreground=fg)
        self.style.map("TNotebook.Tab", background=[("selected", accent), ("active", button_bg)], foreground=[("selected", "white"), ("active", fg)])
        self.style.configure("TButton", background=button_bg, foreground=fg)
        self.style.map("TButton", background=[("active", accent), ("pressed", accent)], foreground=[("active", "white")])
        self.style.configure("TEntry", fieldbackground=field_bg, foreground=fg)
        self.style.configure("TCombobox", fieldbackground=field_bg, foreground=fg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("Listbox", background=field_bg, foreground=fg)
        self.style.configure("Text", background=field_bg, foreground=fg)

    def _update_tracker_button(self):
        self.start_stop_button.configure(text=tracker_button_label(self.tracker_running))


if __name__ == "__main__":
    app = FH6TrackerGUI()
    app.mainloop()
