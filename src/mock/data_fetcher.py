from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from utils.errors import OSMFetchError
from utils.osm import (
    fetch_osm_road_network,
    save_osmnx_as_geojson_separate,
)


def fetch_city_road_network(
    city_name: str,
    bbox: Sequence[float],
    resource_root: Path,
    network_type: str = "drive",
    simplify: bool = True,
    retain_all: bool = False,
    extra_tags: Optional[List[str]] = None,
) -> Path:
    """从 OSM 拉取指定城市的路网数据并保存为 GeoJSON 文件。

    生成的 GeoJSON 文件保存在 {resource_root}/{city_name}/ 目录下，
    包含 nodes.geojson（节点）和 edges.geojson（边）两个文件。

    Args:
        city_name: 城市名称，用作子目录名。
        bbox: 边界框 [left, bottom, right, top] (即 [west, south, east, north])。
        resource_root: 资源根目录，与 src 文件夹同级。
        network_type: OSMnx 路网类型，默认为 "drive"。
        simplify: 是否简化路网几何。
        retain_all: 是否保留所有连通分量。
        extra_tags: 额外需要拉取的 OSM 标签列表。

    Returns:
        城市资源目录的 Path 对象。

    Raises:
        OSMFetchError: OSM 数据拉取或文件写入失败时抛出。
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

    save_osmnx_as_geojson_separate(
        nx_graph=nx_graph,
        nodes_path=nodes_path,
        edges_path=edges_path,
        include_node_attrs=False,
        include_edge_attrs=False,
    )

    return city_dir
