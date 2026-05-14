from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from roadmatch.config import get_config
from roadmatch.errors import DetectionError, GraphError
from roadmatch.graph import RoadGraph
from roadmatch.models import CandidatePath, DetectionSet
from roadmatch.scoring import score_candidate, softmax_confidences


def match_detections(
    graph: RoadGraph,
    detection: DetectionSet,
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[CandidatePath]]:
    validate_detection(detection)

    start_node = graph.nearest_node(detection.start.lon, detection.start.lat)
    end_node = graph.nearest_node(detection.end.lon, detection.end.lat)
    generated_paths = int(get_config(config, ["matching", "generated_paths"], 120))
    top_k = int(get_config(config, ["matching", "top_k"], 10))
    turn_threshold = float(get_config(config, ["matching", "turn_threshold_degrees"], 35.0))
    length_multiplier = float(get_config(config, ["noise", "length_multiplier_mean"], 1.15))
    tolerance_ratio = float(get_config(config, ["noise", "length_tolerance_ratio"], 0.15))
    window_ratio = float(get_config(config, ["matching", "length_window_ratio"], 0.35))
    position_softness = float(get_config(config, ["matching", "event_position_softness_m"], 180.0))
    weights = dict(get_config(config, ["matching", "weights"], {}))

    search_graph, corridor_stats = _build_search_graph(
        graph,
        start_node,
        end_node,
        observed_length_m=detection.observed_length_m,
        length_multiplier=length_multiplier,
        window_ratio=window_ratio,
    )
    raw_paths = search_graph.k_shortest_paths(start_node, end_node, generated_paths)
    if not raw_paths:
        raise GraphError("No candidate paths generated")

    candidates: List[CandidatePath] = []
    for index, path in enumerate(raw_paths, start=1):
        candidate = graph.candidate_from_path(
            path,
            path_id=f"path_{index:03d}",
            turn_threshold_degrees=turn_threshold,
        )
        expected = candidate.length_m * length_multiplier
        if _within_length_window(expected, detection.observed_length_m, window_ratio):
            candidates.append(candidate)

    if not candidates:
        candidates = [
            search_graph.candidate_from_path(
                path,
                path_id=f"path_{index:03d}",
                turn_threshold_degrees=turn_threshold,
            )
            for index, path in enumerate(raw_paths, start=1)
        ]

    scored = []
    for candidate in candidates:
        metrics = score_candidate(
            detection,
            candidate,
            length_multiplier=length_multiplier,
            tolerance_ratio=tolerance_ratio,
            position_softness_m=position_softness,
            weights=weights,
        )
        scored.append((candidate, metrics))

    scored.sort(key=lambda item: item[1]["score"], reverse=True)
    top_scored = scored[:top_k]
    confidences = softmax_confidences([item[1]["score"] for item in top_scored])

    report_candidates = []
    top_candidates = []
    for rank, ((candidate, metrics), confidence) in enumerate(zip(top_scored, confidences), start=1):
        top_candidates.append(candidate)
        report_candidates.append(
            {
                "rank": rank,
                "path_id": candidate.path_id,
                "confidence": confidence,
                "score": metrics["score"],
                "length_m": candidate.length_m,
                "expected_observed_length_m": metrics["expected_observed_length_m"],
                "length_delta_m": metrics["length_delta_m"],
                "length_score": metrics["length_score"],
                "event_score": metrics["event_score"],
                "node_count": len(candidate.nodes),
                "candidate_event_count": len(candidate.events),
                "matched_event_count": metrics["matched_event_count"],
                "nodes": candidate.nodes,
                "event_alignment": [item.to_dict() for item in metrics["alignments"]],
            }
        )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start": detection.start.to_dict(),
        "end": detection.end.to_dict(),
        "snapped_start_node": start_node,
        "snapped_end_node": end_node,
        "observed_length_m": detection.observed_length_m,
        "observed_event_count": len(detection.events),
        "generated_path_count": len(raw_paths),
        "scored_path_count": len(candidates),
        "search_graph": corridor_stats,
        "top_k": len(report_candidates),
        "parameters": {
            "length_multiplier": length_multiplier,
            "length_tolerance_ratio": tolerance_ratio,
            "length_window_ratio": window_ratio,
            "turn_threshold_degrees": turn_threshold,
            "event_position_softness_m": position_softness,
            "weights": weights,
        },
        "candidates": report_candidates,
    }
    if detection.truth is not None:
        report["truth"] = detection.truth
    return report, top_candidates


def validate_detection(detection: DetectionSet) -> None:
    if detection.observed_length_m <= 0:
        raise DetectionError("observed_length_m must be positive")
    for index, event in enumerate(detection.events):
        lo, hi = event.interval_m
        if lo < 0 or hi < 0:
            raise DetectionError(f"Event {index} interval cannot be negative")
        if max(lo, hi) > detection.observed_length_m:
            raise DetectionError(
                f"Event {index} interval exceeds observed_length_m: {event.interval_m}"
            )
        if event.movement not in {"straight", "turn", "unknown"}:
            raise DetectionError(f"Event {index} has invalid movement: {event.movement}")


def _within_length_window(expected_length_m: float, observed_length_m: float, ratio: float) -> bool:
    tolerance = max(observed_length_m * ratio, 1.0)
    return abs(expected_length_m - observed_length_m) <= tolerance


def _build_search_graph(
    graph: RoadGraph,
    start_node: str,
    end_node: str,
    observed_length_m: float,
    length_multiplier: float,
    window_ratio: float,
) -> Tuple[RoadGraph, Dict[str, Any]]:
    approximate_road_length = observed_length_m / max(length_multiplier, 1e-9)
    max_road_length = approximate_road_length * (1.0 + max(window_ratio, 0.0))
    start_distances = graph.shortest_distances(start_node, cutoff_m=max_road_length)
    end_distances = graph.shortest_distances(end_node, cutoff_m=max_road_length)
    corridor_nodes = {
        node_id
        for node_id, start_distance in start_distances.items()
        if node_id in end_distances and start_distance + end_distances[node_id] <= max_road_length
    }
    corridor_nodes.update({start_node, end_node})
    if len(corridor_nodes) < 2 or len(corridor_nodes) == len(graph.nodes):
        return graph, {
            "mode": "full",
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "max_road_length_m": max_road_length,
        }

    subgraph = graph.induced_subgraph(corridor_nodes)
    return subgraph, {
        "mode": "length_corridor",
        "nodes": len(subgraph.nodes),
        "edges": len(subgraph.edges),
        "max_road_length_m": max_road_length,
        "original_nodes": len(graph.nodes),
        "original_edges": len(graph.edges),
    }
