# FH6 Tracker

A **Forza Horizon 6** companion desktop app that tracks your car collection, automatically detects credit earnings via OCR, analyzes race telemetry, and helps you farm credits efficiently.

## About

> **TL;DR:** I got tired of manually tracking cars and guessing which credit farms were actually worth my time—so I coded a companion app to do the heavy lifting. *FH6 Tracker* automatically tracks your collection, logs your payouts live, and breaks down your driving telemetry so you can spend less time staring at spreadsheets and more time behind the wheel.

---

### Why I Built This
Look, I love the *Forza Horizon* series, but the grind can get tedious fast. Whether you're trying to collect every single car in the game or just trying to figure out if that 30-minute race was actually worth the credit payout, doing it manually sucks. 

I built this project to solve my own headache—to make tracking my garage seamless, optimize my credit farming, and actually understand my driving inputs. Over time, it turned into a full-blown tool that I knew other players could use to shave time off their laps and skip the repetitive grind.

---

### Is It Safe? (Zero Risk to Your Account)
**Short answer: 100% yes.** 

*FH6 Tracker* runs entirely on local data that Forza intentionally broadcasts through its built-in **Data Out** network feature. 

* No File Tampering:** It never modifies game files, memory, or scripts.
* No Save Risks:** It never touches your local or cloud save files.
* 100% Local:** It operates entirely on your PC and never sends data to external servers.

---#If you wanna send feedback on it, theres a way in settings tab. 

## Quick Start

### Download & Run (Takes about 2 minutes)

> **You need Python installed. That's the only requirement.**

**Step 1:** Go to the [Releases page](https://github.com/Sushidragon555/FH6_Tracker/releases) and download the latest source code `.zip`

**Step 2:** **Extract the zip** — right-click the file → **"Extract All..."** → pick a location → click **Extract**

**Step 3:** Open the extracted folder and **double-click `Start Here.bat`**

That's it — it installs Python packages automatically and launches the app.

> **Don't have Python?** Install it from [python.org](https://www.python.org/downloads/) first. **Important:** check the box that says **"Add Python to PATH"** during install, then run `Start Here.bat` again.

**Step 4:** In the app, go to the **Settings** tab and enable **"Auto-open with Forza"** so it starts tracking when you launch the game.

---

### Forza Telemetry Setup (Required)

In Forza Horizon 6, go to **Settings → Advanced** and enable **Data Out**:
- IP: `127.0.0.1`
- Port: `9999`
- Rate: **Fast** (60 Hz)

---

## Requirements

- **Windows** (required for telemetry and OCR features)
- **Python 3.10+** (free from [python.org](https://www.python.org/downloads/))
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
- **Race Type** — Auto-detected for racing disciplines (Road, Street, Dirt, Drag, Cross Country). **Drift** and **PR Stunts** are set manually — they aren't races, so the auto-detector won't assign them.

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

## Building a Standalone .exe (Optional)

To build the `.exe` yourself so others don't need Python:

```
pip install pyinstaller
python build_exe.py
```

Output will be in `dist/FH6_Tracker/`. Zip that folder and share it.

---

## Troubleshooting

- **OCR not working?** Make sure Tesseract is installed and the path is set correctly in Settings. Try "Test OCR" to verify.
- **Telemetry not connecting?** Verify Forza has Data Out enabled on port 9999. Check Windows Firewall isn't blocking localhost.
- **Car not detected?** Use "Tag Detected Car" to manually name the car ordinal.
- **App looks broken?** Try switching themes in Settings, or delete `gui_settings.json` to reset to defaults.

---

## License

This project is licensed under the [MIT License](LICENSE).
