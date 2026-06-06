"""Debug visualization for C-edge 6849 in changsha dataset.

Shows the C-edge, connected C-nodes, nearby C-edges, and original road edges.

Usage:
    uv run python tests/debug_6849.py
"""
from __future__ import annotations

import json
import sys
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
    find_small_parallelograms,
    identify_connection_nodes,
    merge_intersection_cnodes,
    merge_t_junction_cnodes,
    recompute_c_edge_geometry,
    split_c_edges_at_intersection_nodes,
    update_c_edge_endpoints,
)
from utils.geometry import haversine_m


def load_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def main() -> None:
    data_dir = Path("resource/changsha")
    edge_features = load_geojson(data_dir / "edges.geojson")
    node_features = load_geojson(data_dir / "nodes.geojson")

    # Pre-filter: only keep edges/nodes near C-edge 6849's known location
    # C-edge 6849 start_coord is approximately (113.065, 28.129)
    center = (113.065, 28.129)
    filter_radius = 2000.0  # meters - generous buffer to ensure local results match full pipeline
    
    print(f"Pre-filtering to {filter_radius}m radius around {center}...")
    print(f"Original: {len(edge_features)} edges, {len(node_features)} nodes")
    
    # Filter edges by midpoint distance
    filtered_edges = []
    for ef in edge_features:
        coords = ef["geometry"]["coordinates"]
        mid_lon = (coords[0][0] + coords[-1][0]) / 2
        mid_lat = (coords[0][1] + coords[-1][1]) / 2
        if haversine_m((mid_lon, mid_lat), center) < filter_radius:
            filtered_edges.append(ef)
    edge_features = filtered_edges
    
    # Collect all node IDs referenced by filtered edges
    referenced_node_ids = set()
    for ef in edge_features:
        referenced_node_ids.add(ef["properties"]["u"])
        referenced_node_ids.add(ef["properties"]["v"])
    
    # Filter nodes: keep if within radius OR referenced by filtered edges
    filtered_nodes = []
    for nf in node_features:
        node_id = nf["properties"]["node_id"]
        coords = nf["geometry"]["coordinates"]
        in_radius = haversine_m((coords[0], coords[1]), center) < filter_radius
        if in_radius or node_id in referenced_node_ids:
            filtered_nodes.append(nf)
    node_features = filtered_nodes
    
    print(f"After filtering: {len(edge_features)} edges, {len(node_features)} nodes")

    print("Splitting edges...")
    edge_features, node_features, split_indices = split_edges_at_intersections(edge_features, node_features)

    print("Clustering...")
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features,
        near_threshold_m=50.0,
        parallel_angle_threshold=15.0,
        overlap_ratio_threshold=0.5,
        overlap_length_threshold_m=120.0,
    )

    node_coords = {}
    for nf in node_features:
        nid = nf["properties"]["node_id"]
        c = nf["geometry"]["coordinates"]
        node_coords[nid] = (c[0], c[1])

    print("Building C-edge graph...")
    c_edges = build_c_edge_graph(edge_clusters, edge_features, node_coords, core_edges_per_cluster=core_edges, near_threshold_m=50.0)

    print("Building node-to-C-edges mapping...")
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)

    print("Filtering spur core edges...")
    core_edges = filter_spur_core_edges(core_edges, edge_clusters, edge_features)
    recompute_c_edge_geometry(c_edges, core_edges, edge_clusters, edge_features, node_coords, 50.0)

    print("Identifying connection nodes...")
    connection_nodes = identify_connection_nodes(node_to_cedges)

    print("Clustering connection nodes...")
    clusters = cluster_connection_nodes(connection_nodes, node_coords)

    print("Creating virtual C-nodes...")
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features,
        near_threshold_m=50.0, parallel_angle_threshold=15.0,
    )

    print("Merging T-junction C-nodes...")
    virtual_cnodes = merge_t_junction_cnodes(virtual_cnodes, 50.0)

    print("Finding parallelograms...")
    parallelograms = find_parallelograms_near_cnodes(
        c_edges, virtual_cnodes, edge_features,
        near_threshold_m=50.0, parallel_angle_threshold=15.0,
    )
    print(f"Parallelograms: {len(parallelograms)}")

    print("Clustering parallelograms into crossroads...")
    intersection_clusters = cluster_parallelograms(parallelograms, cluster_radius_m=50.0)
    print(f"Crossroad clusters: {len(intersection_clusters)}")

    cluster_cnodes_before_merge = []
    for cluster in intersection_clusters:
        cluster_center = cluster['center']
        nearby = []
        for cn_id, vnode in virtual_cnodes.items():
            if haversine_m(vnode['position'], cluster_center) < 50.0:
                nearby.append({
                    'id': cn_id,
                    'position': vnode['position'],
                    'name': vnode['id'],
                })
        
        if nearby:
            avg_lon = sum(cn['position'][0] for cn in nearby) / len(nearby)
            avg_lat = sum(cn['position'][1] for cn in nearby) / len(nearby)
            cluster['center'] = (avg_lon, avg_lat)
        
        cluster_cnodes_before_merge.append(nearby)
        if nearby:
            print(f"  Cluster at ({cluster['center'][0]:.6f}, {cluster['center'][1]:.6f}): {len(nearby)} C-nodes")

    print("Merging intersection C-nodes...")
    virtual_cnodes = merge_intersection_cnodes(virtual_cnodes, intersection_clusters, near_threshold_m=50.0)
    print(f"Virtual C-nodes after intersection merge: {len(virtual_cnodes)}")

    print("Updating C-edge endpoints...")
    update_c_edge_endpoints(c_edges, virtual_cnodes)

    print("Splitting C-edges...")
    c_edges = split_c_edges_at_intersection_nodes(c_edges, virtual_cnodes, edge_clusters, edge_features, 15.0)

    # Focus on the area around C-edge 6849's known location
    # (After filtering, C-edge indices change, so we use the known coordinate)
    focus_radius = 500.0  # meters

    traces = []

    # Collect nearby C-edges first
    nearby_cedges = []
    for ce in c_edges:
        if ce.get("is_split", False):
            continue
        parent_idx = ce.get("parent_idx", ce["idx"])
        d_start = haversine_m(center, ce["start_coord"])
        d_end = haversine_m(center, ce["end_coord"])
        if d_start < focus_radius or d_end < focus_radius:
            nearby_cedges.append((parent_idx, ce))

    # Get unique parent C-edge indices for coloring
    nearby_parents = sorted(set(p for p, _ in nearby_cedges))
    
    # Color palette for different C-edges
    colors = [
        "rgba(255, 0, 0, 0.6)",      # red (6849)
        "rgba(0, 100, 255, 0.6)",    # blue
        "rgba(0, 180, 0, 0.6)",      # green
        "rgba(255, 140, 0, 0.6)",    # orange
        "rgba(148, 0, 211, 0.6)",    # purple
        "rgba(0, 200, 200, 0.6)",    # cyan
        "rgba(139, 0, 139, 0.6)",    # dark magenta
        "rgba(139, 69, 19, 0.6)",    # brown
    ]
    
    # 1. Draw original road edges for all nearby C-edges
    for parent_idx in nearby_parents:
        cluster = edge_clusters[parent_idx]
        color_idx = nearby_parents.index(parent_idx) % len(colors)
        edge_color = colors[color_idx]
        
        # Collect midpoints for this C-edge
        mid_lons = []
        mid_lats = []
        mid_hover_texts = []
        
        for edge_idx in cluster:
            edge = edge_features[edge_idx]
            coords = edge["geometry"]["coordinates"]
            mid_lon = (coords[0][0] + coords[-1][0]) / 2
            mid_lat = (coords[0][1] + coords[-1][1]) / 2
            if haversine_m((mid_lon, mid_lat), center) > focus_radius:
                continue

            hw = edge["properties"].get("highway", "unknown")
            if isinstance(hw, list):
                hw = ",".join(hw)
            d = edge["properties"].get("direction_deg", -1)
            u = edge["properties"].get("u", "?")
            v = edge["properties"].get("v", "?")

            traces.append(go.Scattermap(
                lon=[coords[0][0], coords[-1][0]],
                lat=[coords[0][1], coords[-1][1]],
                mode="lines",
                line=dict(width=1, color=edge_color),
                hovertext=f"C-edge {parent_idx}, Edge {edge_idx}: dir={d:.1f}, hw={hw}",
                hoverinfo="text",
                showlegend=False,
            ))
            
            # Collect midpoint data
            mid_lons.append(mid_lon)
            mid_lats.append(mid_lat)
            mid_hover_texts.append(
                f"Edge {edge_idx}<br>"
                f"C-edge: {parent_idx}<br>"
                f"dir: {d:.1f}°<br>"
                f"hw: {hw}<br>"
                f"u: {u}<br>"
                f"v: {v}"
            )
        
        # Add midpoint markers for this C-edge
        if mid_lons:
            traces.append(go.Scattermap(
                lon=mid_lons,
                lat=mid_lats,
                mode="markers",
                marker=dict(size=4, color=edge_color.replace("0.6", "1.0"), symbol="circle"),
                hovertext=mid_hover_texts,
                hoverinfo="text",
                showlegend=False,
            ))

    # 2. Draw C-edge lines for all nearby C-edges
    for parent_idx, ce in nearby_cedges:
        split_idx = ce.get("split_idx", "-")
        start = ce["start_coord"]
        end = ce["end_coord"]
        
        # Use thicker line for C-edge 6849
        if parent_idx == 6849:
            line_width = 4
            line_color = "red"
        else:
            color_idx = nearby_parents.index(parent_idx) % len(colors)
            line_color = colors[color_idx].replace("0.6", "1.0")  # Make fully opaque
            line_width = 2

        traces.append(go.Scattermap(
            lon=[start[0], end[0]],
            lat=[start[1], end[1]],
            mode="lines",
            line=dict(width=line_width, color=line_color),
            hovertext=f"C-edge {parent_idx}-{split_idx}: dir={ce['direction_deg']:.1f}, size={ce['size']}",
            hoverinfo="text",
            showlegend=False,
        ))

    # 4. Draw C-nodes connected to the main C-edge (originally 6849)
    # Find the C-edge closest to the center point
    min_dist = float('inf')
    main_ce_idx = None
    for i, ce in enumerate(c_edges):
        if ce.get("is_split", False):
            continue
        dist = haversine_m(center, ce["start_coord"])
        if dist < min_dist:
            min_dist = dist
            main_ce_idx = i
    
    if main_ce_idx is not None:
        print(f"Found main C-edge at index {main_ce_idx} (distance: {min_dist:.1f}m)")
        ce6849_vnodes = c_edges[main_ce_idx].get("connected_vnodes", [])
    else:
        ce6849_vnodes = []
    for vn_id in ce6849_vnodes:
        if vn_id not in virtual_cnodes:
            continue
        vn = virtual_cnodes[vn_id]
        pos = vn["position"]
        if haversine_m(pos, center) > focus_radius:
            continue

        connected = sorted(vn["connected_cedges"])
        assoc = vn["c_edge_end_associations"]

        traces.append(go.Scattermap(
            lon=[pos[0]],
            lat=[pos[1]],
            mode="markers+text",
            marker=dict(size=12, color="green", symbol="square"),
            text=[vn["id"]],
            textposition="top center",
            textfont=dict(size=10, color="green"),
            hovertext=f"{vn['id']}<br>Connected: {connected}<br>Assoc: {assoc}<br>Nodes: {len(vn['original_nodes'])}",
            hoverinfo="text",
            showlegend=False,
        ))

    # 5. Draw other nearby C-nodes
    for vn_id, vn in virtual_cnodes.items():
        if vn_id in ce6849_vnodes:
            continue
        pos = vn["position"]
        if haversine_m(pos, center) > focus_radius:
            continue

        connected = sorted(vn["connected_cedges"])

        traces.append(go.Scattermap(
            lon=[pos[0]],
            lat=[pos[1]],
            mode="markers",
            marker=dict(size=6, color="orange", symbol="circle"),
            hovertext=f"{vn['id']}<br>Connected: {connected}<br>Nodes: {len(vn['original_nodes'])}",
            hoverinfo="text",
            showlegend=False,
        ))

    # 6. Draw parallelograms and crossroad clusters
    # Filter parallelograms to only those within focus radius
    nearby_parallelograms = []
    for pg in parallelograms:
        vertices = pg['vertices']
        if any(haversine_m(v, center) < focus_radius for v in vertices):
            nearby_parallelograms.append(pg)
    
    print(f"Nearby parallelograms: {len(nearby_parallelograms)}")
    
    # Batch render all parallelograms in a single trace
    if nearby_parallelograms:
        pg_lons = []
        pg_lats = []
        for pg in nearby_parallelograms:
            vertices = pg['vertices']
            for v in vertices:
                pg_lons.append(v[0])
                pg_lats.append(v[1])
            pg_lons.append(vertices[0][0])
            pg_lats.append(vertices[0][1])
            pg_lons.append(None)
            pg_lats.append(None)
        
        traces.append(go.Scattermap(
            lon=pg_lons,
            lat=pg_lats,
            mode="lines",
            line=dict(width=1, color="rgba(255, 215, 0, 0.5)"),
            showlegend=False,
            hoverinfo="skip",
        ))
        
        # Add center markers with hover info
        center_lons = []
        center_lats = []
        center_hovers = []
        for pg in nearby_parallelograms:
            vertices = pg['vertices']
            cx = sum(v[0] for v in vertices) / 4
            cy = sum(v[1] for v in vertices) / 4
            edges = pg['edges']
            edge_lengths = pg['edge_lengths']
            center_lons.append(cx)
            center_lats.append(cy)
            center_hovers.append(
                f"Edges: {edges[0]},{edges[1]},{edges[2]},{edges[3]}<br>"
                f"Lengths: {edge_lengths[0]:.1f},{edge_lengths[1]:.1f},"
                f"{edge_lengths[2]:.1f},{edge_lengths[3]:.1f}m"
            )
        
        traces.append(go.Scattermap(
            lon=center_lons,
            lat=center_lats,
            mode="markers",
            marker=dict(
                size=14,
                color="rgba(255, 0, 0, 0.7)",
                symbol="circle",
            ),
            hovertext=center_hovers,
            hoverinfo="text",
            showlegend=False,
        ))

    # 7. Draw crossroad cluster centers
    nearby_clusters = []
    nearby_cluster_cnodes = []
    for cluster, cnodes in zip(intersection_clusters, cluster_cnodes_before_merge):
        if haversine_m(cluster['center'], center) < focus_radius:
            nearby_clusters.append(cluster)
            nearby_cluster_cnodes.append(cnodes)
    
    print(f"Nearby crossroad clusters: {len(nearby_clusters)}")
    
    if nearby_clusters:
        cluster_lons = []
        cluster_lats = []
        cluster_hovers = []
        for i, (cluster, cnodes) in enumerate(zip(nearby_clusters, nearby_cluster_cnodes)):
            cluster_lons.append(cluster['center'][0])
            cluster_lats.append(cluster['center'][1])
            merged_names = [cn['name'] for cn in cnodes]
            cluster_hovers.append(
                f"Crossroad #{i}<br>"
                f"Merged C-nodes: {', '.join(merged_names)}<br>"
                f"Count: {len(cnodes)}<br>"
                f"Parallelograms: {len(cluster['parallelograms'])}<br>"
                f"Center: ({cluster['center'][0]:.6f}, {cluster['center'][1]:.6f})"
            )
        
        traces.append(go.Scattermap(
            lon=cluster_lons,
            lat=cluster_lats,
            mode="markers",
            marker=dict(
                size=24,
                color="rgba(0, 100, 255, 0.8)",
                symbol="circle",
            ),
            hovertext=cluster_hovers,
            hoverinfo="text",
            showlegend=False,
        ))

    # 7.5 Draw original C-nodes that will be merged (before merge)
    for cluster_idx, (cluster, cnodes) in enumerate(zip(nearby_clusters, nearby_cluster_cnodes)):
        if not cnodes:
            continue
        
        cluster_center = cluster['center']
        
        orig_lons = [cn['position'][0] for cn in cnodes]
        orig_lats = [cn['position'][1] for cn in cnodes]
        orig_hovers = [
            f"Original C-node {cn['name']}<br>"
            f"Will merge into Crossroad #{cluster_idx}"
            for cn in cnodes
        ]
        
        traces.append(go.Scattermap(
            lon=orig_lons,
            lat=orig_lats,
            mode="markers",
            marker=dict(size=10, color="rgba(148, 0, 211, 0.8)", symbol="circle"),
            hovertext=orig_hovers,
            hoverinfo="text",
            showlegend=False,
        ))
        
        line_lons = []
        line_lats = []
        for cn in cnodes:
            p1 = cn['position']
            p2 = cluster_center
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            dist = haversine_m(p1, p2)
            if dist < 1.0:
                continue
            dash_len = dist / 111000.0 / 8
            steps = max(2, int(dist / (dash_len * 111000.0 * 2)))
            for s in range(steps):
                t1 = s * 2 / (steps * 2)
                t2 = (s * 2 + 1) / (steps * 2)
                line_lons.extend([p1[0] + dx * t1, p1[0] + dx * t2, None])
                line_lats.extend([p1[1] + dy * t1, p1[1] + dy * t2, None])
        
        if line_lons:
            traces.append(go.Scattermap(
                lon=line_lons,
                lat=line_lats,
                mode="lines",
                line=dict(width=2, color="rgba(148, 0, 211, 0.6)"),
                showlegend=False,
                hoverinfo="skip",
            ))

    # 8. Reference grid
    min_lon = center[0] - 0.005
    max_lon = center[0] + 0.005
    min_lat = center[1] - 0.004
    max_lat = center[1] + 0.004

    grid_spacing = 0.001
    lat_lons, lat_lats = [], []
    lat = min_lat
    while lat <= max_lat:
        lat_lons.extend([min_lon, max_lon, None])
        lat_lats.extend([lat, lat, None])
        lat += grid_spacing

    lon_lons, lon_lats = [], []
    lon = min_lon
    while lon <= max_lon:
        lon_lons.extend([lon, lon, None])
        lon_lats.extend([min_lat, max_lat, None])
        lon += grid_spacing

    traces.append(go.Scattermap(
        lon=lat_lons + lon_lons,
        lat=lat_lats + lon_lats,
        mode="lines",
        line=dict(width=1, color="rgba(100, 100, 100, 0.3)"),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Labels
    label_lons, label_lats, label_texts = [], [], []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            label_lons.append(lon)
            label_lats.append(lat)
            label_texts.append(f"{lat:.3f}, {lon:.3f}")
            lon += grid_spacing
        lat += grid_spacing

    traces.append(go.Scattermap(
        lon=label_lons,
        lat=label_lats,
        mode="text",
        text=label_texts,
        textfont=dict(size=8, color="rgba(100, 100, 100, 0.7)"),
        showlegend=False,
        hoverinfo="skip",
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=center[1], lon=center[0]),
            zoom=15,
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        title=dict(text="Debug: C-edge 6849 (red) and nearby C-edges (blue)", x=0.5),
        showlegend=False,
        dragmode="pan",
    )

    output_path = data_dir / "debug_6849.html"
    fig.write_html(str(output_path))
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
