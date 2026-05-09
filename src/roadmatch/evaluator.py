from __future__ import annotations

from typing import Any, Dict, Iterable, List

from roadmatch.graph import RoadGraph
from roadmatch.matcher import match_detections
from roadmatch.simulator import simulate_detections


def evaluate_seeds(graph: RoadGraph, config: Dict[str, Any], seeds: Iterable[int]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    top1_hits = 0
    top5_hits = 0
    total = 0

    for seed in seeds:
        detection = simulate_detections(graph, config, seed=seed)
        report, _ = match_detections(graph, detection, config)
        truth_nodes = tuple(str(item) for item in detection.truth.get("path_nodes", [])) if detection.truth else ()
        truth_length_m = float(detection.truth.get("road_length_m", 0.0)) if detection.truth else 0.0
        similarities = [
            route_similarity(
                truth_nodes,
                tuple(item.get("nodes", [])),
                truth_length_m,
                float(item.get("length_m", 0.0)),
            )
            for item in report.get("candidates", [])
        ]
        top1_hit = bool(similarities and similarities[0]["hit"])
        top5_hit = any(item["hit"] for item in similarities[:5])
        top1_hits += 1 if top1_hit else 0
        top5_hits += 1 if top5_hit else 0
        total += 1
        rows.append(
            {
                "seed": seed,
                "top1_hit": top1_hit,
                "top5_hit": top5_hit,
                "observed_event_count": len(detection.events),
                "truth_road_length_m": truth_length_m if detection.truth else None,
                "best_path_id": report["candidates"][0]["path_id"] if report.get("candidates") else None,
                "best_score": report["candidates"][0]["score"] if report.get("candidates") else None,
                "best_similarity": similarities[0] if similarities else None,
            }
        )

    return {
        "seeds": total,
        "hit_definition": {
            "node_coverage_threshold": 0.85,
            "edge_coverage_threshold": 0.75,
            "length_delta_ratio_threshold": 0.03,
        },
        "top1_hit_rate": top1_hits / total if total else 0.0,
        "top5_hit_rate": top5_hits / total if total else 0.0,
        "runs": rows,
    }


def route_similarity(
    truth_nodes: tuple[str, ...],
    candidate_nodes: tuple[str, ...],
    truth_length_m: float,
    candidate_length_m: float,
) -> Dict[str, Any]:
    if not truth_nodes or not candidate_nodes:
        return {
            "hit": False,
            "node_coverage": 0.0,
            "edge_coverage": 0.0,
            "length_delta_ratio": 1.0,
        }

    truth_node_set = set(truth_nodes)
    candidate_node_set = set(candidate_nodes)
    truth_edges = _edge_set(truth_nodes)
    candidate_edges = _edge_set(candidate_nodes)
    node_coverage = len(truth_node_set & candidate_node_set) / max(len(truth_node_set), 1)
    edge_coverage = len(truth_edges & candidate_edges) / max(len(truth_edges), 1)
    length_delta_ratio = abs(candidate_length_m - truth_length_m) / max(truth_length_m, 1.0)
    exact = truth_nodes == candidate_nodes
    equivalent = (
        length_delta_ratio <= 0.03
        and (node_coverage >= 0.85 or edge_coverage >= 0.75)
    )
    return {
        "hit": exact or equivalent,
        "exact": exact,
        "node_coverage": node_coverage,
        "edge_coverage": edge_coverage,
        "length_delta_ratio": length_delta_ratio,
    }


def _edge_set(nodes: tuple[str, ...]) -> set[tuple[str, str]]:
    edges = set()
    for index in range(len(nodes) - 1):
        a = nodes[index]
        b = nodes[index + 1]
        edges.add((a, b) if a <= b else (b, a))
    return edges
