class RoadmatchError(RuntimeError):
    """Base exception for user-facing Roadmatch failures."""


class ConfigError(RoadmatchError):
    """Configuration is missing or invalid."""


class GraphError(RoadmatchError):
    """Road graph cannot satisfy the requested operation."""


class DetectionError(RoadmatchError):
    """Detection input is invalid."""
