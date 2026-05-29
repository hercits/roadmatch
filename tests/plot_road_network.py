"""加载路网数据并使用 Plotly 交互式绘制。

用法:
    uv run python tests/plot_road_network.py
    uv run python tests/plot_road_network.py --data-dir resource/shanghai

依赖（已添加至 pyproject.toml）:
    plotly>=6.7.0
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import plotly.graph_objects as go


def load_geojson(path: Path) -> list[dict]:
    """加载 GeoJSON 文件并返回 features 列表。

    Args:
        path: GeoJSON 文件路径。

    Returns:
        features 列表。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def build_node_trace(features: list[dict]) -> go.Scattermap:
    """根据节点 GeoJSON 构建散点图 trace（带序号）。"""
    lons, lats, texts = [], [], []
    for idx, f in enumerate(features):
        lons.append(f["geometry"]["coordinates"][0])
        lats.append(f["geometry"]["coordinates"][1])
        texts.append(str(idx))
    return go.Scattermap(
        lon=lons,
        lat=lats,
        mode="markers+text",
        marker=dict(size=6, color="#e63946", opacity=0.85),
        text=texts,
        textposition="top right",
        textfont=dict(size=9, color="#e63946"),
        name="nodes",
        hoverinfo="text",
        hovertext=[f["properties"].get("node_id", "") for f in features],
    )


def build_edge_trace(features: list[dict]) -> go.Scattermap:
    """根据边 GeoJSON 构建线段 trace（带序号标注在中间）。"""
    lons, lats, mid_lons, mid_lats, mid_texts = [], [], [], [], []
    for idx, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        for lon, lat in coords:
            lons.append(lon)
            lats.append(lat)
        lons.append(None)
        lats.append(None)
        # 边标注取中间点
        mid = coords[len(coords) // 2]
        mid_lons.append(mid[0])
        mid_lats.append(mid[1])
        mid_texts.append(str(idx))
    # 先画线
    traces = [
        go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            line=dict(width=2.5, color="#1d3557"),
            name="edges",
            hoverinfo="none",
        ),
        go.Scattermap(
            lon=mid_lons,
            lat=mid_lats,
            mode="text",
            text=mid_texts,
            textposition="middle center",
            textfont=dict(size=8, color="#1d3557"),
            name="edge_labels",
            hoverinfo="none",
        ),
    ]
    return traces


def plot_road_network(
    nodes_path: Path,
    edges_path: Path,
) -> None:
    """使用 Plotly 交互式绘制路网节点和边。

    Args:
        nodes_path: nodes.geojson 路径。
        edges_path: edges.geojson 路径。
    """
    node_features = load_geojson(nodes_path)
    edge_features = load_geojson(edges_path)

    edge_traces = build_edge_trace(edge_features)
    if not isinstance(edge_traces, list):
        edge_traces = [edge_traces]
    node_trace = build_node_trace(node_features)

    fig = go.Figure(data=edge_traces + [node_trace])
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=31.22, lon=121.42),
            zoom=10,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        dragmode="pan",
    )
    fig.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="交互式绘制路网数据")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="数据目录（会查找其中的 nodes.geojson / edges.geojson，与 --nodes/--edges 互斥）",
    )
    parser.add_argument(
        "--nodes",
        type=Path,
        default=Path("data/shanghai/nodes.geojson"),
        help="节点 GeoJSON 文件路径（默认: data/shanghai/nodes.geojson）",
    )
    parser.add_argument(
        "--edges",
        type=Path,
        default=Path("data/shanghai/edges.geojson"),
        help="边 GeoJSON 文件路径（默认: data/shanghai/edges.geojson）",
    )
    args = parser.parse_args()

    # 如果提供了 --data-dir 但未覆盖 --nodes/--edges，则拼接默认文件名
    edges_path = args.edges
    nodes_path = args.nodes
    if args.data_dir and args.nodes == Path("data/shanghai/nodes.geojson"):
        nodes_path = args.data_dir / "nodes.geojson"
    if args.data_dir and args.edges == Path("data/shanghai/edges.geojson"):
        edges_path = args.data_dir / "edges.geojson"

    plot_road_network(nodes_path=nodes_path, edges_path=edges_path)


if __name__ == "__main__":
    main()
