try:
    from pyproj import CRS, Transformer
except Exception:
    CRS = None
    Transformer = None


def require_pyproj():
    if CRS is None or Transformer is None:
        raise RuntimeError(
            "CityGML support requires pyproj. Install it with: pip install pyproj"
        )


def build_transformers(srs_name, sample_point):
    require_pyproj()

    source_crs = CRS.from_user_input(srs_name)
    to_wgs84 = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)

    sample_x, sample_y, sample_z = sample_point
    sample_lon, sample_lat, _ = to_wgs84.transform(sample_x, sample_y, sample_z)

    local_crs = CRS.from_proj4(
        f"+proj=tmerc +lat_0={sample_lat} +lon_0={sample_lon} +k=1 "
        f"+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    to_local = Transformer.from_crs(source_crs, local_crs, always_xy=True)

    return {
        "to_wgs84": to_wgs84,
        "to_local": to_local,
        "origin_lon": sample_lon,
        "origin_lat": sample_lat,
    }


def transform_ring(ring, to_local):
    transformed = []
    for x, y, z in ring:
        lx, ly, lz = to_local.transform(x, y, z)
        transformed.append((float(lx), float(ly), float(lz)))
    return transformed
