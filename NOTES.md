# FH6 Tracker — Working Notes

Internal notes for us. Not for end users. Scratch-pad for ideas and the plan.

## Current state (as of Aug 2026)

- Race recording + analysis: lap splits, corner cuts / wide exits, crash detection,
  drift slides, off-line deviation reference-lap alignment (arc-length).
- Driving tips: corner context (e.g. "never slowed for it"), repeat-mistake
  ("same corner wrong 3 laps"), crash advice, per-race-type tips.
- Race path chart: wheel-zoom / drag-pan / double-click reset, crash diamonds,
  corner-cut markers.
- Drift mode: manual race-type lock (auto-detect can't override Drift/Drag),
  Steer/Handbrake chart, new Yaw-rate chart, RPM/Power/Gear/Inputs hidden.
- Overlay + payout OCR + car lookup + owned-car import (committed).

## Open ideas / next steps

### Track comparison (the big one, user's idea — pick up tomorrow)
Recognize which track a race was driven on (fingerprint by lap *shape*:
heading profile = turn left/right + sharpness along the lap, invariant to
position/rotation/scale). Then compare the current race to the **best recorded
race on that track**:

- Best-lap overlay on the Path chart (faint reference line).
- Braking-point transfer: "you braked ~35 m earlier than your best lap into the hairpin."
- Turn-in transfer: "you turned in ~12 m later at the S-curve."
- Uses existing arc-length alignment machinery in line_analysis.

Stages: (1) fingerprint + matching, needs real multi-track recordings to tune;
(2) best-lap picker per track; (3) braking/turn comparison; (4) UI + tips.

### Recorder / replay tool
Save raw UDP to a file, replay through the pipeline offline. Unblocks
validation (below) and makes bug reports reproducible. Recommended to build
first because track matching needs real recordings.

### Real-telemetry validation
Tips, thresholds, and the yaw chart are all tuned on synthetic fixtures.
Pending: record real races, compare tips to what actually happened, recalibrate.

### Drift Yaw chart feedback
Auto-scaled to each run's peak (y_max = peak*1.1, floor 50 deg/s). Decide later
whether to lock a fixed axis (e.g. 0-400) for cross-car comparison.

### Cross-race comparison (stretch)
Two races, same track/car: "best lap 2.1 s faster, 3 fewer corner cuts."

## Notes / gotchas

- Races shorter than the minimum duration or < 10 samples are discarded on save.
- `make_race` / `make_crash_session` / `make_drift_session` are the synthetic
  fixtures in line_analysis.py; run `python line_analysis.py` for self-tests.
- Launch via the .bat files (pythonw fh6_gui.py / overlay.py); dist/ is a
  PyInstaller artifact, rebuilt with build_exe.py.
