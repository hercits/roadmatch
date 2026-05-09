from __future__ import annotations

import heapq
import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from roadmatch.errors import GraphError
from roadmatch.geometry import (
    Coordinate,
    bearing_degrees,
    classify_turn,
    dedupe_joined_coords,
    haversine_m,
    orient_coords,
    polyline_length_m,
)
from roadmatch.models import CandidateEvent, CandidatePath


@dataclass
class Node:
    node_id: str
    lon: float
    lat: float
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def coord(self) -> Coordinate:
        return self.lon, self.lat


@dataclass
class Edge:
    edge_id: str
    u: str
    v: str
    length_m: float
    coords: List[Coordinate]
    attrs: Dict[str, Any] = field(default_factory=dict)

    def other(self, node_id: str) -> str:
        if node_id == self.u:
            return self.v
        if node_id == self.v:
            return self.u
        raise KeyError(node_id)


class RoadGraph:
    """A lightweight undirected road graph optimized for cable route matching."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[Tuple[str, str], Edge] = {}
        self.adjacency: Dict[str, Set[str]] = {}

    def add_node(self, node_id: Any, lon: float, lat: float, attrs: Optional[Dict[str, Any]] = None) -> None:
        key = str(node_id)
        self.nodes[key] = Node(key, float(lon), float(lat), dict(attrs or {}))
        self.adjacency.setdefault(key, set())

    def add_edge(
        self,
        u: Any,
        v: Any,
        length_m: Optional[float] = None,
        coords: Optional[Sequence[Coordinate]] = None,
        attrs: Optional[Dict[str, Any]] = None,
        edge_id: Optional[str] = None,
    ) -> None:
        u_key = str(u)
        v_key = str(v)
        if u_key == v_key:
            return
        if u_key not in self.nodes or v_key not in self.nodes:
            raise GraphError("Cannot add edge before both endpoint nodes exist")

        edge_key = canonical_edge_key(u_key, v_key)
        fallback_coords = [self.nodes[u_key].coord, self.nodes[v_key].coord]
        clean_coords = list(coords or fallback_coords)
        length = float(length_m) if length_m is not None else polyline_length_m(clean_coords)
        if length <= 0:
            length = haversine_m(self.nodes[u_key].coord, self.nodes[v_key].coord)
        existing = self.edges.get(edge_key)
        if existing is not None and existing.length_m <= length:
            return

        self.edges[edge_key] = Edge(
            edge_id=edge_id or f"{u_key}-{v_key}",
            u=edge_key[0],
            v=edge_key[1],
            length_m=length,
            coords=clean_coords,
            attrs=dict(attrs or {}),
        )
        self.adjacency.setdefault(u_key, set()).add(v_key)
        self.adjacency.setdefault(v_key, set()).add(u_key)

    def neighbors(self, node_id: str) -> Iterable[str]:
        return self.adjacency.get(str(node_id), set())

    def edge_between(self, u: str, v: str) -> Edge:
        edge = self.edges.get(canonical_edge_key(str(u), str(v)))
        if edge is None:
            raise GraphError(f"No edge between {u} and {v}")
        return edge

    def node_degree(self, node_id: str) -> int:
        return len(self.adjacency.get(str(node_id), set()))

    def nearest_node(self, lon: float, lat: float) -> str:
        if not self.nodes:
            raise GraphError("Road graph is empty")
        target = (float(lon), float(lat))
        return min(self.nodes.values(), key=lambda node: haversine_m(node.coord, target)).node_id

    def path_length(self, path: Sequence[str]) -> float:
        if len(path) < 2:
            return 0.0
        return sum(self.edge_between(path[i], path[i + 1]).length_m for i in range(len(path) - 1))

    def oriented_edge_coords(self, u: str, v: str) -> List[Coordinate]:
        edge = self.edge_between(u, v)
        return orient_coords(edge.coords, self.nodes[str(u)].coord, self.nodes[str(v)].coord)

    def path_geometry(self, path: Sequence[str]) -> List[Coordinate]:
        parts = [self.oriented_edge_coords(path[i], path[i + 1]) for i in range(len(path) - 1)]
        return dedupe_joined_coords(parts)

    def shortest_path(
        self,
        source: str,
        target: str,
        banned_nodes: Optional[Set[str]] = None,
        banned_edges: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[str]:
        source = str(source)
        target = str(target)
        if source not in self.nodes or target not in self.nodes:
            raise GraphError("Source or target node is missing from graph")

        banned_nodes = set(banned_nodes or set())
        banned_edges = set(banned_edges or set())
        if source in banned_nodes or target in banned_nodes:
            raise GraphError("Source or target is banned")

        counter = itertools.count()
        queue: List[Tuple[float, int, str]] = [(0.0, next(counter), source)]
        distances: Dict[str, float] = {source: 0.0}
        previous: Dict[str, str] = {}

        while queue:
            distance, _, node_id = heapq.heappop(queue)
            if distance > distances.get(node_id, float("inf")):
                continue
            if node_id == target:
                return _reconstruct_path(previous, source, target)

            for neighbor in self.neighbors(node_id):
                if neighbor in banned_nodes:
                    continue
                if (node_id, neighbor) in banned_edges:
                    continue
                edge = self.edge_between(node_id, neighbor)
                next_distance = distance + edge.length_m
                if next_distance >= distances.get(neighbor, float("inf")):
                    continue
                distances[neighbor] = next_distance
                previous[neighbor] = node_id
                heapq.heappush(queue, (next_distance, next(counter), neighbor))

        raise GraphError(f"No path found between {source} and {target}")

    def shortest_distances(self, source: str, cutoff_m: Optional[float] = None) -> Dict[str, float]:
        source = str(source)
        if source not in self.nodes:
            raise GraphError(f"Source node is missing from graph: {source}")

        counter = itertools.count()
        queue: List[Tuple[float, int, str]] = [(0.0, next(counter), source)]
        distances: Dict[str, float] = {source: 0.0}

        while queue:
            distance, _, node_id = heapq.heappop(queue)
            if distance > distances.get(node_id, float("inf")):
                continue
            if cutoff_m is not None and distance > cutoff_m:
                continue

            for neighbor in self.neighbors(node_id):
                edge = self.edge_between(node_id, neighbor)
                next_distance = distance + edge.length_m
                if cutoff_m is not None and next_distance > cutoff_m:
                    continue
                if next_distance >= distances.get(neighbor, float("inf")):
                    continue
                distances[neighbor] = next_distance
                heapq.heappush(queue, (next_distance, next(counter), neighbor))

        return distances

    def induced_subgraph(self, node_ids: Set[str]) -> "RoadGraph":
        subset = {str(node_id) for node_id in node_ids}
        graph = RoadGraph()
        for node_id in subset:
            node = self.nodes.get(node_id)
            if node is not None:
                graph.add_node(node.node_id, node.lon, node.lat, attrs=node.attrs)
        for edge in self.edges.values():
            if edge.u in subset and edge.v in subset:
                graph.add_edge(
                    edge.u,
                    edge.v,
                    length_m=edge.length_m,
                    coords=edge.coords,
                    attrs=edge.attrs,
                    edge_id=edge.edge_id,
                )
        return graph

    def k_shortest_paths(self, source: str, target: str, k: int) -> List[List[str]]:
        """Generate up to k loopless paths with Yen's algorithm."""

        if k <= 0:
            return []

        first = self.shortest_path(source, target)
        accepted: List[List[str]] = [first]
        candidates: List[Tuple[float, int, List[str]]] = []
        seen = {tuple(first)}
        counter = itertools.count()

        for path_index in range(1, k):
            previous = accepted[path_index - 1]
            for spur_index in range(len(previous) - 1):
                spur_node = previous[spur_index]
                root_path = previous[: spur_index + 1]
                root_length = self.path_length(root_path)

                banned_edges: Set[Tuple[str, str]] = set()
                for accepted_path in accepted:
                    same_root = (
                        len(accepted_path) > spur_index + 1
                        and accepted_path[: spur_index + 1] == root_path
                    )
                    if same_root:
                        a = accepted_path[spur_index]
                        b = accepted_path[spur_index + 1]
                        banned_edges.add((a, b))
                        banned_edges.add((b, a))

                banned_nodes = set(root_path[:-1])
                try:
                    spur_path = self.shortest_path(
                        spur_node,
                        target,
                        banned_nodes=banned_nodes,
                        banned_edges=banned_edges,
                    )
                except GraphError:
                    continue

                total_path = root_path[:-1] + spur_path
                total_key = tuple(total_path)
                if total_key in seen:
                    continue
                seen.add(total_key)
                total_length = root_length + self.path_length(spur_path)
                heapq.heappush(candidates, (total_length, next(counter), total_path))

            if not candidates:
                break
            _, _, next_path = heapq.heappop(candidates)
            accepted.append(next_path)

        return accepted

    def candidate_from_path(
        self,
        path: Sequence[str],
        path_id: str,
        turn_threshold_degrees: float = 35.0,
    ) -> CandidatePath:
        path_nodes = [str(item) for item in path]
        geometry = self.path_geometry(path_nodes)
        events = self.path_events(path_nodes, turn_threshold_degrees=turn_threshold_degrees)
        return CandidatePath(
            path_id=path_id,
            nodes=path_nodes,
            length_m=self.path_length(path_nodes),
            geometry=geometry,
            events=events,
        )

    def path_events(
        self,
        path: Sequence[str],
        turn_threshold_degrees: float = 35.0,
    ) -> List[CandidateEvent]:
        events: List[CandidateEvent] = []
        cumulative = 0.0
        for index in range(1, len(path) - 1):
            prev_node = str(path[index - 1])
            node_id = str(path[index])
            next_node = str(path[index + 1])
            incoming_edge = self.edge_between(prev_node, node_id)
            outgoing_edge = self.edge_between(node_id, next_node)
            cumulative += incoming_edge.length_m

            incoming_coords = self.oriented_edge_coords(prev_node, node_id)
            outgoing_coords = self.oriented_edge_coords(node_id, next_node)
            bearing_in = _edge_terminal_bearing(incoming_coords)
            bearing_out = _edge_initial_bearing(outgoing_coords)
            movement = classify_turn(bearing_in, bearing_out, threshold_degrees=turn_threshold_degrees)
            interchange = merge_interchange_flags(
                edge_likely_interchange(incoming_edge.attrs),
                edge_likely_interchange(outgoing_edge.attrs),
            )
            events.append(
                CandidateEvent(
                    node_id=node_id,
                    distance_m=cumulative,
                    movement=movement,
                    interchange=interchange,
                    bearing_in=bearing_in,
                    bearing_out=bearing_out,
                )
            )
        return events

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "u": edge.u,
                    "v": edge.v,
                    "length_m": edge.length_m,
                    "coords": [[lon, lat] for lon, lat in edge.coords],
                    "attrs": edge.attrs,
                }
                for edge in self.edges.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RoadGraph":
        graph = cls()
        for item in data.get("nodes", []):
            graph.add_node(item["node_id"], item["lon"], item["lat"], attrs=item.get("attrs", {}))
        for item in data.get("edges", []):
            graph.add_edge(
                item["u"],
                item["v"],
                length_m=item["length_m"],
                coords=[(float(lon), float(lat)) for lon, lat in item.get("coords", [])],
                attrs=item.get("attrs", {}),
                edge_id=item.get("edge_id"),
            )
        return graph


