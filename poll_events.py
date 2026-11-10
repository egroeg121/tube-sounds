#!/usr/bin/env python3
"""POC: stream London Underground arrive/depart events to the terminal.

Polls the TfL Unified API (no key needed at this volume) for the whole tube
network, tracks each train's `currentLocation` string, and emits an event
whenever a train transitions into or out of an "At <station>" state.

Usage:  python3 poll_events.py [poll_seconds]
"""

import json
import re
import sys
import time
import urllib.request

LINES = [
    "bakerloo", "central", "circle", "district", "hammersmith-city",
    "jubilee", "metropolitan", "northern", "piccadilly", "victoria",
    "waterloo-city",
]

# ANSI approximations of TfL line colours, for terminal output.
COLOURS = {
    "bakerloo": "\033[38;5;130m", "central": "\033[38;5;196m",
    "circle": "\033[38;5;220m", "district": "\033[38;5;28m",
    "hammersmith-city": "\033[38;5;218m", "jubilee": "\033[38;5;102m",
    "metropolitan": "\033[38;5;90m", "northern": "\033[38;5;250m",
    "piccadilly": "\033[38;5;27m", "victoria": "\033[38;5;45m",
    "waterloo-city": "\033[38;5;122m",
}
RESET = "\033[0m"

URL = f"https://api.tfl.gov.uk/Line/{','.join(LINES)}/Arrivals"

AT_RE = re.compile(r"^At (.+?)(?: Platform.*)?$")


def fetch_states():
    """Return {(line, vehicle): (currentLocation, nearest stationName)}."""
    req = urllib.request.Request(URL, headers={"User-Agent": "tube-sounds-poc/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        preds = json.load(r)
    best = {}
    for p in preds:
        vid = p.get("vehicleId", "")
        if vid in ("", "000"):  # shared placeholder id -> ghost events
            continue
        key = (p["lineId"], vid)
        if key not in best or p["timeToStation"] < best[key]["timeToStation"]:
            best[key] = p
    return {
        k: (v.get("currentLocation", ""), v["stationName"].replace(" Underground Station", ""))
        for k, v in best.items()
    }


def main():
    poll = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    prev = fetch_states()
    print(f"tracking {len(prev)} trains, polling every {poll}s — Ctrl-C to stop")
    while True:
        time.sleep(poll)
        try:
            cur = fetch_states()
        except Exception as e:
            print(f"  ! poll failed ({e}), retrying")
            continue
        for key in prev.keys() & cur.keys():
            (loc_a, sta_a), (loc_b, sta_b) = prev[key], cur[key]
            if loc_a == loc_b:
                continue
            was_at, now_at = AT_RE.match(loc_a), AT_RE.match(loc_b)
            line, vid = key
            c = COLOURS.get(line, "")
            if now_at and not was_at:
                station = now_at.group(1) if now_at.group(1) != "Platform" else sta_b
                print(f"{c}● ARR {line:17s}{RESET} {vid}  {station}")
            elif was_at and not now_at:
                station = was_at.group(1) if was_at.group(1) != "Platform" else sta_a
                print(f"{c}○ DEP {line:17s}{RESET} {vid}  {station}  → {loc_b}")
        prev = cur


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
