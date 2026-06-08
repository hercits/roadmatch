from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from mock.event_simulator_config import (
    BASE_WIDTH_MAX_M,
    BASE_WIDTH_MIN_M,
    CONFIDENCE_LABELS,
    CONFIDENCE_WEIGHTS,
    DEFAULT_WIDTH_RANGE,
    EDGE_TRAVERSAL_PROB,
    FALSE_POSITIVE_RATE_DEG2,
    HIGHWAY_WIDTH_RANGES,
    LARGE_INTERSECTION_RECALL,
    SLACK_AT_INTERSECTION_PROB,
    SLACK_BASE_LENGTH_MAX_M,
    SLACK_BASE_LENGTH_MIN_M,
    SLACK_EXTRA_MULTIPLIER,
    SLACK_EXTRA_PROB_PER_100M,
    SLACK_INTERVAL_MAX_M,
    SLACK_INTERVAL_MEAN_M,
    SLACK_INTERVAL_MIN_M,
    SLACK_MERGE_MIN_GAP_M,
    SMALL_INTERSECTION_RECALL,
    STRAIGHT_PRECISION,
    TURN_PRECISION,
)


def simulate_detections(
    path: List[Dict[str, Any]],
    walkable_graph: Dict[str, Any],
    main_road_level: int = 6,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    graph_nodes = walkable_graph['nodes']
    graph_edges = walkable_graph['edges']

    path_nodes = _extract_path_nodes(path, graph_nodes, graph_edges)
    _classify_intersections(path_nodes, graph_nodes, graph_edges, main_road_level)

    detected = _detect_intersections(path_nodes, rng)
    slacks = _simulate_cable_slack(path_nodes, rng)
    slack_before = _compute_slack_cumulative(path_nodes, slacks)

    detected_intersections: List[Dict[str, Any]] = []
    turning_dict: Dict[str, Dict[str, Any]] = {}
    idx = 0

    for i, pn in enumerate(path_nodes):
        if not detected[i]:
            continue
        idx += 1
        center_m = round(pn['cumulative_dist_m'] + slack_before[i])
        width = _compute_intersection_widths(pn, graph_nodes, graph_edges, rng)
        half_w = round(width / 2)
        start_m = max(0, center_m - half_w)
        end_m = center_m + half_w

        detected_intersections.append({
            'id': idx,
            'start_m': start_m,
            'end_m': end_m,
            'center_m': center_m,
        })

        confidence = _sample_confidence(rng)
        is_turn = pn['is_turn']
        direction = _compute_turning_direction(is_turn, confidence, rng)
        turning_dict[str(idx)] = {
            'confidence_of_turning_types': confidence,
            'turning_directions': [direction],
        }

    total_slack = sum(s['length_m'] for s in slacks)
    path_length = round(path_nodes[-1]['cumulative_dist_m'] + total_slack)

    return {
        'path_length(meter)': path_length,
        'turning': turning_dict,
        'intersec_after_redund(meter)_l_m_s': [detected_intersections],
    }


def _extract_path_nodes(
    path: List[Dict[str, Any]],
    graph_nodes: Dict[str, Dict[str, Any]],
    graph_edges: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    cumulative = 0.0

    for i, entry in enumerate(path):
        if entry['type'] != 'node':
            continue

        node_id = entry['id']
        degree = len(graph_nodes[node_id]['edges'])
        is_turn = entry.get('is_turn', False)

        result.append({
            'node_id': node_id,
            'position': entry['position'],
            'cumulative_dist_m': cumulative,
            'degree': degree,
            'is_turn': is_turn,
            'edge_indices': graph_nodes[node_id]['edges'],
            'is_intersection_candidate': degree >= 3,
            'intersection_type': None,
        })

        if i + 1 < len(path) and path[i + 1]['type'] == 'edge':
            cumulative += path[i + 1]['length_m']

    return result


def _classify_intersections(
    path_nodes: List[Dict[str, Any]],
    graph_nodes: Dict[str, Dict[str, Any]],
    graph_edges: Dict[int, Dict[str, Any]],
    main_road_level: int,
) -> None:
    for pn in path_nodes:
        if pn['degree'] < 3:
            continue
        big_count = 0
        for eidx in pn['edge_indices']:
            edge = graph_edges[eidx]
            if edge['highway_level'] <= main_road_level:
                big_count += 1
        pn['intersection_type'] = 'large' if big_count >= 2 else 'small'


def _detect_intersections(
    path_nodes: List[Dict[str, Any]],
    rng: random.Random,
) -> List[bool]:
    detected = []
    for pn in path_nodes:
        if pn['intersection_type'] == 'large':
            detected.append(rng.random() < LARGE_INTERSECTION_RECALL)
        elif pn['intersection_type'] == 'small':
            detected.append(rng.random() < SMALL_INTERSECTION_RECALL)
        elif pn['degree'] == 2:
            detected.append(rng.random() < FALSE_POSITIVE_RATE_DEG2)
        else:
            detected.append(False)
    return detected


def _sample_truncated_exponential(
    mean: float, lo: float, hi: float, rng: random.Random
) -> float:
    while True:
        x = rng.expovariate(1.0 / mean)
        if lo <= x <= hi:
            return x


def _simulate_cable_slack(
    path_nodes: List[Dict[str, Any]],
    rng: random.Random,
) -> List[Dict[str, float]]:
    total_dist = path_nodes[-1]['cumulative_dist_m']
    slacks: List[Dict[str, float]] = []

    next_interval = _sample_truncated_exponential(
        SLACK_INTERVAL_MEAN_M, SLACK_INTERVAL_MIN_M, SLACK_INTERVAL_MAX_M, rng
    )
    walked = 0.0

    for i, pn in enumerate(path_nodes):
        node_pos = pn['cumulative_dist_m']

        if i > 0:
            edge_len = node_pos - path_nodes[i - 1]['cumulative_dist_m']
            while walked + edge_len >= next_interval:
                slack_pos = next_interval - walked + path_nodes[i - 1]['cumulative_dist_m']
                base_len = rng.uniform(SLACK_BASE_LENGTH_MIN_M, SLACK_BASE_LENGTH_MAX_M)
                slacks.append({'position_m': slack_pos, 'length_m': base_len})
                edge_len -= (next_interval - walked)
                walked = 0.0
                next_interval = _sample_truncated_exponential(
                    SLACK_INTERVAL_MEAN_M, SLACK_INTERVAL_MIN_M, SLACK_INTERVAL_MAX_M, rng
                )
            walked += edge_len

        if pn['degree'] >= 3 and rng.random() < SLACK_AT_INTERSECTION_PROB:
            if slacks and (node_pos - slacks[-1]['position_m']) < SLACK_MERGE_MIN_GAP_M:
                slacks[-1]['position_m'] = node_pos
            else:
                base_len = rng.uniform(SLACK_BASE_LENGTH_MIN_M, SLACK_BASE_LENGTH_MAX_M)
                slacks.append({'position_m': node_pos, 'length_m': base_len})
            walked = 0.0
            next_interval = _sample_truncated_exponential(
                SLACK_INTERVAL_MEAN_M, SLACK_INTERVAL_MIN_M, SLACK_INTERVAL_MAX_M, rng
            )

    for i, slack in enumerate(slacks):
        prev_pos = slacks[i - 1]['position_m'] if i > 0 else 0.0
        next_pos = slacks[i + 1]['position_m'] if i < len(slacks) - 1 else total_dist
        avg_spacing = (slack['position_m'] - prev_pos + next_pos - slack['position_m']) / 2.0
        extra_prob = avg_spacing * SLACK_EXTRA_PROB_PER_100M / 100.0
        if rng.random() < extra_prob:
            slack['length_m'] *= SLACK_EXTRA_MULTIPLIER

    return slacks


def _compute_slack_cumulative(
    path_nodes: List[Dict[str, Any]],
    slacks: List[Dict[str, float]],
) -> List[float]:
    result = []
    for pn in path_nodes:
        cum = sum(s['length_m'] for s in slacks if s['position_m'] <= pn['cumulative_dist_m'])
        result.append(cum)
    return result


def _compute_intersection_widths(
    pn: Dict[str, Any],
    graph_nodes: Dict[str, Dict[str, Any]],
    graph_edges: Dict[int, Dict[str, Any]],
    rng: random.Random,
) -> float:
    base_width = rng.uniform(BASE_WIDTH_MIN_M, BASE_WIDTH_MAX_M)
    total_width = base_width

    for eidx in pn['edge_indices']:
        if rng.random() < EDGE_TRAVERSAL_PROB:
            edge = graph_edges[eidx]
            level = edge['highway_level']
            width_range = HIGHWAY_WIDTH_RANGES.get(level, DEFAULT_WIDTH_RANGE)
            total_width += rng.uniform(width_range[0], width_range[1])

    return total_width


def _sample_confidence(rng: random.Random) -> str:
    total = sum(CONFIDENCE_WEIGHTS)
    r = rng.random() * total
    cumsum = 0.0
    for label, weight in zip(CONFIDENCE_LABELS, CONFIDENCE_WEIGHTS):
        cumsum += weight
        if r <= cumsum:
            return label
    return CONFIDENCE_LABELS[-1]


def _compute_turning_direction(
    is_turn: bool, confidence: str, rng: random.Random
) -> str:
    if is_turn:
        precision = TURN_PRECISION[confidence]
        if rng.random() < precision:
            return "left_or_right"
        else:
            return "straight"
    else:
        precision = STRAIGHT_PRECISION[confidence]
        if rng.random() < precision:
            return "straight"
        else:
            return "left_or_right"
