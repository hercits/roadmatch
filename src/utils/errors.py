class RoadmatchError(RuntimeError):
    """Roadmatch 基础异常。"""


class ConfigError(RoadmatchError):
    """配置缺失或无效。"""


class OSMFetchError(RoadmatchError):
    """OSM 路网数据拉取失败。"""


class GraphError(RoadmatchError):
    """路网图无法满足请求的操作。"""
