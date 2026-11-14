import argparse
import json
import tempfile
from pathlib import Path

from common.datum import check_adjustment, read_areoid, run_dem_geoid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare ASP dem_geoid with the Python areoid")
    parser.add_argument("dem", type=Path)
    parser.add_argument("--areoid", type=Path, default=Path("raw/areoid/mega90n000cb.img"))
    parser.add_argument("--tolerance-m", type=float, default=5.0)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        adjusted = run_dem_geoid(args.dem, Path(tmp) / "out")
        result = check_adjustment(args.dem, adjusted, read_areoid(args.areoid))
    print(json.dumps(result))
    return 0 if result["max_abs_m"] <= args.tolerance_m else 1


if __name__ == "__main__":
    raise SystemExit(main())
