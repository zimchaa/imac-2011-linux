#!/usr/bin/env python3
# List the games you own on Steam that have a native Linux version, read from
# Steam's local cache (appcache/appinfo.vdf and packageinfo.vdf). Steam must have
# been run and logged in at least once. Set STEAM_DIR if Steam is elsewhere.
import struct, sys, os
steam = os.environ.get("STEAM_DIR", os.path.expanduser("~/.local/share/Steam"))

def read_kv(b, i, strtab=None):
    """Binary KeyValues. strtab: v29 appinfo keys are string-table indexes."""
    d = {}
    while True:
        t = b[i]; i += 1
        if t == 8: return d, i
        if strtab is not None:
            k = strtab[struct.unpack_from("<I", b, i)[0]]; i += 4
        else:
            e = b.index(0, i); k = b[i:e].decode("utf-8", "replace"); i = e + 1
        if t == 0: v, i = read_kv(b, i, strtab)
        elif t == 1:
            e = b.index(0, i); v = b[i:e].decode("utf-8", "replace"); i = e + 1
        elif t in (2, 3, 4, 6): v = struct.unpack_from("<i", b, i)[0]; i += 4
        elif t in (7, 10): v = struct.unpack_from("<q", b, i)[0]; i += 8
        else: raise ValueError(f"type {t} at {i}")
        d[k.lower()] = v

def apps():
    b = open(f"{steam}/appcache/appinfo.vdf", "rb").read()
    magic, _ = struct.unpack_from("<II", b, 0)
    assert magic == 0x07564429, hex(magic)
    stoff = struct.unpack_from("<q", b, 8)[0]
    n = struct.unpack_from("<I", b, stoff)[0]; p = stoff + 4; strtab = []
    for _ in range(n):
        e = b.index(0, p); strtab.append(b[p:e].decode("utf-8", "replace")); p = e + 1
    i = 16; out = {}
    while True:
        appid = struct.unpack_from("<I", b, i)[0]
        if appid == 0: break
        size = struct.unpack_from("<I", b, i + 4)[0]
        kv, _ = read_kv(b, i + 8 + 60, strtab)
        out[appid] = kv.get("appinfo", kv)
        i += 8 + size
    return out

def owned():
    b = open(f"{steam}/appcache/packageinfo.vdf", "rb").read()
    i = 8; ids = set()
    while True:
        pid = struct.unpack_from("<I", b, i)[0]
        if pid == 0xFFFFFFFF: break
        i += 4 + 20 + 4 + 8
        kv, i = read_kv(b, i)
        pkg = next(iter(kv.values())) if kv else {}
        ids.update(pkg.get("appids", {}).values())
    return ids

A = apps(); O = owned()
rows = []
for appid in sorted(O):
    c = A.get(appid, {}).get("common", {})
    if str(c.get("type", "")).lower() != "game": continue
    oses = str(c.get("oslist", ""))
    rows.append((c.get("name", f"app {appid}"), appid, oses))
rows.sort(key=lambda r: r[0].lower())
lin = [r for r in rows if "linux" in r[2]]
print(f"{len(rows)} owned games, {len(lin)} with a native Linux version:\n")
for n, a, o in lin: print(f"  {n}  ({a})")
print("\nWindows/Mac only (would need Proton):")
for n, a, o in rows:
    if "linux" not in o: print(f"  {n}  ({a})  [{o or '?'}]")
