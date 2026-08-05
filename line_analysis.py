"""Position-based line analysis for recorded races.

This module turns a race's position samples into lap boundaries and detects
off-line events (corner cuts / missed apexes). It is pure — no GUI, no game
connection — so it can be unit-tested headlessly and shared by the race
analysis tab and any future live view.

Input sample format (matches auto_log.py's race_buffer entries):
    {"t": float seconds, "spd": float mph, ..., "pos": [x, y, z] meters}
Positions use Forza's ground plane: X/Z is the ground, Y is up. X/Z are
verified to be in meters (POS_SCALE == 1.0). Samples arrive at ~20Hz.

Run the self-tests with:
    python line_analysis.py
"""

import math

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

# Standing-start detection. The car must be moving faster than this (m/s) for
# this many *consecutive* samples before we declare the race underway.
LAUNCH_SPEED_MPS = 3.0
LAUNCH_WINDOW = 8              # ~0.4s at 20Hz

# Crossing detector. A "crossing" happens when the car comes back within
# TOL meters of the reference point, was farther than FAR on the previous
# sample, and the last crossing was at least MIN_GAP samples ago. MIN_GAP
# guards against the car lingering near the reference line at low speed.
CROSS_TOL = 10.0
CROSS_FAR = 50.0
CROSS_MIN_GAP = 400            # ~20s at 20Hz (lap times are longer than this)

# Per-lap color palette for the path chart (readable on a dark background).
LAP_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
              "#9467bd", "#8c564b", "#e377c2", "#17becf"]

# --------------------------------------------------------------------------
# Lap splitting (position-return)
# --------------------------------------------------------------------------

def split_laps(samples):
    """Group samples into laps using position returns.

    The reference point is the car's position the moment it first exceeds
    LAUNCH_SPEED_MPS (standing-start races start there). Every time the car
    comes back within CROSS_TOL meters of that point (and was farther than
    CROSS_FAR before it), we start a new lap.

    This algorithm was validated against a real recorded race: it found the
    same two lap crossings as manual inspection of the packet stream.

    Returns a dict (or None if there isn't enough position data to try):
        {
          "reference": (x, z),       # meters, launch position
          "launch_index": int,       # first sample of the race
          "boundaries": [int, ...],  # sample indices where laps start
          "laps": [[int, ...], ...], # per-lap sample indices
        }
    The first "lap" is the partial lap from the standing start to the first
    crossing; every later group is a full lap.
    """
    pts = []
    for idx, s in enumerate(samples):
        p = s.get("pos")
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            continue
        x, z = p[0], p[2]
        if isinstance(x, (int, float)) and isinstance(z, (int, float)):
            pts.append((idx, float(x), float(z)))
    if len(pts) < LAUNCH_WINDOW * 3:
        return None

    launch = None
    run = 0
    for i, (idx, _, _) in enumerate(pts):
        spd = samples[idx].get("spd", 0) or 0
        if spd * 0.44704 > LAUNCH_SPEED_MPS:
            run += 1
            if run >= LAUNCH_WINDOW:
                launch = i - run + 1
                break
        else:
            run = 0
    if launch is None:
        return None

    ref_x, ref_z = pts[launch][1], pts[launch][2]
    boundaries = [pts[launch][0]]
    last_cross = launch
    away = False
    for i in range(launch + 1, len(pts)):
        _, x, z = pts[i]
        dist = math.hypot(x - ref_x, z - ref_z)
        if away and dist <= CROSS_TOL and i - last_cross >= CROSS_MIN_GAP:
            boundaries.append(pts[i][0])
            last_cross = i
            away = False
        elif dist > CROSS_FAR:
            away = True

    laps = []
    for b_idx, b in enumerate(boundaries):
        end = boundaries[b_idx + 1] if b_idx + 1 < len(boundaries) else len(samples)
        laps.append(list(range(b, end)))

    return {
        "reference": (ref_x, ref_z),
        "launch_index": pts[launch][0],
        "boundaries": boundaries,
        "laps": laps,
    }


# --------------------------------------------------------------------------
# Off-line event detection  (YOUR TURN — see docstring below)
# --------------------------------------------------------------------------

