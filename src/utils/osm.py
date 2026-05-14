from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.errors import OSMFetchError
from utils.types import Coordinate


def _ensure_osmnx_tags(ox_module: Any, tags: List[str]) -> None:
    """确保 OSMnx 在下载时包含需要的 OSM 标签字段。"""
    useful_tags = getattr(ox_module.settings, "useful_tags_way", None)
    if useful_tags is None:
        return
    for tag in tags:
        if tag not in useful_tags:
            useful_tags.append(tag)


def _coerce_float(value: Any) -> Optional[float]:
    """安全将输入转为 float，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe_attrs(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """将 OSM 节点/边属性中不可 JSON 序列化的值转为字符串。"""
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


def _coords_from_geometry(geometry: Any) -> Optional[List[Coordinate]]:
    """从 OSMnx 边数据的 geometry 字段中提取坐标序列。"""
    if geometry is None:
        return None
    if hasattr(geometry, "coords"):
        return [(float(x), float(y)) for x, y in geometry.coords]
    if isinstance(geometry, str):
        return _parse_linestring_wkt(geometry)
    return None


def _parse_linestring_wkt(value: str) -> Optional[List[Coordinate]]:
    """解析 WKT 格式的 LINESTRING 字符串为坐标列表。"""
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


def _coords_from_edge_attrs(
    attrs: Dict[str, Any],
    start: Coordinate,
    end: Coordinate,
) -> List[Coordinate]:
    """从边属性提取坐标序列，无 geometry 时则使用起终点。"""
    geometry = attrs.get("geometry")
    coords = _coords_from_geometry(geometry)
    if coords:
        return coords
    return [start, end]


def convert_osmnx_to_geojson(
    nx_graph: Any,
    include_node_attrs: bool = True,
    include_edge_attrs: bool = True,
) -> Dict[str, Any]:
    """将 OSMnx networkx 图转换为 GeoJSON FeatureCollection。

    包含 nodes (Point) 和 edges (LineString) 两部分。

    Args:
        nx_graph: OSMnx 返回的 networkx 图对象。
        include_node_attrs: 是否保留节点 OSM 属性。
        include_edge_attrs: 是否保留边 OSM 属性。

    Returns:
        包含全部节点和边的 GeoJSON FeatureCollection 字典。
    """
    features: List[Dict[str, Any]] = []

    # 转换节点为 Point 要素
    for node_id, attrs in nx_graph.nodes(data=True):
        lon = _coerce_float(attrs.get("x"))
        lat = _coerce_float(attrs.get("y"))
        if lon is None or lat is None:
            continue
        properties: Dict[str, Any] = {"node_id": str(node_id)}
        if include_node_attrs:
            properties.update(_json_safe_attrs(attrs))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": properties,
        })

    # 转换边为 LineString 要素
    for u, v, key, attrs in nx_graph.edges(keys=True, data=True):
        u_key = str(u)
        v_key = str(v)
        start_coord: Coordinate = (float(nx_graph.nodes[u]["x"]), float(nx_graph.nodes[u]["y"]))
        end_coord: Coordinate = (float(nx_graph.nodes[v]["x"]), float(nx_graph.nodes[v]["y"]))
        coords = _coords_from_edge_attrs(attrs, start_coord, end_coord)
        edge_properties: Dict[str, Any] = {
            "edge_id": f"{u_key}-{v_key}-{key}",
            "u": u_key,
            "v": v_key,
        }
        if include_edge_attrs:
            edge_properties.update(_json_safe_attrs(attrs))
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": edge_properties,
        })

    return {"type": "FeatureCollection", "features": features}


def fetch_osm_road_network(
    bbox: Sequence[float],
    network_type: str = "drive",
    simplify: bool = True,
    retain_all: bool = False,
    extra_tags: Optional[List[str]] = None,
) -> Any:
    """通过 OSMnx 从 OpenStreetMap 拉取路网数据。

    Args:
        bbox: 边界框 [left, bottom, right, top] (即 [west, south, east, north])。
        network_type: OSMnx 路网类型，默认为 "drive"。
        simplify: 是否简化路网几何。
        retain_all: 是否保留所有连通分量。
        extra_tags: 额外需要拉取的 OSM 标签列表。

    Returns:
        OSMnx 返回的 networkx 图对象。

    Raises:
        OSMFetchError: 网络异常或 OSM 服务不可用时抛出。
    """
    try:
        import osmnx as ox
    except ImportError as exc:
        raise OSMFetchError(
            "OSM 数据下载需要 OSMnx 库。请使用 Python 3.11+ 并安装项目依赖。"
        ) from exc

    tags = ["bridge", "tunnel", "layer", "junction", "highway", "name"]
    if extra_tags:
        tags.extend(extra_tags)
    _ensure_osmnx_tags(ox, tags)

    try:
        graph_from_bbox = getattr(ox, "graph_from_bbox", None) or ox.graph.graph_from_bbox
        nx_graph = graph_from_bbox(
            bbox,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all,
        )
    except Exception as exc:
        raise OSMFetchError(
            "拉取 OSM 路网数据失败。请检查网络连接（Overpass API），"
            "或使用已有缓存文件。"
        ) from exc

    return nx_graph


def save_geojson(feature_collection: Dict[str, Any], output_path: Path) -> None:
    """将 GeoJSON FeatureCollection 写入文件。

    Args:
        feature_collection: GeoJSON FeatureCollection 字典。
        output_path: 输出文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(feature_collection, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_osmnx_as_geojson_separate(
    nx_graph: Any,
    nodes_path: Path,
    edges_path: Path,
    include_node_attrs: bool = False,
    include_edge_attrs: bool = False,
) -> None:
    """将 OSMnx 图的节点和边分别保存为独立的 GeoJSON 文件。

    节点保存为 Point 要素集合，边保存为 LineString 要素集合。
    边属性中仅保留关键字段（name, highway, bridge, tunnel, layer, junction, oneway）。

    Args:
        nx_graph: OSMnx 返回的 networkx 图对象。
        nodes_path: 节点 GeoJSON 输出路径。
        edges_path: 边 GeoJSON 输出路径。
        include_node_attrs: 是否在节点属性中包含全部 OSM 标签。
        include_edge_attrs: 是否在边属性中包含全部 OSM 标签（否则只保留关键字段）。
    """
    node_features: List[Dict[str, Any]] = []
    edge_features: List[Dict[str, Any]] = []

    for node_id, attrs in nx_graph.nodes(data=True):
        lon = _coerce_float(attrs.get("x"))
        lat = _coerce_float(attrs.get("y"))
        if lon is None or lat is None:
            continue
        properties: Dict[str, Any] = {"node_id": str(node_id)}
        if include_node_attrs:
            properties.update(_json_safe_attrs(attrs))
        node_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": properties,
        })

    for u, v, key, attrs in nx_graph.edges(keys=True, data=True):
        u_key = str(u)
        v_key = str(v)
        start_coord = (float(nx_graph.nodes[u]["x"]), float(nx_graph.nodes[u]["y"]))
        end_coord = (float(nx_graph.nodes[v]["x"]), float(nx_graph.nodes[v]["y"]))
        coords = _coords_from_edge_attrs(attrs, start_coord, end_coord)

        edge_properties: Dict[str, Any] = {
            "edge_id": f"{u_key}-{v_key}-{key}",
            "u": u_key,
            "v": v_key,
        }
        if include_edge_attrs:
            edge_properties.update(_json_safe_attrs(attrs))
        else:
            for field in ("name", "highway", "bridge", "tunnel", "layer", "junction", "oneway"):
                if field in attrs:
                    edge_properties[field] = attrs[field]

        edge_features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": edge_properties,
        })

    save_geojson({"type": "FeatureCollection", "features": node_features}, nodes_path)
    save_geojson({"type": "FeatureCollection", "features": edge_features}, edges_path)
