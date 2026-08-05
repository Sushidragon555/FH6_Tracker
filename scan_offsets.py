"""Offset scanner/analyzer for the FH6 "Data Out" UDP telemetry packet.

Purpose: locate where PositionX/Y/Z (and any racing-line data) live in the
packet, confirm the real packet length, and verify the position-return lap
splitting plan with a real race capture. Three modes:

1) LIVE mode (default): bind UDP 9999 and scan while driving. Every ~0.7s it
   prints one packet's key fields; after ~1-2 minutes of driving press Ctrl+C
   for a candidate report. You must close the GUI / overlay / tracker first so
   this can bind the port.

2) FILE mode: analyze a raw packet capture produced by the normal tracker.
   Launch the GUI with the env var FH6_DUMP_PACKETS=1, drive normally, close
   the GUI, then run:
       python scan_offsets.py packet_dump.bin
   This prints packet lengths, samples of live packets, and the candidate
   report — no need to coordinate a separate live scan.

3) RACE mode: verify race detection + lap splitting. Drive a standing-start
   race (grid lights -> go) with FH6_DUMP_PACKETS=1, then run:
       python scan_offsets.py --race packet_dump.bin
   This finds the grid launch, uses the launch position as the start/finish
   reference, counts lap crossings by position-return, and prints per-lap
   duration/distance so we can confirm the plan works on real data.

Position offsets 244/248/252 are verified; the packet is a constant 324 bytes
and contains NO NormalizedDrivingLine field (confirmed by live capture).
"""

import socket
import struct
import sys

UDP_IP = "0.0.0.0"
UDP_PORT = 9999

# Anchors we already verified (from car_lookup.py) — sanity check these first.
RPM_OFFSET = 16
CAR_ORDINAL_OFFSET = 212
POS_X = 244
POS_Y = 248
POS_Z = 252
SPEED_OFFSET = 256
BOOST_OFFSET = 284
GEAR_OFFSET = 319
STEER_OFFSET = 320
DRV_OFFSET = 321

# Empirically PositionX/Y/Z are in meters: over a 9.5 km race lap, integrating
# the speed field summed to 9566 m vs a position-delta sum of 9572 units (0.06%
# agreement). Used only to render human-readable distance/speed.
POS_SCALE = 1.0  # meters per position unit


def f32(data, off):
    try:
        return struct.unpack("<f", data[off:off + 4])[0]
    except (struct.error, IndexError):
        return None


def i32(data, off):
    try:
        return struct.unpack("<i", data[off:off + 4])[0]
    except (struct.error, IndexError):
        return None


def u8(data, off):
    try:
        return data[off]
    except IndexError:
        return None


def is_live(data):
    return ((f32(data, RPM_OFFSET) or 0) > 0
            or (f32(data, SPEED_OFFSET) or 0) > 0
            or (i32(data, CAR_ORDINAL_OFFSET) or 0) != 0)


def record_packet(data, stats):
    """Accumulate per-offset stats for a packet. Only meaningful if live."""
    n = len(data)
    for off in range(0, n - 3, 4):
        v = f32(data, off)
        if v is None:
            continue
        s = stats.setdefault(off, [0, 0, 0.0, None])
        s[0] += 1
        if 0.0 <= v <= 1.0:
            s[1] += 1
        if abs(v) > s[2]:
            s[2] = abs(v)
        if s[3] is not None and abs(v - s[3]) > 5.0:
            s[2] = max(s[2], abs(v - s[3]))  # marks the field as moving
        s[3] = v


def describe_packet(data):
    """Human-readable dump of one packet's key fields and tail."""
    n = len(data)
    sb = u8(data, STEER_OFFSET)
    steer = (sb - 256 if sb is not None and sb >= 128 else (sb or 0)) / 127.0
    live_tag = "" if is_live(data) else "  <-- NO LIVE TELEMETRY: are you actually driving? (menus send zeros)"
    line = (f"packet len={n} | "
            f"rpm={f32(data, RPM_OFFSET):7.0f} | "
            f"speed={f32(data, SPEED_OFFSET) * 2.23694:5.0f}mph | "
            f"car={i32(data, CAR_ORDINAL_OFFSET):6} | "
            f"gear={u8(data, GEAR_OFFSET)} | "
            f"thr={u8(data, 315) / 255.0:4.2f} | "
            f"steer={steer:+5.2f}{live_tag}")

    px, py, pz = f32(data, POS_X), f32(data, POS_Y), f32(data, POS_Z)
    pos = (f"  position@244/248/252 = {px:9.1f} {py:9.1f} {pz:9.1f}   "
           f"(should move smoothly while you drive)")

    tail = "  tail f32:"
    for off in range(300, n - 3, 4):
        v = f32(data, off)
        vtxt = "      " if v is None else f"{v:8.3f}"
        flag = "" if v is None else (" *" if 0.0 <= v <= 1.0 else "")
        tail += f" [{off:3}] {vtxt}{flag}"

    raw = "  tail bytes 309..%d: %s" % (
        min(n, 340) - 1,
        " ".join(f"{b:02x}" for b in data[309:min(n, 340)]))
    return line, pos, tail, raw