def find_off_line_events(samples, laps, min_speed_mph=40.0, lateral_m=8.0,
                         min_run=5, kink_deg=25.0, merge_gap=10):
    """Find off-line events (corner cuts, wide exits, missed apexes).

    Two methods, picked by how many usable laps exist:

    * Multi-lap (>= 2 groups): deviation from a reference lap. The reference
      is the lap with the fewest sharp heading changes (kinks), ties broken
      by least average deviation to the other laps — so a lap with a corner
      cut (which has kinks) never becomes the reference. Every other lap's
      points are measured against the reference polyline; a contiguous run of
      >= min_run points whose perpendicular distance exceeds lateral_m (at
      speed >= min_speed_mph) is an event at the max-deviation point.
      Deviation to the inside of a corner is a "cut", to the outside a "wide".

    * Single lap (1 group): kink detection. A corner cut shows up as two
      sharp heading changes (chord entry and exit) in quick succession. A
      heading step change > kink_deg at speed >= min_speed_mph is a kink;
      nearby kinks are merged into events with kind="kink".

    Parameters
    ----------
    samples : list of dict
        Full race samples (same list passed to split_laps). Each has "t",
        "spd" (mph) and "pos" [x, y, z] meters.
    laps : dict | None
        Output of split_laps(samples). None means no laps were found; returns [].
    min_speed_mph : float
        Only flag a sample while the car is at least this fast. A car crawling
        through the pits isn't "off line".
    lateral_m : float
        Lateral deviation (meters) beyond which a sample counts as off-line.
    min_run : int
        Contiguous samples (at ~20Hz) the deviation must persist for, so a
        single GPS wobble can't create a fake event.
    kink_deg : float
        Single-lap fallback: heading change (degrees) across one 2-sample
        step that counts as a sharp kink.
    merge_gap : int
        Detections closer than this many samples get merged into one event.

    Returns
    -------
    A list of events sorted by t, each:
        {"t", "x", "z", "speed_mph", "lap", "lateral_m", "kind"}
    where kind is "cut" | "wide" | "kink" and lap is the 0-based index into
    laps["laps"] (matching the L0..Ln labels on the Path chart legend).
    """
    if laps is None or not laps.get("laps"):
        return []

    # Usable lap groups, as (group_index, [(sample_idx, x, z, spd_mph, t)]).
    groups = []
    for gi, group in enumerate(laps["laps"]):
        if len(group) < min_run * 3:
            continue
        pts = []
        for i in group:
            p = samples[i].get("pos")
            if not isinstance(p, (list, tuple)) or len(p) < 3:
                continue
            x, z = p[0], p[2]
            if isinstance(x, (int, float)) and isinstance(z, (int, float)):
                pts.append((i, float(x), float(z),
                            float(samples[i].get("spd", 0) or 0),
                            float(samples[i].get("t", 0) or 0)))
        if len(pts) >= 30:
            groups.append((gi, pts))

    if len(groups) < 2:
        return _find_kinks(groups, min_speed_mph, kink_deg, merge_gap)
    return _find_deviations(groups, min_speed_mph, lateral_m, min_run,
                            merge_gap, kink_deg)


def _point_seg_signed(px, pz, poly):
    """Signed perpendicular distance from (px, pz) to the nearest segment of
    the reference polyline (entries are (idx, x, z, spd, t)).

    Sign is +1 when the point sits to the LEFT of the direction of travel —
    i.e. inside a left-hand corner, which is a cut. Negative is outside (wide).
    """
    best_d2 = float("inf")
    best_side = 0.0
    for a, b in zip(poly, poly[1:]):
        ax, az = a[1], a[2]
        bx, bz = b[1], b[2]
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 <= 1e-12:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / L2))
        qx, qz = ax + t * dx, az + t * dz
        d2 = (px - qx) ** 2 + (pz - qz) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_side = 1.0 if dx * (pz - az) - dz * (px - ax) > 0 else -1.0
    return math.sqrt(best_d2), best_side


