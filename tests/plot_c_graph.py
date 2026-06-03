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

from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import (
    align_parallel_c_edges,
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_crossroad_nodes,
    cluster_near_parallel_edges,
    compute_crossroad_positions,
    connect_shared_nodes,
    create_virtual_cnodes,
    find_crossroad_nodes,
    identify_connection_nodes,
    split_c_edges_at_intersection_nodes,
    update_c_edge_endpoints,
    update_c_edges_for_crossroads,
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


def build_crossroad_trace(
    crossroad_nodes: dict[str, dict],
    crossroad_positions: dict[str, tuple],
    c_edges: list[dict]
) -> go.Scattermap:
    """Build trace for crossroad nodes with enlarged markers and numbers."""
    lons, lats, texts, hover_texts = [], [], [], []
    
    # Sort by node_id for deterministic numbering
    sorted_nodes = sorted(crossroad_nodes.items(), key=lambda x: x[0])
    
    for idx, (node_id, info) in enumerate(sorted_nodes):
        if node_id in crossroad_positions:
            coord = crossroad_positions[node_id]
            lons.append(coord[0])
            lats.append(coord[1])
            texts.append(str(idx))  # Sequential number label
            
            # Build hover text with C-edge details
            ce_details = []
            for ce_idx in info['c_edges']:
                ce = c_edges[ce_idx]
                ce_details.append(f"C{ce_idx}({ce['direction_deg']:.1f}°)")
            
            hover = f"Crossroad {idx}<br>"
            hover += f"C-edges: {', '.join(ce_details)}<br>"
            hover += f"Max angle diff: {info['max_angle_diff']:.1f}°"
            hover_texts.append(hover)
    
    return go.Scattermap(
        lon=lons,
        lat=lats,
        mode="markers+text",
        marker=dict(size=12, color="#e63946", symbol="circle"),
        text=texts,
        textposition="top center",
        textfont=dict(size=10, color="#000000"),
        name=f"Crossroads ({len(crossroad_nodes)})",
        hovertext=hover_texts,
        hoverinfo="text",
    )


def build_virtual_cnode_traces(virtual_cnodes: dict[int, dict]) -> go.Scattermap:
    """Build trace for virtual C-nodes with markers and labels."""
    lons, lats, texts, hover_texts = [], [], [], []
    
    for vnode_id, vnode in virtual_cnodes.items():
        coord = vnode['position']
        lons.append(coord[0])
        lats.append(coord[1])
        texts.append(vnode['id'])  # C-node_X label
        
        # Build hover text with connected C-edges
        connected_cedges = sorted(vnode['connected_cedges'])
        hover = f"{vnode['id']}<br>"
        hover += f"Connected C-edges: {', '.join(f'C{ce}' for ce in connected_cedges)}<br>"
        hover += f"Original nodes: {len(vnode['original_nodes'])}"
        hover_texts.append(hover)
    
    return go.Scattermap(
        lon=lons,
        lat=lats,
        mode="markers+text",
        marker=dict(size=10, color="#1d3557", symbol="square"),
        text=texts,
        textposition="top center",
        textfont=dict(size=9, color="#1d3557"),
        name=f"Virtual C-nodes ({len(virtual_cnodes)})",
        hovertext=hover_texts,
        hoverinfo="text",
    )


def build_c_edge_traces(c_edges: list[dict], node_coords: dict[str, tuple]) -> list[go.Scattermap]:
    traces: list[go.Scattermap] = []

    for ce in c_edges:
        # Skip original C-edges that have been split
        if ce.get('is_split', False):
            continue

        # Use parent_idx for color assignment (so split pieces have the same color)
        parent_idx = ce.get('parent_idx', ce['idx'])
        color = _CLUSTER_COLORS[parent_idx % len(_CLUSTER_COLORS)]
        start = ce["start_coord"]
        end = ce["end_coord"]

        lons = [start[0], end[0]]
        lats = [start[1], end[1]]

        # Build label with split information
        if 'split_idx' in ce:
            label = f"C-edge {parent_idx}-{ce['split_idx']} ({ce['size']} edges, dir={ce['direction_deg']:.1f}°)"
        else:
            label = f"C-edge {parent_idx} ({ce['size']} edges, dir={ce['direction_deg']:.1f}°)"

        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode="lines",
            line=dict(width=3, color=color),
            name=label,
            hoverinfo="name",
        ))

    # Add C-edge labels at midpoints (only for non-split C-edges)
    label_lons = []
    label_lats = []
    label_texts = []
    for ce in c_edges:
        if ce.get('is_split', False):
            continue
        label_lons.append((ce["start_coord"][0] + ce["end_coord"][0]) / 2)
        label_lats.append((ce["start_coord"][1] + ce["end_coord"][1]) / 2)
        if 'split_idx' in ce:
            label_texts.append(f"{ce.get('parent_idx', ce['idx'])}-{ce['split_idx']}")
        else:
            label_texts.append(str(ce.get('parent_idx', ce['idx'])))

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
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 15.0,
    overlap_ratio_threshold: float = 0.5,
    overlap_length_threshold_m: float = 120.0,
) -> None:
    edge_features = load_geojson(edges_path)

    if nodes_path is None:
        nodes_path = edges_path.parent / "nodes.geojson"
    node_features = load_geojson(nodes_path) if nodes_path.exists() else []

    print(f"Original: {len(edge_features)} edges, {len(node_features)} nodes")

    # Split edges at intersections
    print("Splitting edges at intersections...")
    edge_features, node_features, split_indices = split_edges_at_intersections(
        edge_features, node_features
    )
    print(f"After splitting: {len(edge_features)} edges, {len(node_features)} nodes")
    print(f"Split {len(split_indices)} edges")

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

    # Build node-to-C-edges mapping
    print("Building node-to-C-edges mapping...")
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)

    # Identify connection nodes
    print("Identifying connection nodes...")
    connection_nodes = identify_connection_nodes(node_to_cedges)
    print(f"Connection nodes: {len(connection_nodes)}")

    # Cluster connection nodes (2 stages)
    print("Clustering connection nodes...")
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    print(f"Connection node clusters: {len(clusters)}")

    # Create virtual C-nodes
    print("Creating virtual C-nodes...")
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features,
        near_threshold_m=near_threshold_m
    )
    print(f"Virtual C-nodes: {len(virtual_cnodes)}")

    # Update C-edge endpoints
    print("Updating C-edge endpoints...")
    update_c_edge_endpoints(c_edges, virtual_cnodes)

    # Split C-edges at intersection nodes
    print("Splitting C-edges at intersection nodes...")
    c_edges = split_c_edges_at_intersection_nodes(
        c_edges, virtual_cnodes, edge_clusters, edge_features,
        parallel_angle_threshold=parallel_angle_threshold
    )
    print(f"C-edges after splitting: {len(c_edges)}")

    print("Finding crossroad nodes...")
    crossroad_nodes = find_crossroad_nodes(
        c_edges, edge_clusters, edge_features,
        parallel_angle_threshold=parallel_angle_threshold
    )
    print(f"Crossroad nodes: {len(crossroad_nodes)}")

    # Compute intersection positions
    print("Computing crossroad positions...")
    crossroad_positions = compute_crossroad_positions(
        crossroad_nodes, c_edges, node_coords,
        parallel_angle_threshold=parallel_angle_threshold
    )

    # Cluster closely positioned crossroads
    print("Clustering crossroad nodes...")
    clustering_distance_m = overlap_length_threshold_m / 2
    crossroad_positions = cluster_crossroad_nodes(
        crossroad_positions, crossroad_nodes,
        clustering_distance_m=clustering_distance_m
    )

    # Connect shared non-crossroad nodes
    print("Connecting shared nodes...")
    connect_shared_nodes(c_edges, edge_clusters, edge_features,
                        node_coords, crossroad_nodes)

    # Align parallel C-edges
    print("Aligning parallel C-edges...")
    align_parallel_c_edges(c_edges, parallel_angle_threshold)

    # Update C-edges to reach intersection points
    print("Updating C-edges for crossroads...")
    update_c_edges_for_crossroads(c_edges, crossroad_positions)

    center = get_bounds_center(edge_features)

    traces = build_c_edge_traces(c_edges, node_coords)
    
    # Add virtual C-node traces
    if virtual_cnodes:
        traces.append(build_virtual_cnode_traces(virtual_cnodes))
    
    if crossroad_nodes:
        traces.append(build_crossroad_trace(crossroad_nodes, crossroad_positions, c_edges))

    title_text = f"C-edge graph: {len(c_edges)} C-edges, {len(virtual_cnodes)} virtual C-nodes, {len(crossroad_nodes)} crossroads"

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
    parser.add_argument("--near-threshold", type=float, default=50.0)
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