from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

from roadmatch.geometry import point_interval_score
from roadmatch.models import CandidateEvent, CandidatePath, DetectionEvent, DetectionSet, EventAlignment


def length_score(
    candidate_length_m: float,
    observed_length_m: float,
    length_multiplier: float,
    tolerance_ratio: float,
) -> float:
    expected = max(candidate_length_m * length_multiplier, 1.0)
    sigma = max(expected * tolerance_ratio, 1.0)
    z = (observed_length_m - expected) / sigma
    return math.exp(-0.5 * z * z)


def movement_likelihood(candidate_movement: str, observed_movement: str) -> float:
    candidate = candidate_movement if candidate_movement in {"straight", "turn"} else "unknown"
    observed = observed_movement if observed_movement in {"straight", "turn"} else "unknown"
    if observed == "unknown" or candidate == "unknown":
        return 0.60
    if candidate == "straight":
        return 0.90 if observed == "straight" else 0.10
    return 0.40 if observed == "turn" else 0.60


def interchange_likelihood(candidate: Any, observed: Any) -> float:
    if observed in (None, "unknown") or candidate in (None, "unknown"):
        return 0.75
    return 1.0 if bool(candidate) == bool(observed) else 0.30


def single_event_score(
    observed: DetectionEvent,
    candidate: CandidateEvent,
    length_multiplier: float,
    position_softness_m: float,
    weights: Dict[str, float],
) -> Tuple[float, float, float, float]:
    candidate_cable_distance = candidate.distance_m * length_multiplier
    position = point_interval_score(candidate_cable_distance, observed.interval_m, position_softness_m)
    movement = movement_likelihood(candidate.movement, observed.movement)
    interchange = interchange_likelihood(candidate.interchange, observed.interchange)
    total_weight = (
        weights.get("event_position", 0.50)
        + weights.get("event_movement", 0.35)
        + weights.get("event_interchange", 0.15)
    )
    if total_weight <= 0:
        total_weight = 1.0
    score = (
        weights.get("event_position", 0.50) * position
        + weights.get("event_movement", 0.35) * movement
        + weights.get("event_interchange", 0.15) * interchange
    ) / total_weight
    return score, position, movement, interchange


def align_events(
    observed_events: Sequence[DetectionEvent],
    candidate_events: Sequence[CandidateEvent],
    length_multiplier: float,
    position_softness_m: float,
    weights: Dict[str, float],
) -> Tuple[float, List[EventAlignment]]:
    """Monotonic DP alignment. Extra candidate intersections are free to skip."""

    obs_count = len(observed_events)
    cand_count = len(candidate_events)
    if obs_count == 0:
        return 0.5, []

    dp = [[0.0 for _ in range(cand_count + 1)] for _ in range(obs_count + 1)]
    action = [["" for _ in range(cand_count + 1)] for _ in range(obs_count + 1)]
    match_cache: Dict[Tuple[int, int], Tuple[float, float, float, float]] = {}

    for i in range(1, obs_count + 1):
        action[i][0] = "skip_observed"

    for i in range(obs_count + 1):
        for j in range(1, cand_count + 1):
            best_score = dp[i][j - 1]
            best_action = "skip_candidate"

            if i > 0 and dp[i - 1][j] >= best_score:
                best_score = dp[i - 1][j]
                best_action = "skip_observed"

            if i > 0:
                event_score = single_event_score(
                    observed_events[i - 1],
                    candidate_events[j - 1],
                    length_multiplier=length_multiplier,
                    position_softness_m=position_softness_m,
                    weights=weights,
                )
                match_cache[(i - 1, j - 1)] = event_score
                matched_score = dp[i - 1][j - 1] + event_score[0]
                if matched_score >= best_score:
                    best_score = matched_score
                    best_action = "match"

            dp[i][j] = best_score
            action[i][j] = best_action

    alignments_by_obs: Dict[int, EventAlignment] = {}
    i = obs_count
    j = cand_count
    while i > 0 or j > 0:
        current_action = action[i][j] if i >= 0 and j >= 0 else ""
        if current_action == "match":
            score, position, movement, interchange = match_cache[(i - 1, j - 1)]
            alignments_by_obs[i - 1] = EventAlignment(
                observed_index=i - 1,
                candidate_index=j - 1,
                observed=observed_events[i - 1],
                candidate=candidate_events[j - 1],
                score=score,
                position_score=position,
                movement_score=movement,
                interchange_score=interchange,
            )
            i -= 1
            j -= 1
        elif current_action == "skip_candidate" and j > 0:
            j -= 1
        else:
            if i > 0:
                alignments_by_obs[i - 1] = EventAlignment(
                    observed_index=i - 1,
                    candidate_index=None,
                    observed=observed_events[i - 1],
                    candidate=None,
                    score=0.0,
                    position_score=0.0,
                    movement_score=0.0,
                    interchange_score=0.0,
                )
                i -= 1
            elif j > 0:
                j -= 1

    alignments = [alignments_by_obs[index] for index in sorted(alignments_by_obs)]
    normalized = dp[obs_count][cand_count] / max(obs_count, 1)
    return normalized, alignments


def score_candidate(
    detection: DetectionSet,
    candidate: CandidatePath,
    length_multiplier: float,
    tolerance_ratio: float,
    position_softness_m: float,
    weights: Dict[str, float],
) -> Dict[str, Any]:
    l_score = length_score(
        candidate.length_m,
        detection.observed_length_m,
        length_multiplier=length_multiplier,
        tolerance_ratio=tolerance_ratio,
    )
    event_score, alignments = align_events(
        detection.events,
        candidate.events,
        length_multiplier=length_multiplier,
        position_softness_m=position_softness_m,
        weights=weights,
    )
    total_weight = weights.get("length", 0.45) + weights.get("events", 0.55)
    total = (
        weights.get("length", 0.45) * l_score + weights.get("events", 0.55) * event_score
    ) / max(total_weight, 1e-9)
    expected_observed_length = candidate.length_m * length_multiplier
    return {
        "score": total,
        "length_score": l_score,
        "event_score": event_score,
        "expected_observed_length_m": expected_observed_length,
        "length_delta_m": detection.observed_length_m - expected_observed_length,
        "alignments": alignments,
        "matched_event_count": sum(1 for item in alignments if item.candidate is not None),
    }


def softmax_confidences(scores: Sequence[float], temperature: float = 0.15) -> List[float]:
    if not scores:
        return []
    max_score = max(scores)
    scale = max(temperature, 1e-6)
    values = [math.exp((score - max_score) / scale) for score in scores]
    total = sum(values)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores)
    return [value / total for value in values]
