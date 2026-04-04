import math


WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


def geodetic_to_ecef(lon_deg, lat_deg, height_m=0.0):
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

    return [
        east[0], east[1], east[2], 0.0,
        north[0], north[1], north[2], 0.0,
        up[0], up[1], up[2], 0.0,
        x, y, z, 1.0,
    ]


def meters_to_lon_delta(meters, lat_deg):
    lat = math.radians(lat_deg)
    cos_lat = max(0.1, math.cos(lat))
    return meters / (111320.0 * cos_lat)


def meters_to_lat_delta(meters):
    return meters / 110540.0
