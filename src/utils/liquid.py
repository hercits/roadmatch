from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from utils.types import Coordinate


def auto_utm_epsg(center_lon: float, center_lat: float) -> int:
    """Determine the UTM EPSG code from a center coordinate.

    Args:
        center_lon: Longitude in degrees.
        center_lat: Latitude in degrees.

    Returns:
        EPSG code for the appropriate UTM zone (e.g., 32651 for zone 51N).
    """
    zone_number = int((center_lon + 180) / 6) + 1
    if center_lat >= 0:
        return 32600 + zone_number
    return 32700 + zone_number


def liquidify_lines(
    line_coords_list: Sequence[Sequence[Coordinate]],
    buffer_radius_m: float,
    utm_epsg: int,
) -> Polygon | MultiPolygon:
    """Buffer and union a set of line geometries into a liquid shape.

    Each line is buffered by buffer_radius_m in a projected CRS,
    then all buffers are unioned into a single polygon.

    Args:
        line_coords_list: Sequence of line coordinate sequences,
            each a sequence of (lon, lat) pairs.
        buffer_radius_m: Buffer radius in meters.
        utm_epsg: EPSG code for the projected CRS to use for buffering.

    Returns:
        Shapely Polygon or MultiPolygon of the unioned buffers,
        in the projected CRS.
    """
    lines = [LineString(coords) for coords in line_coords_list]
    gdf = gpd.GeoDataFrame(geometry=lines, crs="EPSG:4326")
    gdf_proj = gdf.to_crs(utm_epsg)
    buffered = [geom.buffer(buffer_radius_m) for geom in gdf_proj.geometry]
    return unary_union(buffered)


def liquidify_points(
    point_coords_list: Sequence[Coordinate],
    buffer_radius_m: float,
    utm_epsg: int,
) -> Polygon | MultiPolygon:
    """Buffer and union a set of point geometries into a liquid shape.

    Each point is buffered by buffer_radius_m in a projected CRS,
    then all buffers are unioned into a single polygon.

    Args:
        point_coords_list: Sequence of (lon, lat) coordinates.
        buffer_radius_m: Buffer radius in meters.
        utm_epsg: EPSG code for the projected CRS to use for buffering.

    Returns:
        Shapely Polygon or MultiPolygon of the unioned buffers,
        in the projected CRS.
    """
    points = [Point(lon, lat) for lon, lat in point_coords_list]
    gdf = gpd.GeoDataFrame(geometry=points, crs="EPSG:4326")
    gdf_proj = gdf.to_crs(utm_epsg)
    buffered = [geom.buffer(buffer_radius_m) for geom in gdf_proj.geometry]
    return unary_union(buffered)


def centroid_to_geo(
    polygon: Polygon | MultiPolygon,
    utm_epsg: int,
) -> Coordinate:
    """Convert a polygon centroid from projected CRS to geographic (lon, lat).

    Args:
        polygon: Shapely Polygon or MultiPolygon in projected CRS.
        utm_epsg: EPSG code of the projected CRS.

    Returns:
        (lon, lat) in WGS84.
    """
    centroid_proj = polygon.centroid
    gdf_c = gpd.GeoDataFrame(geometry=[centroid_proj], crs=utm_epsg)
    gdf_c_geo = gdf_c.to_crs("EPSG:4326")
    c = gdf_c_geo.geometry[0]
    return (c.x, c.y)


def polygon_to_geojson_coords(
    polygon: Polygon | MultiPolygon,
    utm_epsg: int,
) -> List[Coordinate]:
    """Convert a polygon boundary from projected CRS to geographic coordinates.

    For MultiPolygons, returns the exterior boundary of the largest
    component (by area). For single Polygons, returns the exterior
    boundary.

    Args:
        polygon: Shapely Polygon or MultiPolygon in projected CRS.
        utm_epsg: EPSG code of the projected CRS.

    Returns:
        List of (lon, lat) coordinates along the polygon exterior boundary.
    """
    if isinstance(polygon, MultiPolygon):
        polygon = max(polygon.geoms, key=lambda g: g.area)

    gdf_p = gpd.GeoDataFrame(geometry=[polygon], crs=utm_epsg)
    gdf_p_geo = gdf_p.to_crs("EPSG:4326")
    poly_geo = gdf_p_geo.geometry[0]
    coords = list(poly_geo.exterior.coords)
    return [(c[0], c[1]) for c in coords]