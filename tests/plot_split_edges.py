"""Visualize split vs non-split edges.

This script visualizes the result of edge splitting at intersections,
showing which edges were split (red, thicker) and which were not (gray, thinner).

Usage:
    cd src && uv run python tests/plot_split_edges.py --data-dir ../resource/miniquad
    cd src && uv run python tests/plot_split_edges.py --edges path/to/edges.geojson --nodes path/to/nodes.geojson
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import plotly.graph_objects as go

from mock.edge_splitter import split_edges_at_intersections


def load_geojson(path: Path) -> list[dict]:
    """Load GeoJSON file and return features list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def plot_split_edges(
    edges_path: Path,
    nodes_path: Path,
    output_path: Path | None = None,
) -> None:
    """Visualize split vs non-split edges.

    Args:
        edges_path: Path to edges GeoJSON file
        nodes_path: Path to nodes GeoJSON file
        output_path: Path to save HTML output (optional)
    """
    # Load data
    print(f"Loading edges from {edges_path}...")
    edge_features = load_geojson(edges_path)
    print(f"Loading nodes from {nodes_path}...")
    node_features = load_geojson(nodes_path)

    print(f"Original: {len(edge_features)} edges, {len(node_features)} nodes")

    # Split edges
    print("Splitting edges at intersections...")
    all_edges, all_nodes, split_indices = split_edges_at_intersections(
        edge_features, node_features
    )

    print(f"After splitting: {len(all_edges)} edges, {len(all_nodes)} nodes")
    print(f"Split edges: {len(split_indices)}")

    # Separate split and non-split edges by checking edge_id format
    # Original edges: {osm_id}_{seg_idx} (e.g., 1531258147-4959292617-0_1)
    # Split segments: {osm_id}_{seg_idx}_{split_idx} (e.g., 1531258147-4959292617-0_1_0)
    split_pattern = re.compile(r'_\d+_\d+$')

    non_split_edges = []
    split_edges = []

    for edge in all_edges:
        edge_id = edge['properties']['edge_id']
        if split_pattern.search(edge_id):
            split_edges.append(edge)
        else:
            non_split_edges.append(edge)

    print(f"Non-split edges: {len(non_split_edges)}")
    print(f"New edge segments: {len(split_edges)}")

    # Build traces (no legend for performance)
    traces = []

    # Non-split edges (thinner, gray)
    if non_split_edges:
        lons, lats = [], []
        for edge in non_split_edges:
            coords = edge['geometry']['coordinates']
            for coord in coords:
                lons.append(coord[0])
                lats.append(coord[1])
            lons.append(None)
            lats.append(None)

        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode='lines',
            line=dict(width=1.5, color='#666666'),
            opacity=0.5,
            showlegend=False,
            hoverinfo='skip',
        ))

    # Split edges (thicker, red)
    if split_edges:
        lons, lats = [], []
        for edge in split_edges:
            coords = edge['geometry']['coordinates']
            for coord in coords:
                lons.append(coord[0])
                lats.append(coord[1])
            lons.append(None)
            lats.append(None)

        traces.append(go.Scattermap(
            lon=lons,
            lat=lats,
            mode='lines',
            line=dict(width=3.0, color='#e63946'),
            showlegend=False,
            hoverinfo='skip',
        ))

    # Calculate bounds
    all_lons = [coord[0] for edge in all_edges for coord in edge['geometry']['coordinates']]
    all_lats = [coord[1] for edge in all_edges for coord in edge['geometry']['coordinates']]

    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)

    # Add 5% padding
    lon_padding = (max_lon - min_lon) * 0.05
    lat_padding = (max_lat - min_lat) * 0.05

    # Create figure
    fig = go.Figure(data=traces)

    fig.update_layout(
        title=f"Split edges: {len(split_indices)} split, {len(edge_features) - len(split_indices)} non-split",
        map=dict(
            style='carto-positron',
            bounds={
                'west': min_lon - lon_padding,
                'east': max_lon + lon_padding,
                'south': min_lat - lat_padding,
                'north': max_lat + lat_padding,
            },
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False,
    )

    # Save
    if output_path is None:
        output_path = edges_path.parent / 'split_edges.html'

    fig.write_html(output_path)
    print(f"Saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Visualize split vs non-split edges")
    parser.add_argument('--data-dir', type=Path, help='Data directory containing edges.geojson and nodes.geojson')
    parser.add_argument('--edges', type=Path, help='Path to edges GeoJSON file')
    parser.add_argument('--nodes', type=Path, help='Path to nodes GeoJSON file')
    parser.add_argument('--output', type=Path, help='Path to save HTML output')

    args = parser.parse_args()

    if args.data_dir:
        edges_path = args.data_dir / 'edges.geojson'
        nodes_path = args.data_dir / 'nodes.geojson'
    elif args.edges and args.nodes:
        edges_path = args.edges
        nodes_path = args.nodes
    else:
        parser.error("Either --data-dir or both --edges and --nodes must be provided")

    plot_split_edges(edges_path, nodes_path, args.output)


if __name__ == '__main__':
    main()
