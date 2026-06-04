"""Split edges at intersection points.

This module splits road edges at their intersection points to ensure that
crossroads have proper node connectivity. This is a lossless transformation
that preserves all edge properties while creating proper topology.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import substring

from utils.geometry import coord_to_node_id, haversine_m, round_coordinate


def split_edges_at_intersections(
    edge_features: List[Dict[str, Any]],
    node_features: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Set[int]]:
    """Split edges at intersection points.

    Args:
        edge_features: Original edge GeoJSON features
        node_features: Original node GeoJSON features

    Returns:
        Tuple of (all_edges, all_nodes, split_edge_indices)
        - all_edges: Non-split originals + new split segments
        - all_nodes: Original nodes + new intersection nodes
        - split_edge_indices: Set of indices of edges that were split
    """
    # Build coord_to_node map from original nodes
    coord_to_node: Dict[Tuple[float, float], str] = {}
    all_nodes = list(node_features)

    for node in node_features:
        coord = tuple(node['geometry']['coordinates'])
        rounded_coord = round_coordinate(coord)
        node_id = node['properties']['node_id']
        coord_to_node[rounded_coord] = node_id

    # Build spatial index
    geometries = [LineString(f['geometry']['coordinates']) for f in edge_features]
    gdf = gpd.GeoDataFrame(
        {'edge_idx': range(len(edge_features))},
        geometry=geometries,
        crs='EPSG:4326',
    )

    # Find candidate pairs with overlapping bounding boxes
    candidates_df = gpd.sjoin(gdf, gdf, how='inner', predicate='intersects')
    candidates_df = candidates_df[candidates_df['edge_idx_left'] < candidates_df['edge_idx_right']]

    # Find intersections
    # Store: Dict[edge_idx, List[Tuple[point, is_endpoint_of_this_edge]]]
    intersections_per_edge: Dict[int, List[Tuple[Tuple[float, float], bool]]] = {}

    for _, row in candidates_df.iterrows():
        i = int(row['edge_idx_left'])
        j = int(row['edge_idx_right'])

        edge_i = edge_features[i]
        edge_j = edge_features[j]

        # Skip if shared endpoints
        if _shares_endpoint(edge_i, edge_j):
            continue

        # Compute intersection
        line_i = geometries[i]
        line_j = geometries[j]
        intersection = line_i.intersection(line_j)

        # Only keep Point intersections
        if intersection.is_empty:
            continue

        if intersection.geom_type == 'Point':
            point = (intersection.x, intersection.y)
            rounded_point = round_coordinate(point)

            # Check if this point is an endpoint of edge i
            coords_i = edge_i['geometry']['coordinates']
            is_endpoint_i = (
                round_coordinate(coords_i[0]) == rounded_point or
                round_coordinate(coords_i[-1]) == rounded_point
            )

            # Check if this point is an endpoint of edge j
            coords_j = edge_j['geometry']['coordinates']
            is_endpoint_j = (
                round_coordinate(coords_j[0]) == rounded_point or
                round_coordinate(coords_j[-1]) == rounded_point
            )

            if i not in intersections_per_edge:
                intersections_per_edge[i] = []
            if j not in intersections_per_edge:
                intersections_per_edge[j] = []

            intersections_per_edge[i].append((rounded_point, is_endpoint_i))
            intersections_per_edge[j].append((rounded_point, is_endpoint_j))

        elif intersection.geom_type == 'MultiPoint':
            for point in intersection.geoms:
                coord = (point.x, point.y)
                rounded_coord = round_coordinate(coord)

                # Check if this point is an endpoint of edge i
                coords_i = edge_i['geometry']['coordinates']
                is_endpoint_i = (
                    round_coordinate(coords_i[0]) == rounded_coord or
                    round_coordinate(coords_i[-1]) == rounded_coord
                )

                # Check if this point is an endpoint of edge j
                coords_j = edge_j['geometry']['coordinates']
                is_endpoint_j = (
                    round_coordinate(coords_j[0]) == rounded_coord or
                    round_coordinate(coords_j[-1]) == rounded_coord
                )

                if i not in intersections_per_edge:
                    intersections_per_edge[i] = []
                if j not in intersections_per_edge:
                    intersections_per_edge[j] = []

                intersections_per_edge[i].append((rounded_coord, is_endpoint_i))
                intersections_per_edge[j].append((rounded_coord, is_endpoint_j))

    # Split edges
    all_edges: List[Dict[str, Any]] = []
    split_edge_indices: Set[int] = set()

    for idx, edge in enumerate(edge_features):
        if idx in intersections_per_edge:
            # Split this edge
            intersection_points = intersections_per_edge[idx]
            new_edges, new_nodes = _split_edge_at_points(
                edge, intersection_points, coord_to_node
            )
            all_edges.extend(new_edges)
            all_nodes.extend(new_nodes)
            split_edge_indices.add(idx)
        else:
            # Keep original
            all_edges.append(edge)

    return all_edges, all_nodes, split_edge_indices


def _shares_endpoint(edge_i: Dict[str, Any], edge_j: Dict[str, Any]) -> bool:
    """Check if two edges share an endpoint (u or v)."""
    props_i = edge_i['properties']
    props_j = edge_j['properties']

    u_i, v_i = props_i['u'], props_i['v']
    u_j, v_j = props_j['u'], props_j['v']

    return u_i == u_j or u_i == v_j or v_i == u_j or v_i == v_j


def _split_edge_at_points(
    edge_feature: Dict[str, Any],
    intersection_points: List[Tuple[Tuple[float, float], bool]],
    coord_to_node: Dict[Tuple[float, float], str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a single edge at given intersection points.

    Args:
        edge_feature: The edge to split
        intersection_points: List of (point, is_endpoint) tuples
        coord_to_node: Mapping from coordinates to node IDs

    Returns:
        Tuple of (new_edges, new_nodes)
    """
    coords = edge_feature['geometry']['coordinates']
    line = LineString(coords)

    # Get normalized distances for each intersection point
    points_with_dist = []
    for point, is_endpoint in intersection_points:
        # Only skip if this point is an endpoint of THIS edge
        if is_endpoint:
            continue

        p = Point(point)
        dist = line.project(p, normalized=True)
        points_with_dist.append((dist, point))

    if not points_with_dist:
        return [edge_feature], []

    # Sort by distance along edge
    points_with_dist.sort(key=lambda x: x[0])

    # Create new nodes for intersection points
    new_nodes: List[Dict[str, Any]] = []
    for _, point in points_with_dist:
        rounded_point = round_coordinate(point)
        if rounded_point not in coord_to_node:
            node_id = coord_to_node_id(rounded_point)
            coord_to_node[rounded_point] = node_id
            new_nodes.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [rounded_point[0], rounded_point[1]],
                },
                'properties': {
                    'node_id': node_id,
                },
            })

    # Split edge into segments
    new_edges: List[Dict[str, Any]] = []
    original_edge_id = edge_feature['properties']['edge_id']

    # Build list of split points with their exact coordinates
    # Format: (normalized_distance, coordinate)
    split_points = [(0.0, round_coordinate(coords[0]))]
    for dist, point in points_with_dist:
        split_points.append((dist, round_coordinate(point)))
    split_points.append((1.0, round_coordinate(coords[-1])))

    for seg_idx in range(len(split_points) - 1):
        start_dist, start_coord = split_points[seg_idx]
        end_dist, end_coord = split_points[seg_idx + 1]

        # Extract segment
        segment = substring(line, start_dist, end_dist, normalized=True)
        segment_coords = list(segment.coords)

        if len(segment_coords) < 2:
            continue

        # Replace first and last coordinates with exact split points
        segment_coords[0] = start_coord
        segment_coords[-1] = end_coord

        # Create edge segment
        new_edge = _create_edge_segment(
            edge_feature, segment_coords, coord_to_node, seg_idx, original_edge_id
        )
        new_edges.append(new_edge)

    return new_edges, new_nodes


def _create_edge_segment(
    original_edge: Dict[str, Any],
    coords: List[Tuple[float, float]],
    coord_to_node: Dict[Tuple[float, float], str],
    segment_idx: int,
    original_edge_id: str,
) -> Dict[str, Any]:
    """Create a new edge feature from a segment of the original edge.

    Edge ID format: {original_edge_id}_{segment_idx}
    Inherits all properties except u, v, length.
    """
    start_coord = round_coordinate(coords[0])
    end_coord = round_coordinate(coords[-1])

    start_node_id = coord_to_node[start_coord]
    end_node_id = coord_to_node[end_coord]

    # Calculate length
    length = haversine_m(start_coord, end_coord)

    # Copy properties and update
    new_props = dict(original_edge['properties'])
    new_props['edge_id'] = f"{original_edge_id}_{segment_idx}"
    new_props['u'] = start_node_id
    new_props['v'] = end_node_id
    new_props['length'] = length

    return {
        'type': 'Feature',
        'geometry': {
            'type': 'LineString',
            'coordinates': [[c[0], c[1]] for c in coords],
        },
        'properties': new_props,
    }
