# FH6 Tracker

A **Forza Horizon 6** companion desktop application that tracks your car collection, automatically detects credit earnings via OCR, analyzes race telemetry, and helps you farm credits efficiently.

Built with Python and Tkinter. Uses UDP telemetry from the game and Tesseract OCR for credit detection.

---

## Requirements

- **Windows** (required for telemetry and OCR features)
- **Python 3.10+** (added to PATH)
- **Forza Horizon 6** installed and running

### Optional (for automatic credit tracking)

- **[Tesseract-OCR](https://github.com/UB-Mannheim/tesseract/wiki)** -- the OCR engine. Auto-detected from common install paths, or set the path manually in Settings.
- Python packages (installed automatically on first run):
  - `pyautogui`
  - `pytesseract`
  - `Pillow`

---

## Installation

1. **Clone or download** this repository:
   ```
   git clone https://github.com/Sushidragon555/FH6_Tracker.git
   ```

2. **Double-click `Launch FH6 Tracker.bat`** -- this will:
   - Install all Python dependencies on first run
   - Auto-update from the repository (`git pull`)
   - Launch the application (no console window)

### Manual launch

```
python fh6_gui.py
```

### Running tests

```
pytest
```

---

## Features

### Collection Manager

Track your entire Forza Horizon 6 car collection.

- **Owned Cars** -- View all cars you own with their in-game value. Double-click to remove. Right-click for context menu (Export to CSV, Search Online, Copy Name).
- **Still Missing** -- See which cars you're missing, sorted by price. Double-click to add to owned. Shows total cost to complete your collection.
- **Filters** -- Search by name, manufacturer, year, or value range. Sort by name or price.
- **Import** -- Add cars by pasting a list or importing a `.txt`/`.csv` file. One car per line, or comma-separated.

### Live Data

Real-time telemetry from your game session.

- **Current Session** -- Live RPM, speed, car name, and credits earned. Session timer runs automatically when Forza is detected.
- **Manual Credits** -- Add credits manually if OCR misses something.
- **Race Recording** -- Start/stop recording detailed telemetry (speed, throttle, brake, steering) for post-race analysis. Use the **F6** hotkey in-game to toggle recording.
- **Auto Garage Detection** -- The app detects which car you're driving via telemetry and auto-adds it to your owned list. If the car isn't in the database, use "Tag Detected Car" to name it.

### Automatic Credit Tracking (OCR)

The app reads your credit balance from the screen automatically.

- **Balance Region** -- Captures the HUD credit number area. Uses a confirmation gate (2 consecutive identical reads) to prevent false positives.
- **Payout Popups** -- Detects post-race payout screens and logs the credits earned.
- **Force Scan** -- Press **F5** to trigger an immediate scan (bypasses rate limiting).
- **Debug Mode** -- Enable in Settings to save every OCR capture to `ocr_debug/` for troubleshooting.

### Method Tracking

Track which farming methods give the best CR/hr.

- **Track a Method** -- Select from presets (Wheelspins, Road Racing, Street Racing, etc.) and start tracking. The app records your starting credits, ending credits, and calculates CR/hr.
- **Method History** -- View past sessions and average CR/hr by method.
- **Credit Transactions** -- Auto-detected credit gains and spends are logged with timestamps and color-coded (green = gain, red = spend).

### Race Analysis

Analyze your driving from recorded races.

- **Speed, Throttle, Brake, Steering Charts** -- Visualize your inputs over the course of a race.
- **Race Summary** -- Average speed, max speed, throttle/brake percentages, gear usage breakdown.
- **Driving Tips** -- Algorithmic suggestions based on your telemetry (braking habits, throttle smoothness, RPM management).

### Stats & Progress

- **Collection Progress** -- Progress bar showing completion percentage.
- **Session Earnings History** -- Bar chart of credits earned per session.
- **Credit Rate** -- Live line chart of your balance over time with a moving average trendline.

### Recommendations

Smart suggestions for your next car purchase based on:
- Manufacturer popularity in your collection
- Price tier (budget vs. premium)
- Special editions and rare cars
- Filter by Best Value, Cheapest Missing, Highest Rated, or By Manufacturer

### Settings

- **Theme** -- Light or Dark mode
- **Performance Mode** -- Quality (1s refresh), Balanced (2s), or Performance (4s). Higher performance = less CPU usage.
- **OCR Configuration** -- Enable/disable, capture screen regions (drag overlay), set Tesseract path, lock regions.
- **Auto-open with Forza** -- Start tracking automatically when the game launches.
- **Export & Backup** -- Export owned cars or full collection to CSV. Backup all data to a ZIP file. Restore from backup.
- **Check for Updates** -- Pull latest changes from the repository. Restart the app to apply.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+F` | Focus search bar (Collection tab) |
| `Ctrl+N` | Focus "Add Car" entry |
| `Ctrl+R` | Refresh all data |
| `F5` | Force-scan for credit popups |
| `F4` | Voice car tagging (in-game, via telemetry subprocess) |
| `F6` | Toggle race recording (in-game) |
| `Esc` | Cancel region capture overlay |

### Mouse Controls

- **Double-click** an owned car to remove it
- **Double-click** a missing car to add it
- **Right-click** for context menu with more options
- **Mouse wheel** to scroll Settings tab

---

## Data Files

All data is stored locally as JSON files:

| File | Description |
|---|---|
| `owned_cars.json` | Your owned car list |
| `gui_settings.json` | Application settings |
| `session_state.json` | Current session data |
| `credit_transactions.json` | Auto-detected credit changes |
| `credit_history.json` | Historical session earnings |
| `methods_history.json` | Method tracking history |
| `telemetry_log.csv` | Raw telemetry data |
| `races/` | Saved race recordings |

---

## How It Works

### Telemetry

The app spawns a background process that listens on UDP port 9999 for Forza Horizon telemetry packets. These 324-byte packets contain car ID, RPM, speed, throttle, brake, steering, gear, and more. The telemetry process logs data to CSV and communicates with the GUI via signal files.

**To enable telemetry in Forza:**
1. Go to **Settings > Advanced** in Forza Horizon 6
2. Enable **Data Out**
3. Set the IP to `127.0.0.1` and port to `9999`
4. Set the rate to **Fast** (60 Hz)

### OCR Credit Detection

The app captures a small region of your screen where the credit balance is displayed, runs Tesseract OCR to read the number, and tracks changes over time. Key techniques:

- Screen captures are **upscaled 2-4x** for better OCR accuracy
- A **confirmation gate** requires 2 consecutive identical reads before recording a change
- **Region auto-adjustment** follows the game window if you move it
- Credit parsing handles OCR noise (commas vs dots, K/M suffixes)

### Performance

The app is designed to be lightweight. In Performance mode, it only reads the screen every 12 seconds and refreshes the UI every 4 seconds, minimizing impact on your game.

---

## Troubleshooting

- **OCR not working?** Make sure Tesseract is installed and the path is set correctly in Settings. Try "Test OCR" to verify. Enable debug logging to see captured images in `ocr_debug/`.
- **Telemetry not connecting?** Verify Forza has Data Out enabled on port 9999. Check Windows Firewall isn't blocking localhost.
- **Car not detected?** Use "Tag Detected Car" to manually name the car ordinal. The reference database covers 600+ cars.
- **App looks broken?** Try switching themes in Settings, or delete `gui_settings.json` to reset to defaults.

---

## License

See repository for license details.
