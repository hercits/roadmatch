from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from roadmatch.config import get_config, required_config
from roadmatch.errors import GraphError
from roadmatch.graph import RoadGraph
from roadmatch.models import DetectionEvent, DetectionSet, Location


def simulate_detections(
    graph: RoadGraph,
    config: Dict[str, Any],
    seed: int = 42,
) -> DetectionSet:
    rng = random.Random(seed)
    start, end, path = choose_demo_route(graph, config)

    turn_threshold = float(get_config(config, ["matching", "turn_threshold_degrees"], 35.0))
    candidate = graph.candidate_from_path(
        path,
        path_id="truth",
        turn_threshold_degrees=turn_threshold,
    )

    mean_multiplier = float(get_config(config, ["noise", "length_multiplier_mean"], 1.15))
    std_multiplier = float(get_config(config, ["noise", "length_multiplier_std"], 0.03))
    length_multiplier = max(0.1, rng.gauss(mean_multiplier, std_multiplier))
    observed_length_m = candidate.length_m * length_multiplier

    detected_ratio = float(get_config(config, ["noise", "detected_intersection_ratio"], 0.30))
    interval_half_width_m = float(get_config(config, ["noise", "interval_half_width_m"], 90.0))
    interval_jitter_m = float(get_config(config, ["noise", "interval_jitter_m"], 35.0))
    straight_accuracy = float(get_config(config, ["noise", "straight_accuracy"], 0.90))
    turn_accuracy = float(get_config(config, ["noise", "turn_accuracy"], 0.40))

    sampled_events = [event for event in candidate.events if rng.random() <= detected_ratio]
    if not sampled_events and candidate.events:
        sampled_events = [rng.choice(candidate.events)]
    sampled_events.sort(key=lambda event: event.distance_m)

    observed_events: List[DetectionEvent] = []
    truth_events = []
    for event in sampled_events:
        center = event.distance_m * length_multiplier + rng.gauss(0.0, interval_jitter_m)
        lo = max(0.0, center - interval_half_width_m)
        hi = min(observed_length_m, center + interval_half_width_m)
        movement = _observe_movement(event.movement, straight_accuracy, turn_accuracy, rng)
        observed_events.append(
            DetectionEvent(
                interval_m=(lo, hi),
                movement=movement,
                interchange=event.interchange,
            )
        )
        truth_events.append(event.to_dict())

    return DetectionSet(
        start=start,
        end=end,
        observed_length_m=observed_length_m,
        events=observed_events,
        metadata={
            "seed": seed,
            "length_multiplier": length_multiplier,
            "noise_model": {
                "detected_intersection_ratio": detected_ratio,
                "straight_accuracy": straight_accuracy,
                "turn_accuracy": turn_accuracy,
            },
        },
        truth={
            "path_nodes": candidate.nodes,
            "road_length_m": candidate.length_m,
            "observed_length_m": observed_length_m,
            "event_count": len(candidate.events),
            "sampled_event_count": len(observed_events),
            "events": truth_events,
        },
    )


def choose_demo_route(graph: RoadGraph, config: Dict[str, Any]) -> Tuple[Location, Location, List[str]]:
    start = Location.from_dict(required_config(config, ["demo", "start"]))
    end_candidates = required_config(config, ["demo", "end_candidates"])
    target = float(get_config(config, ["demo", "target_length_m"], 20_000.0))
    target_min = float(get_config(config, ["demo", "target_min_m"], 18_000.0))
    target_max = float(get_config(config, ["demo", "target_max_m"], 24_000.0))

    start_node = graph.nearest_node(start.lon, start.lat)
    route_options = []
    for item in end_candidates:
        end = Location.from_dict(item)
        end_node = graph.nearest_node(end.lon, end.lat)
        try:
            path = graph.shortest_path(start_node, end_node)
        except GraphError:
            continue
        length = graph.path_length(path)
        in_target_window = target_min <= length <= target_max
        route_options.append((0 if in_target_window else 1, abs(length - target), length, end, path))

    if not route_options:
        raise GraphError("No reachable demo endpoint candidates found")

    route_options.sort(key=lambda item: (item[0], item[1]))
    _, _, _, end, path = route_options[0]
    return start, end, path


def _observe_movement(
    true_movement: str,
    straight_accuracy: float,
    turn_accuracy: float,
    rng: random.Random,
) -> str:
    if true_movement == "straight":
        return "straight" if rng.random() <= straight_accuracy else "turn"
    if true_movement == "turn":
        return "turn" if rng.random() <= turn_accuracy else "straight"
    return "unknown"
