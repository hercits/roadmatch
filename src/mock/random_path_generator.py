from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from utils.geometry import angular_delta_mod360


def generate_random_path(
    walkable_graph: Dict[str, Any],
    total_length_m: float,
    num_turns: int,
    main_road_ratio: float,
    main_road_level: int = 6,
    turn_angle: float = 60.0,
    max_retries: int = 100,
    seed: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Generate a random path on the walkable graph satisfying given constraints.

    Args:
        walkable_graph: Graph from build_walkable_graph().
        total_length_m: Target total path length in meters.
        num_turns: Target number of turns.
        main_road_ratio: Target ratio of main road length (0~1).
        main_road_level: Highway level threshold for main roads (<=this is main road).
        turn_angle: Angle threshold in degrees to count as a turn.
        max_retries: Maximum retry attempts if constraints not met.
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (path, stats):
        - path: List of alternating node/edge dicts. Turns are marked on nodes.
        - stats: Dict with actual statistics.

    Raises:
        RuntimeError: If no valid path found after max_retries.
    """
    if seed is not None:
        random.seed(seed)

    nodes = walkable_graph['nodes']
    edges = walkable_graph['edges']

    if not nodes or not edges:
        raise RuntimeError("Empty walkable graph")

    eligible_nodes = [nid for nid, n in nodes.items() if len(n['edges']) >= 2]
    if not eligible_nodes:
        raise RuntimeError("No nodes with degree >= 2")

    avg_edge_length = sum(e['length_m'] for e in edges.values()) / len(edges)
    est_edges = max(1, int(total_length_m / avg_edge_length))
    ideal_gap = max(1, est_edges / max(1, num_turns))
    cooldown = max(1, int(ideal_gap * 0.5))

    for attempt in range(max_retries):
        start_node = random.choice(eligible_nodes)
        default_dir = random.uniform(0, 360)

        path, stats = _try_generate_path(
            nodes, edges, start_node, default_dir,
            total_length_m, num_turns, main_road_ratio, main_road_level,
            turn_angle, cooldown, ideal_gap, avg_edge_length,
        )

        if path and _validate_constraints(stats, total_length_m, num_turns, main_road_ratio):
            return path, stats

        if attempt == max_retries - 1 and stats:
            print(f"Last attempt stats: {stats}")

    raise RuntimeError(f"Failed to generate valid path after {max_retries} attempts")


def _get_traversal_bearing(
    edge: Dict[str, Any],
    current_node: str,
) -> float:
    """Get the traversal bearing (0-360) based on direction_deg and traversal direction.

    If traversing start->end (forward), bearing = direction_deg.
    If traversing end->start (backward), bearing = (direction_deg + 180) % 360.
    """
    if current_node == edge['start_node']:
        return edge['direction_deg']
    else:
        return (edge['direction_deg'] + 180.0) % 360.0


def _try_generate_path(
    nodes: Dict[str, Dict[str, Any]],
    edges: Dict[int, Dict[str, Any]],
    start_node: str,
    default_dir: float,
    total_length_m: float,
    num_turns: int,
    main_road_ratio: float,
    main_road_level: int,
    turn_angle: float,
    cooldown: int,
    ideal_gap: float,
    avg_edge_length: float,
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """Attempt to generate a single path."""
    path: List[Dict[str, Any]] = []
    current_node = start_node
    accumulated_length = 0.0
    turn_count = 0
    steps_since_last_turn = 0
    forward_length = 0.0
    lateral_length = 0.0
    backward_length = 0.0
    main_road_length = 0.0
    last_edge_idx: Optional[int] = None
    last_bearing: Optional[float] = None
    edge_count = 0
    visited_nodes: set = {start_node}
    turns: List[Dict[str, Any]] = []

    path.append({
        'type': 'node',
        'id': start_node,
        'position': nodes[start_node]['position'],
        'is_turn': False,
        'turn_angle': None,
    })

    min_length = total_length_m * 0.8
    max_length = total_length_m * 1.2

    while accumulated_length < max_length:
        candidate_edges = nodes[current_node]['edges']
        if not candidate_edges:
            break

        valid_candidates = []
        weights = []

        for ce_idx in candidate_edges:
            edge = edges[ce_idx]
            if edge['start_node'] == current_node:
                next_node = edge['end_node']
            else:
                next_node = edge['start_node']

            if next_node in visited_nodes:
                continue

            traversal_bearing = _get_traversal_bearing(edge, current_node)

            if last_bearing is not None:
                bearing_diff = angular_delta_mod360(last_bearing, traversal_bearing)
                if bearing_diff > 150:
                    continue

            weight = 1.0

            if last_bearing is not None:
                bearing_diff = angular_delta_mod360(last_bearing, traversal_bearing)
                is_turn = bearing_diff > turn_angle

                if steps_since_last_turn < cooldown and is_turn:
                    weight *= 0.1
                elif steps_since_last_turn >= ideal_gap * 1.5 and not is_turn:
                    weight *= 0.5
                elif steps_since_last_turn >= ideal_gap * 1.5 and is_turn:
                    weight *= 2.0

            dir_delta = angular_delta_mod360(default_dir, traversal_bearing)
            if dir_delta < 60:
                forward_weight = 2.0
            elif dir_delta < 120:
                forward_weight = 1.0
            else:
                forward_weight = 0.3

            progress = accumulated_length / total_length_m if total_length_m > 0 else 0
            progress_factor = 1.0 + progress * 2.0

            if accumulated_length > 0:
                f_ratio = forward_length / accumulated_length
                l_ratio = lateral_length / accumulated_length
                b_ratio = backward_length / accumulated_length

                if f_ratio < 0.6:
                    if dir_delta < 60:
                        forward_weight *= (1.5 * progress_factor)
                if l_ratio > 0.4:
                    if 60 <= dir_delta < 120:
                        forward_weight *= (0.5 / progress_factor)
                if b_ratio > 0.1:
                    if dir_delta >= 120:
                        forward_weight *= (0.1 / progress_factor)

            weight *= forward_weight

            if accumulated_length > 0:
                current_main_ratio = main_road_length / accumulated_length
                is_main = edge['highway_level'] <= main_road_level

                if current_main_ratio < main_road_ratio * 0.8 and is_main:
                    weight *= 1.5
                elif current_main_ratio > main_road_ratio * 1.2 and not is_main:
                    weight *= 1.5

            remaining_turns = num_turns - turn_count
            remaining_est_edges = (max_length - accumulated_length) / avg_edge_length
            if remaining_turns > 0 and remaining_est_edges > 0:
                needed_gap = remaining_est_edges / remaining_turns
                if needed_gap < cooldown * 1.5:
                    if last_bearing is not None:
                        bearing_diff = angular_delta_mod360(last_bearing, traversal_bearing)
                        if bearing_diff > turn_angle:
                            weight *= 1.5

            valid_candidates.append(ce_idx)
            weights.append(max(0.01, weight))

        if not valid_candidates or sum(weights) <= 0:
            break

        total_weight = sum(weights)
        probs = [w / total_weight for w in weights]
        r = random.random()
        cumsum = 0.0
        chosen_idx = 0
        for i, p in enumerate(probs):
            cumsum += p
            if r <= cumsum:
                chosen_idx = i
                break

        chosen_edge_idx = valid_candidates[chosen_idx]
        chosen_edge = edges[chosen_edge_idx]

        if chosen_edge['start_node'] == current_node:
            next_node = chosen_edge['end_node']
        else:
            next_node = chosen_edge['start_node']

        traversal_bearing = _get_traversal_bearing(chosen_edge, current_node)
        edge_length = chosen_edge['length_m']

        is_turn = False
        turn_angle_val = None
        if last_bearing is not None:
            bearing_diff = angular_delta_mod360(last_bearing, traversal_bearing)
            if bearing_diff > turn_angle:
                is_turn = True
                turn_angle_val = bearing_diff

        path.append({
            'type': 'edge',
            'idx': chosen_edge_idx,
            'length_m': chosen_edge['length_m'],
            'direction_deg': chosen_edge['direction_deg'],
            'highway_level': chosen_edge['highway_level'],
        })
        path.append({
            'type': 'node',
            'id': next_node,
            'position': nodes[next_node]['position'],
            'is_turn': is_turn,
            'turn_angle': turn_angle_val,
        })

        if is_turn:
            turns.append({
                'node_id': next_node,
                'position': nodes[next_node]['position'],
                'angle': turn_angle_val,
                'edge_index': len(path) // 2,
            })

        accumulated_length += edge_length
        edge_count += 1
        visited_nodes.add(next_node)

        dir_delta = angular_delta_mod360(default_dir, traversal_bearing)
        if dir_delta < 60:
            forward_length += edge_length
        elif dir_delta < 120:
            lateral_length += edge_length
        else:
            backward_length += edge_length

        if chosen_edge['highway_level'] <= main_road_level:
            main_road_length += edge_length

        if is_turn:
            turn_count += 1
            steps_since_last_turn = 0
        else:
            steps_since_last_turn += 1

        last_edge_idx = chosen_edge_idx
        last_bearing = traversal_bearing
        current_node = next_node

        if accumulated_length >= total_length_m * 0.95:
            if num_turns * 0.8 <= turn_count <= num_turns * 1.2:
                break

    if accumulated_length < min_length:
        return None, {}

    stats = {
        'total_length_m': accumulated_length,
        'turn_count': turn_count,
        'main_road_length_m': main_road_length,
        'main_road_ratio': main_road_length / accumulated_length if accumulated_length > 0 else 0,
        'forward_length_m': forward_length,
        'lateral_length_m': lateral_length,
        'backward_length_m': backward_length,
        'forward_ratio': forward_length / accumulated_length if accumulated_length > 0 else 0,
        'lateral_ratio': lateral_length / accumulated_length if accumulated_length > 0 else 0,
        'backward_ratio': backward_length / accumulated_length if accumulated_length > 0 else 0,
        'edge_count': edge_count,
        'default_dir': default_dir,
        'turns': turns,
    }

    return path, stats


def _validate_constraints(
    stats: Dict[str, Any],
    total_length_m: float,
    num_turns: int,
    main_road_ratio: float,
) -> bool:
    """Validate that path statistics meet constraints (±20% tolerance)."""
    if not stats:
        return False

    length = stats.get('total_length_m', 0)
    if not (total_length_m * 0.8 <= length <= total_length_m * 1.2):
        return False

    turns = stats.get('turn_count', 0)
    if not (num_turns * 0.8 <= turns <= num_turns * 1.2):
        return False

    ratio = stats.get('main_road_ratio', 0)
    if not (main_road_ratio * 0.8 <= ratio <= main_road_ratio * 1.2):
        return False

    forward_ratio = stats.get('forward_ratio', 0)
    if forward_ratio < 0.6 * 0.8:
        return False

    lateral_ratio = stats.get('lateral_ratio', 0)
    if lateral_ratio > 0.4 * 1.2:
        return False

    backward_ratio = stats.get('backward_ratio', 0)
    if backward_ratio > 0.1 * 1.2:
        return False

    return True
