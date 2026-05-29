from __future__ import annotations

from typing import Any, Dict, List

import geopandas as gpd
from shapely.geometry import LineString

from utils.geometry import (
    angular_delta_mod180,
    circular_span_mod180,
    get_bounds_center,
    haversine_m,
    median_direction_mod180,
    meters_to_degrees_lon,
    offset_to_coordinate,
    point_to_line_distance_m,
    project_point_to_line,
    project_to_bearing_m,
    segment_overlap_length_m,
)


def cluster_near_parallel_edges(
    edge_features: List[Dict[str, Any]],
    near_threshold_m: float = 30.0,
    parallel_angle_threshold: float = 15.0,
    overlap_ratio_threshold: float = 0.5,
    overlap_length_threshold_m: float = 120.0,
) -> tuple[List[List[int]], Dict[int, set]]:
    """Cluster edges that are near, parallel, and overlapping.

    Uses geopandas sjoin for spatial filtering, then applies:
    1. NEAR: all 4 endpoints within near_threshold_m of other edge's line
    2. PARALLEL: angular_delta_mod180(direction_deg) <= parallel_angle_threshold
    3. OVERLAP: projection overlap >= overlap_ratio_threshold OR >= overlap_length_threshold_m

    After initial clustering, subset clusters are merged into larger ones.
    Core edges (pre-merge) are tracked separately for direction checks.

    Args:
        edge_features: List of edge GeoJSON features.
        near_threshold_m: Distance threshold for near check, default 30m.
        parallel_angle_threshold: Angle threshold for parallel check in degrees, default 15.0.
        overlap_ratio_threshold: Minimum overlap ratio, default 0.5 (50%).
        overlap_length_threshold_m: Minimum overlap length in meters, default 120m.

    Returns:
        Tuple of (clusters, core_edges_per_cluster).
        clusters: List of clusters (including singletons), each cluster is a list of edge indices.
        core_edges_per_cluster: Dict mapping cluster index to set of core (pre-merge) edge indices.
    """
    geometries = [LineString(f['geometry']['coordinates']) for f in edge_features]
    gdf = gpd.GeoDataFrame(
        {'edge_idx': range(len(edge_features))},
        geometry=geometries,
        crs='EPSG:4326',
    )

    center = get_bounds_center(edge_features)
    center_lat = center[1]

    buffer_deg = meters_to_degrees_lon(near_threshold_m, center_lat)
    gdf_buffered = gdf.copy()
    gdf_buffered['geometry'] = gdf.geometry.buffer(buffer_deg)

    candidates_df = gpd.sjoin(gdf_buffered, gdf, how='inner', predicate='intersects')
    candidates_df = candidates_df[candidates_df['edge_idx_left'] < candidates_df['edge_idx_right']]

    adjacency: Dict[int, set] = {i: set() for i in range(len(edge_features))}

    for _, row in candidates_df.iterrows():
        i = int(row['edge_idx_left'])
        j = int(row['edge_idx_right'])

        coords_i = edge_features[i]['geometry']['coordinates']
        coords_j = edge_features[j]['geometry']['coordinates']
        a1 = (coords_i[0][0], coords_i[0][1])
        a2 = (coords_i[-1][0], coords_i[-1][1])
        b1 = (coords_j[0][0], coords_j[0][1])
        b2 = (coords_j[-1][0], coords_j[-1][1])

        near_pass = all([
            point_to_line_distance_m(a1, b1, b2) <= near_threshold_m,
            point_to_line_distance_m(a2, b1, b2) <= near_threshold_m,
            point_to_line_distance_m(b1, a1, a2) <= near_threshold_m,
            point_to_line_distance_m(b2, a1, a2) <= near_threshold_m,
        ])
        if not near_pass:
            continue

        dir_i = edge_features[i]['properties']['direction_deg']
        dir_j = edge_features[j]['properties']['direction_deg']
        if angular_delta_mod180(dir_i, dir_j) > parallel_angle_threshold:
            continue

        length_i = haversine_m(a1, a2)
        length_j = haversine_m(b1, b2)
        a1_proj = project_point_to_line(a1, b1, b2)
        a2_proj = project_point_to_line(a2, b1, b2)
        overlap_len = segment_overlap_length_m(a1_proj, a2_proj, b1, b2)

        overlap_ratio_i = overlap_len / length_i if length_i > 0 else 0.0
        overlap_ratio_j = overlap_len / length_j if length_j > 0 else 0.0
        overlap_pass = (overlap_ratio_i >= overlap_ratio_threshold) or \
                       (overlap_ratio_j >= overlap_ratio_threshold) or \
                       (overlap_len >= overlap_length_threshold_m)
        if not overlap_pass:
            continue

        adjacency[i].add(j)
        adjacency[j].add(i)

    visited: set = set()
    clusters: List[List[int]] = []

    for start_idx in range(len(edge_features)):
        if start_idx in visited:
            continue

        cluster: List[int] = []
        queue = [start_idx]
        visited.add(start_idx)

        while queue:
            current = queue.pop(0)
            cluster.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        clusters.append(cluster)

    clusters, core_edges = merge_subset_clusters(clusters, edge_features)

    return clusters, core_edges


