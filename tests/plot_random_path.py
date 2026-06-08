"""Visualize random path generation on C-edge graph.

Generates a random path on the C-edge graph satisfying given constraints
and visualizes it on an interactive plotly map.

Usage:
    cd src && uv run python tests/plot_random_path.py --data-dir ../resource/miniquad \
        --total-length 2000 --num-turns 5 --main-road-ratio 0.6

Dependencies: plotly>=6.7.0
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

from mock.graph_simplifier import build_walkable_graph
from mock.random_path_generator import generate_random_path


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


def plot_random_path(
    data_dir: Path,
    total_length_m: float,
    num_turns: int,
    main_road_ratio: float,
    main_road_level: int = 6,
    turn_angle: float = 60.0,
    max_retries: int = 100,
    seed: int | None = None,
    cache_dir: Path | None = None,
    output_path: Path | None = None,
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

    connected_cnode_ids = set()
    for ce in c_edges:
        connected_cnode_ids.update(ce.get('connected_vnodes', []))
    virtual_cnodes = {cn_id: cn for cn_id, cn in virtual_cnodes.items() if cn_id in connected_cnode_ids}

    print(f"Loaded: {len(c_edges)} C-edges, {len(virtual_cnodes)} C-nodes")

    cached_cluster = _cache_load(cache_dir, "cluster")
    edge_clusters = None
    edge_features = None
    if cached_cluster is not None:
        edge_clusters, _ = cached_cluster
        edges_path = data_dir / "edges.geojson"
        if edges_path.exists():
            edge_features = load_geojson(edges_path)
            print(f"Loaded edge data: {len(edge_clusters)} clusters, {len(edge_features)} edges")

    t0 = time.time()
    print("Building walkable graph...")
    walkable_graph = build_walkable_graph(c_edges, virtual_cnodes, edge_clusters, edge_features)
    print(f"Walkable graph: {len(walkable_graph['nodes'])} nodes, {len(walkable_graph['edges'])} edges [{time.time() - t0:.1f}s]")

    t0 = time.time()
    print(f"Generating random path (length={total_length_m}m, turns={num_turns}, main_road={main_road_ratio:.0%})...")
    path, stats = generate_random_path(
        walkable_graph,
        total_length_m=total_length_m,
        num_turns=num_turns,
        main_road_ratio=main_road_ratio,
        main_road_level=main_road_level,
        turn_angle=turn_angle,
        max_retries=max_retries,
        seed=seed,
    )
    print(f"Path generated [{time.time() - t0:.1f}s]")

    print(f"\nPath statistics:")
    print(f"  Total length: {stats['total_length_m']:.1f}m (target: {total_length_m}m)")
    print(f"  Turn count: {stats['turn_count']} (target: {num_turns})")
    print(f"  Main road ratio: {stats['main_road_ratio']:.1%} (target: {main_road_ratio:.1%})")
    print(f"  Forward: {stats['forward_ratio']:.1%}, Lateral: {stats['lateral_ratio']:.1%}, Backward: {stats['backward_ratio']:.1%}")
    print(f"  Edge count: {stats['edge_count']}")
    print(f"  Default direction: {stats['default_dir']:.1f}°")
    if stats.get('turns'):
        print(f"  Turn details:")
        for i, t in enumerate(stats['turns']):
            print(f"    #{i+1}: {t['angle']:.1f}° at ({t['position'][0]:.6f}, {t['position'][1]:.6f})")

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

    if output_path is None:
        output_path = data_dir / "random_path.html"

    fig.write_html(str(output_path))
    print(f"Saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and visualize random path on C-edge graph")
    parser.add_argument("--data-dir", type=Path, default=Path("resource/miniquad"))
    parser.add_argument("--total-length", type=float, required=True, help="Target total path length in meters")
    parser.add_argument("--num-turns", type=int, required=True, help="Target number of turns")
    parser.add_argument("--main-road-ratio", type=float, required=True, help="Target main road length ratio (0~1)")
    parser.add_argument("--main-road-level", type=int, default=6, help="Highway level threshold for main roads (default: 6)")
    parser.add_argument("--turn-angle", type=float, default=60.0, help="Angle threshold to count as a turn (default: 60°)")
    parser.add_argument("--max-retries", type=int, default=100, help="Maximum retry attempts (default: 100)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Cache directory (default: cache/<data-dir-name>)")
    parser.add_argument("--output", type=Path, default=None, help="Output HTML path")
    args = parser.parse_args()

    plot_random_path(
        data_dir=args.data_dir,
        total_length_m=args.total_length,
        num_turns=args.num_turns,
        main_road_ratio=args.main_road_ratio,
        main_road_level=args.main_road_level,
        turn_angle=args.turn_angle,
        max_retries=args.max_retries,
        seed=args.seed,
        cache_dir=args.cache_dir,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
