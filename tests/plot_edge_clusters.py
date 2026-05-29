"""Visualize road network edge and node clustering results.

Usage:
    cd src && uv run python tests/plot_edge_clusters.py --data-dir ../resource/miniquad

Dependencies (in pyproject.toml):
    plotly>=6.7.0, shapely
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import plotly.graph_objects as go
from shapely.geometry import MultiPoint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.graph_simplifier import cluster_near_parallel_edges, cluster_nodes
from utils.geometry import (
    circular_span_mod180,
    get_bounds_center,
    meters_to_degrees_lon,
)


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


def cluster_color(cluster_idx: int, is_singleton: bool) -> str:
    if is_singleton:
        return "#cccccc"
    return _CLUSTER_COLORS[cluster_idx % len(_CLUSTER_COLORS)]


def detect_outlier_clusters(
    edge_features: list[dict],
    clusters: list[list[int]],
    core_edges_per_cluster: dict[int, set],
    angle_threshold: float = 30.0,
) -> list[tuple[int, float]]:
    outliers: list[tuple[int, float]] = []
    for i, c in enumerate(clusters):
        if len(c) < 2:
            continue
        core = core_edges_per_cluster.get(i, set(c))
        if len(core) < 2:
            continue
        core_dirs = [edge_features[idx]["properties"]["direction_deg"] for idx in core]
        span = circular_span_mod180(core_dirs)
        if span > angle_threshold:
            outliers.append((i, span))
    return outliers


def build_edge_cluster_traces(
    edge_features: list[dict],
    clusters: list[list[int]],
    outlier_indices: set[int],
) -> list[go.Scattermap]:
    traces: list[go.Scattermap] = []

    for cluster_idx, cluster in [(i, c) for i, c in enumerate(clusters) if len(c) > 1]:
        is_outlier = cluster_idx in outlier_indices
        color = "#ff4500" if is_outlier else cluster_color(cluster_idx, False)
        width = 5 if is_outlier else 3
        lons, lats = [], []
        for edge_idx in cluster:
            coords = edge_features[edge_idx]["geometry"]["coordinates"]
            for lon, lat in coords:
                lons.append(lon)
                lats.append(lat)
            lons.append(None)
            lats.append(None)

        label = f"edge cluster {cluster_idx} ({len(cluster)} edges)"
        if is_outlier:
            label += " [OUTLIER]"
        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            line=dict(width=width, color=color),
            name=label,
            hoverinfo="name",
        ))

    singleton_indices = [c[0] for i, c in enumerate(clusters) if len(c) == 1]
    if singleton_indices:
        lons, lats = [], []
        for edge_idx in singleton_indices:
            coords = edge_features[edge_idx]["geometry"]["coordinates"]
            for lon, lat in coords:
                lons.append(lon)
                lats.append(lat)
            lons.append(None)
            lats.append(None)
        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            line=dict(width=1.5, color="#cccccc"),
            name=f"singleton edges ({len(singleton_indices)})",
            hoverinfo="name",
        ))

    return traces


def build_node_cluster_traces(
    node_clusters: list[set],
    node_coords: dict[str, tuple],
    center_lat: float,
    popout_radius_m: float = 10.0,
) -> list[go.Scattermap]:
    traces: list[go.Scattermap] = []
    buffer_deg = meters_to_degrees_lon(popout_radius_m, center_lat)

    multi_clusters = [(i, c) for i, c in enumerate(node_clusters) if len(c) > 1]
    singleton_clusters = [(i, c) for i, c in enumerate(node_clusters) if len(c) == 1]

    for nc_idx, nc in multi_clusters:
        positions = [node_coords[nid] for nid in nc if nid in node_coords]
        if len(positions) < 2:
            continue

        hull = MultiPoint(positions).convex_hull
        popped = hull.buffer(buffer_deg)

        if popped.geom_type == "Polygon":
            exterior = list(popped.exterior.coords)
        elif popped.geom_type == "LineString":
            exterior = list(popped.coords)
        else:
            continue

        lons = [c[0] for c in exterior] + [exterior[0][0]]
        lats = [c[1] for c in exterior] + [exterior[0][1]]

        base_color = _CLUSTER_COLORS[nc_idx % len(_CLUSTER_COLORS)]
        color_rgb = base_color.lstrip("#")
        r, g, b = int(color_rgb[:2], 16), int(color_rgb[2:4], 16), int(color_rgb[4:6], 16)
        fill_color = f"rgba({r},{g},{b},0.3)"
        line_color = f"rgba({r},{g},{b},0.6)"

        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            fill="toself",
            fillcolor=fill_color,
            line=dict(width=1.5, color=line_color),
            name=f"node cluster {nc_idx} ({len(nc)} nodes)",
            hoverinfo="name",
        ))

    if singleton_clusters:
        lons, lats = [], []
        for nc_idx, nc in singleton_clusters:
            for nid in nc:
                if nid in node_coords:
                    pos = node_coords[nid]
                    pt = MultiPoint([pos]).convex_hull
                    popped = pt.buffer(buffer_deg)
                    if popped.geom_type == "Polygon":
                        exterior = list(popped.exterior.coords)
                        for c in exterior:
                            lons.append(c[0])
                            lats.append(c[1])
                        lons.append(None)
                        lats.append(None)

        if lons:
            traces.append(go.Scattermap(
                lon=lons,
                lat=lats,
                mode="lines",
                fill="toself",
                fillcolor="rgba(128,128,128,0.3)",
                line=dict(width=1, color="rgba(128,128,128,0.5)"),
                name=f"singleton nodes ({len(singleton_clusters)})",
                hoverinfo="name",
            ))

    return traces


def plot_edge_clusters(
    edges_path: Path,
    nodes_path: Path | None = None,
    output_path: Path | None = None,
    near_threshold_m: float = 30.0,
    parallel_angle_threshold: float = 15.0,
    overlap_ratio_threshold: float = 0.5,
    overlap_length_threshold_m: float = 120.0,
    node_near_threshold_m: float = 30.0,
    popout_radius_m: float = 10.0,
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

    total_edges = len(edge_features)
    total_ec = len(edge_clusters)
    non_singleton_ec = sum(1 for c in edge_clusters if len(c) > 1)
    singleton_ec = sum(1 for c in edge_clusters if len(c) == 1)
    largest_ec = max(len(c) for c in edge_clusters)

    print(f"Total edges: {total_edges}")
    print(f"Edge clusters: {total_ec} ({non_singleton_ec} multi, {singleton_ec} single), max {largest_ec}")

    outliers = detect_outlier_clusters(edge_features, edge_clusters, core_edges, angle_threshold=30.0)
    outlier_indices = {idx for idx, _ in outliers}
    print(f"Outlier edge clusters: {len(outliers)}")

    traces = build_edge_cluster_traces(edge_features, edge_clusters, outlier_indices)

    node_clusters: list[set] = []
    if node_features:
        print(f"Clustering nodes with near_threshold={node_near_threshold_m}m...")
        node_clusters = cluster_nodes(
            edge_clusters,
            edge_features,
            node_features,
            core_edges_per_cluster=core_edges,
            near_threshold_m=node_near_threshold_m,
        )
        total_nc = len(node_clusters)
        multi_nc = sum(1 for c in node_clusters if len(c) > 1)
        singleton_nc = sum(1 for c in node_clusters if len(c) == 1)
        largest_nc = max(len(c) for c in node_clusters) if node_clusters else 0
        print(f"Node clusters: {total_nc} ({multi_nc} multi, {singleton_nc} single), max {largest_nc}")

        center = get_bounds_center(edge_features)
        node_traces = build_node_cluster_traces(
            node_clusters,
            build_node_coords(node_features),
            center[1],
            popout_radius_m=popout_radius_m,
        )
        traces.extend(node_traces)
    else:
        center = get_bounds_center(edge_features)

    title_text = (
        f"Edges: {total_edges} → {total_ec} clusters "
        f"({non_singleton_ec} multi, {singleton_ec} single)"
    )
    if node_clusters:
        title_text += (
            f" | Nodes: {len(node_clusters)} clusters "
            f"({multi_nc} multi, {singleton_nc} single)"
        )

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
        output_path = edges_path.parent / "edge_clusters.html"

    fig.write_html(str(output_path))
    print(f"Saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize road network edge & node clustering")
    parser.add_argument("--data-dir", type=Path, default=Path("resource/miniquad"))
    parser.add_argument("--edges", type=Path, default=None)
    parser.add_argument("--nodes", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--near-threshold", type=float, default=30.0)
    parser.add_argument("--parallel-angle-threshold", type=float, default=15.0)
    parser.add_argument("--overlap-ratio", type=float, default=0.5)
    parser.add_argument("--overlap-length", type=float, default=120.0)
    parser.add_argument("--node-near-threshold", type=float, default=30.0)
    parser.add_argument("--popout-radius", type=float, default=10.0)
    args = parser.parse_args()

    edges_path = args.edges or args.data_dir / "edges.geojson"
    nodes_path = args.nodes or args.data_dir / "nodes.geojson"

    plot_edge_clusters(
        edges_path=edges_path,
        nodes_path=nodes_path,
        output_path=args.output,
        near_threshold_m=args.near_threshold,
        parallel_angle_threshold=args.parallel_angle_threshold,
        overlap_ratio_threshold=args.overlap_ratio,
        overlap_length_threshold_m=args.overlap_length,
        node_near_threshold_m=args.node_near_threshold,
        popout_radius_m=args.popout_radius,
    )


if __name__ == "__main__":
    main()