def _downsample(pts, step=2.0):
    """Keep points of a lap spaced roughly ``step`` meters apart."""
    if len(pts) < 2:
        return pts[:]
    out = [pts[0]]
    acc = 0.0
    for prev, cur in zip(pts, pts[1:]):
        acc += math.hypot(cur[1] - prev[1], cur[2] - prev[2])
        if acc >= step:
            out.append(cur)
            acc = 0.0
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def _avg_abs_dev(pts, ref_poly, min_speed_mph):
    """Mean absolute distance of a lap's (fast) points to a reference polyline."""
    total = 0.0
    n = 0
    for _, x, z, spd, _ in pts:
        if spd < min_speed_mph:
            continue
        d = _point_seg_signed(x, z, ref_poly)[0]
        if math.isfinite(d):
            total += abs(d)
            n += 1
    return total / max(n, 1)


def _kinks_in_lap(pts, min_speed_mph, kink_deg):
    """Sharp heading changes inside one lap. Returns [(index_in_pts, degrees)]."""
    n = len(pts)
    if n < 3:
        return []
    heads = [math.degrees(math.atan2(pts[i + 1][2] - pts[i][2],
                                     pts[i + 1][1] - pts[i][1]))
             for i in range(n - 1)]
    kinks = []
    for i in range(1, n - 1):
        d = (heads[i] - heads[i - 1] + 180.0) % 360.0 - 180.0
        if abs(d) >= kink_deg and pts[i][3] >= min_speed_mph:
            kinks.append((i, abs(d)))
    return kinks


def _pick_reference(groups, min_speed_mph, kink_deg):
    """Pick the lap everything else is measured against: fewest kinks first
    (a corner-cut lap has kinks), then least average deviation to the others."""
    best = None
    best_score = None
    for gi, pts in groups:
        kinks = len(_kinks_in_lap(pts, min_speed_mph, kink_deg))
        others = [_downsample(o[1], 2.0) for o in groups if o[0] != gi]
        dev = sum(_avg_abs_dev(pts, poly, min_speed_mph) for poly in others)
        dev /= max(len(others), 1)
        score = (kinks, dev)
        if best_score is None or score < best_score:
            best_score = score
            best = (gi, pts)
    return best


def _merge_events(events, merge_gap):
    """Merge detections within ``merge_gap`` samples, keeping the strongest."""
    if not events:
        return []
    events.sort(key=lambda e: e["idx"])
    merged = []
    for ev in events:
        if merged and ev["idx"] - merged[-1]["idx"] <= merge_gap:
            if ev.get("mag", 0) > merged[-1].get("mag", 0):
                merged[-1] = ev
        else:
            merged.append(ev)
    return merged


def _find_deviations(groups, min_speed_mph, lateral_m, min_run, merge_gap, kink_deg):
    """Multi-lap method: flag sustained lateral deviation from a reference lap."""
    ref = _pick_reference(groups, min_speed_mph, kink_deg)
    ref_poly = _downsample(ref[1], 2.0)
    events = []
    for gi, pts in groups:
        if gi == ref[0]:
            continue
        devs = [_point_seg_signed(x, z, ref_poly) for _, x, z, _, _ in pts]
        i = 0
        n = len(pts)
        while i < n:
            if (pts[i][3] >= min_speed_mph and abs(devs[i][0]) > lateral_m
                    and math.isfinite(devs[i][0])):
                j = i
                while (j < n and pts[j][3] >= min_speed_mph
                       and abs(devs[j][0]) > lateral_m
                       and math.isfinite(devs[j][0])):
                    j += 1
                if j - i >= min_run:
                    best = max(range(i, j), key=lambda k: abs(devs[k][0]))
                    events.append({
                        "idx": pts[best][0],
                        "t": pts[best][4],
                        "x": pts[best][1],
                        "z": pts[best][2],
                        "speed_mph": pts[best][3],
                        "lap": gi,
                        "lateral_m": abs(devs[best][0]),
                        "mag": abs(devs[best][0]),
                        "kind": "cut" if devs[best][1] > 0 else "wide",
                    })
                i = j
            else:
                i += 1
    merged = _merge_events(events, merge_gap)
    merged.sort(key=lambda e: e["t"])
    return merged


