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
import math
import pickle
import sys
import time
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import (
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_near_parallel_edges,
    cluster_parallelograms,
    create_virtual_cnodes,
    filter_spur_core_edges,
    find_parallelograms_near_cnodes,
    identify_connection_nodes,
    merge_intersection_cnodes,
    merge_t_junction_cnodes,
    recompute_c_edge_geometry,
    split_c_edges_at_intersection_nodes,
    update_c_edge_endpoints,
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
        hovertext=hover_texts,
        hoverinfo="text",
        showlegend=False,
    )


def build_c_edge_traces(c_edges: list[dict], node_coords: dict[str, tuple]) -> list[go.Scattermap]:
    """Build optimized traces for C-edges.
    
    Optimizations:
    1. Batch rendering: merge all C-edges into single trace with None separators
    2. Unified color: use single color instead of cycling
    3. No legend: disable legend for C-edges
    4. Midpoint hover: add invisible hover markers at edge midpoints
    """
    traces: list[go.Scattermap] = []

    # Batch render all C-edges in a single trace
    all_lons = []
    all_lats = []
    hover_lons = []
    hover_lats = []
    hover_texts = []
    label_texts = []

    for ce in c_edges:
        # Skip original C-edges that have been split
        if ce.get('is_split', False):
            continue

        start = ce["start_coord"]
        end = ce["end_coord"]

        # Add line segment with None separator
        all_lons.extend([start[0], end[0], None])
        all_lats.extend([start[1], end[1], None])

        # Build hover text
        parent_idx = ce.get('parent_idx', ce['idx'])
        if 'split_idx' in ce:
            label = f"C-edge {parent_idx}-{ce['split_idx']}"
        else:
            label = f"C-edge {parent_idx}"
        
        hover_text = f"{label}<br>Size: {ce['size']} edges<br>Direction: {ce['direction_deg']:.1f}°"
        
        # Add midpoint for hover and label
        mid_lon = (start[0] + end[0]) / 2
        mid_lat = (start[1] + end[1]) / 2
        hover_lons.append(mid_lon)
        hover_lats.append(mid_lat)
        hover_texts.append(hover_text)
        
        # Add edge index label
        if 'split_idx' in ce:
            label_texts.append(f"{parent_idx}-{ce['split_idx']}")
        else:
            label_texts.append(str(parent_idx))

    # Single trace for all C-edges
    traces.append(go.Scattermap(
        lon=all_lons,
        lat=all_lats,
        mode="lines",
        line=dict(width=2, color="#1f77b4"),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Midpoint labels with hover
    if hover_lons:
        traces.append(go.Scattermap(
            lon=hover_lons,
            lat=hover_lats,
            mode="markers+text",
            marker=dict(size=1, opacity=0),
            text=label_texts,
            textposition="middle center",
            textfont=dict(size=10, color="#222222"),
            hovertext=hover_texts,
            hoverinfo="text",
            showlegend=False,
        ))

    # Collect all endpoint nodes
    endpoint_nodes = set()
    for ce in c_edges:
        if ce["start_node_id"]:
            endpoint_nodes.add(ce["start_node_id"])
        if ce["end_node_id"]:
            endpoint_nodes.add(ce["end_node_id"])

    # Add endpoint node markers (batch rendered)
    if endpoint_nodes:
        node_lons = []
        node_lats = []
        for nid in endpoint_nodes:
            if nid in node_coords:
                coord = node_coords[nid]
                node_lons.append(coord[0])
                node_lats.append(coord[1])

        if node_lons:
            traces.append(go.Scattermap(
                lon=node_lons,
                lat=node_lats,
                mode="markers",
                marker=dict(size=4, color="#333333"),
                showlegend=False,
                hoverinfo="skip",
            ))

    return traces


def build_reference_grid_traces(
    edge_features: list[dict],
    grid_spacing_deg: float = 0.01,
) -> list[go.Scattermap]:
    """Build latitude/longitude reference grid lines.
    
    Args:
        edge_features: List of edge GeoJSON features to determine bounds.
        grid_spacing_deg: Grid spacing in degrees (default 0.01 = ~1km).
    
    Returns:
        List of Scattermap traces for grid lines and labels.
    """
    # Get bounds from edge features
    min_lon = min(f['geometry']['coordinates'][0][0] for f in edge_features)
    max_lon = max(f['geometry']['coordinates'][-1][0] for f in edge_features)
    min_lat = min(f['geometry']['coordinates'][0][1] for f in edge_features)
    max_lat = max(f['geometry']['coordinates'][-1][1] for f in edge_features)
    
    # Extend bounds slightly
    margin = grid_spacing_deg * 0.5
    min_lon -= margin
    max_lon += margin
    min_lat -= margin
    max_lat += margin
    
    traces: list[go.Scattermap] = []
    
    # Generate latitude lines (horizontal)
    lat_start = (min_lat // grid_spacing_deg) * grid_spacing_deg
    lat_end = ((max_lat // grid_spacing_deg) + 1) * grid_spacing_deg
    
    lat_lons = []
    lat_lats = []
    lat_texts = []
    
    lat = lat_start
    while lat <= lat_end:
        lat_lons.extend([min_lon, max_lon, None])
        lat_lats.extend([lat, lat, None])
        lat_texts.extend([f"{lat:.3f}°", "", None])
        lat += grid_spacing_deg
    
    if lat_lons:
        traces.append(go.Scattermap(
            lon=lat_lons,
            lat=lat_lats,
            mode="lines",
            line=dict(width=1, color="rgba(100, 100, 100, 0.3)"),
            showlegend=False,
            hoverinfo="skip",
        ))
    
    # Generate longitude lines (vertical)
    lon_start = (min_lon // grid_spacing_deg) * grid_spacing_deg
    lon_end = ((max_lon // grid_spacing_deg) + 1) * grid_spacing_deg
    
    lon_lons = []
    lon_lats = []
    
    lon = lon_start
    while lon <= lon_end:
        lon_lons.extend([lon, lon, None])
        lon_lats.extend([min_lat, max_lat, None])
        lon += grid_spacing_deg
    
    if lon_lons:
        traces.append(go.Scattermap(
            lon=lon_lons,
            lat=lon_lats,
            mode="lines",
            line=dict(width=1, color="rgba(100, 100, 100, 0.3)"),
            showlegend=False,
            hoverinfo="skip",
        ))
    
    # Add labels at grid intersections
    label_lons = []
    label_lats = []
    label_texts = []
    
    lat = lat_start
    while lat <= lat_end:
        lon = lon_start
        while lon <= lon_end:
            label_lons.append(lon)
            label_lats.append(lat)
            label_texts.append(f"{lat:.3f}°, {lon:.3f}°")
            lon += grid_spacing_deg
        lat += grid_spacing_deg
    
    if label_lons:
        traces.append(go.Scattermap(
            lon=label_lons,
            lat=label_lats,
            mode="markers+text",
            marker=dict(size=1, color="rgba(100, 100, 100, 0.5)"),
            text=label_texts,
            textposition="top right",
            textfont=dict(size=8, color="rgba(100, 100, 100, 0.7)"),
            showlegend=False,
            hoverinfo="skip",
        ))
    
    return traces


def find_parallelograms_sliding_window(
    c_edges: list[dict],
    virtual_cnodes: dict[int, dict],
    edge_features: list[dict],
    window_size_m: int = 500,
    step_m: int = 250,
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 15.0,
) -> list[dict]:
    """Find parallelograms using a sliding window approach.
    
    Args:
        c_edges: List of C-edge dicts.
        virtual_cnodes: Dict of C-node dicts.
        edge_features: List of edge GeoJSON features.
        window_size_m: Window size in meters (default 500).
        step_m: Step size in meters (default 250).
        near_threshold_m: Distance threshold for C-node clustering.
        parallel_angle_threshold: Angle threshold for parallel detection.
    
    Returns:
        List of parallelogram dicts (deduplicated).
    """
    from utils.geometry import haversine_m
    
    # Calculate bounds from edge_features
    min_lon = float('inf')
    max_lon = float('-inf')
    min_lat = float('inf')
    max_lat = float('-inf')
    
    for ef in edge_features:
        coords = ef['geometry']['coordinates']
        for c in coords:
            min_lon = min(min_lon, c[0])
            max_lon = max(max_lon, c[0])
            min_lat = min(min_lat, c[1])
            max_lat = max(max_lat, c[1])
    
    # Convert meters to degrees (approximate)
    avg_lat = (min_lat + max_lat) / 2
    lat_per_m = 1.0 / 111000.0
    lon_per_m = 1.0 / (111000.0 * math.cos(math.radians(avg_lat)))
    
    window_size_lon = window_size_m * lon_per_m
    window_size_lat = window_size_m * lat_per_m
    step_lon = step_m * lon_per_m
    step_lat = step_m * lat_per_m
    
    # Generate window grid
    all_parallelograms = []
    seen_edge_sets = set()
    
    window_count = 0
    lat = min_lat
    while lat < max_lat:
        lon = min_lon
        while lon < max_lon:
            window_count += 1
            
            # Window bounds
            win_min_lon = lon
            win_max_lon = lon + window_size_lon
            win_min_lat = lat
            win_max_lat = lat + window_size_lat
            
            # Filter edge_features: midpoint in window
            local_edges = []
            for idx, ef in enumerate(edge_features):
                coords = ef['geometry']['coordinates']
                mid_lon = (coords[0][0] + coords[-1][0]) / 2
                mid_lat = (coords[0][1] + coords[-1][1]) / 2
                if win_min_lon <= mid_lon <= win_max_lon and win_min_lat <= mid_lat <= win_max_lat:
                    local_edges.append((idx, ef))
            
            # Filter virtual_cnodes: position in window
            local_cnodes = {}
            for cn_id, vnode in virtual_cnodes.items():
                pos = vnode['position']
                if win_min_lon <= pos[0] <= win_max_lon and win_min_lat <= pos[1] <= win_max_lat:
                    local_cnodes[cn_id] = vnode
            
            # Skip if no data in window
            if not local_edges or not local_cnodes:
                lon += step_lon
                continue
            
            # Run parallelogram detection
            local_parallelograms = find_parallelograms_near_cnodes(
                c_edges, local_cnodes, edge_features,
                near_threshold_m=near_threshold_m,
                parallel_angle_threshold=parallel_angle_threshold,
            )
            
            # Deduplicate
            for pg in local_parallelograms:
                edge_set = frozenset(pg['edges'])
                if edge_set not in seen_edge_sets:
                    seen_edge_sets.add(edge_set)
                    all_parallelograms.append(pg)
            
            lon += step_lon
        lat += step_lat
    
    print(f"Sliding window: {window_count} windows, {len(all_parallelograms)} parallelograms")
    return all_parallelograms


PIPELINE_STEPS = [
    "crop",
    "split",
    "cluster",
    "c_edges",
    "node_to_cedges",
    "filter_spur",
    "connection_nodes",
    "cluster_connection",
    "virtual_cnodes",
    "merge_t_junction",
    "parallelograms",
    "crossroads",
    "merge_intersection",
    "update_endpoints",
    "split_c_edges",
]


def _cache_load(cache_dir: Path, step_name: str):
    path = cache_dir / f"{step_name}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            data = pickle.load(f)
        print(f"  [cache hit] {step_name}")
        return data
    return None


def _cache_save(cache_dir: Path, step_name: str, data) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{step_name}.pkl"
    with open(path, "wb") as f:
        pickle.dump(data, f)


def _should_compute(step_name: str, resume_from: str | None) -> bool:
    if resume_from is None:
        return False
    resume_idx = PIPELINE_STEPS.index(resume_from)
    step_idx = PIPELINE_STEPS.index(step_name)
    return step_idx >= resume_idx


def plot_c_edge_graph(
    edges_path: Path,
    nodes_path: Path | None = None,
    output_path: Path | None = None,
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 15.0,
    overlap_ratio_threshold: float = 0.5,
    overlap_length_threshold_m: float = 120.0,
    sliding_window: tuple[int, int] | None = None,
    crop_ratio: float | None = None,
    cache_dir: Path | None = None,
    resume_from: str | None = None,
) -> None:
    edge_features = load_geojson(edges_path)

    if nodes_path is None:
        nodes_path = edges_path.parent / "nodes.geojson"
    node_features = load_geojson(nodes_path) if nodes_path.exists() else []

    print(f"Original: {len(edge_features)} edges, {len(node_features)} nodes")

    # Step: crop
    cached = _cache_load(cache_dir, "crop") if cache_dir and crop_ratio is not None and not _should_compute("crop", resume_from) else None
    if cached:
        edge_features, node_features = cached
    elif crop_ratio is not None:
        lons, lats = [], []
        for ef in edge_features:
            for c in ef['geometry']['coordinates']:
                lons.append(c[0])
                lats.append(c[1])
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        center_lon = (min_lon + max_lon) / 2
        center_lat = (min_lat + max_lat) / 2
        half_width = (max_lon - min_lon) * crop_ratio / 2
        half_height = (max_lat - min_lat) * crop_ratio / 2
        crop_min_lon = center_lon - half_width
        crop_max_lon = center_lon + half_width
        crop_min_lat = center_lat - half_height
        crop_max_lat = center_lat + half_height

        filtered_edges = []
        for ef in edge_features:
            coords = ef['geometry']['coordinates']
            mid_lon = (coords[0][0] + coords[-1][0]) / 2
            mid_lat = (coords[0][1] + coords[-1][1]) / 2
            if crop_min_lon <= mid_lon <= crop_max_lon and crop_min_lat <= mid_lat <= crop_max_lat:
                filtered_edges.append(ef)
        edge_features = filtered_edges

        referenced_node_ids = set()
        for ef in edge_features:
            referenced_node_ids.add(ef['properties']['u'])
            referenced_node_ids.add(ef['properties']['v'])

        filtered_nodes = []
        for nf in node_features:
            node_id = nf['properties']['node_id']
            coords = nf['geometry']['coordinates']
            in_crop = crop_min_lon <= coords[0] <= crop_max_lon and crop_min_lat <= coords[1] <= crop_max_lat
            if in_crop or node_id in referenced_node_ids:
                filtered_nodes.append(nf)
        node_features = filtered_nodes

        print(f"After crop ({crop_ratio:.0%}): {len(edge_features)} edges, {len(node_features)} nodes")
        if cache_dir:
            _cache_save(cache_dir, "crop", (edge_features, node_features))

    t_total = time.time()

    # Step: split
    cached = _cache_load(cache_dir, "split") if cache_dir and not _should_compute("split", resume_from) else None
    if cached:
        edge_features, node_features = cached
    else:
        t0 = time.time()
        print("Splitting edges at intersections...")
        edge_features, node_features, split_indices = split_edges_at_intersections(
            edge_features, node_features
        )
        print(f"After splitting: {len(edge_features)} edges, {len(node_features)} nodes")
        print(f"Split {len(split_indices)} edges [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "split", (edge_features, node_features))

    # Step: cluster
    cached = _cache_load(cache_dir, "cluster") if cache_dir and not _should_compute("cluster", resume_from) else None
    if cached:
        edge_clusters, core_edges = cached
    else:
        t0 = time.time()
        print(f"Clustering {len(edge_features)} edges...")
        edge_clusters, core_edges = cluster_near_parallel_edges(
            edge_features,
            near_threshold_m=near_threshold_m,
            parallel_angle_threshold=parallel_angle_threshold,
            overlap_ratio_threshold=overlap_ratio_threshold,
            overlap_length_threshold_m=overlap_length_threshold_m,
        )
        print(f"Clusters: {len(edge_clusters)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "cluster", (edge_clusters, core_edges))

    node_coords = build_node_coords(node_features)

    # Step: c_edges
    cached = _cache_load(cache_dir, "c_edges") if cache_dir and not _should_compute("c_edges", resume_from) else None
    if cached:
        c_edges = cached
    else:
        t0 = time.time()
        print(f"Building C-edge graph...")
        c_edges = build_c_edge_graph(
            edge_clusters, edge_features, node_coords,
            core_edges_per_cluster=core_edges,
            near_threshold_m=near_threshold_m,
        )
        print(f"C-edges: {len(c_edges)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "c_edges", c_edges)

    # Step: node_to_cedges
    cached = _cache_load(cache_dir, "node_to_cedges") if cache_dir and not _should_compute("node_to_cedges", resume_from) else None
    if cached:
        node_to_cedges = cached
    else:
        t0 = time.time()
        print("Building node-to-C-edges mapping...")
        node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
        print(f"  [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "node_to_cedges", node_to_cedges)

    # Step: filter_spur
    cached = _cache_load(cache_dir, "filter_spur") if cache_dir and not _should_compute("filter_spur", resume_from) else None
    if cached:
        core_edges, c_edges = cached
    else:
        t0 = time.time()
        print("Filtering spur core edges...")
        core_edges = filter_spur_core_edges(core_edges, edge_clusters, edge_features)
        recompute_c_edge_geometry(c_edges, core_edges, edge_clusters, edge_features, node_coords, near_threshold_m)
        print(f"  [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "filter_spur", (core_edges, c_edges))

    # Step: connection_nodes
    cached = _cache_load(cache_dir, "connection_nodes") if cache_dir and not _should_compute("connection_nodes", resume_from) else None
    if cached:
        connection_nodes = cached
    else:
        t0 = time.time()
        print("Identifying connection nodes...")
        connection_nodes = identify_connection_nodes(node_to_cedges)
        print(f"Connection nodes: {len(connection_nodes)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "connection_nodes", connection_nodes)

    # Step: cluster_connection
    cached = _cache_load(cache_dir, "cluster_connection") if cache_dir and not _should_compute("cluster_connection", resume_from) else None
    if cached:
        clusters = cached
    else:
        t0 = time.time()
        print("Clustering connection nodes...")
        clusters = cluster_connection_nodes(connection_nodes, node_coords)
        print(f"Connection node clusters: {len(clusters)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "cluster_connection", clusters)

    # Step: virtual_cnodes
    cached = _cache_load(cache_dir, "virtual_cnodes") if cache_dir and not _should_compute("virtual_cnodes", resume_from) else None
    if cached:
        virtual_cnodes = cached
    else:
        t0 = time.time()
        print("Creating virtual C-nodes...")
        virtual_cnodes = create_virtual_cnodes(
            clusters, connection_nodes, c_edges, node_coords,
            edge_clusters, core_edges, edge_features,
            near_threshold_m=near_threshold_m,
            parallel_angle_threshold=parallel_angle_threshold
        )
        print(f"Virtual C-nodes: {len(virtual_cnodes)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "virtual_cnodes", virtual_cnodes)

    # Step: merge_t_junction
    cached = _cache_load(cache_dir, "merge_t_junction") if cache_dir and not _should_compute("merge_t_junction", resume_from) else None
    if cached:
        virtual_cnodes = cached
    else:
        t0 = time.time()
        print("Merging T-junction C-nodes...")
        virtual_cnodes = merge_t_junction_cnodes(virtual_cnodes, near_threshold_m=near_threshold_m)
        print(f"Virtual C-nodes after merge: {len(virtual_cnodes)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "merge_t_junction", virtual_cnodes)

    # Step: parallelograms
    cached = _cache_load(cache_dir, "parallelograms") if cache_dir and not _should_compute("parallelograms", resume_from) else None
    if cached:
        parallelograms = cached
    else:
        t0 = time.time()
        if sliding_window:
            window_size_m, step_m = sliding_window
            print(f"Finding parallelograms with sliding window ({window_size_m}m, step {step_m}m)...")
            parallelograms = find_parallelograms_sliding_window(
                c_edges, virtual_cnodes, edge_features,
                window_size_m=window_size_m,
                step_m=step_m,
                near_threshold_m=near_threshold_m,
                parallel_angle_threshold=parallel_angle_threshold,
            )
        else:
            print("Finding parallelograms...")
            parallelograms = find_parallelograms_near_cnodes(
                c_edges, virtual_cnodes, edge_features,
                near_threshold_m=near_threshold_m,
                parallel_angle_threshold=parallel_angle_threshold,
            )
        print(f"Parallelograms: {len(parallelograms)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "parallelograms", parallelograms)

    # Step: crossroads
    cached = _cache_load(cache_dir, "crossroads") if cache_dir and not _should_compute("crossroads", resume_from) else None
    if cached:
        intersection_clusters = cached
    else:
        t0 = time.time()
        print("Clustering parallelograms into crossroads...")
        intersection_clusters = cluster_parallelograms(parallelograms, cluster_radius_m=50.0)
        print(f"Crossroad clusters: {len(intersection_clusters)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "crossroads", intersection_clusters)

    # Step: merge_intersection
    cached = _cache_load(cache_dir, "merge_intersection") if cache_dir and not _should_compute("merge_intersection", resume_from) else None
    if cached:
        virtual_cnodes = cached
    else:
        t0 = time.time()
        print("Merging intersection C-nodes...")
        virtual_cnodes = merge_intersection_cnodes(virtual_cnodes, intersection_clusters, near_threshold_m=near_threshold_m)
        print(f"Virtual C-nodes after intersection merge: {len(virtual_cnodes)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "merge_intersection", virtual_cnodes)

    # Step: update_endpoints
    cached = _cache_load(cache_dir, "update_endpoints") if cache_dir and not _should_compute("update_endpoints", resume_from) else None
    if cached:
        c_edges = cached
    else:
        t0 = time.time()
        print("Updating C-edge endpoints...")
        update_c_edge_endpoints(c_edges, virtual_cnodes)
        print(f"  [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "update_endpoints", c_edges)

    # Step: split_c_edges
    cached = _cache_load(cache_dir, "split_c_edges") if cache_dir and not _should_compute("split_c_edges", resume_from) else None
    if cached:
        c_edges = cached
    else:
        t0 = time.time()
        print("Splitting C-edges at intersection nodes...")
        c_edges = split_c_edges_at_intersection_nodes(
            c_edges, virtual_cnodes, edge_clusters, edge_features,
            parallel_angle_threshold=parallel_angle_threshold
        )
        print(f"C-edges after splitting: {len(c_edges)} [{time.time() - t0:.1f}s]")
        if cache_dir:
            _cache_save(cache_dir, "split_c_edges", c_edges)

    print(f"Total pipeline: {time.time() - t_total:.1f}s")

    center = get_bounds_center(edge_features)

    traces = build_c_edge_traces(c_edges, node_coords)
    
    # Add virtual C-node traces
    if virtual_cnodes:
        traces.append(build_virtual_cnode_traces(virtual_cnodes))
    
    # Add reference grid traces
    traces.extend(build_reference_grid_traces(edge_features))

    title_text = f"C-edge graph: {len(c_edges)} C-edges, {len(virtual_cnodes)} virtual C-nodes"

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=center[1], lon=center[0]),
            zoom=13,
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        title=dict(text=title_text, x=0.5),
        showlegend=False,
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
    parser.add_argument("--sliding-window", type=str, default=None,
                        help="Sliding window as 'size_m,step_m' (e.g., '500,250')")
    parser.add_argument("--crop", type=float, default=None,
                        help="Crop to fraction of bounds (e.g., 0.5 for 50%% width/height)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Directory to save/load intermediate results")
    parser.add_argument("--resume-from", type=str, default=None, choices=PIPELINE_STEPS,
                        help="Step name to resume from (skip cache for this and later steps)")
    args = parser.parse_args()

    edges_path = args.edges or args.data_dir / "edges.geojson"
    nodes_path = args.nodes or args.data_dir / "nodes.geojson"

    sliding_window = None
    if args.sliding_window:
        parts = args.sliding_window.split(",")
        sliding_window = (int(parts[0]), int(parts[1]))

    cache_dir = args.cache_dir
    if cache_dir is None and args.data_dir:
        cache_dir = Path("cache") / args.data_dir.name

    plot_c_edge_graph(
        edges_path=edges_path,
        nodes_path=nodes_path,
        output_path=args.output,
        near_threshold_m=args.near_threshold,
        parallel_angle_threshold=args.parallel_angle_threshold,
        overlap_ratio_threshold=args.overlap_ratio,
        overlap_length_threshold_m=args.overlap_length,
        sliding_window=sliding_window,
        crop_ratio=args.crop,
        cache_dir=cache_dir,
        resume_from=args.resume_from,
    )


if __name__ == "__main__":
    main()