def merge_subset_clusters(
    clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
) -> tuple[List[List[int]], Dict[int, set]]:
    """Merge clusters whose node-set is a strict subset of another cluster.

    A cluster's node-set is the set of all endpoint node IDs of its edges.
    If cluster A's node-set is a strict subset of cluster B's, A is merged
    into B. If multiple candidate B clusters exist, the first one found is
    chosen. Singletons are treated as normal clusters.

    Core edges (the absorber cluster's original edges) are tracked separately
    so that direction checks can exclude merged edges.

    Args:
        clusters: List of edge index lists (clusters).
        edge_features: Edge GeoJSON features.

    Returns:
        Tuple of (merged_clusters, core_edges_per_cluster).
        merged_clusters: Merged clusters with no subset relationships remaining.
        core_edges_per_cluster: Dict mapping new cluster index to set of core edge indices.
    """
    cluster_node_sets: List[set] = []
    for c in clusters:
        node_set = set()
        for edge_idx in c:
            props = edge_features[edge_idx]['properties']
            node_set.add(props['u'])
            node_set.add(props['v'])
        cluster_node_sets.append(node_set)

    core_edges: Dict[int, set] = {i: set(c) for i, c in enumerate(clusters)}

    merge_into: Dict[int, int] = {}

    for i in range(len(clusters)):
        if i in merge_into:
            continue
        for j in range(len(clusters)):
            if i == j or j in merge_into:
                continue
            if cluster_node_sets[i] < cluster_node_sets[j]:
                merge_into[i] = j
                break

    merged_clusters: Dict[int, List[int]] = {}
    absorber: Dict[int, int] = {}

    for i, c in enumerate(clusters):
        target = i
        while target in merge_into:
            target = merge_into[target]
        merged_clusters.setdefault(target, []).extend(c)
        if target not in absorber:
            absorber[target] = target

    result_clusters = [merged_clusters[k] for k in sorted(merged_clusters.keys())]

    result_core_edges: Dict[int, set] = {}
    for new_idx, old_key in enumerate(sorted(merged_clusters.keys())):
        result_core_edges[new_idx] = core_edges[absorber[old_key]]

    return result_clusters, result_core_edges






