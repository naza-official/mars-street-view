"""L6 Radiation and atmosphere — build per-site time series and summary cards.

Input  : $MSV_DATA/raw/l6_radiation_atmo/meda/meda_derived.tar.gz   (read in place, one pass)
         $MSV_DATA/raw/l6_radiation_atmo/meda/cal_tirs_1min.csv     (optional: ground/air temp)
         $MSV_DATA/raw/l6_radiation_atmo/rad/rad_dose.csv           (MSL RAD dosimetry)
         $MSV_DATA/out/l3_poses/<v>/  (via layer.json)              (rover position per sol, Jezero)
Output : $MSV_DATA/rover/l6_radiation_atmo/jezero/vN/   (MEDA, Perseverance)
         $MSV_DATA/rover/l6_radiation_atmo/gale/vN/     (MSL RAD, Curiosity)
           meda_jezero_daily.parquet   one row per (sol, variable): n, mean, min, max, p05, p95,
                                       local true solar time of min/max, rover lon/lat that sol
           meda_jezero_5min.parquet    5-minute means per variable (for plots and joins)
           rad_gale_daily.parquet      one row per sol: dose rate B/E in µGy/day
           summary_jezero.json, summary_gale.json   per-site cards for the viewer
           layer.json

Data rules (MEDA readme / SIS):
  * derived level where it exists; empty derived values are not reliable and stay NaN
  * 999999999 is a fill value -> NaN
  * humidity before sol 63 and wind before sol 36 are not science-grade -> dropped
  * ground temperature = calibrated TIRS GROUND_TEMP (8-14 µm brightness T) where the flags of the
    downward channels are 1 (wheel/sun out of view, footprint lit, rover still, low tilt); the mast-
    head flag concerns the upward channels only
  * values outside physical ranges (VALID_RANGE) are spikes in the raw data -> NaN
  * MEDA "RDS" measures sunlight and dust, not cosmic radiation; radiation = MSL RAD at Gale
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_common.pds import data_root  # noqa: E402
from b_common.publish import contract_sources, latest_layer, layer_dir, write_layer  # noqa: E402

VERSION = "v1"
LAYER = "l6_radiation_atmo"
GALE_BBOX = [137.30, -4.80, 137.50, -4.55]
FILL = 1e8

# sensor product -> {column: (variable name, unit)}
MEDA_VARS = {
    "PS": {"PRESSURE": ("pressure", "Pa")},
    "RHS": {
        "LOCAL_RELATIVE_HUMIDITY": ("relative_humidity", "%"),
        "VOLUME_MIXING_RATIO": ("water_vmr", "ppm"),
    },
    "WS": {
        "HORIZONTAL_WIND_SPEED": ("wind_speed", "m/s"),
        "WIND_DIRECTION": ("wind_direction", "deg"),
    },
    "TIRS": {
        "UPWARD_LW_IRRADIANCE": ("upward_lw", "W/m2"),
        "DOWNWARD_LW_IRRADIANCE": ("downward_lw", "W/m2"),
    },
}
FIRST_GOOD_SOL = {"relative_humidity": 63, "water_vmr": 63, "wind_speed": 36, "wind_direction": 36}
GROUND_FLAGS = ["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV", "SUN_OUTSIDE_TIRS_FOV", "ROVER_LOW_TILT",
                "TIRS_GROUND_FOOTPRINT_NOT_IN_SHADOW", "ROVER_STILL"]
VALID_RANGE = {
    "pressure": (400.0, 1200.0), "relative_humidity": (0.0, 100.0), "water_vmr": (0.0, 1000.0),
    "wind_speed": (0.0, 60.0), "wind_direction": (0.0, 360.0), "upward_lw": (0.0, 800.0),
    "downward_lw": (0.0, 400.0), "ground_temp": (140.0, 330.0), "air_temp": (140.0, 300.0),
}


def in_range(var: str, v: pd.Series) -> pd.Series:
    lo, hi = VALID_RANGE.get(var, (-FILL, FILL))
    return v.where((v >= lo) & (v <= hi))
UNITS = {v: u for d in MEDA_VARS.values() for v, u in d.values()}
UNITS.update({"ground_temp": "K", "air_temp": "K"})


def ltst_hours(s: pd.Series) -> pd.Series:
    """'0139 00:06:58' -> 0.116 (local true solar hours)."""
    t = s.str.split(" ").str[-1].str.split(":", expand=True).astype(float)
    return t[0] + t[1] / 60 + t[2] / 3600


def long_frame(df: pd.DataFrame, sol: int, cols: dict) -> pd.DataFrame:
    out = []
    lt = ltst_hours(df["LTST"])
    for col, (var, _) in cols.items():
        if col not in df:
            continue
        v = pd.to_numeric(df[col], errors="coerce")
        v = in_range(var, v.where(v.abs() < FILL))
        if sol < FIRST_GOOD_SOL.get(var, 0):
            continue
        out.append(pd.DataFrame({"sol": sol, "variable": var, "ltst_h": lt, "value": v}).dropna())
    return (
        pd.concat(out, ignore_index=True)
        if out
        else pd.DataFrame(columns=["sol", "variable", "ltst_h", "value"])
    )


def read_meda_tar(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One streaming pass; each 1 Hz sol file is reduced to daily + 5-minute rows before the next.

    Concatenating the raw 1 Hz rows first needs tens of GB of RAM (3.5k sols x ~50k rows x
    several variables) and took WSL down on a 16 GB machine.
    """
    days, bins = [], []
    n = 0
    with tarfile.open(path, "r|gz") as tar:
        for m in tar:
            m_ = re.search(r"WE__(\d{4})_+DER_([A-Z]+)_+P\d+\.CSV$", m.name)
            if not m_ or m_[2] not in MEDA_VARS:
                continue
            sol, sensor = int(m_[1]), m_[2]
            df = pd.read_csv(
                io.BytesIO(tar.extractfile(m).read()),
                usecols=lambda c, s_=sensor: c in {"LTST", *MEDA_VARS[s_]},
                dtype=str,
            )
            long = long_frame(df, sol, MEDA_VARS[sensor])
            if not long.empty:
                days.append(daily(long))
                bins.append(five_min(long))
            n += 1
            if n % 500 == 0:
                print(f"  {n} MEDA files read (sol {sol})", flush=True)
    print(f"  {n} MEDA files read")
    return pd.concat(days, ignore_index=True), pd.concat(bins, ignore_index=True)


