from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


Movement = str
InterchangeValue = Optional[bool]


@dataclass(frozen=True)
class Location:
    name: str
    lon: float
    lat: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Location":
        return cls(
            name=str(data.get("name", "")),
            lon=float(data["lon"]),
            lat=float(data["lat"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionEvent:
    interval_m: Tuple[float, float]
    movement: Movement
    interchange: InterchangeValue = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectionEvent":
        interval = data["interval_m"]
        interchange = data.get("interchange")
        interchange = _parse_interchange(interchange)
        return cls(
            interval_m=(float(interval[0]), float(interval[1])),
            movement=str(data.get("movement", "unknown")),
            interchange=interchange,
        )

    def to_dict(self) -> Dict[str, Any]:
        interchange: Any = self.interchange
        if interchange is None:
            interchange = "unknown"
        return {
            "interval_m": [self.interval_m[0], self.interval_m[1]],
            "movement": self.movement,
            "interchange": interchange,
        }


def _parse_interchange(value: Any) -> InterchangeValue:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "unknown", "none", "null"}:
        return None
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return bool(value)


@dataclass
class DetectionSet:
    start: Location
    end: Location
    observed_length_m: float
    events: List[DetectionEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    truth: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectionSet":
        return cls(
            start=Location.from_dict(data["start"]),
            end=Location.from_dict(data["end"]),
            observed_length_m=float(data["observed_length_m"]),
            events=[DetectionEvent.from_dict(item) for item in data.get("events", [])],
            metadata=dict(data.get("metadata", {})),
            truth=data.get("truth"),
        )

    def to_dict(self, include_truth: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "observed_length_m": self.observed_length_m,
            "events": [event.to_dict() for event in self.events],
            "metadata": self.metadata,
        }
        if include_truth and self.truth is not None:
            payload["truth"] = self.truth
        return payload


@dataclass(frozen=True)
class CandidateEvent:
    node_id: str
    distance_m: float
    movement: Movement
    interchange: InterchangeValue
    bearing_in: float
    bearing_out: float

    def to_dict(self) -> Dict[str, Any]:
        interchange: Any = self.interchange
        if interchange is None:
            interchange = "unknown"
        return {
            "node_id": self.node_id,
            "distance_m": self.distance_m,
            "movement": self.movement,
            "interchange": interchange,
            "bearing_in": self.bearing_in,
            "bearing_out": self.bearing_out,
        }


@dataclass
class CandidatePath:
    path_id: str
    nodes: List[str]
    length_m: float
    geometry: List[Tuple[float, float]]
    events: List[CandidateEvent]

    def to_summary(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "node_count": len(self.nodes),
            "length_m": self.length_m,
            "event_count": len(self.events),
        }


@dataclass(frozen=True)
class EventAlignment:
    observed_index: int
    candidate_index: Optional[int]
    observed: DetectionEvent
    candidate: Optional[CandidateEvent]
    score: float
    position_score: float
    movement_score: float
    interchange_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observed_index": self.observed_index,
            "candidate_index": self.candidate_index,
            "observed": self.observed.to_dict(),
            "candidate": self.candidate.to_dict() if self.candidate is not None else None,
            "score": self.score,
            "position_score": self.position_score,
            "movement_score": self.movement_score,
            "interchange_score": self.interchange_score,
        }