def _find_kinks(groups, min_speed_mph, kink_deg, merge_gap):
    """Single-lap method: sharp heading changes = corner cuts."""
    events = []
    for gi, pts in groups:
        for i, mag in _kinks_in_lap(pts, min_speed_mph, kink_deg):
            events.append({
                "idx": pts[i][0],
                "t": pts[i][4],
                "x": pts[i][1],
                "z": pts[i][2],
                "speed_mph": pts[i][3],
                "lap": gi,
                "lateral_m": 0.0,
                "mag": mag,
                "kind": "kink",
            })
    return _merge_events(events, merge_gap)


# --------------------------------------------------------------------------
# Drift segment detection
# --------------------------------------------------------------------------

def _wrap_angle(a):
    """Wrap an angle in radians to the range (-pi, pi]."""
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def detect_drift_segments(samples, min_speed_mph=30.0, min_dur_s=0.5,
                          merge_gap_s=0.35, yaw_deg_per_s=25.0,
                          steer_deadband=0.12, hbrk_gate=0.5):
    """Find contiguous drift slides from recorded telemetry.

    The packet has no yaw-rate channel, so a slide is inferred from the path
    the car carves out. The travel heading (from position) is computed over a
    ~4-sample window to fight the 0.1 m position rounding; a sample counts as
    drifting when the heading is rotating at >= yaw_deg_per_s while moving at
    speed AND that rotation is NOT produced by the steering (counter-steer:
    wheels opposed to the turn) or is handbrake-initiated. A gripped car's
    heading follows the front wheels, so a fast rotation with the wheels
    pointed away from it means the rear is sliding out.

    Parameters
    ----------
    samples : list of dict
        Same format as split_laps (each has "t", "spd", "str", "hbrk", "pos").
    min_speed_mph : float
        Slower than this isn't a slide, it's a slow turn.
    min_dur_s : float
        Slides shorter than this are dropped (a flick, not a drift).
    merge_gap_s : float
        Gaps between detections shorter than this are merged into one slide.
    yaw_deg_per_s : float
        Heading-rotation rate above which the car is clearly not driving in a
        straight-ish, gripped fashion.
    steer_deadband : float
        Steering magnitude below which we treat the wheel as centered.
    hbrk_gate : float
        Handbrake above this counts as slide initiation regardless of steer.

    Returns
    -------
    A list of segments sorted by start sample (or None when samples are
    unusable / lack position data), each:
        {"start", "end", "duration_s", "avg_speed_mph", "avg_steer",
         "peak_yaw_deg_s"}
    """
    if not samples or len(samples) < 12:
        return None
    n = len(samples)
    xs, zs = [], []
    for s in samples:
        p = s.get("pos")
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            return None
        xs.append(p[0])
        zs.append(p[2])
    ts = [s.get("t", 0) for s in samples]
    if not any(ts) or (ts[-1] or 0) <= (ts[0] or 0):
        ts = [i / 20.0 for i in range(n)]

    heading = [0.0] * n
    for i in range(2, n - 2):
        dx = xs[i + 2] - xs[i - 2]
        dz = zs[i + 2] - zs[i - 2]
        if abs(dx) > 1e-9 or abs(dz) > 1e-9:
            heading[i] = math.atan2(dz, dx)

    flags = [False] * n
    for i in range(2, n - 2):
        spd = samples[i].get("spd", 0) or 0
        if spd < min_speed_mph:
            continue
        d = _wrap_angle(heading[i + 2] - heading[i - 2])
        dt = (ts[i + 2] - ts[i - 2]) or 0.4
        if math.degrees(abs(d)) / dt < yaw_deg_per_s:
            continue
        steer = samples[i].get("str", 0) or 0
        steer_sign = 1 if steer > steer_deadband else (-1 if steer < -steer_deadband else 0)
        turn_sign = 1 if d > 0 else -1
        counter = steer_sign != 0 and steer_sign != turn_sign
        if counter or (samples[i].get("hbrk", 0) or 0) > hbrk_gate:
            flags[i] = True

    segs = []
    start = None
    for i in range(n):
        if flags[i] and start is None:
            start = i
        elif not flags[i] and start is not None:
            segs.append((start, i - 1))
            start = None
    if start is not None:
        segs.append((start, n - 1))

    merged = []
    for seg in segs:
        if merged and (ts[seg[0]] - ts[merged[-1][1]]) <= merge_gap_s:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    out = []
    for a, b in merged:
        dur = ts[b] - ts[a]
        if dur < min_dur_s:
            continue
        seg_speeds = [samples[i].get("spd", 0) or 0 for i in range(a, b + 1)]
        seg_steers = [abs(samples[i].get("str", 0) or 0) for i in range(a, b + 1)]
        yaws = []
        for i in range(a, b + 1):
            if 2 <= i < n - 2:
                dd = _wrap_angle(heading[i + 2] - heading[i - 2])
                dtt = (ts[i + 2] - ts[i - 2]) or 0.4
                yaws.append(math.degrees(abs(dd)) / dtt)
        out.append({
            "start": a,
            "end": b,
            "duration_s": dur,
            "avg_speed_mph": sum(seg_speeds) / max(len(seg_speeds), 1),
            "avg_steer": sum(seg_steers) / max(len(seg_steers), 1),
            "peak_yaw_deg_s": max(yaws) if yaws else 0.0,
        })

    return out


