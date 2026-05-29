from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from utils.errors import OSMFetchError
from utils.geometry import bearing_degrees, coord_to_node_id, haversine_m, polyline_length_m, round_coordinate
from utils.osm import fetch_osm_road_network, save_geojson, save_osmnx_as_geojson_separate
from utils.types import Coordinate


def split_edges_to_segments(
    node_features: List[Dict[str, Any]],
    edge_features: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split edges into straight segments by converting waypoints to nodes.

    This is a lossless transformation that:
    1. Normalizes node IDs to coordinate-based format: '{lon:.7f}_{lat:.7f}'
    2. Merges nodes that coincide at the same coordinate
    3. Splits edges with intermediate waypoints into straight segments
    4. Creates new nodes at waypoint locations

    Args:
        node_features: List of node GeoJSON features.
        edge_features: List of edge GeoJSON features.

    Returns:
        Tuple of (new_node_features, new_edge_features).
    """
    node_id_remap: Dict[str, str] = {}
    coord_to_node: Dict[Coordinate, str] = {}
    new_nodes: List[Dict[str, Any]] = []

    for feature in node_features:
        props = feature["properties"]
        old_id = props["node_id"]
        coords = feature["geometry"]["coordinates"]
        coord = (coords[0], coords[1])
        rounded_coord = round_coordinate(coord)
        node_id = coord_to_node_id(rounded_coord)

        node_id_remap[old_id] = node_id

        if rounded_coord not in coord_to_node:
            coord_to_node[rounded_coord] = node_id
            new_nodes.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [rounded_coord[0], rounded_coord[1]],
                },
                "properties": {
                    "node_id": node_id,
                },
            })

    new_edges: List[Dict[str, Any]] = []

    for feature in edge_features:
        props = feature["properties"]
        original_edge_id = props["edge_id"]
        original_u = props["u"]
        original_v = props["v"]

        coords = feature["geometry"]["coordinates"]
        if not coords or len(coords) < 2:
            continue

        rounded_coords = [round_coordinate((c[0], c[1])) for c in coords]

        for i, rc in enumerate(rounded_coords):
            if rc not in coord_to_node:
                node_id = coord_to_node_id(rc)
                coord_to_node[rc] = node_id
                new_nodes.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [rc[0], rc[1]],
                    },
                    "properties": {
                        "node_id": node_id,
                    },
                })

        for seg_idx in range(len(rounded_coords) - 1):
            start_coord = rounded_coords[seg_idx]
            end_coord = rounded_coords[seg_idx + 1]

            start_node_id = coord_to_node[start_coord]
            end_node_id = coord_to_node[end_coord]

            segment_coords = [start_coord, end_coord]
            segment_length = haversine_m(start_coord, end_coord)

            new_props = dict(props)
            new_props["edge_id"] = f"{original_edge_id}_{seg_idx}"
            new_props["u"] = start_node_id
            new_props["v"] = end_node_id
            new_props["length"] = segment_length
            new_props["direction_deg"] = round(bearing_degrees(start_coord, end_coord) % 180.0, 2)

            new_edges.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[c[0], c[1]] for c in segment_coords],
                },
                "properties": new_props,
            })

    return new_nodes, new_edges


def fetch_city_road_network(
    city_name: str,
    bbox: Sequence[float],
    resource_root: Path,
    network_type: str = "drive",
    simplify: bool = True,
    retain_all: bool = False,
    extra_tags: List[str] | None = None,
) -> Path:
    """Fetch road network from OSM and save as GeoJSON files.

    Saves raw OSMnx output to raw/ subdirectory, then transforms to split
    edge segments in the main directory.

    Args:
        city_name: City name, used as subdirectory name.
        bbox: Bounding box [left, bottom, right, top] (i.e., [west, south, east, north]).
        resource_root: Root directory for resources.
        network_type: OSMnx network type, default "drive".
        simplify: Whether to simplify network geometry.
        retain_all: Whether to retain all connected components.
        extra_tags: Additional OSM tags to fetch.

    Returns:
        Path to the city resource directory.

    Raises:
        OSMFetchError: If OSM data fetch or file write fails.
    """
    city_dir = resource_root / city_name
    city_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = city_dir / "nodes.geojson"
    edges_path = city_dir / "edges.geojson"

    nx_graph = fetch_osm_road_network(
        bbox=bbox,
        network_type=network_type,
        simplify=simplify,
        retain_all=retain_all,
        extra_tags=extra_tags,
    )

    raw_dir = city_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    raw_nodes_path = raw_dir / "nodes.geojson"
    raw_edges_path = raw_dir / "edges.geojson"
    save_osmnx_as_geojson_separate(
        nx_graph=nx_graph,
        nodes_path=raw_nodes_path,
        edges_path=raw_edges_path,
        include_node_attrs=True,
        include_edge_attrs=True,
    )

    raw_nodes_data = json.loads(raw_nodes_path.read_text(encoding="utf-8"))
    raw_edges_data = json.loads(raw_edges_path.read_text(encoding="utf-8"))

    split_nodes, split_edges = split_edges_to_segments(
        raw_nodes_data["features"],
        raw_edges_data["features"],
    )

    save_geojson({"type": "FeatureCollection", "features": split_nodes}, nodes_path)
    save_geojson({"type": "FeatureCollection", "features": split_edges}, edges_path)

    return city_dir