def canonical_edge_key(u: str, v: str) -> Tuple[str, str]:
    return (u, v) if u <= v else (v, u)


def _reconstruct_path(previous: Dict[str, str], source: str, target: str) -> List[str]:
    path = [target]
    node_id = target
    while node_id != source:
        node_id = previous[node_id]
        path.append(node_id)
    path.reverse()
    return path


def save_road_graph_json(graph: RoadGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_road_graph_json(path: Path) -> RoadGraph:
    return RoadGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))


def edge_likely_interchange(attrs: Dict[str, Any]) -> Optional[bool]:
    """Infer bridge/tunnel/interchange hints from OSM-like edge attributes."""

    bridge = _truthy_osm(attrs.get("bridge"))
    tunnel = _truthy_osm(attrs.get("tunnel"))
    if bridge or tunnel:
        return True

    layer = attrs.get("layer")
    if isinstance(layer, list):
        layer = layer[0] if layer else None
    try:
        if layer is not None and int(str(layer)) != 0:
            return True
    except (TypeError, ValueError):
        pass

    junction = _as_text(attrs.get("junction")).lower()
    highway = _as_text(attrs.get("highway")).lower()
    if "motorway_junction" in junction or "_link" in highway:
        return True

    if any(key in attrs for key in ("bridge", "tunnel", "layer", "junction", "highway")):
        return False
    return None


def merge_interchange_flags(a: Optional[bool], b: Optional[bool]) -> Optional[bool]:
    if a is True or b is True:
        return True
    if a is False or b is False:
        return False
    return None


def _edge_initial_bearing(coords: Sequence[Coordinate]) -> float:
    if len(coords) < 2:
        return 0.0
    return bearing_degrees(coords[0], coords[1])


def _edge_terminal_bearing(coords: Sequence[Coordinate]) -> float:
    if len(coords) < 2:
        return 0.0
    return bearing_degrees(coords[-2], coords[-1])


def _truthy_osm(value: Any) -> bool:
    if isinstance(value, list):
        return any(_truthy_osm(item) for item in value)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text not in {"", "0", "false", "no", "none", "nan"}


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(_as_text(item) for item in value)
    return "" if value is None else str(value)
