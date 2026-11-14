from dataclasses import dataclass
from pathlib import Path

import yaml

SITES_DIR = Path(__file__).resolve().parent.parent / "configs" / "sites"


@dataclass(frozen=True)
class Site:
    name: str
    bbox_lonlat: tuple[float, float, float, float]
    centre_lonlat: tuple[float, float]
    local_crs: dict


def load_site(name: str, sites_dir: Path = SITES_DIR) -> Site:
    data = yaml.safe_load((sites_dir / f"{name}.yaml").read_text())
    return Site(
        name=data["name"],
        bbox_lonlat=tuple(data["bbox_lonlat"]),
        centre_lonlat=tuple(data["centre_lonlat"]),
        local_crs=data["local_crs"],
    )