# --------------------------------------------------------------------------
# Synthetic fixtures + self-tests
# --------------------------------------------------------------------------

def _loop_point(d, X=200.0, Z=150.0, R=40.0):
    """(x, z, heading_deg) at arc distance d along a CCW rounded rectangle.

    Track is a stadium loop: two 320m straights at z=+-150, two 220m
    straights at x=+-200, four R=40 quarter-corners. Total ~1331m. d is taken
    modulo the loop length. Lap index = int(d // total).
    """
    sx = 2.0 * (X - R)
    sz = 2.0 * (Z - R)
    arc = math.pi * R / 2.0
    total = 2.0 * sx + 2.0 * sz + 4.0 * arc
    lap, d = divmod(d, total)

    if d < sx:                                   # bottom straight (+x)
        return (-X + R + d, -Z, 0.0), lap
    d -= sx
    if d < arc:                                  # BR corner (turn to +z)
        a = -math.pi / 2 + d / R
        cx, cz = X - R, -(Z - R)
        return (cx + R * math.cos(a), cz + R * math.sin(a),
                math.degrees(a + math.pi / 2)), lap
    d -= arc
    if d < sz:                                   # right straight (+z)
        return (X, -(Z - R) + d, 90.0), lap
    d -= sz
    if d < arc:                                  # TR corner (turn to -x)
        a = 0.0 + d / R
        cx, cz = X - R, Z - R
        return (cx + R * math.cos(a), cz + R * math.sin(a),
                math.degrees(a + math.pi / 2)), lap
    d -= arc
    if d < sx:                                   # top straight (-x)
        return (X - R - d, Z, 180.0), lap
    d -= sx
    if d < arc:                                  # TL corner (turn to -z)
        a = math.pi / 2 + d / R
        cx, cz = -(X - R), Z - R
        return (cx + R * math.cos(a), cz + R * math.sin(a),
                math.degrees(a + math.pi / 2)), lap
    d -= arc
    if d < sz:                                   # left straight (-z)
        return (-X, Z - R - d, 270.0), lap
    d -= arc
    a = math.pi + d / R                          # BL corner (turn to +x)
    cx, cz = -(X - R), -(Z - R)
    return (cx + R * math.cos(a), cz + R * math.sin(a),
            math.degrees(a + math.pi / 2)), lap


def _chord_point(u, X=200.0, Z=150.0, R=40.0):
    """Corner-cut shortcut: straight line across the BR corner from
    (X-R, -Z) to (X, -(Z-R)). Max lateral deviation from the true arc is
    R*(1-cos(45deg)) ~ 11.7m — well past the 8m detection threshold."""
    a = (X - R, -(Z - R) - R)                    # arc entry, heading +x
    b = (X - R + R, -(Z - R))                    # arc exit, heading +z
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u), 45.0


