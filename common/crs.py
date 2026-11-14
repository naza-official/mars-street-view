from functools import cache

import numpy as np
from pyproj import CRS, Transformer
from pyproj.crs import GeocentricCRS, ProjectedCRS
from pyproj.crs.coordinate_operation import EquidistantCylindricalConversion

from common.sites import Site

MARS_RADIUS_M = 3396190.0
METRES_PER_DEGREE = 2 * np.pi * MARS_RADIUS_M / 360.0
BASE_CRS_CODE = "IAU_2015:49900"
GLOBAL_CRS_CODE = "IAU_2015:49915"
DATUM_ID = "sphere_3396190"


@cache
def geographic() -> CRS:
    return CRS(BASE_CRS_CODE)


@cache
def global_cylindrical() -> CRS:
    return CRS(GLOBAL_CRS_CODE)


def site_crs(site: Site) -> CRS:
    params = site.local_crs
    conversion = EquidistantCylindricalConversion(
        latitude_first_parallel=params["lat_ts"],
        latitude_natural_origin=params["lat_0"],
        longitude_natural_origin=params["lon_0"],
    )
    return ProjectedCRS(conversion, name=f"Mars sphere / {site.name} local eqc",
                        geodetic_crs=geographic())


@cache
def mars_fixed() -> CRS:
    return GeocentricCRS(name="Mars (2015) - Sphere / Mars-fixed XYZ", datum=geographic().datum)


def lonlat_to_xyz_transformer() -> Transformer:
    return Transformer.from_crs(geographic().geodetic_crs, mars_fixed(), always_xy=True)


def lonlat_to_xyz(lon: np.ndarray, lat: np.ndarray, height: np.ndarray) -> np.ndarray:
    lon_r, lat_r = np.radians(lon), np.radians(lat)
    r = MARS_RADIUS_M + np.asarray(height, dtype=np.float64)
    return np.stack(
        [r * np.cos(lat_r) * np.cos(lon_r), r * np.cos(lat_r) * np.sin(lon_r), r * np.sin(lat_r)],
        axis=-1,
    )


def is_mars_sphere(crs: CRS) -> bool:
    ellipsoid = crs.ellipsoid
    if ellipsoid is None:
        return False
    return (
        abs(ellipsoid.semi_major_metre - MARS_RADIUS_M) < 1e-3
        and abs(ellipsoid.semi_minor_metre - MARS_RADIUS_M) < 1e-3
    )


def base_authority(crs: CRS) -> str | None:
    geodetic = crs.geodetic_crs
    if geodetic is None:
        return None
    if geodetic.equals(geographic(), ignore_axis_order=True):
        return BASE_CRS_CODE
    authority = geodetic.to_authority(min_confidence=90)
    return ":".join(authority) if authority else None


def normalise_lon_360(lon: np.ndarray) -> np.ndarray:
    return np.mod(lon, 360.0)
