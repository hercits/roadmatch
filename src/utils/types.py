from typing import Tuple

Coordinate = Tuple[float, float]
"""地理坐标 (longitude, latitude)。"""

HIGHWAY_LEVEL: dict[str, int] = {
    "motorway": 0,
    "motorway_link": 1,
    "trunk": 2,
    "trunk_link": 3,
    "primary": 4,
    "primary_link": 5,
    "secondary": 6,
    "secondary_link": 7,
    "tertiary": 8,
    "tertiary_link": 9,
    "residential": 10,
    "living_street": 12,
    "unclassified": 14,
}
"""OSM highway 等级映射，数值越小等级越高。"""