def build_c_edge_graph(
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    core_edges_per_cluster: Dict[int, set] | None = None,
    near_threshold_m: float = 50.0,
) -> List[Dict[str, Any]]:
    """Build a C-edge graph from edge clusters.

    Each C-edge is an edge cluster with a representative line geometry.
    For each C-edge, we identify the endpoint nodes (original nodes at the
    extremes along the major direction).

    Args:
        edge_clusters: List of edge index lists from cluster_near_parallel_edges.
        edge_features: Edge GeoJSON features.
        node_coords: Dict of node_id -> (lon, lat).
        core_edges_per_cluster: Dict mapping cluster index to set of core edge
            indices. If None, all edges are treated as core.
        near_threshold_m: Near threshold used for edge clustering (default 50m).
            Edge buffer radius is derived as near_threshold_m / 2.

    Returns:
        List of C-edge dicts with {idx, start_coord, end_coord, direction_deg,
        start_node_id, end_node_id, size}.
    """
    from utils.liquid import (
        auto_utm_epsg,
        centroid_to_geo,
        liquidify_lines,
    )

    center = get_bounds_center(edge_features)
    utm_epsg = auto_utm_epsg(center[0], center[1])
    edge_buffer_radius_m = near_threshold_m / 2.0

    c_edges: List[Dict[str, Any]] = []
    for ce_idx, ec in enumerate(edge_clusters):
        core = core_edges_per_cluster.get(ce_idx, set(ec)) if core_edges_per_cluster else set(ec)

        # Compute major direction from core edges
        core_directions = [edge_features[idx]['properties']['direction_deg'] for idx in core]
        major_dir = median_direction_mod180(core_directions)

        # Get core edge coordinates for liquidification
        core_line_coords = [
            edge_features[edge_idx]['geometry']['coordinates']
            for edge_idx in core
        ]

        if not core_line_coords:
            core_line_coords = [
                edge_features[edge_idx]['geometry']['coordinates']
                for edge_idx in ec
            ]

        # Liquidify to get origin
        liquid_shape = liquidify_lines(core_line_coords, edge_buffer_radius_m, utm_epsg)
        origin = centroid_to_geo(liquid_shape, utm_epsg)

        # Project all core edge endpoints onto the major direction
        t_values: List[float] = []
        for edge_idx in core:
            coords = edge_features[edge_idx]['geometry']['coordinates']
            p1 = (coords[0][0], coords[0][1])
            p2 = (coords[-1][0], coords[-1][1])

            t1 = project_to_bearing_m(p1, origin, major_dir)
            t2 = project_to_bearing_m(p2, origin, major_dir)
            t_values.extend([t1, t2])

        if not t_values:
            for edge_idx in ec:
                coords = edge_features[edge_idx]['geometry']['coordinates']
                p1 = (coords[0][0], coords[0][1])
                p2 = (coords[-1][0], coords[-1][1])

                t1 = project_to_bearing_m(p1, origin, major_dir)
                t2 = project_to_bearing_m(p2, origin, major_dir)
                t_values.extend([t1, t2])

        t_min = min(t_values)
        t_max = max(t_values)

        start_coord = offset_to_coordinate(origin, major_dir, t_min, 0.0)
        end_coord = offset_to_coordinate(origin, major_dir, t_max, 0.0)

        # Identify endpoint nodes: find original nodes closest to start_coord and end_coord
        all_node_ids: set = set()
        for edge_idx in ec:
            props = edge_features[edge_idx]['properties']
            all_node_ids.add(props['u'])
            all_node_ids.add(props['v'])

        # Find node closest to start_coord
        start_node_id = None
        start_dist = float('inf')
        for nid in all_node_ids:
            if nid in node_coords:
                dist = haversine_m(start_coord, node_coords[nid])
                if dist < start_dist:
                    start_dist = dist
                    start_node_id = nid

        # Find node closest to end_coord
        end_node_id = None
        end_dist = float('inf')
        for nid in all_node_ids:
            if nid in node_coords:
                dist = haversine_m(end_coord, node_coords[nid])
                if dist < end_dist:
                    end_dist = dist
                    end_node_id = nid

        c_edges.append({
            'idx': ce_idx,
            'start_coord': start_coord,
            'end_coord': end_coord,
            'direction_deg': major_dir,
            'start_node_id': start_node_id,
            'end_node_id': end_node_id,
            'size': len(ec),
        })

    return c_edges
