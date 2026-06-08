from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from utils.types import HIGHWAY_LEVEL

LEVEL_TO_HIGHWAY: dict[int, str] = {}
for _highway_type, _level in HIGHWAY_LEVEL.items():
    if _level not in LEVEL_TO_HIGHWAY:
        LEVEL_TO_HIGHWAY[_level] = _highway_type


def _clean_node_id(node_id: str) -> str:
    if node_id.startswith("C-node_"):
        return node_id[7:]
    return node_id


def _round_coord(coord: tuple | list, decimals: int = 7) -> list:
    return [round(c, decimals) for c in coord]


def export_walkable_graph_to_geojson(
    walkable_graph: Dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    edges_data = walkable_graph['edges']
    nodes_data = walkable_graph['nodes']

    edge_features = []
    for edge_idx, edge in edges_data.items():
        highway_level = edge.get('highway_level', 99)
        highway_type = LEVEL_TO_HIGHWAY.get(highway_level, "unknown")

        feature = {
            "type": "Feature",
            "properties": {
                "id": str(edge_idx),
                "highway": highway_type,
                "highway_level": highway_level,
                "line_length": round(edge.get('length_m', 0.0), 7),
                "direction_deg": round(edge.get('direction_deg', 0.0), 7),
                "start_node": _clean_node_id(edge['start_node']),
                "end_node": _clean_node_id(edge['end_node']),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    _round_coord(edge['start_coord']),
                    _round_coord(edge['end_coord']),
                ],
            },
        }
        edge_features.append(feature)

    edge_geojson = {
        "type": "FeatureCollection",
        "name": "edges",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC::CRS84"},
        },
        "features": edge_features,
    }

    edge_path = output_dir / "edges.geojson"
    with open(edge_path, "w", encoding="utf-8") as f:
        json.dump(edge_geojson, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(edge_features)} edges to {edge_path}")

    node_features = []
    for node_id, node in nodes_data.items():
        connected_edges = [str(eidx) for eidx in node.get('edges', [])]

        feature = {
            "type": "Feature",
            "properties": {
                "id": _clean_node_id(node_id),
                "degree": len(connected_edges),
                "connected_edges": connected_edges,
            },
            "geometry": {
                "type": "Point",
                "coordinates": _round_coord(node['position']),
            },
        }
        node_features.append(feature)

    node_geojson = {
        "type": "FeatureCollection",
        "name": "nodes",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": node_features,
    }

    node_path = output_dir / "nodes.geojson"
    with open(node_path, "w", encoding="utf-8") as f:
        json.dump(node_geojson, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(node_features)} nodes to {node_path}")
