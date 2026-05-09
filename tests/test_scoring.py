import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roadmatch.models import CandidateEvent, DetectionEvent
from roadmatch.scoring import align_events, length_score, movement_likelihood


class ScoringTests(unittest.TestCase):
    def test_length_score_peaks_at_expected_multiplier(self):
        score = length_score(
            candidate_length_m=1000.0,
            observed_length_m=1150.0,
            length_multiplier=1.15,
            tolerance_ratio=0.15,
        )
        self.assertAlmostEqual(score, 1.0, delta=1e-9)

    def test_movement_confusion_matrix(self):
        self.assertEqual(movement_likelihood("straight", "straight"), 0.90)
        self.assertEqual(movement_likelihood("straight", "turn"), 0.10)
        self.assertEqual(movement_likelihood("turn", "turn"), 0.40)
        self.assertEqual(movement_likelihood("turn", "straight"), 0.60)

    def test_alignment_skips_unobserved_candidate_events(self):
        observed = [
            DetectionEvent(interval_m=(2250.0, 2350.0), movement="turn", interchange=False)
        ]
        candidates = [
            CandidateEvent("b", 1000.0, "straight", False, 90.0, 90.0),
            CandidateEvent("c", 2000.0, "turn", False, 90.0, 0.0),
            CandidateEvent("d", 3000.0, "straight", False, 0.0, 0.0),
        ]
        score, alignments = align_events(
            observed,
            candidates,
            length_multiplier=1.15,
            position_softness_m=150.0,
            weights={
                "event_position": 0.50,
                "event_movement": 0.35,
                "event_interchange": 0.15,
            },
        )
        self.assertGreater(score, 0.75)
        self.assertEqual(alignments[0].candidate.node_id, "c")


if __name__ == "__main__":
    unittest.main()
