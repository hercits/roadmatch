from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple


Coordinate = Tuple[float, float]

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(a: Coordinate, b: Coordinate) -> float:
    """Return great-circle distance in meters between lon/lat coordinates."""

    lon1, lat1 = a
    lon2, lat2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    h = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def polyline_length_m(coords: Sequence[Coordinate]) -> float:
    if len(coords) < 2:
        return 0.0
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def bearing_degrees(a: Coordinate, b: Coordinate) -> float:
    """Return initial bearing from coordinate a to b in degrees clockwise from north."""

    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360.0) % 360.0


def angular_delta_degrees(a: float, b: float) -> float:
    """Return the smallest absolute difference between two bearings."""

    return abs((b - a + 180.0) % 360.0 - 180.0)


def classify_turn(bearing_in: float, bearing_out: float, threshold_degrees: float = 35.0) -> str:
    delta = angular_delta_degrees(bearing_in, bearing_out)
    if delta <= threshold_degrees:
        return "straight"
    return "turn"


def orient_coords(coords: Sequence[Coordinate], start: Coordinate, end: Coordinate) -> List[Coordinate]:
    """Orient an edge polyline so it runs from start toward end."""

    if not coords:
        return [start, end]
    direct = haversine_m(coords[0], start) + haversine_m(coords[-1], end)
    reverse = haversine_m(coords[-1], start) + haversine_m(coords[0], end)
    if reverse < direct:
        return list(reversed(coords))
    return list(coords)


def dedupe_joined_coords(parts: Iterable[Sequence[Coordinate]]) -> List[Coordinate]:
    joined: List[Coordinate] = []
    for part in parts:
        for coord in part:
            if not joined or joined[-1] != coord:
                joined.append(coord)
    return joined


def point_interval_score(point_m: float, interval_m: Tuple[float, float], softness_m: float) -> float:
    """Score how well a point falls in an observed interval.

    Inside the interval scores 1. Outside decays smoothly with distance.
    """

    lo, hi = interval_m
    if lo > hi:
        lo, hi = hi, lo
    if lo <= point_m <= hi:
        return 1.0
    distance = lo - point_m if point_m < lo else point_m - hi
    softness = max(softness_m, 1.0)
    return math.exp(-distance / softness)


def interpolate_polyline(coords: Sequence[Coordinate], distance_m: float) -> Coordinate:
    """Return an approximate lon/lat coordinate at cumulative distance along a polyline."""

    if not coords:
        raise ValueError("Cannot interpolate an empty polyline")
    if len(coords) == 1 or distance_m <= 0:
        return coords[0]

    remaining = distance_m
    for index in range(len(coords) - 1):
        segment_len = haversine_m(coords[index], coords[index + 1])
        if segment_len <= 0:
            continue
        if remaining <= segment_len:
            ratio = remaining / segment_len
            lon = coords[index][0] + (coords[index + 1][0] - coords[index][0]) * ratio
            lat = coords[index][1] + (coords[index + 1][1] - coords[index][1]) * ratio
            return lon, lat
        remaining -= segment_len
    return coords[-1]
