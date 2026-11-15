import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from layers.l1_global.region import sample

KM_PER_DEG = 3396.19 * np.pi / 180
STOP = "m2020_site1_drive52"


def box(lon: float, lat: float, half_km: float) -> list[float]:
    dlat = half_km / KM_PER_DEG
    dlon = half_km / (KM_PER_DEG * np.cos(np.radians(lat)))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def stats(d: np.ndarray) -> dict:
    return {"n": int(d.size), "median_m": round(float(np.median(d)), 1),
            "p95_abs_m": round(float(np.percentile(np.abs(d), 95)), 1),
            "min_m": round(float(d.min()), 1), "max_m": round(float(d.max()), 1)}


def main(poses: Path, dtm: Path, half_km: float) -> int:
    df = pd.read_parquet(poses, columns=["stop_id", "lon", "lat", "height_above_sphere_m"])
    stop = df[df.stop_id == STOP].iloc[0]
    b = box(stop.lon, stop.lat, half_km)
    inside = df[df.lon.between(b[0], b[2]) & df.lat.between(b[1], b[3])]
    diff = inside.height_above_sphere_m.to_numpy() - sample(dtm, inside.lon.to_numpy(),
                                                            inside.lat.to_numpy())
    stop_l1 = sample(dtm, np.array([stop.lon]), np.array([stop.lat]))[0]
    print(json.dumps({
        "box_lonlat": [round(v, 4) for v in b],
        "pilot_stop": {"lon": stop.lon, "lat": stop.lat, "pose_h_m": stop.height_above_sphere_m,
                       "l1_h_m": round(float(stop_l1), 1),
                       "pose_minus_l1_m": round(float(stop.height_above_sphere_m - stop_l1), 1)},
        "poses_in_box_pose_minus_l1": stats(diff),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])))
