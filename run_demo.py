"""Local entrypoint for running the Roadmatch Shanghai demo.

Usage:
    python run_demo.py
    python run_demo.py --seed 7
    python run_demo.py --config configs/demo_shanghai.yaml --skip-fetch
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from roadmatch.config import ensure_project_dirs, load_config, output_path, project_path
from roadmatch.data import fetch_osm_data, load_graph
from roadmatch.matcher import match_detections
from roadmatch.simulator import simulate_detections
from roadmatch.visualize import write_candidates_geojson, write_map_html


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    ensure_project_dirs(config)

    road_graph_path = project_path(config, "road_graph_json", "road_graph.json")
    graphml_path = project_path(config, "graphml", "graph.graphml")
    if not args.skip_fetch and not road_graph_path.exists() and not graphml_path.exists():
        print("No cached road graph found. Fetching OpenStreetMap data...")
        fetch_osm_data(config)

    graph = load_graph(config)
    detection = simulate_detections(graph, config, seed=args.seed)
    report, candidates = match_detections(graph, detection, config)

    detections_path = output_path(config, "detections.json")
    report_path = output_path(config, "match_report.json")
    candidates_path = output_path(config, "candidates.geojson")
    map_path = output_path(config, "map.html")

    write_json(detection.to_dict(include_truth=True), detections_path)
    write_json(report, report_path)
    write_candidates_geojson(candidates, report, candidates_path)
    write_map_html(graph, candidates, report, detection, map_path)

    best = report["candidates"][0] if report.get("candidates") else None
    print("Roadmatch demo finished.")
    print(f"Detections: {detections_path}")
    print(f"Report:     {report_path}")
    print(f"GeoJSON:    {candidates_path}")
    print(f"Map:        {map_path}")
    if best:
        print(
            "Best path:  "
            f"{best['path_id']} score={best['score']:.4f} "
            f"confidence={best['confidence']:.2%} "
            f"length={best['length_m']:.1f}m"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Roadmatch demo.")
    parser.add_argument("--config", default="configs/demo_shanghai.yaml")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Require cached road data instead of fetching OSM data if missing.",
    )
    return parser.parse_args()


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
