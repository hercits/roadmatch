from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from roadmatch.config import ensure_project_dirs, load_config, output_path, project_path
from roadmatch.data import fetch_osm_data, graph_stats, load_graph
from roadmatch.errors import RoadmatchError
from roadmatch.evaluator import evaluate_seeds
from roadmatch.matcher import match_detections
from roadmatch.models import DetectionSet
from roadmatch.simulator import simulate_detections
from roadmatch.visualize import write_candidates_geojson, write_map_html


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RoadmatchError as exc:
        print(f"roadmatch: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="roadmatch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Download and cache OSM road data")
    fetch.add_argument("--config", required=True)
    fetch.set_defaults(func=cmd_fetch_data)

    simulate = subparsers.add_parser("simulate", help="Generate noisy test detections")
    simulate.add_argument("--config", required=True)
    simulate.add_argument("--seed", type=int, default=42)
    simulate.set_defaults(func=cmd_simulate)

    match = subparsers.add_parser("match", help="Match detections to Top-K road paths")
    match.add_argument("--config", required=True)
    match.add_argument("--detections", required=True)
    match.set_defaults(func=cmd_match)

    demo = subparsers.add_parser("run-demo", help="Fetch data if needed, simulate, and match")
    demo.add_argument("--config", required=True)
    demo.add_argument("--seed", type=int, default=42)
    demo.set_defaults(func=cmd_run_demo)

    evaluate = subparsers.add_parser("evaluate", help="Run Monte Carlo evaluation")
    evaluate.add_argument("--config", required=True)
    evaluate.add_argument("--seeds", type=int, default=50)
    evaluate.set_defaults(func=cmd_evaluate)

    return parser


def cmd_fetch_data(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ensure_project_dirs(config)
    graph = fetch_osm_data(config)
    stats = graph_stats(graph)
    print(
        f"Fetched graph: {stats['nodes']} nodes, {stats['edges']} edges, "
        f"{stats['total_edge_length_m']:.0f} total edge meters"
    )


def cmd_simulate(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ensure_project_dirs(config)
    graph = load_graph(config)
    detection = simulate_detections(graph, config, seed=args.seed)
    detections_path = output_path(config, "detections.json")
    _write_json(detection.to_dict(include_truth=True), detections_path)
    print(f"Wrote detections: {detections_path}")


def cmd_match(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ensure_project_dirs(config)
    graph = load_graph(config)
    detection = _read_detection(Path(args.detections))
    _run_match_outputs(graph, detection, config)


def cmd_run_demo(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ensure_project_dirs(config)
    road_graph_path = project_path(config, "road_graph_json", "road_graph.json")
    graphml_path = project_path(config, "graphml", "graph.graphml")
    if not road_graph_path.exists() and not graphml_path.exists():
        print("No road graph cache found; fetching OSM data...")
        fetch_osm_data(config)

    graph = load_graph(config)
    detection = simulate_detections(graph, config, seed=args.seed)
    detections_path = output_path(config, "detections.json")
    _write_json(detection.to_dict(include_truth=True), detections_path)
    print(f"Wrote detections: {detections_path}")
    _run_match_outputs(graph, detection, config)


def cmd_evaluate(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    ensure_project_dirs(config)
    graph = load_graph(config)
    results = evaluate_seeds(graph, config, range(args.seeds))
    evaluation_path = output_path(config, "evaluation.json")
    _write_json(results, evaluation_path)
    print(
        f"Wrote evaluation: {evaluation_path} "
        f"(Top-1={results['top1_hit_rate']:.1%}, Top-5={results['top5_hit_rate']:.1%})"
    )


def _run_match_outputs(graph: Any, detection: DetectionSet, config: Dict[str, Any]) -> None:
    report, candidates = match_detections(graph, detection, config)
    report_path = output_path(config, "match_report.json")
    candidates_path = output_path(config, "candidates.geojson")
    map_path = output_path(config, "map.html")
    _write_json(report, report_path)
    write_candidates_geojson(candidates, report, candidates_path)
    write_map_html(graph, candidates, report, detection, map_path)
    print(f"Wrote report: {report_path}")
    print(f"Wrote candidates: {candidates_path}")
    print(f"Wrote map: {map_path}")


def _read_detection(path: Path) -> DetectionSet:
    return DetectionSet.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _write_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