def read_tirs(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["sol", "variable", "ltst_h", "value"])
    t = pd.read_csv(path, dtype={"LTST": str})
    ok = np.ones(len(t), bool)
    for f in GROUND_FLAGS:
        if f in t:
            ok &= t[f].isna() | (t[f] == 1)
    t["ltst_h"] = ltst_hours(t["LTST"])
    g = t[ok].assign(variable="ground_temp", value=pd.to_numeric(t.GROUND_TEMP, errors="coerce"))
    a = t.assign(variable="air_temp", value=pd.to_numeric(t.AIR_TEMP, errors="coerce"))
    g["value"] = in_range("ground_temp", g.value)
    a["value"] = in_range("air_temp", a.value)
    return pd.concat([g, a])[["sol", "variable", "ltst_h", "value"]].dropna()


def daily(long: pd.DataFrame) -> pd.DataFrame:
    g = long.groupby(["sol", "variable"])
    d = g.value.agg(
        n="size",
        mean="mean",
        min="min",
        max="max",
        p05=lambda x: x.quantile(0.05),
        p95=lambda x: x.quantile(0.95),
    ).reset_index()
    imin = long.loc[g.value.idxmin(), ["sol", "variable", "ltst_h"]].rename(
        columns={"ltst_h": "ltst_of_min_h"}
    )
    imax = long.loc[g.value.idxmax(), ["sol", "variable", "ltst_h"]].rename(
        columns={"ltst_h": "ltst_of_max_h"}
    )
    d = d.merge(imin, on=["sol", "variable"]).merge(imax, on=["sol", "variable"])
    d["unit"] = d.variable.map(UNITS)
    return d


def five_min(long: pd.DataFrame) -> pd.DataFrame:
    long = long.assign(bin_h=np.floor(long.ltst_h.astype(float) * 12) / 12)
    return (
        long.groupby(["sol", "variable", "bin_h"])
        .value.mean()
        .reset_index()
        .rename(columns={"bin_h": "ltst_h"})
    )


def rover_position_per_sol(sols: np.ndarray) -> pd.DataFrame:
    try:
        l3 = latest_layer("l3_poses", "jezero")
    except FileNotFoundError:
        return pd.DataFrame({"sol": sols})
    p = pd.read_parquet(
        l3 / json.loads((l3 / "layer.json").read_text())["assets"]["poses"],
        columns=["sol", "site", "drive", "lon", "lat", "height_above_sphere_m"],
    )
    p = p.dropna(subset=["sol"]).sort_values(["sol", "site", "drive"]).groupby("sol").tail(1)
    s = pd.DataFrame({"sol": np.sort(sols)})
    # rover position at the end of the latest sol <= this sol (it stays put between drives)
    return pd.merge_asof(s, p.astype({"sol": int}), on="sol", direction="backward").rename(
        columns={"lon": "rover_lon", "lat": "rover_lat", "height_above_sphere_m": "rover_height_m"}
    )


def rad_daily(path: Path) -> pd.DataFrame:
    r = pd.read_csv(path)
    r = r.dropna(subset=["dose_rate_b_ugy_h"])
    r = r[r.sol > 0]
    d = (
        r.groupby("sol")
        .agg(
            n_obs=("obs", "size"),
            dose_b_ugy_day=("dose_rate_b_ugy_h", "median"),
            dose_e_ugy_day=("dose_rate_e_ugy_h", "median"),
        )
        .reset_index()
    )
    d[["dose_b_ugy_day", "dose_e_ugy_day"]] *= 24
    return d


