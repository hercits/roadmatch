SLACK_INTERVAL_MIN_M = 50.0
SLACK_INTERVAL_MAX_M = 250.0
SLACK_INTERVAL_MEAN_M = 150.0
SLACK_AT_INTERSECTION_PROB = 0.40
SLACK_MERGE_MIN_GAP_M = 50.0
SLACK_BASE_LENGTH_MIN_M = 10.0
SLACK_BASE_LENGTH_MAX_M = 20.0
SLACK_EXTRA_PROB_PER_100M = 0.05
SLACK_EXTRA_MULTIPLIER = 2.0

LARGE_INTERSECTION_RECALL = 0.80
SMALL_INTERSECTION_RECALL = 0.30
FALSE_POSITIVE_RATE_DEG2 = 0.10

BASE_WIDTH_MIN_M = 15.0
BASE_WIDTH_MAX_M = 50.0
EDGE_TRAVERSAL_PROB = 0.50
HIGHWAY_WIDTH_RANGES: dict[int, tuple[float, float]] = {
    0: (30.0, 70.0),
    1: (30.0, 70.0),
    2: (20.0, 60.0),
    3: (20.0, 60.0),
    4: (15.0, 40.0),
    5: (15.0, 40.0),
    6: (10.0, 30.0),
    7: (10.0, 30.0),
    8: (5.0, 25.0),
    9: (5.0, 25.0),
}
DEFAULT_WIDTH_RANGE = (5.0, 15.0)

CONFIDENCE_WEIGHTS = (2, 6, 2)
CONFIDENCE_LABELS = ("medium", "medium_high", "high")
STRAIGHT_PRECISION: dict[str, float] = {
    "medium": 0.80,
    "medium_high": 0.90,
    "high": 0.97,
}
TURN_PRECISION: dict[str, float] = {
    "medium": 0.60,
    "medium_high": 0.75,
    "high": 0.90,
}
