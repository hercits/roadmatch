"""Visualize the C-edge graph (clustered road edges).

C-edges are shown as representative lines following road geometry.
Endpoint nodes are shown as markers.

Usage:
    cd src && uv run python tests/plot_c_graph.py --data-dir ../resource/miniquad

Dependencies: plotly>=6.7.0, shapely
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.graph_simplifier import (
    build_c_edge_graph,
    cluster_near_parallel_edges,
)
from utils.geometry import get_bounds_center


def load_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def build_node_coords(node_features: list[dict]) -> dict[str, tuple]:
    coords: dict[str, tuple] = {}
    for f in node_features:
        nid = f["properties"]["node_id"]
        c = f["geometry"]["coordinates"]
        coords[nid] = (c[0], c[1])
    return coords


_CLUSTER_COLORS = [
    "#e63946", "#1d3557", "#457b9d", "#f4a261", "#2a9d8f",
    "#e9c46a", "#264653", "#a8dadc", "#6d6875", "#b5838d",
    "#ffb4a2", "#e5989b", "#9b2226", "#ae2012", "#bb3e03",
    "#ca6702", "#ee9b00", "#94d2bd", "#0a9396", "#005f73",
    "#3d5a80", "#98c1d9", "#544b61", "#7b2d8e", "#f72585",
    "#7209b7", "#3a0ca3", "#4361ee", "#4cc9f0", "#06d6a0",
    "#118ab2", "#073b4c", "#ffd166", "#ef476f", "#26547c",
]


def build_c_edge_traces(c_edges: list[dict], node_coords: dict[str, tuple]) -> list[go.Scattermap]:
    traces: list[go.Scattermap] = []

    for ce in c_edges:
        idx = ce["idx"]
        color = _CLUSTER_COLORS[idx % len(_CLUSTER_COLORS)]
        start = ce["start_coord"]
        end = ce["end_coord"]

        lons = [start[0], end[0]]
        lats = [start[1], end[1]]

        label = f"C-edge {idx} ({ce['size']} edges, dir={ce['direction_deg']:.1f}°)"

        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            line=dict(width=3, color=color),
            name=label,
            hoverinfo="name",
        ))

    # Add C-edge labels at midpoints
    label_lons = [(ce["start_coord"][0] + ce["end_coord"][0]) / 2 for ce in c_edges]
    label_lats = [(ce["start_coord"][1] + ce["end_coord"][1]) / 2 for ce in c_edges]
    label_texts = [str(ce["idx"]) for ce in c_edges]

    traces.append(go.Scattermap(
        lon=label_lons,
        lat=label_lats,
        mode="text",
        text=label_texts,
        textposition="middle center",
        textfont=dict(size=11, color="#222222"),
        name="C-edge labels",
        hoverinfo="skip",
        showlegend=False,
    ))

    # Collect all endpoint nodes
    endpoint_nodes = set()
    for ce in c_edges:
        if ce["start_node_id"]:
            endpoint_nodes.add(ce["start_node_id"])
        if ce["end_node_id"]:
            endpoint_nodes.add(ce["end_node_id"])

    # Add endpoint node markers
    if endpoint_nodes:
        node_lons = []
        node_lats = []
        node_texts = []
        for nid in endpoint_nodes:
            if nid in node_coords:
                coord = node_coords[nid]
                node_lons.append(coord[0])
                node_lats.append(coord[1])
                # Use short node ID (last part after underscore)
                short_id = nid.split("_")[-1] if "_" in nid else nid
                node_texts.append(short_id)

        if node_lons:
            traces.append(go.Scattermap(
                lon=node_lons,
                lat=node_lats,
                mode="markers",
                marker=dict(size=6, color="#333333"),
                name=f"Endpoint nodes ({len(endpoint_nodes)})",
                hoverinfo="name",
            ))

    return traces


def plot_c_edge_graph(
    edges_path: Path,
    nodes_path: Path | None = None,
    output_path: Path | None = None,
    near_threshold_m: float = 30.0,
    parallel_angle_threshold: float = 15.0,
    overlap_ratio_threshold: float = 0.5,
    overlap_length_threshold_m: float = 120.0,
) -> None:
    edge_features = load_geojson(edges_path)

    if nodes_path is None:
        nodes_path = edges_path.parent / "nodes.geojson"
    node_features = load_geojson(nodes_path) if nodes_path.exists() else []

    print(f"Clustering {len(edge_features)} edges...")
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features,
        near_threshold_m=near_threshold_m,
        parallel_angle_threshold=parallel_angle_threshold,
        overlap_ratio_threshold=overlap_ratio_threshold,
        overlap_length_threshold_m=overlap_length_threshold_m,
    )

    node_coords = build_node_coords(node_features)

    print(f"Building C-edge graph...")
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords,
        core_edges_per_cluster=core_edges,
        near_threshold_m=near_threshold_m,
    )

    print(f"C-edges: {len(c_edges)}")

    center = get_bounds_center(edge_features)

    traces = build_c_edge_traces(c_edges, node_coords)

    title_text = f"C-edge graph: {len(c_edges)} C-edges"

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=center[1], lon=center[0]),
            zoom=13,
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        title=dict(text=title_text, x=0.5),
        showlegend=True,
        legend=dict(
            y=0.99,
            x=0.01,
            font=dict(size=10),
        ),
        dragmode="pan",
    )

    if output_path is None:
        output_path = edges_path.parent / "c_edge_graph.html"

    fig.write_html(str(output_path))
    print(f"Saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize C-edge graph (clustered road edges)")
    parser.add_argument("--data-dir", type=Path, default=Path("resource/miniquad"))
    parser.add_argument("--edges", type=Path, default=None)
    parser.add_argument("--nodes", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--near-threshold", type=float, default=30.0)
    parser.add_argument("--parallel-angle-threshold", type=float, default=15.0)
    parser.add_argument("--overlap-ratio", type=float, default=0.5)
    parser.add_argument("--overlap-length", type=float, default=120.0)
    args = parser.parse_args()

    edges_path = args.edges or args.data_dir / "edges.geojson"
    nodes_path = args.nodes or args.data_dir / "nodes.geojson"

    plot_c_edge_graph(
        edges_path=edges_path,
        nodes_path=nodes_path,
        output_path=args.output,
        near_threshold_m=args.near_threshold,
        parallel_angle_threshold=args.parallel_angle_threshold,
        overlap_ratio_threshold=args.overlap_ratio,
        overlap_length_threshold_m=args.overlap_length,
    )


if __name__ == "__main__":
    main()