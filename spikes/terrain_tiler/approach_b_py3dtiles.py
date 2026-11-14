import argparse
import subprocess
from pathlib import Path

import numpy as np
from grid import load_grid


def write_xyz(dem: Path, out: Path) -> Path:
    grid = load_grid(dem)
    xyz = grid.xyz.reshape(-1, 3)
    path = out / "points.xyz"
    out.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, xyz, fmt="%.4f")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dem", type=Path)
    parser.add_argument("--out", type=Path, default=Path("raw/spike/b_py3dtiles"))
    args = parser.parse_args()
    points = write_xyz(args.dem, args.out / "input")
    subprocess.run(["py3dtiles", "convert", str(points), "--out", str(args.out / "tiles"),
                    "--spec-version", "1.1", "--jobs", "4", "--overwrite"], check=True)


if __name__ == "__main__":
    main()
