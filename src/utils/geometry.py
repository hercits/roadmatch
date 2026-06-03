from __future__ import annotations

import math
from typing import Sequence

from utils.types import Coordinate


def haversine_m(a: Coordinate, b: Coordinate) -> float:
    """Calculate the great-circle distance between two points in meters.

    Args:
        a: First coordinate (lon, lat).
        b: Second coordinate (lon, lat).

    Returns:
        Distance in meters.
    """
    lon1, lat1 = a
    lon2, lat2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    h = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def polyline_length_m(coords: Sequence[Coordinate]) -> float:
    """Calculate the total length of a coordinate sequence in meters.

    Args:
        coords: Sequence of (lon, lat) coordinates.

    Returns:
        Total length in meters.
    """
    if len(coords) < 2:
        return 0.0
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def round_coordinate(coord: Coordinate, decimals: int = 7) -> Coordinate:
    """Round a coordinate to specified decimal places.

    Args:
        coord: (lon, lat) coordinate.
        decimals: Number of decimal places, default 7 (~1cm precision).

    Returns:
        Rounded (lon, lat) coordinate.
    """
    lon, lat = coord
    return (round(lon, decimals), round(lat, decimals))


def coord_to_node_id(coord: Coordinate, decimals: int = 7) -> str:
    """Generate a node ID from a coordinate.

    Format: '{lon:.{decimals}f}_{lat:.{decimals}f}'

    Args:
        coord: (lon, lat) coordinate.
        decimals: Number of decimal places, default 7.

    Returns:
        Node ID string.
    """
    lon, lat = round_coordinate(coord, decimals)
    return f"{lon:.{decimals}f}_{lat:.{decimals}f}"


def point_to_line_distance_m(point: Coordinate, line_start: Coordinate, line_end: Coordinate) -> float:
    """Calculate perpendicular distance from a point to an infinite line.

    Args:
        point: The point to measure from (lon, lat).
        line_start: Start point of the line (lon, lat).
        line_end: End point of the line (lon, lat).

    Returns:
        Distance in meters.
    """
    if line_start == line_end:
        return haversine_m(point, line_start)
    
    px, py = point
    x1, y1 = line_start
    x2, y2 = line_end
    
    dx = x2 - x1
    dy = y2 - y1
    
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return haversine_m(point, line_start)
    
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    
    return haversine_m(point, (proj_x, proj_y))


def bearing_degrees(a: Coordinate, b: Coordinate) -> float:
    """Calculate azimuth from a to b in degrees.

    0° = north, 90° = east, 180° = south, 270° = west.
    Clockwise from north.

    Args:
        a: Start point (lon, lat).
        b: End point (lon, lat).

    Returns:
        Azimuth in degrees, range [0, 360).
    """
    lon1, lat1 = a
    lon2, lat2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360.0) % 360.0


def angular_delta_mod180(a: float, b: float) -> float:
    """Calculate minimum angular difference on the [0, 180) circle.

    Since edges are undirected, direction_deg ranges [0, 180).
    This computes the smallest angular distance between two values
    on that circle.

    Args:
        a: First direction in degrees [0, 180).
        b: Second direction in degrees [0, 180).

    Returns:
        Minimum angular difference in degrees.
    """
    d = abs(a - b)
    return min(d, 180.0 - d)


def line_intersection_2d(
    p1: Coordinate, p2: Coordinate,
    p3: Coordinate, p4: Coordinate,
) -> Coordinate | None:
    """Find intersection of two infinite lines defined by point pairs.

    Lines are infinite (not segments), defined by two points each:
    line1 = (p1, p2), line2 = (p3, p4).

    Args:
        p1: First point on line 1 (lon, lat).
        p2: Second point on line 1 (lon, lat).
        p3: First point on line 2 (lon, lat).
        p4: Second point on line 2 (lon, lat).

    Returns:
        Intersection point (lon, lat) or None if lines are parallel.
    """
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4

    # Denominator for parametric intersection
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    # Check for parallel lines
    if abs(denom) < 1e-10:
        return None

    # Compute parameter t for line 1
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom

    # Compute intersection point
    x = x1 + t * (x2 - x1)
    y = y1 + t * (y2 - y1)

    return (x, y)


def circular_span_mod180(values: Sequence[float]) -> float:
    """Calculate the minimum arc that contains all values on the [0, 180) circle.

    The span = 180 - max_gap, where max_gap is the largest angular gap
    between consecutive sorted values on the circle.

    Args:
        values: Sequence of direction values in [0, 180).

    Returns:
        Span in degrees. 0 if single value or all identical,
        up to 180 if values are uniformly distributed.
    """
    if len(values) <= 1:
        return 0.0

    sorted_vals = sorted(set(values))
    n = len(sorted_vals)

    if n == 1:
        return 0.0

    gaps = []
    for i in range(n):
        next_val = sorted_vals[(i + 1) % n]
        curr_val = sorted_vals[i]
        gap = (next_val - curr_val) % 180.0
        gaps.append(gap)

    max_gap = max(gaps)
    return 180.0 - max_gap


