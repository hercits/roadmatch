"""Generate multiple random paths with detection simulations.

Generates 5 random paths on the C-edge graph, simulates fiber optic detection
events, and saves both visualization and detection results.

Usage:
    cd src && uv run python tests/generate_paths.py --data-dir ../resource/miniquad --city changsha

Dependencies: plotly>=6.7.0
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import random
import sys
import time
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.graph_simplifier import build_walkable_graph
from mock.random_path_generator import generate_random_path
from mock.event_simulator import simulate_detections
from mock.graph_exporter import export_walkable_graph_to_geojson


PATH_CONFIGS = [
    {"total_length_m": 20000, "num_turns": 12, "main_road_ratio": 0.8, "seed": 1001},
    {"total_length_m": 22000, "num_turns": 13, "main_road_ratio": 0.8, "seed": 2002},
    {"total_length_m": 25000, "num_turns": 15, "main_road_ratio": 0.8, "seed": 3003},
    {"total_length_m": 28000, "num_turns": 17, "main_road_ratio": 0.8, "seed": 4004},
    {"total_length_m": 30000, "num_turns": 18, "main_road_ratio": 0.8, "seed": 5005},
]


def load_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def _cache_load(cache_dir: Path, step_name: str):
    path = cache_dir / f"{step_name}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            data = pickle.load(f)
        print(f"  [cache hit] {step_name}")
        return data
    return None


def _create_path_visualization(
    path: list[dict],
    stats: dict,
    c_edges: list[dict],
    virtual_cnodes: dict,
    main_road_level: int,
) -> go.Figure:
    active_edges = [ce for ce in c_edges if not ce.get('is_split', False)]

    bg_lons, bg_lats = [], []
    for ce in active_edges:
        connected = ce.get('connected_vnodes', [])
        if len(connected) < 2:
            continue
        start_vnode = virtual_cnodes.get(connected[0])
        end_vnode = virtual_cnodes.get(connected[1])
        if not start_vnode or not end_vnode:
            continue
        bg_lons.extend([start_vnode['position'][0], end_vnode['position'][0], None])
        bg_lats.extend([start_vnode['position'][1], end_vnode['position'][1], None])

    path_lons, path_lats = [], []
    path_main_lons, path_main_lats = [], []
    path_minor_lons, path_minor_lats = [], []

    for item in path:
        if item['type'] == 'node':
            path_lons.append(item['position'][0])
            path_lats.append(item['position'][1])

    for i, item in enumerate(path):
        if item['type'] == 'edge':
            prev_node = path[i - 1]
            next_node = path[i + 1]
            is_main = item['highway_level'] <= main_road_level
            if is_main:
                path_main_lons.extend([prev_node['position'][0], next_node['position'][0], None])
                path_main_lats.extend([prev_node['position'][1], next_node['position'][1], None])
            else:
                path_minor_lons.extend([prev_node['position'][0], next_node['position'][0], None])
                path_minor_lats.extend([prev_node['position'][1], next_node['position'][1], None])

    start_pos = path[0]['position']
    end_pos = path[-1]['position']

    turn_lons, turn_lats, turn_texts = [], [], []
    for item in path:
        if item['type'] == 'node' and item.get('is_turn'):
            turn_lons.append(item['position'][0])
            turn_lats.append(item['position'][1])
            turn_texts.append(f"Turn {item['turn_angle']:.0f}°")

    default_dir = stats['default_dir']
    arrow_len = 0.002
    dir_rad = math.radians(default_dir)
    arrow_end_lon = start_pos[0] + arrow_len * math.sin(dir_rad)
    arrow_end_lat = start_pos[1] + arrow_len * math.cos(dir_rad)

    traces = [
        go.Scattermap(
            lon=bg_lons, lat=bg_lats,
            mode='lines',
            line=dict(width=1, color='rgba(180,180,180,0.4)'),
            name='C-edges',
            hoverinfo='skip',
        ),
    ]

    if path_main_lons:
        traces.append(go.Scattermap(
            lon=path_main_lons, lat=path_main_lats,
            mode='lines',
            line=dict(width=4, color='#e63946'),
            name='Path (main road)',
        ))

    if path_minor_lons:
        traces.append(go.Scattermap(
            lon=path_minor_lons, lat=path_minor_lats,
            mode='lines',
            line=dict(width=4, color='#457b9d'),
            name='Path (minor road)',
        ))

    traces.append(go.Scattermap(
        lon=path_lons, lat=path_lats,
        mode='lines',
        line=dict(width=2, color='rgba(0,0,0,0.3)'),
        name='Path outline',
        hoverinfo='skip',
    ))

    traces.append(go.Scattermap(
        lon=[start_pos[0]], lat=[start_pos[1]],
        mode='markers',
        marker=dict(size=14, color='#2a9d8f', symbol='circle'),
        name='Start',
        text=['Start'],
        hoverinfo='name',
    ))

    traces.append(go.Scattermap(
        lon=[end_pos[0]], lat=[end_pos[1]],
        mode='markers',
        marker=dict(size=14, color='#e76f51', symbol='circle'),
        name='End',
        text=['End'],
        hoverinfo='name',
    ))

    traces.append(go.Scattermap(
        lon=[start_pos[0], arrow_end_lon],
        lat=[start_pos[1], arrow_end_lat],
        mode='lines',
        line=dict(width=3, color='#f4a261'),
        name=f'Default dir ({default_dir:.0f}°)',
        hoverinfo='name',
    ))

    if turn_lons:
        traces.append(go.Scattermap(
            lon=turn_lons, lat=turn_lats,
            mode='markers+text',
            marker=dict(size=10, color='#e9c46a', symbol='diamond'),
            text=turn_texts,
            textposition='top center',
            textfont=dict(size=9),
            name='Turns',
            hoverinfo='text',
        ))

    title_text = (
        f"Random Path: {stats['total_length_m']:.0f}m, "
        f"{stats['turn_count']} turns, "
        f"main road {stats['main_road_ratio']:.0%}, "
        f"F/L/B: {stats['forward_ratio']:.0%}/{stats['lateral_ratio']:.0%}/{stats['backward_ratio']:.0%}"
    )

    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lon=start_pos[0], lat=start_pos[1]),
            zoom=15,
        ),
        title=dict(text=title_text, x=0.5),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        dragmode="pan",
    )

    return fig


def generate_paths(
    data_dir: Path,
    city: str,
    output_dir: Path,
    main_road_level: int = 6,
    turn_angle: float = 60.0,
    max_retries: int = 100,
    cache_dir: Path | None = None,
) -> None:
    if cache_dir is None:
        cache_dir = Path("cache") / data_dir.name

    cached_edges = _cache_load(cache_dir, "filter_dangling")
    if cached_edges is None:
        cached_edges = _cache_load(cache_dir, "update_endpoints")
    if cached_edges is None:
        print("Error: No cached c_edges found. Run plot_c_graph.py first.")
        return

    c_edges = cached_edges

    cached_cnodes = _cache_load(cache_dir, "merge_intersection")
    if cached_cnodes is None:
        cached_cnodes = _cache_load(cache_dir, "merge_intermediate_t")
    if cached_cnodes is None:
        cached_cnodes = _cache_load(cache_dir, "merge_t_junction")
    if cached_cnodes is None:
        cached_cnodes = _cache_load(cache_dir, "virtual_cnodes")
    if cached_cnodes is None:
        print("Error: No cached virtual_cnodes found. Run plot_c_graph.py first.")
        return

    virtual_cnodes = cached_cnodes

    cached_cluster = _cache_load(cache_dir, "cluster")
    edge_clusters = None
    edge_features = None
    if cached_cluster is not None:
        edge_clusters, _ = cached_cluster
        edges_path = data_dir / "raw" / "edges.geojson"
        if edges_path.exists():
            edge_features = load_geojson(edges_path)
            print(f"Loaded edge data: {len(edge_clusters)} clusters, {len(edge_features)} edges")

    connected_cnode_ids = set()
    for ce in c_edges:
        connected_cnode_ids.update(ce.get('connected_vnodes', []))
    virtual_cnodes = {cn_id: cn for cn_id, cn in virtual_cnodes.items() if cn_id in connected_cnode_ids}

    print(f"Loaded: {len(c_edges)} C-edges, {len(virtual_cnodes)} C-nodes")

    t0 = time.time()
    print("Building walkable graph...")
    walkable_graph = build_walkable_graph(c_edges, virtual_cnodes, edge_clusters, edge_features)
    print(f"Walkable graph: {len(walkable_graph['nodes'])} nodes, {len(walkable_graph['edges'])} edges [{time.time() - t0:.1f}s]")

    export_walkable_graph_to_geojson(walkable_graph, data_dir)

    graph_nodes = walkable_graph['nodes']
    lons = [n['position'][0] for n in graph_nodes.values()]
    lats = [n['position'][1] for n in graph_nodes.values()]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    center_lon = (min_lon + max_lon) / 2

    city_output_dir = output_dir / city
    city_output_dir.mkdir(parents=True, exist_ok=True)

    for i, cfg in enumerate(PATH_CONFIGS, start=1):
        path_dir = city_output_dir / f"path_{i}"
        path_dir.mkdir(exist_ok=True)

        print(f"\n--- Path {i}: length={cfg['total_length_m']}m, turns={cfg['num_turns']}, seed={cfg['seed']} ---")

        rng = random.Random(cfg["seed"])
        top_offset = rng.uniform(0.10, 0.20)
        start_lat = max_lat - top_offset * (max_lat - min_lat)
        lon_offset = rng.uniform(-0.3, 0.3) * (max_lon - min_lon)
        start_position = (center_lon + lon_offset, start_lat)
        default_direction = 180.0 + rng.uniform(-15.0, 15.0)
        print(f"  Start: ({start_position[0]:.6f}, {start_position[1]:.6f}), dir: {default_direction:.1f}°")

        t0 = time.time()
        path, stats = generate_random_path(
            walkable_graph,
            total_length_m=cfg["total_length_m"],
            num_turns=cfg["num_turns"],
            main_road_ratio=cfg["main_road_ratio"],
            main_road_level=main_road_level,
            turn_angle=turn_angle,
            max_retries=max_retries,
            seed=cfg["seed"],
            relax_directional_constraints=True,
            start_position=start_position,
            default_direction=default_direction,
        )
        print(f"  Path generated: {stats['total_length_m']:.1f}m, {stats['turn_count']} turns, main_road={stats['main_road_ratio']:.1%} [{time.time() - t0:.1f}s]")

        detection = simulate_detections(path, walkable_graph, main_road_level=main_road_level, seed=cfg["seed"])
        n_intersec = len(detection["intersec_after_redund(meter)_l_m_s"][0])
        print(f"  Detection: {detection['path_length(meter)']}m, {n_intersec} intersections")

        fig = _create_path_visualization(path, stats, c_edges, virtual_cnodes, main_road_level)
        html_path = path_dir / "path.html"
        fig.write_html(str(html_path))
        print(f"  Saved: {html_path}")

        json_path = path_dir / "detection.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(detection, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {json_path}")

        endpoints = {
            "start": {
                "id": path[0]['id'].removeprefix("C-node_"),
                "lon": round(path[0]['position'][0], 7),
                "lat": round(path[0]['position'][1], 7),
            },
            "end": {
                "id": path[-1]['id'].removeprefix("C-node_"),
                "lon": round(path[-1]['position'][0], 7),
                "lat": round(path[-1]['position'][1], 7),
            },
        }
        endpoints_path = path_dir / "endpoints.json"
        with open(endpoints_path, "w", encoding="utf-8") as f:
            json.dump(endpoints, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {endpoints_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate multiple random paths with detection simulations")
    parser.add_argument("--data-dir", type=Path, default=Path("resource/miniquad"))
    parser.add_argument("--city", type=str, required=True, help="City/network name for output subdirectory")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Output root directory")
    parser.add_argument("--main-road-level", type=int, default=6, help="Highway level threshold for main roads (default: 6)")
    parser.add_argument("--turn-angle", type=float, default=60.0, help="Angle threshold to count as a turn (default: 60°)")
    parser.add_argument("--max-retries", type=int, default=100, help="Maximum retry attempts (default: 100)")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Cache directory (default: cache/<data-dir-name>)")
    args = parser.parse_args()

    generate_paths(
        data_dir=args.data_dir,
        city=args.city,
        output_dir=args.output_dir,
        main_road_level=args.main_road_level,
        turn_angle=args.turn_angle,
        max_retries=args.max_retries,
        cache_dir=args.cache_dir,
    )


if __name__ == "__main__":
    main()