def print_report(stats, total, live_count):
    print("\n================= candidate report =================")
    print(f"Packets analyzed: {total} | with live telemetry: {live_count}")
    print("Driving-line candidates (>=70% of packets in [0,1] AND actually varies):")
    drv_rows = []
    for off, (total_s, in01, maxabs, _prev) in sorted(stats.items()):
        if total_s >= 40 and in01 >= 0.7 * total_s and maxabs > 0.05:
            drv_rows.append((off, in01 / total_s, maxabs))
    for off, frac, maxabs in drv_rows:
        print(f"  offset {off:>4}  {frac * 100:.0f}% in [0,1]  max-abs {maxabs:.2f}")
    if not drv_rows:
        print("  (none found — either the game wasn't sending Data Out, or drv is")
        print("   not a simple F32 in this packet)")

    print("\nPosition-like candidates (large magnitude, moving >50 units):")
    pos_rows = []
    for off, (total_s, _in01, maxabs, _prev) in sorted(stats.items()):
        if total_s >= 40 and maxabs > 100.0:
            pos_rows.append((off, maxabs))
    for off, maxabs in pos_rows:
        print(f"  offset {off:>4}  max-abs {maxabs:9.1f}")
    if not pos_rows:
        print("  (none found)")
    print("\nDone. Paste the report back to continue.")


