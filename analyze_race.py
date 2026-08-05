"""Analyze a recorded race JSON with the position-based line analysis.

Fast-iteration tool for tuning line_analysis.py thresholds. Prints lap
stats and any off-line events the detector finds (with the numbers needed
to decide whether lateral_m / min-speed / kink thresholds are right).

Usage:
    python analyze_race.py races\\race_20260101_120000.json
    python analyze_race.py races\\race_...json --lateral 6 --min-speed 45
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import line_analysis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("race_file")
    ap.add_argument("--lateral", type=float, default=8.0)
    ap.add_argument("--min-speed", type=float, default=40.0)
    ap.add_argument("--min-run", type=int, default=5)
    ap.add_argument("--kink", type=float, default=25.0)
    ap.add_argument("--merge", type=int, default=10)
    args = ap.parse_args()

    with open(args.race_file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    samples = data.get("samples") or []
    if not samples:
        print("No samples in race file.")
        return
    print(f"Race {os.path.basename(args.race_file)}: {len(samples)} samples, "
          f"{data.get('duration_seconds', '?')}s, type={data.get('race_type', '?')}")

    laps = line_analysis.split_laps(samples)
    if laps is None:
        print("Could not split laps (no usable position data / no launch).")
        return
    print(f"Launch index {laps['launch_index']} "
          f"(t={samples[laps['launch_index']].get('t', 0):.1f}s)")
    print(f"Reference (start/finish): "
          f"({laps['reference'][0]:.1f}, {laps['reference'][1]:.1f})")
    print(f"Boundaries (sample idx): {laps['boundaries']}")

    print("\nPer-lap:")
    for gi, group in enumerate(laps["laps"]):
        if len(group) < 2:
            continue
        a, b = group[0], group[-1]
        dt = (samples[b].get("t", 0) or 0) - (samples[a].get("t", 0) or 0)
        dist = sum(
            math.hypot(samples[group[j]]["pos"][0] - samples[group[j - 1]]["pos"][0],
                       samples[group[j]]["pos"][2] - samples[group[j - 1]]["pos"][2])
            for j in range(1, len(group)))
        print(f"  L{gi}: {len(group):5d} pts  {dt:6.1f}s  {dist:7.0f} m  "
              f"avg {dist / max(dt, 1e-6) * 2.23694:5.0f} mph")

    events = line_analysis.find_off_line_events(
        samples, laps, min_speed_mph=args.min_speed, lateral_m=args.lateral,
        min_run=args.min_run, kink_deg=args.kink, merge_gap=args.merge)
    print(f"\nOff-line events (lateral={args.lateral:.1f}m, "
          f"min-speed={args.min_speed:.0f}mph, min-run={args.min_run}, "
          f"kink={args.kink:.0f}deg, merge={args.merge}):")
    if not events:
        print("  none")
    for e in events:
        print(f"  L{e['lap']} t={e['t']:6.1f}s  "
              f"({e['x']:8.1f}, {e['z']:8.1f})  {e['speed_mph']:5.0f}mph  "
              f"lateral {e['lateral_m']:5.1f}m  kind={e['kind']}")


if __name__ == "__main__":
    main()
