from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from roadmatch.config import get_config, project_path
from roadmatch.errors import GraphError, RoadmatchError
from roadmatch.geometry import Coordinate
from roadmatch.graph import RoadGraph, load_road_graph_json, save_road_graph_json


def fetch_osm_data(config: Dict[str, Any]) -> RoadGraph:
    """Fetch OSM road data through OSMnx and persist project artifacts."""

    try:
        import osmnx as ox
    except ImportError as exc:
        raise RoadmatchError(
            "OSM data download requires OSMnx. Use Python 3.11+ and install the project."
        ) from exc

    bbox = tuple(float(item) for item in get_config(config, ["osm", "bbox"]))
    network_type = str(get_config(config, ["osm", "network_type"], "drive"))
    simplify = bool(get_config(config, ["osm", "simplify"], True))
    retain_all = bool(get_config(config, ["osm", "retain_all"], False))

    graphml_path = project_path(config, "graphml", "graph.graphml")
    road_graph_path = project_path(config, "road_graph_json", "road_graph.json")
    nodes_geojson_path = project_path(config, "nodes_geojson", "nodes.geojson")
    edges_geojson_path = project_path(config, "edges_geojson", "edges.geojson")
    graphml_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        graph_from_bbox = getattr(ox, "graph_from_bbox", None) or ox.graph.graph_from_bbox
        _ensure_osmnx_tags(ox, ["bridge", "tunnel", "layer", "junction", "highway", "name"])
        nx_graph = graph_from_bbox(
            bbox,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all,
        )
        save_graphml = getattr(ox, "save_graphml", None) or ox.io.save_graphml
        save_graphml(nx_graph, filepath=graphml_path)
    except Exception as exc:  # pragma: no cover - requires live OSM service
        raise RoadmatchError(
            "Failed to fetch OSM road data. Check network access to the Overpass API, "
            "or provide an existing data/shanghai/road_graph.json cache."
        ) from exc

    graph = road_graph_from_osmnx(nx_graph)
    save_road_graph_json(graph, road_graph_path)
    write_nodes_geojson(graph, nodes_geojson_path)
    write_edges_geojson(graph, edges_geojson_path)
    return graph


def load_graph(config: Dict[str, Any]) -> RoadGraph:
    """Load the cached lightweight graph, falling back to OSMnx GraphML."""

    road_graph_path = project_path(config, "road_graph_json", "road_graph.json")
    if road_graph_path.exists():
        return load_road_graph_json(road_graph_path)

    graphml_path = project_path(config, "graphml", "graph.graphml")
    if not graphml_path.exists():
        raise GraphError(
            f"No graph cache found. Run `roadmatch fetch-data --config ...` first. "
            f"Missing {road_graph_path} and {graphml_path}."
        )

    try:
        import osmnx as ox
    except ImportError as exc:
        raise RoadmatchError(
            "Loading GraphML directly requires OSMnx. Install dependencies or keep road_graph.json."
        ) from exc

    load_graphml = getattr(ox, "load_graphml", None) or ox.io.load_graphml
    nx_graph = load_graphml(graphml_path)
    graph = road_graph_from_osmnx(nx_graph)
    save_road_graph_json(graph, road_graph_path)
    return graph


def road_graph_from_osmnx(nx_graph: Any) -> RoadGraph:
    graph = RoadGraph()
    for node_id, attrs in nx_graph.nodes(data=True):
        lon = _coerce_float(attrs.get("x"))
        lat = _coerce_float(attrs.get("y"))
        if lon is None or lat is None:
            continue
        graph.add_node(str(node_id), lon, lat, attrs=_json_safe_attrs(attrs))

    for u, v, key, attrs in nx_graph.edges(keys=True, data=True):
        u_key = str(u)
        v_key = str(v)
        if u_key not in graph.nodes or v_key not in graph.nodes:
            continue
        length = _coerce_float(attrs.get("length"))
        coords = _coords_from_edge_attrs(attrs, graph.nodes[u_key].coord, graph.nodes[v_key].coord)
        graph.add_edge(
            u_key,
            v_key,
            length_m=length,
            coords=coords,
            attrs=_json_safe_attrs(attrs),
            edge_id=f"{u_key}-{v_key}-{key}",
        )
    return graph


def write_nodes_geojson(graph: RoadGraph, path: Path) -> None:
    features = []
    for node in graph.nodes.values():
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [node.lon, node.lat]},
                "properties": {"node_id": node.node_id, **_small_properties(node.attrs)},
            }
        )
    _write_geojson(features, path)


def write_edges_geojson(graph: RoadGraph, path: Path) -> None:
    features = []
    for edge in graph.edges.values():
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lon, lat in edge.coords],
                },
                "properties": {
                    "edge_id": edge.edge_id,
                    "u": edge.u,
                    "v": edge.v,
                    "length_m": edge.length_m,
                    **_small_properties(edge.attrs),
                },
            }
        )
    _write_geojson(features, path)


def _write_geojson(features: Iterable[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": list(features)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _coords_from_edge_attrs(
    attrs: Dict[str, Any],
    start: Coordinate,
    end: Coordinate,
) -> List[Coordinate]:
    geometry = attrs.get("geometry")
    coords = _coords_from_geometry(geometry)
    if coords:
        return coords
    return [start, end]


def _coords_from_geometry(geometry: Any) -> Optional[List[Coordinate]]:
    if geometry is None:
        return None
    if hasattr(geometry, "coords"):
        return [(float(x), float(y)) for x, y in geometry.coords]
    if isinstance(geometry, str):
        return _parse_linestring_wkt(geometry)
    return None


def _parse_linestring_wkt(value: str) -> Optional[List[Coordinate]]:
    text = value.strip()
    if not text.upper().startswith("LINESTRING"):
        return None
    match = re.search(r"\((.*)\)", text)
    if not match:
        return None
    coords: List[Coordinate] = []
    for pair in match.group(1).split(","):
        parts = pair.strip().split()
        if len(parts) < 2:
            continue
        coords.append((float(parts[0]), float(parts[1])))
    return coords or None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe_attrs(attrs: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in attrs.items():
        if key == "geometry":
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, (list, tuple)):
            safe[str(key)] = [
                item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                for item in value
            ]
        else:
            safe[str(key)] = str(value)
    return safe


def _small_properties(attrs: Dict[str, Any]) -> Dict[str, Any]:
    keep = {}
    for key in ("name", "highway", "bridge", "tunnel", "layer", "junction", "oneway"):
        if key in attrs:
            keep[key] = attrs[key]
    return keep


def graph_stats(graph: RoadGraph) -> Dict[str, Any]:
    edge_length = sum(edge.length_m for edge in graph.edges.values())
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "total_edge_length_m": edge_length,
    }


def _ensure_osmnx_tags(ox: Any, tags: List[str]) -> None:
    useful_tags = getattr(ox.settings, "useful_tags_way", None)
    if useful_tags is None:
        return
    for tag in tags:
        if tag not in useful_tags:
            useful_tags.append(tag)