def read_dump(path):
    """Read a packet_dump.bin (2-byte little-endian length + payload each)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        print(f"Could not read {path}: {exc}")
        return None, None
    packets = []
    off = 0
    while off + 2 <= len(raw):
        ln = struct.unpack("<H", raw[off:off + 2])[0]
        off += 2
        if off + ln > len(raw):
            break
        packets.append(raw[off:off + ln])
        off += ln
    lengths = {}
    for p in packets:
        lengths[len(p)] = lengths.get(len(p), 0) + 1
    return packets, lengths


def analyze_file(path):
    packets, lengths = read_dump(path)
    if packets is None:
        return
    print(f"Read {len(packets)} packets from {path}")

    print("Packet length distribution:",
          ", ".join(f"{k} bytes x{v}" for k, v in sorted(lengths.items())))

    live = [p for p in packets if is_live(p)]
    print(f"With live telemetry: {len(live)}")
    if not live:
        print("\nNo live packets captured — the game wasn't sending driving data")
        print("during this capture (menus/pause send zeros). Drive for ~30-60s")
        print("with FH6_DUMP_PACKETS=1, then close the GUI and retry.")
        return

    # Sample spread across the capture so we see the packet in different states.
    print("\nSample of live packets:")
    step = max(1, len(live) // 8)
    for i in range(0, len(live), step):
        for text in describe_packet(live[i]):
            print(text)
        print()

    stats = {}
    for p in live:
        record_packet(p, stats)
    print_report(stats, len(packets), len(live))

    pxs = [f32(p, POS_X) for p in live if f32(p, POS_X) is not None]
    pzs = [f32(p, POS_Z) for p in live if f32(p, POS_Z) is not None]
    if pxs and pzs:
        def spread(vals):
            return max(vals) - min(vals)
        print("position sanity (all live packets):")
        print(f"  X @244  min={min(pxs):9.1f} max={max(pxs):9.1f} spread={spread(pxs):9.1f}")
        print(f"  Z @252  min={min(pzs):9.1f} max={max(pzs):9.1f} spread={spread(pzs):9.1f}")
        print("  (a large spread means the car actually moved = offsets likely right)")


def run_live():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((UDP_IP, UDP_PORT))
    except OSError as exc:
        print(f"Could not bind UDP {UDP_PORT}: {exc}")
        print("Close the GUI / overlay / tracker first, then run this again.")
        sys.exit(1)
    sock.settimeout(1.0)

    stats = {}
    live_count = 0

    print("FH6 Data Out packet scanner — drive around, steer across the road.")
    print("Ctrl+C after ~1-2 min for the candidate report.\n")

    try:
        while True:
            try:
                data, _ = sock.recvfrom(1024)
            except socket.timeout:
                continue

            for text in describe_packet(data):
                print(text)

            if is_live(data):
                live_count += 1
                record_packet(data, stats)

            # drain any extra packets queued during this tick, then pause
            time_slept = 0.0
            while time_slept < 0.7:
                try:
                    sock.recvfrom(1024)
                except socket.timeout:
                    time_slept = 1.0
                    break
                time_slept += 0.05
    except KeyboardInterrupt:
        pass

    print_report(stats, sum(1 for s in stats.values() if s[0] > 0), live_count)


def analyze_race(path):
    """Verify race detection + position-return lap splitting on a real capture.

    Use a STANDING-START race (grid lights -> go): the launch position becomes
    the start/finish reference, and every time the car returns near it after
    leaving, that's a lap crossing. Also confirms the race sends no longer
    packet. The dump records every packet, so ~60 packets/second.
    """
    packets, lengths = read_dump(path)
    if packets is None:
        return
    print(f"Read {len(packets)} packets from {path}")
    print("Packet length distribution:",
          ", ".join(f"{k} bytes x{v}" for k, v in sorted(lengths.items())))

    live = [p for p in packets if is_live(p)]
    print(f"With live telemetry: {len(live)}")
    if not live:
        print("\nNo live packets — the game wasn't sending driving data during")
        print("this capture. Race for ~2-3 laps with FH6_DUMP_PACKETS=1, then")
        print("close the GUI and retry.")
        return

    xs = [f32(p, POS_X) or 0.0 for p in live]
    zs = [f32(p, POS_Z) or 0.0 for p in live]
    sp = [f32(p, SPEED_OFFSET) or 0.0 for p in live]  # m/s

    # 1) Standing-start launch = first time the car sustains speed (~7 mph+).
    LAUNCH_MPS = 3.0
    launch = None
    for i in range(len(live) - 15):
        if sp[i] > LAUNCH_MPS and all(s > LAUNCH_MPS for s in sp[i:i + 15]):
            launch = i
            break
    if launch is None:
        print("\nCould not find a standing-start launch (car never held speed")
        print("above ~7 mph for 15 consecutive packets). Drive a standing-start")
        print("race (grid lights -> go), not a rolling start, for this to work.")
        return
    xref, zref = xs[launch], zs[launch]
    print(f"\nStanding-start launch at packet {launch} of {len(live)} "
          f"(speed {sp[launch] * 2.23694:.0f} mph);")
    print(f"reference (start/finish) = ({xref:.1f}, {zref:.1f})")

    # 2) Count returns to the reference after leaving it. Tolerance/hysteresis
    #    avoid counting the stationary grid as lap crossings.
    TOL = 10.0    # within ~10 units (meters) of the reference = crossing the line
    FAR = 50.0    # must leave the reference by at least this before a return counts
    MIN_GAP = 1200  # ~20s between crossings at 60Hz
    crossings = []  # (packet_index, distance_to_ref)
    away = False
    last = -10 ** 9
    for i in range(launch, len(live)):
        d = ((xs[i] - xref) ** 2 + (zs[i] - zref) ** 2) ** 0.5
        if away and d < TOL and (i - last) > MIN_GAP:
            crossings.append((i, d))
            away = False
            last = i
        elif d > FAR:
            away = True

    print(f"\nLap crossings detected: {len(crossings)}")
    if not crossings:
        print("No returns near the reference — the reference may not be on the")
        print("finish line (rolling start?), or the tolerance is off.")
    for idx, d in crossings:
        print(f"  packet {idx}  ~t+{(idx - launch) / 60.0:6.1f}s  "
              f"dist to ref {d:5.1f}  speed {sp[idx] * 2.23694:5.0f}mph")

    # 3) Per-lap duration / distance / avg speed — should be roughly consistent
    #    between laps if the splitting is right.
    bounds = [launch] + [c[0] for c in crossings]
    print("\nPer-lap stats (distance in meters, ~60Hz capture):")
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        dt = (b - a) / 60.0
        dist_units = 0.0
        for j in range(a + 1, b):
            dx = xs[j] - xs[j - 1]
            dz = zs[j] - zs[j - 1]
            dist_units += (dx * dx + dz * dz) ** 0.5
        dist_m = dist_units * POS_SCALE
        print(f"  lap {k}: {dt:6.1f}s  distance {dist_m:8.0f} m  "
              f"avg speed {dist_m / max(dt, 1e-6) * 2.23694:5.0f}mph")

    print(f"\nPosition extent: X {min(xs):.0f}..{max(xs):.0f}  "
          f"Z {min(zs):.0f}..{max(zs):.0f}  (car moved this far)")
    print("Position units are meters (validated against the speed field).")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--race":
        if len(args) > 1:
            analyze_race(args[1])
        else:
            print("Usage: python scan_offsets.py --race <dump.bin>")
    elif args:
        analyze_file(args[0])
    else:
        run_live()