def card_stats(d: pd.DataFrame, var: str) -> dict | None:
    x = d[d.variable == var]
    if x.empty:
        return None
    return {
        "unit": UNITS[var],
        "sols": int(x.sol.nunique()),
        "sol_range": [int(x.sol.min()), int(x.sol.max())],
        "daily_mean_median": round(float(x["mean"].median()), 3),
        "lowest": round(float(x["min"].min()), 3),
        "highest": round(float(x["max"].max()), 3),
        "typical_daily_min": round(float(x["min"].median()), 3),
        "typical_daily_max": round(float(x["max"].median()), 3),
    }


def main() -> None:
    argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()
    raw = data_root() / "raw" / "l6_radiation_atmo"
    out = layer_dir(LAYER, "jezero", VERSION)
    gale_out = layer_dir(LAYER, "gale", VERSION)

    print("MEDA derived ...")
    d, b = read_meda_tar(raw / "meda" / "meda_derived.tar.gz")
    tirs = read_tirs(raw / "meda" / "cal_tirs_1min.csv")
    if not tirs.empty:
        tirs["sol"] = tirs.sol.astype(int)
        d = pd.concat([d, daily(tirs)], ignore_index=True)
        b = pd.concat([b, five_min(tirs)], ignore_index=True)
    # a sol can have several files of one sensor (product versions / split days): keep the larger n
    d = (
        d.sort_values("n")
        .drop_duplicates(["sol", "variable"], keep="last")
        .sort_values(["sol", "variable"])
    )
    b = b.groupby(["sol", "variable", "ltst_h"]).value.mean().reset_index()

    pos = rover_position_per_sol(d.sol.unique())
    d = d.merge(pos, on="sol", how="left")
    d.to_parquet(out / "meda_jezero_daily.parquet", index=False)
    b.to_parquet(out / "meda_jezero_5min.parquet", index=False)

    rad = rad_daily(raw / "rad" / "rad_dose.csv")
    rad.to_parquet(gale_out / "rad_gale_daily.parquet", index=False)

    built = datetime.now(UTC).isoformat(timespec="seconds")
    jez = {
        "site": "jezero",
        "rover": "perseverance",
        "instrument": "MEDA",
        "built": built,
        "variables": {v: card_stats(d, v) for v in sorted(d.variable.unique())},
        "note": "MEDA RDS measures sunlight and dust, not cosmic radiation; no dose at Jezero.",
    }
    gale = {
        "site": "gale",
        "rover": "curiosity",
        "instrument": "MSL RAD",
        "built": built,
        "dose_rate_b": {
            "unit": "µGy/day",
            "sols": int(len(rad)),
            "sol_range": [int(rad.sol.min()), int(rad.sol.max())],
            "median": round(float(rad.dose_b_ugy_day.median()), 1),
            "p05": round(float(rad.dose_b_ugy_day.quantile(0.05)), 1),
            "p95": round(float(rad.dose_b_ugy_day.quantile(0.95)), 1),
            "max": round(float(rad.dose_b_ugy_day.max()), 1),
            "sol_of_max": int(rad.loc[rad.dose_b_ugy_day.idxmax(), "sol"]),
        },
        "note": "Silicon detector B total dose; median of ~16-minute observations per sol. "
        "Sampled every 10th sol unless fetched with --sol-step 1.",
    }
    (out / "summary_jezero.json").write_text(json.dumps(jez, indent=2, ensure_ascii=False))
    (gale_out / "summary_gale.json").write_text(json.dumps(gale, indent=2, ensure_ascii=False))

    srcs = contract_sources([raw / "fetch_manifest.json"])
    meda_srcs = [x for x in srcs if "Mars2020" in x["url"]]
    rad_srcs = [x for x in srcs if "MSL-M-RAD" in x["url"]]
    jb = d[["rover_lon", "rover_lat"]].dropna()
    pad = 0.01
    write_layer(
        out,
        LAYER,
        "jezero",
        VERSION,
        [
            jb.rover_lon.min() - pad,
            jb.rover_lat.min() - pad,
            jb.rover_lon.max() + pad,
            jb.rover_lat.max() + pad,
        ],
        {
            "meda_daily": "meda_jezero_daily.parquet",
            "meda_5min": "meda_jezero_5min.parquet",
            "summary": "summary_jezero.json",
        },
        meda_srcs,
        depends_on=[f"rover/l3_poses/jezero/{latest_layer('l3_poses', 'jezero').name}"],
        notes="MEDA at the rover, per sol and 5-minute LTST bins. Units: "
        + ", ".join(f"{k} {v}" for k, v in UNITS.items()),
    )
    write_layer(
        gale_out,
        LAYER,
        "gale",
        VERSION,
        GALE_BBOX,
        {"rad_daily": "rad_gale_daily.parquet", "summary": "summary_gale.json"},
        rad_srcs,
        notes="MSL RAD total dose rate, detectors B and E, µGy/day (median per sol).",
    )
    print(
        json.dumps({k: v for k, v in jez["variables"].items()}, indent=1, ensure_ascii=False)[:2500]
    )
    print(json.dumps(gale["dose_rate_b"], ensure_ascii=False))
    print("->", out, gale_out)


if __name__ == "__main__":
    main()
