#!/usr/bin/env python3
"""Pre-build a compact National Rail network file from TfL's route sequences.

TfL is a London agency but its API knows GB-wide National Rail geometry. Fetching
all 25 operators live would be ~12 MB per page load, so distil it once into a
small static asset: a deduped station table plus polylines referencing it.
"""
import json, urllib.request, concurrent.futures, collections

OPERATORS = ['avanti-west-coast','c2c','chiltern-railways','crosscountry',
 'east-midlands-railway','gatwick-express','grand-central','greater-anglia',
 'great-northern','great-western-railway','heathrow-express','hull-trains',
 'island-line','london-north-eastern-railway','lumo','merseyrail','northern-rail',
 'scotrail','southeastern','southern','south-western-railway','thameslink',
 'transpennine-express','transport-for-wales','west-midlands-trains']
UA = {'User-Agent': 'tube-sounds-poc/0.1'}

def fetch(op):
    for direction in ('outbound', 'inbound'):
        try:
            u = f"https://api.tfl.gov.uk/Line/{op}/Route/Sequence/{direction}"
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                d = json.load(r)
            seqs = d.get('stopPointSequences') or []
            if seqs:
                return op, seqs
        except Exception as e:
            print(f"  {op}/{direction} failed: {e}")
    return op, []

stations = {}          # id -> [name, lat, lon]
routes = []            # [operator, [station index, ...]]
order = []

with concurrent.futures.ThreadPoolExecutor(6) as ex:
    for op, seqs in ex.map(fetch, OPERATORS):
        n = 0
        for s in seqs:
            idxs = []
            for sp in s.get('stopPoint', []):
                sid = sp.get('stationId') or sp.get('id')
                lat, lon = sp.get('lat'), sp.get('lon')
                if not sid or not lat or not lon:
                    continue
                if sid not in stations:
                    stations[sid] = [ (sp.get('name') or '').replace(' Rail Station',''),
                                      round(lat, 4), round(lon, 4) ]
                    order.append(sid)
                idxs.append(order.index(sid) if False else None)
            # index lookup built after the fact for speed
            routes.append([op, [sp.get('stationId') or sp.get('id') for sp in s.get('stopPoint', [])]])
            n += 1
        print(f"  {op:28s} {n} branches")

index = {sid: i for i, sid in enumerate(order)}
compact = []
for op, ids in routes:
    seq = [index[i] for i in ids if i in index]
    # drop consecutive duplicates and 1-stop fragments
    dedup = [seq[0]] if seq else []
    for x in seq[1:]:
        if x != dedup[-1]:
            dedup.append(x)
    if len(dedup) > 1:
        compact.append([op, dedup])

out = {
    'stations': [stations[sid] for sid in order],
    'routes': compact,
}
path = '/home/george/projects/tube-sounds/nr-network.json'
with open(path, 'w') as f:
    json.dump(out, f, separators=(',', ':'))

import os
lats = [s[1] for s in out['stations']]
lons = [s[2] for s in out['stations']]
print(f"\nstations: {len(out['stations'])}   polylines: {len(compact)}")
print(f"lat {min(lats):.3f}..{max(lats):.3f}   lon {min(lons):.3f}..{max(lons):.3f}")
print(f"wrote {path}  ({os.path.getsize(path)/1024:.0f} KB)")
by_op = collections.Counter(op for op, _ in compact)
print("branches per operator:", dict(by_op.most_common(6)))