def make_race(num_full_laps=3, cut_lap=None, dt=0.05):
    """Synthesize a race around the stadium loop.

    num_full_laps: full loops after the standing start.
    cut_lap: which full lap (1-based) takes the BR-corner chord shortcut.
    Returns a list of samples in auto_log format.
    """
    samples = []
    t = 0.0
    s = 0.0                                      # arc distance from loop start
    v = 0.0
    while True:
        pos, lap = _loop_point(s)
        if lap >= num_full_laps + 1:
            break
        # Speed profile: slow through corners, fast on straights. Also ramp up
        # from a standing start over the first ~3s so launch detection works.
        _, _, heading = pos
        on_straight = heading in (0.0, 90.0, 180.0, 270.0)
        cruise = 60.0 if on_straight else 25.0
        if t < 3.0:
            cruise = min(cruise, 60.0 * t / 3.0)
        v = cruise
        x, z = pos[0], pos[1]

        # Corner cut injection: on cut_lap, replace the BR arc with the chord.
        if cut_lap is not None and lap == cut_lap - 1:
            d = s % _loop_total()
            sx = 2.0 * (200.0 - 40.0)
            arc = math.pi * 40.0 / 2.0
            if sx <= d < sx + arc:
                (x, z), _ = _chord_point((d - sx) / arc)
                v = 35.0

        samples.append({
            "t": round(t, 2),
            "spd": round(v * 2.23694, 1),
            "thr": 1.0, "brk": 0.0, "str": 0.0,
            "rpm": 4000, "gear": 4, "hbrk": 0.0,
            "pos": [round(x, 1), 0.0, round(z, 1)],
        })
        s += v * dt
        t += dt
    return samples


def _loop_total():
    return 2.0 * 2.0 * (200.0 - 40.0) + 2.0 * 2.0 * (150.0 - 40.0) \
        + 4.0 * math.pi * 40.0 / 2.0


def _drift_path_samples(phases, dt=0.05):
    """Integrate a 2D path from (seconds, speed_mph, turn_rate_deg_s, steer,
    hbrk, thr) phases. Positions are rounded to 0.1 m like real recordings.
    Positive turn rate = turning left; positive steer = steering left."""
    samples = []
    t = 0.0
    x = z = 0.0
    heading = 0.0
    for secs, speed_mph, turn, steer, hbrk, thr in phases:
        n = int(round(secs / dt))
        v = speed_mph * 0.44704
        for _ in range(n):
            heading += math.radians(turn) * dt
            x += math.cos(heading) * v * dt
            z += math.sin(heading) * v * dt
            samples.append({
                "t": round(t, 3),
                "spd": round(speed_mph, 1),
                "thr": thr, "brk": 0.0, "str": steer,
                "hbrk": hbrk, "rpm": 5000, "gear": 3,
                "pos": [round(x, 1), 0.0, round(z, 1)],
            })
            t += dt
    return samples


def make_drift_session():
    """Synthetic drift run: straight approach, a long counter-steered
    left-hand slide (handbrake-initiated, wheels opposite the turn), exit."""
    return _drift_path_samples([
        (1.0, 40.0, 0.0, 0.0, 0.0, 0.8),       # approach straight
        (2.5, 50.0, 60.0, -0.55, 1.0, 0.7),    # drifting: turn left, steer right
        (1.0, 50.0, 0.0, 0.0, 0.0, 0.5),       # exit straight
    ])


def make_grip_corner_session():
    """Synthetic clean corner: the same arc but the steering matches the turn
    (gripped) and there is no handbrake — must NOT be flagged as a drift."""
    return _drift_path_samples([
        (1.0, 40.0, 0.0, 0.0, 0.0, 0.8),
        (2.5, 50.0, 60.0, 0.55, 0.0, 0.5),     # steering INTO the left turn
        (1.0, 50.0, 0.0, 0.0, 0.0, 0.5),
    ])


