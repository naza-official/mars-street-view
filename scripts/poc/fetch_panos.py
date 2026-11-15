import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "layers"))

from b_common.pds import download, list_dir, raw_dir  # noqa: E402

PDS = "https://pds-imaging.jpl.nasa.gov/data/mars2020/mars2020_navcam_ops_mosaic/"
PATTERN = re.compile(r":data:(n_lrgb_(\d{4})_ras_(\d{3})(\d{4})_cyl_s_\w+?)::")


def panoramas(inventory: Path) -> dict[tuple[int, int], tuple[int, str]]:
    found: dict[tuple[int, int], tuple[int, str]] = {}
    for line in inventory.read_text().splitlines():
        m = PATTERN.search(line)
        if m:
            found.setdefault((int(m[3]), int(m[4])), (int(m[2]), m[1].upper()))
    return found


def fetch_stop(site: int, drive: int, sol: int, product: str, root: Path) -> int:
    stop_dir = root / f"m2020_site{site}_drive{drive}" / "mosaic"
    entries = list_dir(f"{PDS}data/sol/{sol:05d}/ids/rdr/mosaic/")
    hits = [e for e in entries if e.name.upper().startswith(product)]
    for e in hits:
        download(e.url, stop_dir / e.name, quiet=True)
    return len(hits)


def main() -> int:
    root = raw_dir("l4_streetview")
    stops = panoramas(root / "_index" / "mars2020_navcam_ops_mosaic.inv.csv")
    for i, ((site, drive), (sol, product)) in enumerate(sorted(stops.items()), 1):
        n = fetch_stop(site, drive, sol, product, root)
        print(f"[{i}/{len(stops)}] site {site} drive {drive} sol {sol}: {n} files {product}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
