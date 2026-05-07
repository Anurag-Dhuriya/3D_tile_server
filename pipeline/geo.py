import math


WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3

_M_PER_DEG_LON = math.pi * WGS84_A / 180.0
_M_PER_DEG_LAT = math.pi * WGS84_A * (1.0 - WGS84_E2) / 180.0


def geodetic_to_ecef(lon_deg, lat_deg, height_m=0.0):
    if not (-90.0 <= lat_deg <= 90.0):
        raise ValueError(f"lat_deg must be in [-90, 90], got {lat_deg}")
    if not (-180.0 <= lon_deg <= 180.0):
        raise ValueError(f"lon_deg must be in [-180, 180], got {lon_deg}")

    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)

    x = (n + height_m) * cos_lat * cos_lon
    y = (n + height_m) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + height_m) * sin_lat
    return x, y, z


def east_north_up_transform(lon_deg, lat_deg, height_m=0.0):
    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    east = (-sin_lon, cos_lon, 0.0)
    north = (-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat)
    up = (cos_lat * cos_lon, cos_lat * sin_lon, sin_lat)

    x, y, z = geodetic_to_ecef(lon_deg, lat_deg, height_m)

    # Column-major 4x4 matrix for glTF / 3D Tiles.
    # Columns are: East, North, Up, Translation.
    return [
        east[0], east[1], east[2], 0.0,
        north[0], north[1], north[2], 0.0,
        up[0], up[1], up[2], 0.0,
        x, y, z, 1.0,
    ]


def meters_to_lon_delta(meters, lat_deg):
    lat = math.radians(lat_deg)
    cos_lat = max(1e-9, math.cos(lat))
    return meters / (_M_PER_DEG_LON * cos_lat)


def meters_to_lat_delta(meters):
    return meters / _M_PER_DEG_LAT
