"""Debug visualization: C-edge graph before merge_intermediate_t.

Shows the area around C-node 3728 (±500m) using data from merge_t_junction stage.
Includes original edges from C-edge 4045 cluster to analyze clustering.

Usage:
    cd D:\01-Codes\roadmatch && uv run python tests/debug_plot_area.py
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path

import plotly.graph_objects as go


def load_pickle(path: Path):
    with open(path, "rb") as f:
        return pickle.load(f)


def haversine_m(coord1: tuple, coord2: tuple) -> float:
    R = 6371000
    lat1, lat2 = math.radians(coord1[1]), math.radians(coord2[1])
    dlat = math.radians(coord2[1] - coord1[1])
    dlon = math.radians(coord2[0] - coord1[0])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def main():
    cache_dir = Path("cache/changsha")
    vnodes = load_pickle(cache_dir / "merge_t_junction.pkl")
    c_edges = load_pickle(cache_dir / "filter_link.pkl")
    edge_clusters, core_edges = load_pickle(cache_dir / "cluster.pkl")
    edge_features, node_features = load_pickle(cache_dir / "split.pkl")

    center = (113.0105, 28.1370)
    radius_m = 500
    lat_per_m = 1.0 / 111000.0
    lon_per_m = 1.0 / (111000.0 * math.cos(math.radians(center[1])))
    dlat = radius_m * lat_per_m
    dlon = radius_m * lon_per_m

    min_lon, max_lon = center[0] - dlon, center[0] + dlon
    min_lat, max_lat = center[1] - dlat, center[1] + dlat

    visible_cnodes = {}
    for cn_id, cn in vnodes.items():
        pos = cn["position"]
        if min_lon <= pos[0] <= max_lon and min_lat <= pos[1] <= max_lat:
            visible_cnodes[cn_id] = cn

    visible_cn_ids = set(visible_cnodes.keys())
    visible_cedges = []
    for ce in c_edges:
        s, e = ce["start_coord"], ce["end_coord"]
        s_in = min_lon <= s[0] <= max_lon and min_lat <= s[1] <= max_lat
        e_in = min_lon <= e[0] <= max_lon and min_lat <= e[1] <= max_lat
        if s_in or e_in:
            visible_cedges.append(ce)

    highlight_cedges = {4044, 4068}
    highlight_cnodes = {3744}

    cn3727_nodes = {
        '113.0103385_28.1371243',
        '113.0106711_28.1369635',
    }

    traces = []

    other_lons, other_lats, other_labels, other_hovers = [], [], [], []
    for ce in visible_cedges:
        parent = ce.get("parent_idx", ce["idx"])
        if parent in highlight_cedges:
            continue
        label = f"{parent}-{ce['split_idx']}" if "split_idx" in ce else str(parent)
        s, e = ce["start_coord"], ce["end_coord"]
        hover = (
            f"C-edge {label}<br>"
            f"idx={ce['idx']}<br>"
            f"Direction: {ce['direction_deg']:.1f}°<br>"
            f"Size: {ce['size']}<br>"
            f"Start: ({s[0]:.7f}, {s[1]:.7f})<br>"
            f"End: ({e[0]:.7f}, {e[1]:.7f})"
        )
        other_lons.extend([s[0], e[0], None])
        other_lats.extend([s[1], e[1], None])
        other_labels.append(label)
        other_hovers.append(hover)

    if other_lons:
        traces.append(go.Scattermap(
            lon=other_lons, lat=other_lats, mode="lines",
            line=dict(width=1.5, color="rgba(150,150,150,0.5)"),
            hoverinfo="skip", showlegend=False,
        ))
        mid_lons, mid_lats = [], []
        for i in range(0, len(other_lons), 3):
            if other_lons[i] is not None and other_lons[i + 1] is not None:
                mid_lons.append((other_lons[i] + other_lons[i + 1]) / 2)
                mid_lats.append((other_lats[i] + other_lats[i + 1]) / 2)
        if mid_lons:
            traces.append(go.Scattermap(
                lon=mid_lons, lat=mid_lats, mode="text",
                text=other_labels,
                textfont=dict(size=8, color="rgba(150,150,150,0.7)"),
                hovertext=other_hovers, hoverinfo="text",
                showlegend=False,
            ))

    colors = {4044: "#e63946", 4045: "#f4a261", 4046: "#2a9d8f", 7878: "#457b9d"}
    for ce in visible_cedges:
        parent = ce.get("parent_idx", ce["idx"])
        if parent not in highlight_cedges:
            continue
        s, e = ce["start_coord"], ce["end_coord"]
        label = f"{parent}-{ce['split_idx']}" if "split_idx" in ce else str(parent)
        hover = (
            f"C-edge {label}<br>"
            f"idx={ce['idx']}<br>"
            f"Direction: {ce['direction_deg']:.1f}°<br>"
            f"Size: {ce['size']}<br>"
            f"Start: ({s[0]:.7f}, {s[1]:.7f})<br>"
            f"End: ({e[0]:.7f}, {e[1]:.7f})"
        )
        traces.append(go.Scattermap(
            lon=[s[0], e[0]], lat=[s[1], e[1]], mode="lines",
            line=dict(width=3, color=colors.get(parent, "#e63946")),
            name=f"C-edge {label}",
            hovertext=[hover, hover], hoverinfo="text",
        ))
        mid_lon = (s[0] + e[0]) / 2
        mid_lat = (s[1] + e[1]) / 2
        traces.append(go.Scattermap(
            lon=[mid_lon], lat=[mid_lat], mode="text",
            text=[label],
            textfont=dict(size=11, color=colors.get(parent, "#e63946"), family="Arial Black"),
            hoverinfo="skip", showlegend=False,
        ))

    from collections import defaultdict as _dd

    def _find_components(cluster_indices):
        adj = _dd(set)
        for idx in cluster_indices:
            ef = edge_features[idx]
            u, v = ef['properties']['u'], ef['properties']['v']
            adj[u].add(v)
            adj[v].add(u)
        all_n = set()
        for idx in cluster_indices:
            ef = edge_features[idx]
            all_n.add(ef['properties']['u'])
            all_n.add(ef['properties']['v'])
        vis = set()
        comps = []
        for node in all_n:
            if node in vis:
                continue
            cn = set()
            ce = []
            q = [node]
            while q:
                n = q.pop(0)
                if n in vis:
                    continue
                vis.add(n)
                cn.add(n)
                for nb in adj[n]:
                    if nb not in vis:
                        q.append(nb)
            for idx in cluster_indices:
                ef = edge_features[idx]
                u, v = ef['properties']['u'], ef['properties']['v']
                if u in cn and v in cn:
                    ce.append(idx)
            comps.append((cn, ce))
        comps.sort(key=lambda x: -len(x[0]))
        return comps

    comp_colors = ["#e63946", "#457b9d", "#2a9d8f", "#f4a261", "#e9c46a"]

    for ce_parent in [4044, 4068]:
        cluster = edge_clusters[ce_parent]
        comps = _find_components(cluster)

        for ci, (comp_nodes, comp_edges) in enumerate(comps):
            color = comp_colors[ci % len(comp_colors)] if ci > 0 else "rgba(150,150,150,0.4)"
            comp_label = f"C{ce_parent}-comp{ci}" if ci > 0 else f"C{ce_parent}-main"

            edge_lons, edge_lats, edge_hovers = [], [], []
            label_lons, label_lats, label_texts = [], [], []

            for idx in sorted(comp_edges):
                ef = edge_features[idx]
                coords = ef['geometry']['coordinates']
                u = ef['properties']['u']
                v = ef['properties']['v']
                start = (coords[0][0], coords[0][1])
                end = (coords[-1][0], coords[-1][1])
                d = ef['properties']['direction_deg']
                is_core = idx in core_edges.get(ce_parent, set())
                touches_target = u in cn3727_nodes or v in cn3727_nodes

                hover = (
                    f"Edge {idx} ({comp_label})<br>"
                    f"dir={d:.1f}, core={is_core}<br>"
                    f"u={u}<br>v={v}<br>"
                    f"touches_target={touches_target}"
                )
                edge_lons.extend([start[0], end[0], None])
                edge_lats.extend([start[1], end[1], None])
                edge_hovers.append(hover)

                mid_lon = (start[0] + end[0]) / 2
                mid_lat = (start[1] + end[1]) / 2
                label_lons.append(mid_lon)
                label_lats.append(mid_lat)
                label_texts.append(str(idx))

            if edge_lons:
                width = 3 if ci > 0 else 1.5
                traces.append(go.Scattermap(
                    lon=edge_lons, lat=edge_lats, mode="lines",
                    line=dict(width=width, color=color),
                    hovertext=edge_hovers, hoverinfo="text",
                    name=f"{comp_label} ({len(comp_edges)}e)",
                    showlegend=True,
                ))
            if label_lons:
                traces.append(go.Scattermap(
                    lon=label_lons, lat=label_lats, mode="text",
                    text=label_texts,
                    textfont=dict(size=7, color=color),
                    hoverinfo="skip", showlegend=False,
                ))

    hl_lons, hl_lats, hl_texts, hl_hovers = [], [], [], []
    other_cn_lons, other_cn_lats, other_cn_texts, other_cn_hovers = [], [], [], []

    for cn_id, cn in visible_cnodes.items():
        pos = cn["position"]
        connected = sorted(cn["connected_cedges"])
        end_assocs = cn.get("c_edge_end_associations", {})
        if isinstance(end_assocs, dict):
            assoc_str = ", ".join(f"{ce}({et})" for ce, et in end_assocs.items())
        else:
            assoc_str = ", ".join(f"{ce}({et})" for ce, et in end_assocs)
        orig_count = len(cn["original_nodes"]) if isinstance(cn["original_nodes"], list) else len(cn["original_nodes"])
        hover = (
            f"C-node {cn_id}<br>"
            f"Position: ({pos[0]:.7f}, {pos[1]:.7f})<br>"
            f"Connected: {connected}<br>"
            f"End assocs: {assoc_str}<br>"
            f"Original nodes: {orig_count}"
        )
        if cn_id in highlight_cnodes:
            hl_lons.append(pos[0])
            hl_lats.append(pos[1])
            hl_texts.append(str(cn_id))
            hl_hovers.append(hover)
        else:
            other_cn_lons.append(pos[0])
            other_cn_lats.append(pos[1])
            other_cn_texts.append(str(cn_id))
            other_cn_hovers.append(hover)

    if other_cn_lons:
        traces.append(go.Scattermap(
            lon=other_cn_lons, lat=other_cn_lats,
            mode="markers+text",
            marker=dict(size=8, color="#457b9d", symbol="square"),
            text=other_cn_texts,
            textposition="top center",
            textfont=dict(size=8, color="#457b9d"),
            hovertext=other_cn_hovers, hoverinfo="text",
            showlegend=False,
        ))

    if hl_lons:
        traces.append(go.Scattermap(
            lon=hl_lons, lat=hl_lats,
            mode="markers+text",
            marker=dict(size=14, color="#e63946", symbol="circle"),
            text=hl_texts,
            textposition="top center",
            textfont=dict(size=11, color="#e63946", family="Arial Black"),
            hovertext=hl_hovers, hoverinfo="text",
            name="Problem nodes",
        ))

    orig_node_lons, orig_node_lats, orig_node_texts, orig_node_hovers = [], [], [], []
    for nid in cn3727_nodes:
        parts = nid.split('_')
        lon, lat = float(parts[0]), float(parts[1])
        orig_node_lons.append(lon)
        orig_node_lats.append(lat)
        orig_node_texts.append(nid[:12] + "...")
        orig_node_hovers.append(f"Original node: {nid}")

    if orig_node_lons:
        traces.append(go.Scattermap(
            lon=orig_node_lons, lat=orig_node_lats,
            mode="markers+text",
            marker=dict(size=10, color="#ffd166", symbol="diamond"),
            text=orig_node_texts,
            textposition="bottom center",
            textfont=dict(size=7, color="#ffd166"),
            hovertext=orig_node_hovers, hoverinfo="text",
            name="C-node 3687 original nodes",
        ))

    title = (
        f"Debug: C-edge 4044/4068 cluster components | "
        f"C-node 3687 area"
    )

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=center[1], lon=center[0]),
            zoom=15,
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        title=dict(text=title, x=0.5),
        showlegend=True,
        legend=dict(x=0.01, y=0.99),
        dragmode="pan",
    )

    output = Path("resource/changsha/debug_pre_merge_intermediate_t.html")
    fig.write_html(str(output))
    print(f"Saved to {output}")


if __name__ == "__main__":
    main()
