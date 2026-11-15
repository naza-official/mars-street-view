"""L6 Radiation and atmosphere — fetch raw inputs.

MEDA (Perseverance, Jezero)
  * `meda_derived.tar.gz` (~3.7 GB): the DER_* products per sol — pressure (PS), humidity
    (RHS), TIRS ground/air temperature, wind (WS), and second-by-second ANCILLARY geometry
    (sun position, rover location/attitude). Kept compressed; build.py reads members in place.
    MEDA readme: derived level is the recommended science level where it exists.
    Also used by L5 (TIRS ground temperature = ground truth for the orbital thermal layer).
  * MEDA bundle documents (SIS, release notes).

MSL RAD (Curiosity, Gale)
  * RDR files are one ~20–35 MB text file per sol (5.5k files, ~150 GB), almost all of it
    pulse-height and histogram data. We stream each file and keep only the per-observation
    dosimetry (total dose rate in detectors B and E, µGy/h) -> rad/rad_dose.csv (a few MB).
  * `--sol-step N` samples every Nth sol (default 1 = all). The index, volume docs and the
    RDR SIS are always fetched.

Usage:
  python layers/l6_radiation_atmo/fetch.py                      # MEDA + RAD, all sols
  python layers/l6_radiation_atmo/fetch.py --only rad --sol-step 10
Output: $MSV_DATA/raw/l6_radiation_atmo/{meda,rad}/
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_common.pds import USER_AGENT, download, list_dir, raw_dir, write_manifest  # noqa: E402

MEDA = "https://pds-atmospheres.nmsu.edu/PDS/data/PDS4/Mars2020/"
RAD = "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/"

RAD_FIELDS = [
    "file",
    "obs",
    "sol",
    "start_obs_mars",
    "start_obs_utc",
    "qc",
    "rover_lat",
    "rover_lon",
    "rover_elev",
    "dose_rate_b_ugy_h",
    "dose_rate_e_ugy_h",
]


def fetch_meda(out: Path) -> list[dict]:
    meda = out / "meda"
    print("MEDA derived tarball (~3.7 GB)")
    download(MEDA + "meda_derived.tar.gz", meda / "meda_derived.tar.gz")
    docs = MEDA + "mars2020_meda/document/"
    download(MEDA + "mars2020_meda/readme.txt", meda / "readme.txt")
    for e in list_dir(docs):
        if not e.is_dir:
            download(e.url, meda / "document" / e.name, quiet=True)
    return [
        {
            "name": "Mars 2020 MEDA derived (PDS Atmospheres)",
            "url": MEDA + "meda_derived.tar.gz",
            "licence": "NASA PDS, public domain",
        }
    ]


def parse_rad_stream(lines, fname: str):
    """Yield one dict per [OBSERVATION: nn] block with its total dose rates."""
    rec: dict = {}
    pending = None  # which dose key the next numeric line belongs to
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            m = re.match(r"\[(\w+): (\d+)\]", line)
            if not m:
                continue
            sect, idx = m[1], int(m[2])
            if sect == "OBSERVATION":
                if rec:
                    yield rec
                rec = {"file": fname, "obs": idx}
                pending = None
            elif sect == "DOSIMETRY_TOTAL_DOSE_B":
                pending = "dose_rate_b_ugy_h"
            elif sect == "DOSIMETRY_TOTAL_DOSE_E":
                pending = "dose_rate_e_ugy_h"
            else:
                pending = None
            continue
        if pending and rec:
            try:
                rec[pending] = float(line.split()[0])
            except ValueError:
                pass
            pending = None
            continue
        if rec and "=" in line and len(rec) < 12:
            k, v = line.split("=", 1)
            k = k.strip().lower()
            v = v.strip().strip('"')
            if k in (
                "start_obs_mars",
                "start_obs_utc",
                "qc",
                "rover_lat",
                "rover_lon",
                "rover_elev",
            ):
                rec[k] = v
                if k == "start_obs_mars":
                    head = v.split()[0] if v.split() else ""
                    rec["sol"] = int(head) if head.isdigit() else file_sol(fname)
    if rec:
        yield rec


def file_sol(fname: str) -> int | None:
    """RAD_RDR_2013_318_12_01_0453_V00.TXT -> 453 (used when a block says START_OBS_MARS=N/A)."""
    m = re.search(r"_(\d{4})_V\d+\.TXT$", fname)
    return int(m[1]) if m else None


def fetch_rad(out: Path, sol_step: int, sol_min: int, sol_max: int) -> list[dict]:
    rad = out / "rad"
    for rel in [
        "AAREADME.TXT",
        "ERRATA.TXT",
        "INDEX/INDEX.TAB",
        "INDEX/INDEX.LBL",
        "DOCUMENT/RAD_RDR_SIS.PDF",
        "DOCUMENT/RAD_RDR_SIS.HTM",
        "DOCUMENT/TIMING_ERRORS.TXT",
    ]:
        download(RAD + rel, rad / rel, quiet=True)

    files = []
    for row in csv.reader(open(rad / "INDEX/INDEX.TAB")):
        if len(row) < 2 or not row[1].strip().strip('"').startswith("/DATA/SOL_"):
            continue
        lbl = row[1].strip().strip('"')
        m = re.search(r"_(\d{4})_V\d+\.LBL$", lbl)
        if m:
            files.append((int(m[1]), lbl[1:].replace(".LBL", ".TXT")))
    files.sort()
    sols = sorted({s for s, _ in files if sol_min <= s <= sol_max})
    keep_sols = set(sols[::sol_step])
    todo = [(s, f) for s, f in files if s in keep_sols]

    dest = rad / "rad_dose.csv"
    done_files = set()
    if dest.exists():
        with open(dest) as f:
            done_files = {r["file"] for r in csv.DictReader(f)}
    new = not dest.exists()
    print(f"RAD: {len(todo)} sol files to scan ({len(done_files)} already done), step {sol_step}")
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    with open(dest, "a", newline="") as fo:
        w = csv.DictWriter(fo, RAD_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for i, (sol, rel) in enumerate(todo, 1):
            name = rel.rsplit("/", 1)[-1]
            if name in done_files:
                continue
            for _attempt in range(4):
                try:
                    with s.get(RAD + rel, stream=True, timeout=300) as r:
                        r.raise_for_status()
                        rows = list(parse_rad_stream(r.iter_lines(decode_unicode=True), name))
                    break
                except requests.RequestException as e:
                    print(f"  retry {name}: {e}", file=sys.stderr)
                    rows = None
            if rows is None:
                print(f"  FAILED {name}", file=sys.stderr)
                continue
            w.writerows(rows)
            fo.flush()
            print(f"  [{i}/{len(todo)}] sol {sol} {name}: {len(rows)} obs", flush=True)
    return [
        {
            "name": "MSL RAD RDR (PDS PPI), dosimetry extracted",
            "url": RAD,
            "licence": "NASA PDS, public domain",
        }
    ]


TIRS_KEEP = [
    "SCLK",
    "LMST",
    "LTST",
    "AIR_TEMP",
    "AIR_TEMP_UNCERTAINTY",
    "GROUND_TEMP",
    "GROUND_TEMP_UNCERTAINTY",
    "UPWARD_LW_IRRADIANCE",
    "DOWNWARD_LW_IRRADIANCE",
    "RSM_HEAD_OUTSIDE_TIRS_UPWARD_LOOKING_FOV",
    "WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV",
    "SUN_OUTSIDE_TIRS_FOV",
    "ROVER_LOW_TILT",
    "TIRS_GROUND_FOOTPRINT_NOT_IN_SHADOW",
    "ROVER_STILL",
]


def fetch_tirs(
    out: Path, sol_step: int, sol_min: int, sol_max: int, every_s: float = 60.0
) -> list[dict]:
    """MEDA calibrated TIRS -> ground + air temperature, one row per `every_s` seconds.

    GROUND_TEMP (8-14 µm brightness temperature) exists only at the calibrated level; the full
    calibrated tarball is 13 GB and CAL_TIRS alone ~5 MB/sol, so each sol file is streamed and
    decimated instead of stored. Used by L5 (ground truth for orbital thermal inertia) and L6.
    """
    base = MEDA + "mars2020_meda/data_calibrated_env/"
    dest = out / "meda" / "cal_tirs_1min.csv"
    done = set()
    if dest.exists():
        with open(dest) as f:
            done = {int(r["sol"]) for r in csv.DictReader(f)}
    sol_dirs = []
    for rng in list_dir(base):
        if not rng.is_dir or not rng.name.startswith("sol_"):
            continue
        lo, hi = (int(x) for x in rng.name.split("_")[1:3])
        if hi < sol_min or lo > sol_max:
            continue
        for sd in list_dir(rng.url):
            if sd.is_dir and sd.name.startswith("sol_"):
                sol = int(sd.name[4:])
                if sol_min <= sol <= sol_max:
                    sol_dirs.append((sol, sd.url))
    sol_dirs.sort()
    todo = [x for x in sol_dirs[::sol_step] if x[0] not in done]
    print(f"TIRS: {len(sol_dirs)} sols available, {len(todo)} to fetch (step {sol_step})")
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    new = not dest.exists()
    with open(dest, "a", newline="") as fo:
        w = csv.writer(fo)
        if new:
            w.writerow(["sol"] + TIRS_KEEP)
        for i, (sol, url) in enumerate(todo, 1):
            files = [e for e in list_dir(url) if "CAL_TIRS" in e.name and e.name.endswith(".CSV")]
            if not files:
                continue
            f = sorted(files, key=lambda e: e.name)[-1]  # highest product version
            for _attempt in range(4):
                try:
                    rows = []
                    with s.get(f.url, stream=True, timeout=300) as r:
                        r.raise_for_status()
                        lines = r.iter_lines(decode_unicode=True)
                        hdr = next(lines).split(",")
                        idx = [hdr.index(c) if c in hdr else None for c in TIRS_KEEP]
                        last = -1e18
                        for line in lines:
                            v = line.split(",")
                            try:
                                t = float(v[0])
                            except ValueError:
                                continue
                            if t - last < every_s:
                                continue
                            last = t
                            rows.append(
                                [sol] + [v[j] if j is not None and j < len(v) else "" for j in idx]
                            )
                    break
                except requests.RequestException as e:
                    print(f"  retry sol {sol}: {e}", file=sys.stderr)
                    rows = None
            if rows is None:
                print(f"  FAILED sol {sol}", file=sys.stderr)
                continue
            w.writerows(rows)
            fo.flush()
            print(f"  [{i}/{len(todo)}] sol {sol} {f.name}: {len(rows)} rows", flush=True)
    return [
        {
            "name": "Mars 2020 MEDA calibrated TIRS (PDS Atmospheres), decimated to 1/min",
            "url": base,
            "licence": "NASA PDS, public domain",
        }
    ]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", choices=["meda", "rad", "tirs"])
    ap.add_argument("--sol-step", type=int, default=1)
    ap.add_argument("--sol-min", type=int, default=0)
    ap.add_argument("--sol-max", type=int, default=99999)
    a = ap.parse_args()
    out = raw_dir("l6_radiation_atmo")
    sources = []
    if a.only in (None, "meda"):
        sources += fetch_meda(out)
    if a.only in (None, "rad"):
        sources += fetch_rad(out, a.sol_step, a.sol_min, a.sol_max)
    if a.only in (None, "tirs"):
        sources += fetch_tirs(out, a.sol_step, a.sol_min, a.sol_max)
    write_manifest(out, sources)
    print("done ->", out)


if __name__ == "__main__":
    main()
