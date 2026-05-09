import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadmatch.geometry import bearing_degrees, classify_turn, point_interval_score, polyline_length_m


class GeometryTests(unittest.TestCase):
    def test_bearing_and_turn_classification(self):
        north = bearing_degrees((0.0, 0.0), (0.0, 1.0))
        east = bearing_degrees((0.0, 0.0), (1.0, 0.0))

        self.assertAlmostEqual(north, 0.0, delta=0.5)
        self.assertAlmostEqual(east, 90.0, delta=0.5)
        self.assertEqual(classify_turn(0.0, 12.0, threshold_degrees=35.0), "straight")
        self.assertEqual(classify_turn(0.0, 90.0, threshold_degrees=35.0), "turn")

    def test_interval_score(self):
        self.assertEqual(point_interval_score(105.0, (100.0, 120.0), 50.0), 1.0)
        self.assertGreater(point_interval_score(90.0, (100.0, 120.0), 50.0), 0.8)
        self.assertLess(point_interval_score(0.0, (100.0, 120.0), 20.0), 0.01)

    def test_polyline_length_is_positive(self):
        length = polyline_length_m([(0.0, 0.0), (0.01, 0.0)])
        self.assertTrue(math.isfinite(length))
        self.assertGreater(length, 1000.0)


if __name__ == "__main__":
    unittest.main()
