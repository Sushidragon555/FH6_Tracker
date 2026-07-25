# FH6 Tracker

A **Forza Horizon 6** companion desktop app that tracks your car collection, automatically detects credit earnings via OCR, analyzes race telemetry, and helps you farm credits efficiently.

### About

This is a personal project built out of genuine love for the Forza Horizon series. It started as a way to help with the grind — tracking which cars I own, figuring out the most efficient farming methods, and understanding my driving habits through telemetry. Over time it grew into a full-featured companion app that I hope helps other players improve their experience and capabilities in the game. Whether you're a completionist trying to collect every car or a racer looking to shave seconds off your times, this tool is here to make the grind a little easier and a lot more fun.

**Is it safe?** Yes — FH6 Tracker reads data that Forza Horizon 6 intentionally broadcasts over a local network port (a built-in feature called "Data Out"). It never modifies game files, never touches your save data, and never connects to the internet. Everything stays on your PC. It's the same principle used by tools like SimHub and telemetry overlays that the racing community has trusted for years.

<!-- Add a screenshot of the main window here -->
<!-- ![FH6 Tracker Main](screenshots/main.png) -->

---

## Quick Start

### Option 1 — Download the .exe (Recommended, no Python needed)

> **This is the easiest way to get started. Takes about 30 seconds.**

**Step 1:** Go to the [Releases page](https://github.com/Sushidragon555/FH6_Tracker/releases)

**Step 2:** Click on the **latest version** at the top, then click **`FH6_Tracker.zip`** to download it

**Step 3:** Open the downloaded `.zip` file. **Extract the entire folder** somewhere on your PC (Desktop, Documents, wherever you want)

> **Tip:** Right-click the `.zip` file → **"Extract All..."** → choose a location → click **Extract**

**Step 4:** Open the extracted folder and **double-click `FH6_Tracker.exe`**

That's it — the app will open. No installation, no Python, no setup.

**Step 5 (Important):** In the app, go to the **Settings** tab and enable **"Auto-open with Forza"** so it starts tracking automatically when you launch the game.

---

### Option 2 — Run from Source (Requires Python)

> **Only needed if you want to modify the code or contribute.**

1. **Install Python 3.10+** from [python.org](https://www.python.org/downloads/) — **check "Add Python to PATH"** during install
2. **Download or clone** this repository
3. **Double-click `Start Here.bat`** — it installs everything and launches the app

Or if you prefer the command line:
```
pip install -r requirements.txt
python fh6_gui.py
```

---

## Requirements

- **Windows** (required for telemetry and OCR features)
- **Forza Horizon 6** installed and running

### Optional (for automatic credit tracking)

- **[Tesseract-OCR](https://github.com/UB-Mannheim/tesseract/wiki)** — the OCR engine. Auto-detected from common install paths, or set the path manually in Settings. Only needed if you want automatic credit tracking from screen reading.

---

## Features

### Collection Manager

Track your entire Forza Horizon 6 car collection.

- **Owned Cars** — View all cars you own with their in-game value. Double-click to remove. Right-click for context menu (Export to CSV, Search Online, Copy Name).
- **Still Missing** — See which cars you're missing, sorted by price. Double-click to add to owned. Shows total cost to complete your collection.
- **Filters** — Search by name, manufacturer, year, or value range. Sort by name or price.
- **Import** — Add cars by pasting a list or importing a `.txt`/`.csv` file.

<!-- Add a screenshot of the collection tab here -->
<!-- ![Collection Tab](screenshots/collection.png) -->

### Live Data

Real-time telemetry from your game session.

- **Current Session** — Live RPM, speed, car name, and credits earned.
- **Race Recording** — Start/stop recording detailed telemetry for post-race analysis. Use **F6** hotkey in-game.
- **Auto Garage Detection** — Detects which car you're driving and auto-adds it to your owned list.

<!-- Add a screenshot of the live data tab here -->
<!-- ![Live Data](screenshots/live_data.png) -->

### Automatic Credit Tracking (OCR)

The app reads your credit balance from the screen automatically.

- **Balance Region** — Captures the HUD credit number area with a confirmation gate to prevent false positives.
- **Payout Popups** — Detects post-race payout screens and logs credits earned.
- **Force Scan** — Press **F5** to trigger an immediate scan.

### Race Analysis

Analyze your driving from recorded races.

- **Speed, Throttle, Brake, Steering Charts** — Visualize your inputs over the course of a race.
- **Race Summary** — Average speed, max speed, throttle/brake percentages, gear usage.
- **Driving Tips** — Algorithmic suggestions based on your telemetry.

<!-- Add a screenshot of race analysis here -->
<!-- ![Race Analysis](screenshots/race_analysis.png) -->

### Method Tracking

Track which farming methods give the best CR/hr.

- **Track a Method** — Select from presets (Wheelspins, Road Racing, Street Racing, etc.) and start tracking.
- **Method History** — View past sessions and average CR/hr by method.

### Recommendations

Smart suggestions for your next car purchase based on manufacturer popularity, price tier, special editions, and rare cars.

### Settings

- **Theme** — Light or Dark mode
- **Performance Mode** — Quality (1s), Balanced (2s), or Performance (4s) refresh rates
- **Auto-open with Forza** — Start tracking automatically when the game launches
- **Export & Backup** — Export owned cars to CSV, backup/restore all data as ZIP

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+F` | Focus search bar (Collection tab) |
| `Ctrl+N` | Focus "Add Car" entry |
| `Ctrl+R` | Refresh all data |
| `F4` | Voice car tagging (in-game) |
| `F5` | Force-scan for credit popups |
| `F6` | Toggle race recording |
| `F7` | Toggle method tracking (CR/hr timer) |

---

## Forza Telemetry Setup

1. Go to **Settings > Advanced** in Forza Horizon 6
2. Enable **Data Out**
3. Set IP to `127.0.0.1` and port to `9999`
4. Set rate to **Fast** (60 Hz)

---

## Building from Source

To build the `.exe` yourself:

```
pip install pyinstaller
python build_exe.py
```

Output will be in `dist/FH6_Tracker/`.

---

## Troubleshooting

- **OCR not working?** Make sure Tesseract is installed and the path is set correctly in Settings. Try "Test OCR" to verify.
- **Telemetry not connecting?** Verify Forza has Data Out enabled on port 9999. Check Windows Firewall isn't blocking localhost.
- **Car not detected?** Use "Tag Detected Car" to manually name the car ordinal.
- **App looks broken?** Try switching themes in Settings, or delete `gui_settings.json` to reset to defaults.

---

## Running Tests

```
pytest
```
