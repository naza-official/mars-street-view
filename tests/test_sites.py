from common.sites import SITES_DIR, load_site


def test_jezero_config_exists() -> None:
    assert (SITES_DIR / "jezero.yaml").is_file()


def test_jezero_bbox_matches_plan() -> None:
    assert load_site("jezero").bbox_lonlat == (77.2, 18.2, 77.7, 18.7)


def test_jezero_centre_inside_bbox() -> None:
    site = load_site("jezero")
    west, south, east, north = site.bbox_lonlat
    lon, lat = site.centre_lonlat
    assert west < lon < east and south < lat < north


def test_jezero_local_crs_on_mars_sphere() -> None:
    crs = load_site("jezero").local_crs
    assert crs["projection"] == "eqc"
    assert crs["radius_m"] == 3396190
    assert "+R=3396190" in crs["proj4"]