def median_direction_mod180(directions: Sequence[float]) -> float:
    """Compute the median direction on the [0, 180) circle.

    Finds the minimum arc containing all values (via max-gap method),
    then returns the median of all values within that arc (including
    duplicates). The median is robust against short outlier edges that
    would skew the midpoint.

    For example, directions {5, 5, 20, 170, 177} span the arc from
    170 to 20 (crossing 0); the median of [170, 177, 5, 5, 20] in
    that arc is 5.

    Args:
        directions: Sequence of direction values in [0, 180).

    Returns:
        Median direction in degrees [0, 180). Returns 0.0 if empty,
        or the single value if only one value.
    """
    if len(directions) == 0:
        return 0.0

    dirs = sorted(d % 180.0 for d in directions)
    n = len(dirs)

    if n == 1:
        return dirs[0]

    max_gap = 0.0
    max_gap_idx = -1

    for i in range(n):
        next_val = dirs[(i + 1) % n]
        curr_val = dirs[i]
        gap = (next_val - curr_val) % 180.0
        if gap > max_gap:
            max_gap = gap
            max_gap_idx = i

    span_start_idx = (max_gap_idx + 1) % n

    if span_start_idx <= max_gap_idx:
        span_values = dirs[span_start_idx : max_gap_idx + 1]
    else:
        span_values = dirs[span_start_idx:] + dirs[: max_gap_idx + 1]

    span_start = span_values[0]
    shifted = [(v - span_start) % 180.0 for v in span_values]

    m = len(shifted)
    if m == 1:
        median_shifted = shifted[0]
    elif m % 2 == 1:
        median_shifted = shifted[m // 2]
    else:
        median_shifted = (shifted[m // 2 - 1] + shifted[m // 2]) / 2.0

    return (span_start + median_shifted) % 180.0


def project_to_bearing_m(node: Coordinate, origin: Coordinate, bearing_deg: float) -> float:
    """Project a node onto a bearing direction, returning 1D coordinate in meters.

    Converts the offset from origin to local metric coordinates, then
    projects onto the bearing unit vector.

    Args:
        node: Node coordinate (lon, lat).
        origin: Origin coordinate (lon, lat) for the projection.
        bearing_deg: Bearing in degrees (N=0, clockwise, range [0, 180)).

    Returns:
        1D projection coordinate in meters along the bearing direction.
    """
    theta_rad = bearing_deg * math.pi / 180.0
    u_east = math.sin(theta_rad)
    u_north = math.cos(theta_rad)

    lat_avg = (node[1] + origin[1]) / 2.0
    deg_per_m_lon = meters_to_degrees_lon(1.0, lat_avg)
    deg_per_m_lat = meters_to_degrees_lat(1.0)

    dx_m = (node[0] - origin[0]) / deg_per_m_lon
    dy_m = (node[1] - origin[1]) / deg_per_m_lat

    return dx_m * u_east + dy_m * u_north


def signed_perpendicular_offset_m(node: Coordinate, origin: Coordinate, bearing_deg: float) -> float:
    """Compute signed perpendicular offset of a node from a bearing axis.

    Positive offset = right side of the bearing direction.
    Negative offset = left side.

    Args:
        node: Node coordinate (lon, lat).
        origin: Origin coordinate (lon, lat) for the axis.
        bearing_deg: Bearing in degrees (N=0, clockwise).

    Returns:
        Signed perpendicular offset in meters.
    """
    theta_rad = bearing_deg * math.pi / 180.0
    perp_east = math.cos(theta_rad)
    perp_north = -math.sin(theta_rad)

    lat_avg = (node[1] + origin[1]) / 2.0
    deg_per_m_lon = meters_to_degrees_lon(1.0, lat_avg)
    deg_per_m_lat = meters_to_degrees_lat(1.0)

    dx_m = (node[0] - origin[0]) / deg_per_m_lon
    dy_m = (node[1] - origin[1]) / deg_per_m_lat

    return dx_m * perp_east + dy_m * perp_north


def offset_to_coordinate(
    origin: Coordinate,
    bearing_deg: float,
    distance_along_m: float,
    distance_perpendicular_m: float,
) -> Coordinate:
    """Convert local metric offsets along/perpendicular to a bearing to geographic coordinates.

    Inverse of project_to_bearing_m / signed_perpendicular_offset_m:
    given distances along the bearing and perpendicular to it, compute
    the geographic coordinate relative to an origin.

    Args:
        origin: Origin coordinate (lon, lat).
        bearing_deg: Bearing in degrees (N=0, clockwise).
        distance_along_m: Distance along the bearing direction in meters.
        distance_perpendicular_m: Distance perpendicular to bearing in meters
            (positive = right side).

    Returns:
        Geographic coordinate (lon, lat).
    """
    theta_rad = bearing_deg * math.pi / 180.0
    dir_east = math.sin(theta_rad)
    dir_north = math.cos(theta_rad)
    perp_east = math.cos(theta_rad)
    perp_north = -math.sin(theta_rad)

    east_m = distance_along_m * dir_east + distance_perpendicular_m * perp_east
    north_m = distance_along_m * dir_north + distance_perpendicular_m * perp_north

    deg_per_m_lon = meters_to_degrees_lon(1.0, origin[1])
    deg_per_m_lat = meters_to_degrees_lat(1.0)

    delta_lon = east_m * deg_per_m_lon
    delta_lat = north_m * deg_per_m_lat

    return (origin[0] + delta_lon, origin[1] + delta_lat)


def project_point_to_line(point: Coordinate, line_start: Coordinate, line_end: Coordinate) -> Coordinate:
    """Project a point onto an infinite line.

    Args:
        point: The point to project (lon, lat).
        line_start: Start point of the line (lon, lat).
        line_end: End point of the line (lon, lat).

    Returns:
        Projected coordinate (lon, lat) on the infinite line.
    """
    if line_start == line_end:
        return line_start
    
    px, py = point
    x1, y1 = line_start
    x2, y2 = line_end
    
    dx = x2 - x1
    dy = y2 - y1
    
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return line_start
    
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    
    return (proj_x, proj_y)


def segment_overlap_length_m(
    proj_start: Coordinate,
    proj_end: Coordinate,
    seg_start: Coordinate,
    seg_end: Coordinate,
) -> float:
    """Calculate overlap length between two segments on the same infinite line.

    All coordinates should be on or near the same line.
    Uses the fractional parameter t along the reference segment axis
    for 1D overlap calculation, then converts overlap to meters.

    Args:
        proj_start: Start of projected segment (lon, lat).
        proj_end: End of projected segment (lon, lat).
        seg_start: Start of reference segment (lon, lat).
        seg_end: End of reference segment (lon, lat).

    Returns:
        Overlap length in meters.
    """
    dx = seg_end[0] - seg_start[0]
    dy = seg_end[1] - seg_start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return 0.0

    def t_param(coord: Coordinate) -> float:
        return ((coord[0] - seg_start[0]) * dx + (coord[1] - seg_start[1]) * dy) / length_sq

    t_a1 = t_param(proj_start)
    t_a2 = t_param(proj_end)
    t_b1 = 0.0
    t_b2 = 1.0

    if t_a1 > t_a2:
        t_a1, t_a2 = t_a2, t_a1

    overlap_t_start = max(t_a1, t_b1)
    overlap_t_end = min(t_a2, t_b2)

    if overlap_t_start >= overlap_t_end:
        return 0.0

    seg_length = haversine_m(seg_start, seg_end)
    return (overlap_t_end - overlap_t_start) * seg_length


def meters_to_degrees_lon(meters: float, lat: float) -> float:
    """Convert meters to degrees longitude at a given latitude.

    Args:
        meters: Distance in meters.
        lat: Latitude in degrees.

    Returns:
        Equivalent degrees longitude.
    """
    lat_rad = math.radians(lat)
    meters_per_degree_lon = 6_371_008.8 * math.cos(lat_rad) * math.pi / 180.0
    return meters / meters_per_degree_lon


def meters_to_degrees_lat(meters: float) -> float:
    """Convert meters to degrees latitude.

    Args:
        meters: Distance in meters.

    Returns:
        Equivalent degrees latitude.
    """
    meters_per_degree_lat = 6_371_008.8 * math.pi / 180.0
    return meters / meters_per_degree_lat


def get_bounds_center(features: Sequence[dict]) -> Coordinate:
    """Calculate the center of the bounding box containing all features.

    Args:
        features: List of GeoJSON features (nodes or edges).

    Returns:
        Center coordinate (lon, lat).
    """
    lons: list[float] = []
    lats: list[float] = []
    
    for f in features:
        geom = f['geometry']
        if geom['type'] == 'Point':
            coords = geom['coordinates']
            lons.append(coords[0])
            lats.append(coords[1])
        elif geom['type'] == 'LineString':
            coords = geom['coordinates']
            for c in coords:
                lons.append(c[0])
                lats.append(c[1])
    
    if not lons or not lats:
        return (0.0, 0.0)
    
    center_lon = (min(lons) + max(lons)) / 2
    center_lat = (min(lats) + max(lats)) / 2
    
    return (center_lon, center_lat)