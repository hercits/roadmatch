import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadmatch.graph import RoadGraph
from roadmatch.matcher import match_detections
from roadmatch.models import DetectionEvent, DetectionSet, Location
from roadmatch.visualize import write_candidates_geojson, write_map_html


def fixture_graph():
    graph = RoadGraph()
    # Coordinates are simple lon/lat positions for bearing; explicit lengths control routing.
    for node_id, lon, lat in [
        ("a", 0.00, 0.00),
        ("b", 0.01, 0.00),
        ("c", 0.02, 0.00),
        ("d", 0.02, 0.01),
        ("e", 0.03, 0.01),
        ("x", 0.01, 0.02),
        ("y", 0.025, 0.025),
    ]:
        graph.add_node(node_id, lon, lat)

    graph.add_edge("a", "b", length_m=1000.0, coords=[(0.00, 0.00), (0.01, 0.00)])
    graph.add_edge("b", "c", length_m=1000.0, coords=[(0.01, 0.00), (0.02, 0.00)])
    graph.add_edge("c", "d", length_m=1000.0, coords=[(0.02, 0.00), (0.02, 0.01)])
    graph.add_edge("d", "e", length_m=1000.0, coords=[(0.02, 0.01), (0.03, 0.01)])

    graph.add_edge("a", "x", length_m=1900.0, coords=[(0.00, 0.00), (0.01, 0.02)])
    graph.add_edge("x", "y", length_m=1900.0, coords=[(0.01, 0.02), (0.025, 0.025)])
    graph.add_edge("y", "e", length_m=1900.0, coords=[(0.025, 0.025), (0.03, 0.01)])
    return graph


class IntegrationFixtureTests(unittest.TestCase):
    def test_noisy_detection_recovers_true_path_top1(self):
        graph = fixture_graph()
        detection = DetectionSet(
            start=Location("start", 0.0, 0.0),
            end=Location("end", 0.03, 0.01),
            observed_length_m=4600.0,
            events=[
                DetectionEvent(interval_m=(2250.0, 2350.0), movement="turn", interchange=False)
            ],
            truth={"path_nodes": ["a", "b", "c", "d", "e"]},
        )
        config = {
            "noise": {
                "length_multiplier_mean": 1.15,
                "length_tolerance_ratio": 0.15,
            },
            "matching": {
                "generated_paths": 10,
                "top_k": 3,
                "length_window_ratio": 0.50,
                "turn_threshold_degrees": 35,
                "event_position_softness_m": 150,
                "weights": {
                    "length": 0.45,
                    "events": 0.55,
                    "event_position": 0.50,
                    "event_movement": 0.35,
                    "event_interchange": 0.15,
                },
            },
        }

        report, _ = match_detections(graph, detection, config)
        self.assertEqual(report["candidates"][0]["nodes"], ["a", "b", "c", "d", "e"])
        self.assertGreater(report["candidates"][0]["confidence"], 0.5)

    def test_geojson_and_html_outputs_are_written(self):
        graph = fixture_graph()
        detection = DetectionSet(
            start=Location("start", 0.0, 0.0),
            end=Location("end", 0.03, 0.01),
            observed_length_m=4600.0,
            events=[
                DetectionEvent(interval_m=(2250.0, 2350.0), movement="turn", interchange=False)
            ],
        )
        config = {
            "noise": {"length_multiplier_mean": 1.15, "length_tolerance_ratio": 0.15},
            "matching": {
                "generated_paths": 10,
                "top_k": 3,
                "length_window_ratio": 0.50,
                "turn_threshold_degrees": 35,
                "event_position_softness_m": 150,
                "weights": {
                    "length": 0.45,
                    "events": 0.55,
                    "event_position": 0.50,
                    "event_movement": 0.35,
                    "event_interchange": 0.15,
                },
            },
        }
        report, candidates = match_detections(graph, detection, config)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            geojson_path = tmp_path / "candidates.geojson"
            html_path = tmp_path / "map.html"
            write_candidates_geojson(candidates, report, geojson_path)
            write_map_html(graph, candidates, report, detection, html_path)
            self.assertIn("FeatureCollection", geojson_path.read_text(encoding="utf-8"))
            self.assertIn("Roadmatch", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