def _main():
    import sys

    state = {"fails": 0}

    def check(name, cond, detail=""):
        if cond:
            print(f"  PASS  {name}")
        else:
            state["fails"] += 1
            print(f"  FAIL  {name} {detail}")

    print("== split_laps ==")
    race = make_race(num_full_laps=3)
    laps = split_laps(race)
    check("found laps", laps is not None)
    if laps:
        check("4 lap groups (1 partial + 3 full)",
              len(laps["laps"]) == 4, f"got {len(laps['laps'])}")
        check("launch_index < 80", laps["launch_index"] < 80,
              f"got {laps['launch_index']}")
        check("boundaries strictly increasing",
              laps["boundaries"] == sorted(laps["boundaries"]))
        ref = laps["reference"]
        check("reference near loop start", abs(ref[0] - (-160.0)) < 60
              and abs(ref[1] - (-150.0)) < 60, f"got {ref}")

    print("== find_off_line_events (clean race) ==")
    try:
        events = find_off_line_events(race, laps)
        check("no events on clean laps", len(events) == 0, f"got {len(events)}")
    except NotImplementedError:
        print("  SKIP  stub not implemented yet")

    print("== find_off_line_events (one corner cut) ==")
    cut_race = make_race(num_full_laps=3, cut_lap=2)
    try:
        cut_laps = split_laps(cut_race)
        events = find_off_line_events(cut_race, cut_laps)
        check("exactly 1 event on a cut lap", len(events) == 1,
              f"got {len(events)}: {[e['kind'] for e in events]}")
        if events:
            ev = events[0]
            # cut_lap=2 means the 2nd full lap, which is position-return group 1
            # (group 0 is the standing-start lap; the legend labels it L1).
            check("event is in lap 1 (2nd full lap)", ev["lap"] == 1,
                  f"got {ev['lap']}")
            check("event near the BR corner (x>140, z<-120)",
                  ev["x"] > 140 and ev["z"] < -120,
                  f"got ({ev['x']:.0f}, {ev['z']:.0f})")
            check("event reports lateral_m > 8",
                  ev.get("lateral_m", 0) > 8, f"got {ev.get('lateral_m', 0):.1f}")
            check("event has speed >= min_speed_mph",
                  ev["speed_mph"] >= 40.0, f"got {ev['speed_mph']:.0f}")
    except NotImplementedError:
        print("  SKIP  stub not implemented yet")

    print("== find_off_line_events (single clean lap fallback) ==")
    try:
        single = make_race(num_full_laps=1)
        single_laps = split_laps(single)
        check("single lap splits to 2 groups",
              single_laps and len(single_laps["laps"]) == 2,
              f"got {len(single_laps['laps']) if single_laps else None}")
        events = find_off_line_events(single, single_laps)
        check("no events on clean single lap", len(events) == 0,
              f"got {len(events)}")
    except NotImplementedError:
        print("  SKIP  stub not implemented yet")

    print("== find_off_line_events (single lap with a cut) ==")
    try:
        single_cut = make_race(num_full_laps=1, cut_lap=1)
        events = find_off_line_events(single_cut, split_laps(single_cut))
        check(">=1 event on cut single lap", len(events) >= 1,
              f"got {len(events)}")
    except NotImplementedError:
        print("  SKIP  stub not implemented yet")

    print("== detect_drift_segments ==")
    drift = detect_drift_segments(make_drift_session())
    check("drift session returns segments", bool(drift))
    if drift:
        check("one merged slide", len(drift) == 1, f"got {len(drift)}")
        check("slide lasts ~2.5s",
              abs(drift[0]["duration_s"] - 2.5) < 0.5,
              f"got {drift[0]['duration_s']:.1f}s")
        check("slide speed ~50mph",
              abs(drift[0]["avg_speed_mph"] - 50) < 10,
              f"got {drift[0]['avg_speed_mph']:.0f}")

    grip = detect_drift_segments(make_grip_corner_session())
    check("gripped corner is not a drift", not grip,
          f"got {len(grip) if grip else 0}")

    no_pos = [{"t": 0.05 * i, "spd": 50.0, "str": 0.0, "hbrk": 0.0}
              for i in range(100)]
    check("no position data -> None",
          detect_drift_segments(no_pos) is None)

    if state["fails"]:
        print(f"\n{state['fails']} test(s) FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    _main()
