import numpy as np
import pytest
from pyproj import CRS, Transformer

from common import crs
from common.sites import load_site


def test_geographic_is_iau_sphere() -> None:
    geo = crs.geographic()
    assert geo.to_authority() == ("IAU_2015", "49900")
    assert crs.is_mars_sphere(geo)


def test_global_is_clon_180_sphere() -> None:
    glob = crs.global_cylindrical()
    assert crs.is_mars_sphere(glob)
    assert crs.base_authority(glob) == crs.BASE_CRS_CODE
    to_xy = Transformer.from_crs(crs.geographic(), glob, always_xy=True)
    x0, _ = to_xy.transform(0.0001, 0)
    x360, _ = to_xy.transform(359.9999, 0)
    assert x0 == pytest.approx(-np.pi * crs.MARS_RADIUS_M, abs=10)
    assert x360 == pytest.approx(np.pi * crs.MARS_RADIUS_M, abs=10)


def test_site_crs_centred_and_keeps_authority() -> None:
    site = load_site("jezero")
    local = crs.site_crs(site)
    assert crs.base_authority(local) == crs.BASE_CRS_CODE
    to_xy = Transformer.from_crs(crs.geographic(), local, always_xy=True)
    x, y = to_xy.transform(*site.centre_lonlat)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1e-6)


def test_site_crs_metric_scale_on_sphere() -> None:
    local = crs.site_crs(load_site("jezero"))
    to_xy = Transformer.from_crs(crs.geographic(), local, always_xy=True)
    _, y = to_xy.transform(77.5, 19.4)
    assert y == pytest.approx(crs.METRES_PER_DEGREE, rel=1e-9)


def test_xyz_transformer_matches_formula() -> None:
    lon = np.array([77.5, 0.0, 300.0])
    lat = np.array([18.4, 0.0, -45.0])
    h = np.array([-2500.0, 0.0, 10.0])
    x, y, z = crs.lonlat_to_xyz_transformer().transform(lon, lat, h)
    expected = crs.lonlat_to_xyz(lon, lat, h)
    np.testing.assert_allclose(np.stack([x, y, z], axis=-1), expected, atol=1e-6)


def test_xyz_radius_is_sphere_plus_height() -> None:
    xyz = crs.lonlat_to_xyz(np.array([77.5]), np.array([18.4]), np.array([-2500.0]))
    assert np.linalg.norm(xyz) == pytest.approx(crs.MARS_RADIUS_M - 2500.0)


def test_earth_crs_is_not_mars() -> None:
    assert not crs.is_mars_sphere(CRS("EPSG:4326"))
    assert crs.base_authority(CRS("EPSG:3857")) == "EPSG:4326"
