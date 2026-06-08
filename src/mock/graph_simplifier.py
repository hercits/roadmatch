from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import geopandas as gpd
from shapely.geometry import LineString, MultiPoint, Point

from utils.geometry import (
    angular_delta_mod180,
    circular_span_mod180,
    get_bounds_center,
    haversine_m,
    line_intersection_2d,
    median_direction_mod180,
    meters_to_degrees_lon,
    offset_to_coordinate,
    point_to_line_distance_m,
    polyline_length_m,
    project_point_to_line,
    project_to_bearing_m,
    segment_overlap_length_m,
    signed_perpendicular_offset_m,
)
from utils.types import HIGHWAY_LEVEL


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

    clusters, core_edges = merge_subset_clusters(clusters, edge_features, near_threshold_m)

    return clusters, core_edges


def merge_subset_clusters(
    clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    near_threshold_m: float = 30.0,
) -> tuple[List[List[int]], Dict[int, set]]:
    """Merge clusters via node-set subset and convex hull containment.

    Two-pass approach:
    1. Node-set strict subset: merge A into B if A's nodes ⊂ B's nodes
    2. Convex hull containment: merge A into B if all A's nodes are within
       B's buffered convex hull (buffer = near_threshold_m / 2)

    Core edges (the absorber cluster's original edges) are tracked separately
    so that direction checks can exclude merged edges.

    Args:
        clusters: List of edge index lists (clusters).
        edge_features: Edge GeoJSON features.
        near_threshold_m: Distance threshold for hull buffering, default 30m.

    Returns:
        Tuple of (merged_clusters, core_edges_per_cluster).
        merged_clusters: Merged clusters with no subset/containment relationships.
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

    # Calculate total length of each initial cluster
    cluster_lengths: Dict[int, float] = {}
    for i, c in enumerate(clusters):
        total_length = 0.0
        for edge_idx in c:
            coords = edge_features[edge_idx]['geometry']['coordinates']
            total_length += polyline_length_m(coords)
        cluster_lengths[i] = total_length

    core_edges: Dict[int, set] = {i: set(c) for i, c in enumerate(clusters)}

    merge_into: Dict[int, int] = {}

    for i in range(len(clusters)):
        if i in merge_into:
            continue
        for j in range(len(clusters)):
            if i == j or j in merge_into:
                continue
            if cluster_node_sets[i] <= cluster_node_sets[j]:
                merge_into[i] = j
                break

    alive = [i for i in range(len(clusters)) if i not in merge_into]

    if len(alive) > 1:
        node_coords: Dict[str, tuple] = {}
        for c in clusters:
            for edge_idx in c:
                coords = edge_features[edge_idx]['geometry']['coordinates']
                props = edge_features[edge_idx]['properties']
                u, v = props['u'], props['v']
                if u not in node_coords:
                    node_coords[u] = (coords[0][0], coords[0][1])
                if v not in node_coords:
                    node_coords[v] = (coords[-1][0], coords[-1][1])

        all_points = list(node_coords.values())
        gdf_all = gpd.GeoDataFrame(
            geometry=[Point(lon, lat) for lon, lat in all_points],
            crs='EPSG:4326'
        )

        center_lon = sum(p[0] for p in all_points) / len(all_points)
        center_lat = sum(p[1] for p in all_points) / len(all_points)
        zone_number = int((center_lon + 180) / 6) + 1
        utm_epsg = 32600 + zone_number if center_lat >= 0 else 32700 + zone_number

        gdf_proj = gdf_all.to_crs(utm_epsg)
        proj_coords = [(p.x, p.y) for p in gdf_proj.geometry]

        node_to_proj: Dict[str, tuple] = {}
        for node_id, proj_pt in zip(node_coords.keys(), proj_coords):
            node_to_proj[node_id] = proj_pt

        buffer_radius_m = near_threshold_m / 2.0

        cluster_hulls: Dict[int, Any] = {}
        cluster_bboxes: Dict[int, tuple] = {}

        for i in alive:
            nodes = cluster_node_sets[i]
            if len(nodes) == 0:
                continue
            proj_points = [Point(node_to_proj[n]) for n in nodes]
            hull = MultiPoint(proj_points).convex_hull
            buffered_hull = hull.buffer(buffer_radius_m)
            cluster_hulls[i] = buffered_hull
            cluster_bboxes[i] = buffered_hull.bounds

        for i in alive:
            if i in merge_into:
                continue
            nodes_i = cluster_node_sets[i]
            proj_points_i = [node_to_proj[n] for n in nodes_i]
            min_x_i = min(p[0] for p in proj_points_i)
            max_x_i = max(p[0] for p in proj_points_i)
            min_y_i = min(p[1] for p in proj_points_i)
            max_y_i = max(p[1] for p in proj_points_i)

            for j in alive:
                if i == j or j in merge_into:
                    continue
                if len(nodes_i) >= len(cluster_node_sets[j]):
                    continue

                bbox_j = cluster_bboxes[j]
                if not (min_x_i >= bbox_j[0] and max_x_i <= bbox_j[2] and
                        min_y_i >= bbox_j[1] and max_y_i <= bbox_j[3]):
                    continue

                hull_j = cluster_hulls[j]
                all_inside = all(Point(p).within(hull_j) for p in proj_points_i)

                if all_inside:
                    merge_into[i] = j
                    break

    merged_clusters: Dict[int, List[int]] = {}
    absorber: Dict[int, int] = {}
    # merged_sources: Dict[int, List[int]] = {}  # Track which initial clusters contributed (DISABLED)

    for i, c in enumerate(clusters):
        target = i
        while target in merge_into:
            target = merge_into[target]
        merged_clusters.setdefault(target, []).extend(c)
        if target not in absorber:
            absorber[target] = target
        # merged_sources.setdefault(target, []).append(i)  # DISABLED

    result_clusters = [merged_clusters[k] for k in sorted(merged_clusters.keys())]

    result_core_edges: Dict[int, set] = {}
    for new_idx, old_key in enumerate(sorted(merged_clusters.keys())):
        # Original logic: use absorber cluster's edges
        result_core_edges[new_idx] = core_edges[absorber[old_key]]
        
        # DISABLED: Select the initial cluster with the longest total length
        # source_clusters = merged_sources[old_key]
        # longest_source = max(source_clusters, key=lambda idx: cluster_lengths[idx])
        # result_core_edges[new_idx] = core_edges[longest_source]
        
        # # Debug: print info for cluster 638 specifically
        # if new_idx == 638:
        #     print(f"Cluster 638: {len(merged_clusters[old_key])} edges from {len(source_clusters)} sources")
        #     print(f"  Longest source: cluster {longest_source} with {len(core_edges[longest_source])} edges, length {cluster_lengths[longest_source]:.1f}m")
        #     top_5 = sorted(source_clusters, key=lambda idx: cluster_lengths[idx], reverse=True)[:5]
        #     for idx in top_5:
        #         print(f"    Source {idx}: {len(core_edges[idx])} edges, length {cluster_lengths[idx]:.1f}m")

    return result_clusters, result_core_edges


def _edge_highway_level(edge_feature: Dict[str, Any]) -> int:
    """Get highway level for an edge. Lower value = higher priority.
    
    For multi-value highway tags (list), takes the minimum (highest priority).
    Returns 99 for unknown/missing highway types.
    """
    highway = edge_feature.get('properties', {}).get('highway')
    if highway is None:
        return 99
    
    if isinstance(highway, list):
        if not highway:
            return 99
        return min(HIGHWAY_LEVEL.get(h, 99) for h in highway)
    
    return HIGHWAY_LEVEL.get(highway, 99)



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

        levels = {idx: _edge_highway_level(edge_features[idx]) for idx in core}
        min_level = min(levels.values()) if levels else 99
        priority_core = {idx for idx, lvl in levels.items() if lvl == min_level}
        if not priority_core:
            priority_core = core

        # Compute major direction from priority core edges
        core_directions = [edge_features[idx]['properties']['direction_deg'] for idx in priority_core]
        major_dir = median_direction_mod180(core_directions)

        # Get priority core edge coordinates for liquidification
        core_line_coords = [
            edge_features[edge_idx]['geometry']['coordinates']
            for edge_idx in priority_core
        ]

        if not core_line_coords:
            core_line_coords = [
                edge_features[edge_idx]['geometry']['coordinates']
                for edge_idx in ec
            ]

        # Liquidify to get origin
        liquid_shape = liquidify_lines(core_line_coords, edge_buffer_radius_m, utm_epsg)
        origin = centroid_to_geo(liquid_shape, utm_epsg)

        # Project all core edge endpoints onto the major direction for extent
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
        road_length_m = t_max - t_min

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
            'parent_idx': ce_idx,
            'start_coord': start_coord,
            'end_coord': end_coord,
            'direction_deg': major_dir,
            'start_node_id': start_node_id,
            'end_node_id': end_node_id,
            'size': len(ec),
            'length_m': haversine_m(start_coord, end_coord),
            'road_length_m': road_length_m,
            'highway_level': min_level,
        })

    return c_edges


def filter_small_link_cedges(
    c_edges: List[Dict[str, Any]],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    max_size: int = 2,
) -> List[Dict[str, Any]]:
    """Remove small C-edges composed entirely of *_link highway edges.

    Args:
        c_edges: List of C-edge dicts.
        edge_clusters: List of edge index lists for each cluster.
        edge_features: Original edge GeoJSON features.
        max_size: Maximum C-edge size to consider for removal.

    Returns:
        Filtered list of C-edges with small pure-link ones removed.
    """
    link_types = {'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'}
    keep = []

    for ce in c_edges:
        pidx = ce.get('parent_idx', ce['idx'])
        if pidx >= len(edge_clusters) or ce.get('size', 0) > max_size:
            keep.append(ce)
            continue

        cluster = edge_clusters[pidx]
        all_link = True
        has_edges = False
        for ei in cluster:
            if ei >= len(edge_features):
                continue
            has_edges = True
            hw = edge_features[ei]['properties'].get('highway', '')
            types = hw if isinstance(hw, list) else [hw]
            if not all(isinstance(t, str) and t in link_types for t in types):
                all_link = False
                break

        if has_edges and all_link:
            continue
        keep.append(ce)

    return keep


def build_node_to_cedges_map(
    c_edges: List[Dict[str, Any]],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
) -> Dict[str, set]:
    """Build mapping from node_id to set of C-edge indices.

    Args:
        c_edges: List of C-edge dicts.
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.

    Returns:
        Dict mapping node_id to set of C-edge indices.
    """
    node_to_cedges: Dict[str, set] = {}

    for ce in c_edges:
        ce_idx = ce['idx']
        if ce_idx >= len(edge_clusters):
            continue
        cluster = edge_clusters[ce_idx]
        for edge_idx in cluster:
            props = edge_features[edge_idx]['properties']
            u = props['u']
            v = props['v']

            if u not in node_to_cedges:
                node_to_cedges[u] = set()
            node_to_cedges[u].add(ce_idx)

            if v not in node_to_cedges:
                node_to_cedges[v] = set()
            node_to_cedges[v].add(ce_idx)

    return node_to_cedges


def filter_spur_core_edges(
    core_edges_per_cluster: Dict[int, set],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    c_edges: List[Dict[str, Any]] | None = None,
) -> Dict[int, set]:
    """Filter out spur core edges (edges where both endpoints only belong to current C-edge).

    Spur edges are branches that don't connect to other C-edges. Removing them
    improves C-edge geometry by eliminating directional bias from spurs.

    Args:
        core_edges_per_cluster: Dict mapping cluster index to set of core edge indices.
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.
        c_edges: Optional list of active C-edges. If provided, only iterate over these.

    Returns:
        Filtered core_edges_per_cluster with spur edges removed.
    """
    node_to_cedges: Dict[str, set] = {}
    active_indices = {ce['idx'] for ce in c_edges} if c_edges else set(range(len(edge_clusters)))
    for ce_idx in active_indices:
        if ce_idx >= len(edge_clusters):
            continue
        cluster = edge_clusters[ce_idx]
        for edge_idx in cluster:
            props = edge_features[edge_idx]['properties']
            u = props['u']
            v = props['v']

            if u not in node_to_cedges:
                node_to_cedges[u] = set()
            node_to_cedges[u].add(ce_idx)

            if v not in node_to_cedges:
                node_to_cedges[v] = set()
            node_to_cedges[v].add(ce_idx)

    filtered_core_edges = {}
    for ce_idx, core_edges in core_edges_per_cluster.items():
        filtered = set()
        for edge_idx in core_edges:
            edge = edge_features[edge_idx]
            u = edge['properties']['u']
            v = edge['properties']['v']

            u_cedges = node_to_cedges.get(u, set())
            v_cedges = node_to_cedges.get(v, set())

            u_shared = len(u_cedges) > 1
            v_shared = len(v_cedges) > 1

            if not (u_shared or v_shared):
                continue

            filtered.add(edge_idx)

        if not filtered:
            filtered = core_edges

        filtered_core_edges[ce_idx] = filtered

    return filtered_core_edges


def recompute_c_edge_geometry(
    c_edges: List[Dict[str, Any]],
    core_edges_per_cluster: Dict[int, set],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    near_threshold_m: float = 50.0,
) -> None:
    """Recompute C-edge geometry using filtered core edges.

    Updates c_edges in-place with new direction, start_coord, end_coord.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        core_edges_per_cluster: Dict mapping cluster index to set of core edge indices.
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.
        node_coords: Dict of node_id -> (lon, lat).
        near_threshold_m: Near threshold used for edge clustering (default 50m).
    """
    from utils.liquid import (
        auto_utm_epsg,
        centroid_to_geo,
        liquidify_lines,
    )

    center = get_bounds_center(edge_features)
    utm_epsg = auto_utm_epsg(center[0], center[1])
    edge_buffer_radius_m = near_threshold_m / 2.0

    for c_edge in c_edges:
        ce_idx = c_edge['idx']
        core = core_edges_per_cluster.get(ce_idx, set(edge_clusters[ce_idx]))

        levels = {idx: _edge_highway_level(edge_features[idx]) for idx in core}
        min_level = min(levels.values()) if levels else 99
        priority_core = {idx for idx, lvl in levels.items() if lvl == min_level}
        if not priority_core:
            priority_core = core

        core_directions = [edge_features[idx]['properties']['direction_deg'] for idx in priority_core]
        major_dir = median_direction_mod180(core_directions)

        core_line_coords = [
            edge_features[edge_idx]['geometry']['coordinates']
            for edge_idx in priority_core
        ]

        if not core_line_coords:
            core_line_coords = [
                edge_features[edge_idx]['geometry']['coordinates']
                for edge_idx in edge_clusters[ce_idx]
            ]

        liquid_shape = liquidify_lines(core_line_coords, edge_buffer_radius_m, utm_epsg)
        origin = centroid_to_geo(liquid_shape, utm_epsg)

        t_values: List[float] = []
        for edge_idx in core:
            coords = edge_features[edge_idx]['geometry']['coordinates']
            p1 = (coords[0][0], coords[0][1])
            p2 = (coords[-1][0], coords[-1][1])

            t1 = project_to_bearing_m(p1, origin, major_dir)
            t2 = project_to_bearing_m(p2, origin, major_dir)
            t_values.extend([t1, t2])

        if not t_values:
            for edge_idx in edge_clusters[ce_idx]:
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

        c_edge['direction_deg'] = major_dir
        c_edge['start_coord'] = start_coord
        c_edge['end_coord'] = end_coord


def identify_connection_nodes(node_to_cedges: Dict[str, set]) -> Dict[str, set]:
    """Identify nodes that belong to 2+ C-edges.

    Args:
        node_to_cedges: Dict from build_node_to_cedges_map().

    Returns:
        Dict mapping node_id to set of C-edge indices (only for connection nodes).
    """
    return {
        node: cedges
        for node, cedges in node_to_cedges.items()
        if len(cedges) >= 2
    }


def cluster_connection_nodes(
    connection_nodes: Dict[str, set],
    node_coords: Dict[str, tuple],
) -> List[List[str]]:
    """Cluster connection nodes through 2 stages.

    Stage 1: For each pair of C-edges, cluster their shared nodes.
    Stage 2: Merge clusters that share a common node (transitive closure).

    Args:
        connection_nodes: Dict from identify_connection_nodes().
        node_coords: Dict of node_id -> (lon, lat).

    Returns:
        List of clusters, each cluster is a list of node_ids.
    """
    # Stage 1: Build clusters by grouping nodes by their C-edge combinations
    # This is much faster than checking all C-edge pairs
    
    # Group nodes by their C-edge set (as a frozenset for hashing)
    cedge_to_nodes: Dict[frozenset, List[str]] = {}
    for node, cedges in connection_nodes.items():
        key = frozenset(cedges)
        if key not in cedge_to_nodes:
            cedge_to_nodes[key] = []
        cedge_to_nodes[key].append(node)
    
    # Each group of nodes sharing the same C-edge set forms a cluster
    pair_clusters = [set(nodes) for nodes in cedge_to_nodes.values() if len(nodes) > 0]

    # Stage 2: Merge clusters that share nodes (transitive closure)
    # Use Union-Find for efficiency
    parent = list(range(len(pair_clusters)))
    
    def find(i: int) -> int:
        if parent[i] != i:
            parent[i] = find(parent[i])
        return parent[i]
    
    def union(i: int, j: int) -> None:
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j
    
    # Build mapping: node -> cluster indices
    node_to_clusters: Dict[str, List[int]] = {}
    for i, cluster in enumerate(pair_clusters):
        for node in cluster:
            if node not in node_to_clusters:
                node_to_clusters[node] = []
            node_to_clusters[node].append(i)
    
    # Merge clusters that share nodes
    for node, cluster_indices in node_to_clusters.items():
        if len(cluster_indices) > 1:
            for i in range(1, len(cluster_indices)):
                union(cluster_indices[0], cluster_indices[i])
    
    # Build merged clusters
    merged_clusters: Dict[int, set] = {}
    for i, cluster in enumerate(pair_clusters):
        root = find(i)
        if root not in merged_clusters:
            merged_clusters[root] = set()
        merged_clusters[root].update(cluster)
    
    # Convert sets to lists
    return [list(cluster) for cluster in merged_clusters.values()]


def average_position(nodes: List[str], node_coords: Dict[str, tuple]) -> tuple:
    """Compute average position of nodes.

    Args:
        nodes: List of node_ids.
        node_coords: Dict of node_id -> (lon, lat).

    Returns:
        Average (lon, lat) position.
    """
    # Filter out nodes that don't exist in node_coords
    valid_nodes = [n for n in nodes if n in node_coords]
    if not valid_nodes:
        # Fallback: return None if no valid nodes
        return None
    
    avg_lon = sum(node_coords[n][0] for n in valid_nodes) / len(valid_nodes)
    avg_lat = sum(node_coords[n][1] for n in valid_nodes) / len(valid_nodes)
    return (avg_lon, avg_lat)


def project_to_line(pos: tuple, line_start: tuple, line_end: tuple) -> tuple:
    """Project position to infinite line defined by two points.

    Args:
        pos: Position to project (lon, lat).
        line_start: Start point of line (lon, lat).
        line_end: End point of line (lon, lat).

    Returns:
        Projected position (lon, lat).
    """
    return project_point_to_line(pos, line_start, line_end)


def bfs_shortest_distance(start_node: str, adjacency: Dict[str, set]) -> Dict[str, int]:
    """BFS to find shortest path distances from start_node to all reachable nodes.
    
    Args:
        start_node: Starting node ID.
        adjacency: Adjacency list mapping node_id to set of neighbor node_ids.
    
    Returns:
        Dict mapping node_id to shortest distance (number of edges).
    """
    distances = {start_node: 0}
    queue = [start_node]
    
    while queue:
        current = queue.pop(0)
        current_dist = distances[current]
        
        if current in adjacency:
            for neighbor in adjacency[current]:
                if neighbor not in distances:
                    distances[neighbor] = current_dist + 1
                    queue.append(neighbor)
    
    return distances


def identify_endpoint_nodes_for_cedge(
    c_edge_idx: int,
    c_edges: List[Dict[str, Any]],
    edge_clusters: List[List[int]],
    core_edges: Dict[int, set],
    edge_features: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    near_threshold_m: float = 50.0,
    c_edge_map: Dict[int, Dict[str, Any]] | None = None,
) -> set:
    """Identify endpoint nodes for a C-edge.

    A node is an endpoint node if:
    a) It appears in only one core edge of this C-edge, OR
    b) It doesn't appear in any core edge, but appears in only one non-core edge,
       and the nearest core node is an endpoint node.
    c) It is within near_threshold_m/2 distance of any endpoint identified by (a) or (b).

    Args:
        c_edge_idx: Index of the C-edge.
        c_edges: List of C-edge dicts.
        edge_clusters: List of edge index lists for each C-edge.
        core_edges: Dict mapping C-edge index to set of core edge indices.
        edge_features: List of edge GeoJSON features.
        node_coords: Dict of node_id -> (lon, lat).
        near_threshold_m: Distance threshold in meters for relaxation (default 50.0).

    Returns:
        Set of endpoint node IDs.
    """
    # Get all edges in this C-edge
    all_edge_indices = edge_clusters[c_edge_idx]
    core_edge_indices = core_edges.get(c_edge_idx, set())
    non_core_edge_indices = set(all_edge_indices) - core_edge_indices

    # Get all nodes in this C-edge
    all_nodes = set()
    for edge_idx in all_edge_indices:
        edge = edge_features[edge_idx]
        all_nodes.add(edge['properties']['u'])
        all_nodes.add(edge['properties']['v'])

    # Get all core nodes
    core_nodes = set()
    for edge_idx in core_edge_indices:
        edge = edge_features[edge_idx]
        core_nodes.add(edge['properties']['u'])
        core_nodes.add(edge['properties']['v'])

    # Identify endpoint nodes
    endpoint_nodes = set()
    
    # Special case: always include start_node_id and end_node_id
    ce = c_edge_map[c_edge_idx] if c_edge_map else c_edges[c_edge_idx]
    if ce.get('start_node_id') and ce['start_node_id'] in node_coords:
        endpoint_nodes.add(ce['start_node_id'])
    if ce.get('end_node_id') and ce['end_node_id'] in node_coords:
        endpoint_nodes.add(ce['end_node_id'])

    for node in all_nodes:
        # Skip nodes that don't exist in node_coords
        if node not in node_coords:
            continue
            
        # Count core and non-core edges containing this node
        core_count = 0
        non_core_count = 0

        for edge_idx in core_edge_indices:
            edge = edge_features[edge_idx]
            if edge['properties']['u'] == node or edge['properties']['v'] == node:
                core_count += 1

        for edge_idx in non_core_edge_indices:
            edge = edge_features[edge_idx]
            if edge['properties']['u'] == node or edge['properties']['v'] == node:
                non_core_count += 1

        # Condition a: appears in only one core edge
        if core_count == 1:
            endpoint_nodes.add(node)
        # Condition b: doesn't appear in any core edge, but appears in only one non-core edge
        elif core_count == 0 and non_core_count == 1:
            # Build adjacency list from all edges in C-edge
            adjacency = {}
            for edge_idx in all_edge_indices:
                edge = edge_features[edge_idx]
                u = edge['properties']['u']
                v = edge['properties']['v']
                
                if u not in adjacency:
                    adjacency[u] = set()
                if v not in adjacency:
                    adjacency[v] = set()
                
                adjacency[u].add(v)
                adjacency[v].add(u)
            
            # Find shortest graph distances from this node to all core nodes
            distances = bfs_shortest_distance(node, adjacency)
            
            # Find minimum graph distance to any core node
            min_graph_dist = float('inf')
            for core_node in core_nodes:
                if core_node in distances:
                    graph_dist = distances[core_node]
                    if graph_dist < min_graph_dist:
                        min_graph_dist = graph_dist
            
            # Collect all core nodes with minimum graph distance
            nearest_core_nodes = []
            for core_node in core_nodes:
                if core_node in distances and distances[core_node] == min_graph_dist:
                    nearest_core_nodes.append(core_node)
            
            # Check if ALL nearest core nodes are endpoint nodes
            if nearest_core_nodes:
                all_are_endpoints = True
                for nearest_core_node in nearest_core_nodes:
                    nearest_core_count = 0
                    for edge_idx in core_edge_indices:
                        edge = edge_features[edge_idx]
                        if edge['properties']['u'] == nearest_core_node or edge['properties']['v'] == nearest_core_node:
                            nearest_core_count += 1
                    
                    if nearest_core_count != 1:
                        all_are_endpoints = False
                        break
                
                if all_are_endpoints:
                    endpoint_nodes.add(node)

    # Relaxation round: add nodes within near_threshold_m/2 of any endpoint
    relax_distance = near_threshold_m / 2
    original_endpoints = endpoint_nodes.copy()
    for node in all_nodes:
        if node in endpoint_nodes or node not in node_coords:
            continue
        for ep_node in original_endpoints:
            if ep_node in node_coords:
                dist = haversine_m(node_coords[node], node_coords[ep_node])
                if dist <= relax_distance:
                    endpoint_nodes.add(node)
                    break

    return endpoint_nodes


def _compute_cnode_position(
    nodes: List[str],
    connected_cedges: set,
    c_edge_end_associations: set,
    c_edges: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 15.0,
    c_edge_map: Dict[int, Dict[str, Any]] | None = None,
) -> tuple:
    """Compute C-node position based on connected C-edges using geometric intersection.
    
    Groups C-edges by direction using parallel_angle_threshold, then computes
    intersections only between non-parallel groups.
    
    Args:
        nodes: List of node IDs in this cluster.
        connected_cedges: Set of C-edge indices connected to this cluster.
        c_edge_end_associations: Set of (ce_idx, end_type) tuples.
        c_edges: List of C-edge dicts.
        node_coords: Dict of node_id -> (lon, lat).
        near_threshold_m: Distance threshold for intersection validation (default 50.0).
        parallel_angle_threshold: Angle threshold for grouping parallel C-edges (default 15.0).
    
    Returns:
        Computed position (lon, lat).
    """
    _cem = c_edge_map or {ce['idx']: ce for ce in c_edges}

    # Group C-edges by direction using parallel_angle_threshold
    direction_groups: List[List[int]] = []
    group_representatives: List[float] = []
    
    for ce_idx in connected_cedges:
        ce_dir = _cem[ce_idx]['direction_deg']
        
        assigned = False
        for i, rep_dir in enumerate(group_representatives):
            if angular_delta_mod180(ce_dir, rep_dir) < parallel_angle_threshold:
                direction_groups[i].append(ce_idx)
                assigned = True
                break
        
        if not assigned:
            direction_groups.append([ce_idx])
            group_representatives.append(ce_dir)
    
    num_groups = len(direction_groups)
    
    # Helper to get average position with fallback
    def get_avg_pos():
        avg_pos = average_position(nodes, node_coords)
        if avg_pos is None:
            for node in nodes:
                if node in node_coords:
                    return node_coords[node]
        return avg_pos
    
    # Helper to project to nearest C-edge
    def project_to_nearest(pos):
        if pos is None:
            pos = get_avg_pos()
        if pos is None:
            return None
        min_dist = float('inf')
        best_proj = pos
        for ce_idx in connected_cedges:
            ce = _cem[ce_idx]
            proj = project_to_line(pos, ce['start_coord'], ce['end_coord'])
            dist = haversine_m(pos, proj)
            if dist < min_dist:
                min_dist = dist
                best_proj = proj
        return best_proj
    
    # Helper to validate intersection is within reasonable distance
    def validate_intersection(intersection, ce1_idx, ce2_idx):
        ce1 = _cem[ce1_idx]
        ce2 = _cem[ce2_idx]
        
        ce1_start_proj = project_to_bearing_m(ce1['start_coord'], ce1['start_coord'], ce1['direction_deg'])
        ce1_end_proj = project_to_bearing_m(ce1['end_coord'], ce1['start_coord'], ce1['direction_deg'])
        ce1_min = min(ce1_start_proj, ce1_end_proj)
        ce1_max = max(ce1_start_proj, ce1_end_proj)
        
        ce2_start_proj = project_to_bearing_m(ce2['start_coord'], ce2['start_coord'], ce2['direction_deg'])
        ce2_end_proj = project_to_bearing_m(ce2['end_coord'], ce2['start_coord'], ce2['direction_deg'])
        ce2_min = min(ce2_start_proj, ce2_end_proj)
        ce2_max = max(ce2_start_proj, ce2_end_proj)
        
        int_proj_ce1 = project_to_bearing_m(intersection, ce1['start_coord'], ce1['direction_deg'])
        int_proj_ce2 = project_to_bearing_m(intersection, ce2['start_coord'], ce2['direction_deg'])
        
        # Relaxed validation: allow within near_threshold_m beyond extent
        in_range_ce1 = (ce1_min - near_threshold_m) <= int_proj_ce1 <= (ce1_max + near_threshold_m)
        in_range_ce2 = (ce2_min - near_threshold_m) <= int_proj_ce2 <= (ce2_max + near_threshold_m)
        
        return in_range_ce1 or in_range_ce2
    
    if num_groups == 1:
        # All C-edges parallel: project average position onto the line
        avg_pos = get_avg_pos()
        ce_idx = direction_groups[0][0]
        ce = _cem[ce_idx]
        return project_to_line(avg_pos, ce['start_coord'], ce['end_coord'])
    
    elif num_groups == 2:
        has_end_g0 = any(
            any(ce_idx == ce for ce, _ in c_edge_end_associations)
            for ce_idx in direction_groups[0]
        )
        has_end_g1 = any(
            any(ce_idx == ce for ce, _ in c_edge_end_associations)
            for ce_idx in direction_groups[1]
        )
        
        if has_end_g0 and not has_end_g1:
            main_ce = _cem[direction_groups[1][0]]
            avg_pos = get_avg_pos()
            return project_to_line(avg_pos, main_ce['start_coord'], main_ce['end_coord'])
        elif has_end_g1 and not has_end_g0:
            main_ce = _cem[direction_groups[0][0]]
            avg_pos = get_avg_pos()
            return project_to_line(avg_pos, main_ce['start_coord'], main_ce['end_coord'])
        
        # Two non-parallel groups: compute intersection
        ce1_idx = direction_groups[0][0]
        ce2_idx = direction_groups[1][0]
        ce1 = _cem[ce1_idx]
        ce2 = _cem[ce2_idx]
        
        intersection = line_intersection_2d(
            ce1['start_coord'], ce1['end_coord'],
            ce2['start_coord'], ce2['end_coord'],
        )
        
        if intersection is None:
            return project_to_nearest(get_avg_pos())
        
        if validate_intersection(intersection, ce1_idx, ce2_idx):
            return intersection
        else:
            return project_to_nearest(get_avg_pos())
    
    else:
        # 3+ non-parallel groups: compute cross-group intersections and average
        intersections = []
        
        for i in range(num_groups):
            for j in range(i + 1, num_groups):
                ce1_idx = direction_groups[i][0]
                ce2_idx = direction_groups[j][0]
                ce1 = _cem[ce1_idx]
                ce2 = _cem[ce2_idx]
                
                intersection = line_intersection_2d(
                    ce1['start_coord'], ce1['end_coord'],
                    ce2['start_coord'], ce2['end_coord'],
                )
                
                if intersection is not None:
                    if validate_intersection(intersection, ce1_idx, ce2_idx):
                        intersections.append(intersection)
        
        if intersections:
            avg_lon = sum(p[0] for p in intersections) / len(intersections)
            avg_lat = sum(p[1] for p in intersections) / len(intersections)
            return (avg_lon, avg_lat)
        else:
            return project_to_nearest(get_avg_pos())


def create_virtual_cnodes(
    clusters: List[List[str]],
    connection_nodes: Dict[str, set],
    c_edges: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    edge_clusters: List[List[int]],
    core_edges: Dict[int, set],
    edge_features: List[Dict[str, Any]],
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 15.0,
) -> Dict[int, Dict[str, Any]]:
    """Create virtual C-nodes at strategic positions.

    Args:
        clusters: List of clusters from cluster_connection_nodes().
        connection_nodes: Dict from identify_connection_nodes().
        c_edges: List of C-edge dicts.
        node_coords: Dict of node_id -> (lon, lat).
        edge_clusters: List of edge index lists for each C-edge.
        core_edges: Dict mapping C-edge index to set of core edge indices.
        edge_features: List of edge GeoJSON features.
        near_threshold_m: Distance threshold in meters for endpoint relaxation (default 50.0).
        parallel_angle_threshold: Angle threshold for grouping parallel C-edges (default 15.0).

    Returns:
        Dict mapping cluster_id to virtual C-node info:
        {
            'id': str,
            'position': (lon, lat),
            'connected_cedges': set,
            'original_nodes': list
        }
    """
    # Identify endpoint nodes for all C-edges (cached)
    c_edge_map = {ce['idx']: ce for ce in c_edges}
    cedge_endpoint_nodes: Dict[int, set] = {}
    for ce in c_edges:
        ce_idx = ce['idx']
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords,
            near_threshold_m=near_threshold_m, c_edge_map=c_edge_map
        )

    virtual_cnodes: Dict[int, Dict[str, Any]] = {}

    for cluster_id, nodes in enumerate(clusters):
        # Collect all C-edges connected to this cluster
        connected_cedges = set()
        for node in nodes:
            connected_cedges.update(connection_nodes[node])

        # Count how many C-edges have at least one endpoint node in this cluster
        endpoint_cedge_count = 0
        for ce_idx in connected_cedges:
            for node in nodes:
                if node in cedge_endpoint_nodes[ce_idx]:
                    endpoint_cedge_count += 1
                    break

        # Track which end of each C-edge this cluster is associated with
        # Use expanded endpoint nodes (cedge_endpoint_nodes) instead of exact start/end node matching
        c_edge_end_associations = set()
        for ce_idx in connected_cedges:
            ce = c_edge_map[ce_idx]
            ep_nodes = cedge_endpoint_nodes.get(ce_idx, set())
            
            # Project all endpoint nodes onto C-edge direction to split into start/end groups
            start_proj = project_to_bearing_m(ce['start_coord'], ce['start_coord'], ce['direction_deg'])
            end_proj = project_to_bearing_m(ce['end_coord'], ce['start_coord'], ce['direction_deg'])
            mid_proj = (start_proj + end_proj) / 2
            
            start_end_nodes = set()
            end_end_nodes = set()
            for ep_node in ep_nodes:
                if ep_node not in node_coords:
                    continue
                
                # Filter: only consider endpoint nodes within near_threshold_m of geometric endpoints
                # or near endpoint in projection direction (parabola check for extension)
                dist_to_start = haversine_m(node_coords[ep_node], ce['start_coord'])
                dist_to_end = haversine_m(node_coords[ep_node], ce['end_coord'])
                if dist_to_start > near_threshold_m and dist_to_end > near_threshold_m:
                    # Check if node is near endpoint in projection direction
                    proj_ext = project_to_bearing_m(
                        node_coords[ep_node], ce['start_coord'], ce['direction_deg'])
                    perp = abs(signed_perpendicular_offset_m(
                        node_coords[ep_node], ce['start_coord'], ce['direction_deg']))
                    a = near_threshold_m
                    # Distance along axis from each endpoint
                    axis_dist_start = abs(proj_ext - start_proj)
                    axis_dist_end = abs(proj_ext - end_proj)
                    # Parabola check: perp² ≤ 4a·axis_dist
                    # Allow if within parabola of either endpoint
                    in_start_region = axis_dist_start <= near_threshold_m and perp * perp <= 4 * a * axis_dist_start
                    in_end_region = axis_dist_end <= near_threshold_m and perp * perp <= 4 * a * axis_dist_end
                    # Also allow if beyond endpoint (extension direction)
                    start_ext = max(0.0, start_proj - proj_ext)
                    end_ext = max(0.0, proj_ext - end_proj)
                    in_start_parabola = start_ext > 0 and perp * perp <= 4 * a * start_ext
                    in_end_parabola = end_ext > 0 and perp * perp <= 4 * a * end_ext
                    if not (in_start_region or in_end_region or in_start_parabola or in_end_parabola):
                        continue
                
                proj = project_to_bearing_m(node_coords[ep_node], ce['start_coord'], ce['direction_deg'])
                if proj < mid_proj:
                    start_end_nodes.add(ep_node)
                else:
                    end_end_nodes.add(ep_node)
            
            # Check if cluster nodes are exclusively on one side of the midpoint
            # If nodes are on both sides (crossing midpoint), don't add association
            # and let geometric fallback decide based on cluster average position
            start_count = sum(1 for node in nodes if node in start_end_nodes)
            end_count = sum(1 for node in nodes if node in end_end_nodes)
            
            if start_count > 0 and end_count == 0:
                c_edge_end_associations.add((ce_idx, 'start'))
            elif end_count > 0 and start_count == 0:
                c_edge_end_associations.add((ce_idx, 'end'))
            # If both start_count > 0 and end_count > 0, don't add association

        # Geometric fallback: for C-edges without end associations, check if cluster is near an endpoint
        cedges_with_association = set(ce_idx for ce_idx, _ in c_edge_end_associations)
        avg_pos = average_position(nodes, node_coords)
        if avg_pos is None:
            for node in nodes:
                if node in node_coords:
                    avg_pos = node_coords[node]
                    break
        
        if avg_pos is not None:
            for ce_idx in connected_cedges:
                if ce_idx in cedges_with_association:
                    continue
                ce = c_edge_map[ce_idx]
                dist_to_start = haversine_m(avg_pos, ce['start_coord'])
                dist_to_end = haversine_m(avg_pos, ce['end_coord'])
                
                # Project cluster onto C-edge direction
                start_proj = project_to_bearing_m(ce['start_coord'], ce['start_coord'], ce['direction_deg'])
                end_proj = project_to_bearing_m(ce['end_coord'], ce['start_coord'], ce['direction_deg'])
                mid_proj = (start_proj + end_proj) / 2
                cluster_proj = project_to_bearing_m(avg_pos, ce['start_coord'], ce['direction_deg'])
                
                if cluster_proj < mid_proj:
                    if dist_to_start <= near_threshold_m:
                        c_edge_end_associations.add((ce_idx, 'start'))
                    else:
                        # Check parabola for start region/extension
                        perp = abs(signed_perpendicular_offset_m(
                            avg_pos, ce['start_coord'], ce['direction_deg']))
                        a = near_threshold_m
                        axis_dist_start = abs(cluster_proj - start_proj)
                        in_start_region = axis_dist_start <= near_threshold_m and perp * perp <= 4 * a * axis_dist_start
                        start_ext = max(0.0, start_proj - cluster_proj)
                        in_start_parabola = start_ext > 0 and perp * perp <= 4 * a * start_ext
                        if in_start_region or in_start_parabola:
                            c_edge_end_associations.add((ce_idx, 'start'))
                elif cluster_proj >= mid_proj:
                    if dist_to_end <= near_threshold_m:
                        c_edge_end_associations.add((ce_idx, 'end'))
                    else:
                        # Check parabola for end region/extension
                        perp = abs(signed_perpendicular_offset_m(
                            avg_pos, ce['start_coord'], ce['direction_deg']))
                        a = near_threshold_m
                        axis_dist_end = abs(cluster_proj - end_proj)
                        in_end_region = axis_dist_end <= near_threshold_m and perp * perp <= 4 * a * axis_dist_end
                        end_ext = max(0.0, cluster_proj - end_proj)
                        in_end_parabola = end_ext > 0 and perp * perp <= 4 * a * end_ext
                        if in_end_region or in_end_parabola:
                            c_edge_end_associations.add((ce_idx, 'end'))
                # else: T-junction (middle of C-edge), no association

        # Count how many C-edges actually end at this cluster
        cedges_ending_here = set(ce_idx for ce_idx, _ in c_edge_end_associations)
        cedges_ending_count = len(cedges_ending_here)
        
        # Compute position using helper function
        virtual_pos = _compute_cnode_position(
            nodes, connected_cedges, c_edge_end_associations, c_edges, node_coords,
            near_threshold_m=near_threshold_m,
            parallel_angle_threshold=parallel_angle_threshold,
            c_edge_map=c_edge_map
        )

        virtual_cnodes[cluster_id] = {
            'id': f'C-node_{cluster_id}',
            'position': virtual_pos,
            'connected_cedges': connected_cedges,
            'original_nodes': nodes,
            'c_edge_end_associations': c_edge_end_associations,
        }

    # Merge clusters that share any C-edge end
    # Build mapping: (c_edge_idx, end_type) -> list of cluster_ids
    end_to_clusters = {}
    for cluster_id, vnode in virtual_cnodes.items():
        for (ce_idx, end_type) in vnode.get('c_edge_end_associations', set()):
            key = (ce_idx, end_type)
            if key not in end_to_clusters:
                end_to_clusters[key] = []
            end_to_clusters[key].append(cluster_id)

    # Use Union-Find to merge clusters
    parent = {cid: cid for cid in virtual_cnodes.keys()}

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        root_x = find(x)
        root_y = find(y)
        if root_x != root_y:
            parent[root_x] = root_y

    # Merge clusters that share ANY C-edge end
    for (ce_idx, end_type), cluster_ids in end_to_clusters.items():
        if len(cluster_ids) > 1:
            # Merge all clusters in this group
            for i in range(1, len(cluster_ids)):
                union(cluster_ids[0], cluster_ids[i])

    # Build merged clusters
    merged_clusters = {}
    for cluster_id in virtual_cnodes.keys():
        root = find(cluster_id)
        if root not in merged_clusters:
            merged_clusters[root] = []
        merged_clusters[root].append(cluster_id)

    # Create new virtual C-nodes for merged clusters
    new_virtual_cnodes = {}
    for new_id, (root, old_cluster_ids) in enumerate(sorted(merged_clusters.items())):
        # Combine all nodes from merged clusters
        all_nodes = []
        all_connected_cedges = set()
        all_c_edge_end_associations = set()
        
        for old_id in old_cluster_ids:
            old_vnode = virtual_cnodes[old_id]
            all_nodes.extend(old_vnode['original_nodes'])
            all_connected_cedges.update(old_vnode['connected_cedges'])
            all_c_edge_end_associations.update(old_vnode.get('c_edge_end_associations', set()))
        
        # Remove duplicates
        all_nodes = list(set(all_nodes))
        
        # Determine position
        if len(old_cluster_ids) == 1:
            # Not merged: preserve original position
            virtual_pos = virtual_cnodes[old_cluster_ids[0]]['position']
        else:
            # Merged: re-compute position using the same logic
            virtual_pos = _compute_cnode_position(
                all_nodes, all_connected_cedges, all_c_edge_end_associations,
                c_edges, node_coords, near_threshold_m=near_threshold_m,
                parallel_angle_threshold=parallel_angle_threshold,
                c_edge_map=c_edge_map
            )
        
        new_virtual_cnodes[new_id] = {
            'id': f'C-node_{new_id}',
            'position': virtual_pos,
            'connected_cedges': all_connected_cedges,
            'original_nodes': all_nodes,
            'c_edge_end_associations': all_c_edge_end_associations,
        }

    return new_virtual_cnodes


def merge_t_junction_cnodes(
    virtual_cnodes: Dict[int, Dict[str, Any]],
    c_edges: List[Dict[str, Any]],
    near_threshold_m: float = 50.0,
    parallel_angle_threshold: float = 30.0,
) -> Dict[int, Dict[str, Any]]:
    """Merge T-junction C-nodes into their corresponding endpoint C-nodes.

    A T-junction occurs when two C-nodes share a C-edge, but one connects to
    the endpoint (has end association) while the other connects to the middle
    (no end association). These C-nodes are geometrically close but not merged
    by the standard merge logic.

    This function identifies such pairs and merges the T-junction C-node into
    the endpoint C-node, transferring its unique C-edges and associations.

    Parallel edges are excluded: if the T-junction C-node brings C-edges that
    are parallel to the shared C-edge (angle diff < parallel_angle_threshold),
    the merge is skipped since parallel edges cannot form a T-junction.

    Args:
        virtual_cnodes: Dict from create_virtual_cnodes().
        c_edges: List of C-edge dicts (needed for direction checks).
        near_threshold_m: Distance threshold for considering C-nodes as close (default 50.0).
        parallel_angle_threshold: Angle threshold for parallel detection (default 30.0).

    Returns:
        Updated dict of virtual C-nodes with T-junctions merged and re-numbered.
    """
    c_edge_map = {ce['idx']: ce for ce in c_edges}
    cedge_to_cnodes: Dict[int, List[int]] = {}
    for cn_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            if ce_idx not in cedge_to_cnodes:
                cedge_to_cnodes[ce_idx] = []
            cedge_to_cnodes[ce_idx].append(cn_id)

    to_delete = set()

    for ce_idx, cn_list in cedge_to_cnodes.items():
        for i in range(len(cn_list)):
            for j in range(i + 1, len(cn_list)):
                cn1_id, cn2_id = cn_list[i], cn_list[j]
                if cn1_id in to_delete or cn2_id in to_delete:
                    continue

                cn1 = virtual_cnodes[cn1_id]
                cn2 = virtual_cnodes[cn2_id]

                dist = haversine_m(cn1['position'], cn2['position'])
                if dist >= near_threshold_m:
                    continue

                shared = cn1['connected_cedges'] & cn2['connected_cedges']
                if not shared:
                    continue

                for ce in shared:
                    cn1_has = any(c == ce for c, _ in cn1['c_edge_end_associations'])
                    cn2_has = any(c == ce for c, _ in cn2['c_edge_end_associations'])

                    if cn1_has and not cn2_has:
                        ep_cn, tj_cn = cn1_id, cn2_id
                    elif cn2_has and not cn1_has:
                        ep_cn, tj_cn = cn2_id, cn1_id
                    else:
                        continue

                    shared_ce = c_edge_map.get(ce)
                    if not shared_ce:
                        continue
                    shared_dir = shared_ce['direction_deg']

                    tj_vnode = virtual_cnodes[tj_cn]
                    tj_brought = tj_vnode['connected_cedges'] - {ce}
                    has_parallel = False
                    for brought_ce_idx in tj_brought:
                        brought_ce = c_edge_map.get(brought_ce_idx)
                        if not brought_ce:
                            continue
                        if angular_delta_mod180(brought_ce['direction_deg'], shared_dir) < parallel_angle_threshold:
                            has_parallel = True
                            break
                    if has_parallel:
                        continue

                    ep_vnode = virtual_cnodes[ep_cn]
                    tj_vnode = virtual_cnodes[tj_cn]

                    ep_vnode['connected_cedges'].update(tj_vnode['connected_cedges'])
                    ep_vnode['c_edge_end_associations'].update(tj_vnode['c_edge_end_associations'])
                    ep_vnode['original_nodes'] = list(set(ep_vnode['original_nodes'] + tj_vnode['original_nodes']))

                    to_delete.add(tj_cn)
                    break

    for cn_id in to_delete:
        del virtual_cnodes[cn_id]

    new_cnodes: Dict[int, Dict[str, Any]] = {}
    for new_id, (old_id, vnode) in enumerate(sorted(virtual_cnodes.items())):
        vnode['id'] = f'C-node_{new_id}'
        new_cnodes[new_id] = vnode

    return new_cnodes


def merge_intermediate_t_junctions(
    virtual_cnodes: Dict[int, Dict[str, Any]],
    c_edges: List[Dict[str, Any]],
    near_threshold_m: float = 50.0,
    angle_threshold_deg: float = 15.0,
) -> Dict[int, Dict[str, Any]]:
    """Merge intermediate T-junction C-nodes on opposite sides of the same C-edge.

    Identifies pairs of C-nodes that:
    1. Both connect to the same C-edge as intermediate (non-endpoint) T-junctions
    2. Have close projection positions on that C-edge (< near_threshold_m)
    3. Have similar crossing angles (< angle_threshold_deg)
    4. Their crossing C-edges extend to opposite sides of the shared C-edge

    Args:
        virtual_cnodes: Dict from create_virtual_cnodes() or merge_t_junction_cnodes().
        c_edges: List of C-edge dicts.
        near_threshold_m: Maximum projection distance for merging (default 50.0).
        angle_threshold_deg: Maximum angle difference for merging (default 15.0).

    Returns:
        Updated dict of virtual C-nodes with intermediate T-junctions merged and re-numbered.
    """
    # Build C-edge index map
    c_edge_map = {ce['idx']: ce for ce in c_edges}

    # Identify intermediate T-junction C-nodes for each C-edge
    # An intermediate T-junction is a C-node connected to a C-edge but without end association
    cedge_to_intermediate_cnodes: Dict[int, List[int]] = {}

    for cn_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            # Check if this is an intermediate connection (no end association)
            has_end_assoc = any(
                assoc_ce_idx == ce_idx
                for assoc_ce_idx, _ in vnode['c_edge_end_associations']
            )
            if not has_end_assoc:
                if ce_idx not in cedge_to_intermediate_cnodes:
                    cedge_to_intermediate_cnodes[ce_idx] = []
                cedge_to_intermediate_cnodes[ce_idx].append(cn_id)

    # Find pairs to merge
    to_merge: List[Tuple[int, int]] = []  # (primary_cn_id, secondary_cn_id)

    for ce_idx, cn_list in cedge_to_intermediate_cnodes.items():
        if len(cn_list) < 2:
            continue

        ce = c_edge_map.get(ce_idx)
        if not ce:
            continue

        # Check all pairs
        for i in range(len(cn_list)):
            for j in range(i + 1, len(cn_list)):
                cn1_id = cn_list[i]
                cn2_id = cn_list[j]

                cn1 = virtual_cnodes[cn1_id]
                cn2 = virtual_cnodes[cn2_id]

                # Calculate projection positions on the C-edge
                proj1 = project_to_bearing_m(
                    cn1['position'], ce['start_coord'], ce['direction_deg']
                )
                proj2 = project_to_bearing_m(
                    cn2['position'], ce['start_coord'], ce['direction_deg']
                )

                # Check projection distance
                proj_dist = abs(proj1 - proj2)
                if proj_dist >= near_threshold_m:
                    continue

                # Check angle similarity and opposite sides
                # Get the crossing C-edge for each C-node (the one that's not ce_idx)
                cn1_other_cedges = cn1['connected_cedges'] - {ce_idx}
                cn2_other_cedges = cn2['connected_cedges'] - {ce_idx}

                if not cn1_other_cedges or not cn2_other_cedges:
                    continue

                # Use the first other C-edge for angle comparison
                cn1_other_ce = c_edge_map.get(next(iter(cn1_other_cedges)))
                cn2_other_ce = c_edge_map.get(next(iter(cn2_other_cedges)))

                if not cn1_other_ce or not cn2_other_ce:
                    continue

                angle_diff = angular_delta_mod180(
                    cn1_other_ce['direction_deg'], cn2_other_ce['direction_deg']
                )
                if angle_diff >= angle_threshold_deg:
                    continue

                shared_dir = ce['direction_deg']
                if (angular_delta_mod180(cn1_other_ce['direction_deg'], shared_dir) < angle_threshold_deg or
                        angular_delta_mod180(cn2_other_ce['direction_deg'], shared_dir) < angle_threshold_deg):
                    continue

                # Check which side of the C-edge the far endpoints of crossing C-edges are on
                # Find the far endpoint for each crossing C-edge
                cn1_pos = cn1['position']
                cn2_pos = cn2['position']

                # For cn1's crossing C-edge, find the endpoint farther from cn1
                dist1_start = haversine_m(cn1_pos, cn1_other_ce['start_coord'])
                dist1_end = haversine_m(cn1_pos, cn1_other_ce['end_coord'])
                if dist1_start > dist1_end:
                    far_end1 = cn1_other_ce['start_coord']
                else:
                    far_end1 = cn1_other_ce['end_coord']

                # For cn2's crossing C-edge, find the endpoint farther from cn2
                dist2_start = haversine_m(cn2_pos, cn2_other_ce['start_coord'])
                dist2_end = haversine_m(cn2_pos, cn2_other_ce['end_coord'])
                if dist2_start > dist2_end:
                    far_end2 = cn2_other_ce['start_coord']
                else:
                    far_end2 = cn2_other_ce['end_coord']

                # Calculate perpendicular offset of far endpoints relative to shared C-edge
                perp_far1 = signed_perpendicular_offset_m(
                    far_end1, ce['start_coord'], ce['direction_deg']
                )
                perp_far2 = signed_perpendicular_offset_m(
                    far_end2, ce['start_coord'], ce['direction_deg']
                )

                # Must be on opposite sides (signs differ)
                if (perp_far1 > 0) == (perp_far2 > 0):
                    continue

                # This pair should be merged
                to_merge.append((cn1_id, cn2_id))

    # Perform merges
    to_delete = set()

    for primary_id, secondary_id in to_merge:
        if primary_id in to_delete or secondary_id in to_delete:
            continue

        primary = virtual_cnodes[primary_id]
        secondary = virtual_cnodes[secondary_id]

        # Merge secondary into primary
        primary['connected_cedges'].update(secondary['connected_cedges'])
        primary['c_edge_end_associations'].update(secondary['c_edge_end_associations'])
        primary['original_nodes'] = list(
            set(primary['original_nodes'] + secondary['original_nodes'])
        )

        # Update position to average
        avg_lon = (primary['position'][0] + secondary['position'][0]) / 2
        avg_lat = (primary['position'][1] + secondary['position'][1]) / 2
        primary['position'] = (avg_lon, avg_lat)

        to_delete.add(secondary_id)

    # Delete merged C-nodes
    for cn_id in to_delete:
        del virtual_cnodes[cn_id]

    # Re-number C-nodes
    new_cnodes: Dict[int, Dict[str, Any]] = {}
    for new_id, (old_id, vnode) in enumerate(sorted(virtual_cnodes.items())):
        vnode['id'] = f'C-node_{new_id}'
        new_cnodes[new_id] = vnode

    return new_cnodes


def split_c_edges_at_intersection_nodes(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    parallel_angle_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Split C-edges at intersection C-nodes to maintain all connections.

    For C-edges connected to 3+ C-nodes, splits them into multiple pieces
    at intersection C-nodes (C-nodes connected to non-parallel C-edges).

    Args:
        c_edges: List of C-edge dicts (modified in-place for marking splits).
        virtual_cnodes: Dict from create_virtual_cnodes().
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.
        parallel_angle_threshold: Angle threshold for parallel detection.

    Returns:
        Updated list of C-edges (original + split pieces).
    """
    # Build index map for filtered C-edges
    c_edge_map = {ce['idx']: ce for ce in c_edges}

    # Build reverse mapping: C-edge index -> list of connected C-node indices
    cedge_to_vnodes: Dict[int, List[int]] = {}
    for vnode_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            if ce_idx not in cedge_to_vnodes:
                cedge_to_vnodes[ce_idx] = []
            cedge_to_vnodes[ce_idx].append(vnode_id)

    new_c_edges = []
    next_idx = max(ce['idx'] for ce in c_edges) + 1

    for c_edge in c_edges:
        ce_idx = c_edge['idx']
        if ce_idx not in cedge_to_vnodes:
            continue

        connected_vnodes = cedge_to_vnodes[ce_idx]

        # Only split if 3+ connected C-nodes
        if len(connected_vnodes) < 3:
            continue

        # Local dictionary to store projected positions for this C-edge
        projected_positions = {}

        # Classify each C-node as endpoint or intersection
        endpoint_vnodes = []
        intersection_vnodes = []

        for vnode_id in connected_vnodes:
            vnode = virtual_cnodes[vnode_id]
            is_endpoint = False

            # Check if this C-node is an endpoint of the current C-edge
            for (assoc_ce_idx, end_type) in vnode.get('c_edge_end_associations', set()):
                if assoc_ce_idx == ce_idx:
                    is_endpoint = True
                    break

            # Check if connected to at least one parallel C-edge
            if not is_endpoint:
                for other_ce_idx in vnode['connected_cedges']:
                    if other_ce_idx == ce_idx:
                        continue
                    angle_diff = angular_delta_mod180(
                        c_edge['direction_deg'],
                        c_edge_map[other_ce_idx]['direction_deg']
                    )
                    if angle_diff < parallel_angle_threshold:
                        is_endpoint = True
                        break

            if is_endpoint:
                endpoint_vnodes.append(vnode_id)
            else:
                intersection_vnodes.append(vnode_id)

        # Identify 2 endpoint C-nodes
        if len(endpoint_vnodes) >= 2:
            # Use the 2 endpoint C-nodes with most extreme projections
            projections = []
            for vnode_id in endpoint_vnodes:
                vnode = virtual_cnodes[vnode_id]
                proj = project_to_bearing_m(
                    vnode['position'],
                    c_edge['start_coord'],
                    c_edge['direction_deg']
                )
                projections.append((proj, vnode_id))
            projections.sort(key=lambda x: x[0])
            start_vnode_id = projections[0][1]
            end_vnode_id = projections[-1][1]
        elif len(endpoint_vnodes) == 1:
            # Use the 1 endpoint C-node + nearest intersection C-node
            endpoint_vnode_id = endpoint_vnodes[0]
            endpoint_proj = project_to_bearing_m(
                virtual_cnodes[endpoint_vnode_id]['position'],
                c_edge['start_coord'],
                c_edge['direction_deg']
            )

            # Find nearest intersection C-node
            nearest_intersection = None
            min_dist = float('inf')
            for vnode_id in intersection_vnodes:
                vnode = virtual_cnodes[vnode_id]
                proj = project_to_bearing_m(
                    vnode['position'],
                    c_edge['start_coord'],
                    c_edge['direction_deg']
                )
                dist = abs(proj - endpoint_proj)
                if dist < min_dist:
                    min_dist = dist
                    nearest_intersection = vnode_id

            if endpoint_proj < 0:
                start_vnode_id = endpoint_vnode_id
                end_vnode_id = nearest_intersection
            else:
                start_vnode_id = nearest_intersection
                end_vnode_id = endpoint_vnode_id
        else:
            # No endpoint C-nodes, find 2 nearest to geometric endpoints
            projections = []
            for vnode_id in intersection_vnodes:
                vnode = virtual_cnodes[vnode_id]
                proj = project_to_bearing_m(
                    vnode['position'],
                    c_edge['start_coord'],
                    c_edge['direction_deg']
                )
                projections.append((proj, vnode_id))
            projections.sort(key=lambda x: x[0])
            start_vnode_id = projections[0][1]
            end_vnode_id = projections[-1][1]

        # Track endpoint sources
        start_from_endpoint = start_vnode_id in endpoint_vnodes
        end_from_endpoint = end_vnode_id in endpoint_vnodes

        # Calculate geometric extent
        start_proj = project_to_bearing_m(
            c_edge['start_coord'],
            c_edge['start_coord'],
            c_edge['direction_deg']
        )
        end_proj = project_to_bearing_m(
            c_edge['end_coord'],
            c_edge['start_coord'],
            c_edge['direction_deg']
        )
        geometric_start = min(start_proj, end_proj)
        geometric_end = max(start_proj, end_proj)

        # Filter and merge out-of-range intersection C-nodes
        valid_intersection_vnodes = []
        tolerance = 1.0  # 1 meter tolerance

        # Skip splitting if C-edge is too short (< 1m)
        if geometric_end - geometric_start < tolerance:
            continue

        # Get endpoint positions for comparison
        start_vnode_pos = virtual_cnodes[start_vnode_id]['position']
        end_vnode_pos = virtual_cnodes[end_vnode_id]['position']

        for vnode_id in intersection_vnodes:
            # Skip if this is the start or end endpoint (already handled)
            if vnode_id == start_vnode_id or vnode_id == end_vnode_id:
                continue

            vnode = virtual_cnodes[vnode_id]
            vnode_pos = vnode['position']
            proj = project_to_bearing_m(
                vnode_pos,
                c_edge['start_coord'],
                c_edge['direction_deg']
            )

            # Check if within 1m or 7th decimal of start or end endpoint
            dist_to_start = haversine_m(vnode_pos, start_vnode_pos)
            dist_to_end = haversine_m(vnode_pos, end_vnode_pos)
            
            # Check coordinate difference (7th decimal)
            coord_diff_start = max(
                abs(vnode_pos[0] - start_vnode_pos[0]),
                abs(vnode_pos[1] - start_vnode_pos[1])
            )
            coord_diff_end = max(
                abs(vnode_pos[0] - end_vnode_pos[0]),
                abs(vnode_pos[1] - end_vnode_pos[1])
            )
            
            # If within 1m or 7th decimal (1e-7) of endpoint, skip it
            if (dist_to_start < tolerance or coord_diff_start < 1e-7 or
                dist_to_end < tolerance or coord_diff_end < 1e-7):
                continue

            # Check if within geometric extent
            if geometric_start - tolerance <= proj <= geometric_end + tolerance:
                valid_intersection_vnodes.append(vnode_id)
            else:
                # Out of range: handle based on endpoint source
                if proj < geometric_start - tolerance:
                    # Out of range at start
                    if start_from_endpoint:
                        # Parallel C-edge exists at start, update position to start endpoint
                        projected_positions[vnode_id] = start_vnode_pos
                    else:
                        # No parallel C-edge at start, this becomes the new start endpoint
                        start_vnode_id = vnode_id
                        start_from_endpoint = False
                else:  # proj > geometric_end + tolerance
                    # Out of range at end
                    if end_from_endpoint:
                        # Parallel C-edge exists at end, update position to end endpoint
                        projected_positions[vnode_id] = end_vnode_pos
                    else:
                        # No parallel C-edge at end, this becomes the new end endpoint
                        end_vnode_id = vnode_id
                        end_from_endpoint = False

        intersection_vnodes = valid_intersection_vnodes

        # Project all C-nodes onto the parent C-edge's line to ensure collinearity
        all_vnode_ids = [start_vnode_id] + intersection_vnodes + [end_vnode_id]
        # Remove duplicates while preserving order
        seen = set()
        unique_vnode_ids = []
        for vnode_id in all_vnode_ids:
            if vnode_id not in seen:
                seen.add(vnode_id)
                unique_vnode_ids.append(vnode_id)

        # Project each C-node onto the line defined by parent C-edge
        for vnode_id in unique_vnode_ids:
            vnode = virtual_cnodes[vnode_id]
            vnode_pos = vnode['position']

            # Project onto the parent C-edge's line
            proj = project_to_bearing_m(
                vnode_pos,
                c_edge['start_coord'],
                c_edge['direction_deg']
            )

            # Convert projection back to coordinate on the line (no perpendicular offset)
            projected_pos = offset_to_coordinate(
                c_edge['start_coord'],
                c_edge['direction_deg'],
                proj,
                0.0  # Force perpendicular offset to zero
            )

            # Store projected position locally for this C-edge
            projected_positions[vnode_id] = projected_pos

        # Sort all C-nodes by projection
        all_vnodes = [start_vnode_id] + intersection_vnodes + [end_vnode_id]
        if start_vnode_id == end_vnode_id:
            all_vnodes = [start_vnode_id] + intersection_vnodes
        elif start_vnode_id in intersection_vnodes:
            intersection_vnodes.remove(start_vnode_id)
            all_vnodes = [start_vnode_id] + intersection_vnodes + [end_vnode_id]
        elif end_vnode_id in intersection_vnodes:
            intersection_vnodes.remove(end_vnode_id)
            all_vnodes = [start_vnode_id] + intersection_vnodes + [end_vnode_id]

        projections = []
        for vnode_id in all_vnodes:
            vnode = virtual_cnodes[vnode_id]
            # Use projected position if available, otherwise use original position
            vnode_pos = projected_positions.get(vnode_id, vnode['position'])
            proj = project_to_bearing_m(
                vnode_pos,
                c_edge['start_coord'],
                c_edge['direction_deg']
            )
            projections.append((proj, vnode_id))
        projections.sort(key=lambda x: x[0])

        # Create C-edge pieces
        split_pieces = []
        for i in range(len(projections) - 1):
            start_proj, start_vnode_id = projections[i]
            end_proj, end_vnode_id = projections[i + 1]

            start_vnode = virtual_cnodes[start_vnode_id]
            end_vnode = virtual_cnodes[end_vnode_id]

            # Count edges in this segment
            segment_edges = []
            for edge_idx in edge_clusters[ce_idx]:
                edge = edge_features[edge_idx]
                edge_mid = (
                    (edge['geometry']['coordinates'][0][0] + edge['geometry']['coordinates'][-1][0]) / 2,
                    (edge['geometry']['coordinates'][0][1] + edge['geometry']['coordinates'][-1][1]) / 2
                )
                edge_proj = project_to_bearing_m(
                    edge_mid,
                    c_edge['start_coord'],
                    c_edge['direction_deg']
                )
                if start_proj <= edge_proj <= end_proj:
                    segment_edges.append(edge_idx)

            # Use projected positions if available
            start_coord = projected_positions.get(start_vnode_id, start_vnode['position'])
            end_coord = projected_positions.get(end_vnode_id, end_vnode['position'])

            piece = {
                'idx': next_idx,
                'parent_idx': c_edge['parent_idx'],
                'split_idx': i,
                'start_coord': start_coord,
                'end_coord': end_coord,
                'direction_deg': c_edge['direction_deg'],
                'start_node_id': start_vnode['id'],
                'end_node_id': end_vnode['id'],
                'size': len(segment_edges),
                'connected_vnodes': [start_vnode_id, end_vnode_id],
                'length_m': haversine_m(start_coord, end_coord),
                'road_length_m': end_proj - start_proj,
                'highway_level': c_edge.get('highway_level', 99),
            }
            split_pieces.append(piece)
            next_idx += 1

        # Mark original C-edge as split
        c_edge['is_split'] = True
        c_edge['split_pieces'] = [p['idx'] for p in split_pieces]

        new_c_edges.extend(split_pieces)

    # Handle C-edges with 1 or 2 connected C-nodes (no splitting needed)
    for c_edge in c_edges:
        ce_idx = c_edge['idx']
        if ce_idx not in cedge_to_vnodes:
            continue
        
        # Skip original C-edges that have been split
        if c_edge.get('is_split', False):
            continue
        
        connected_vnodes = cedge_to_vnodes[ce_idx]
        
        if len(connected_vnodes) == 1:
            # Only one virtual C-node: use it as the closer endpoint
            vnode_id = connected_vnodes[0]
            vnode = virtual_cnodes[vnode_id]
            vnode_pos = vnode['position']
            
            # Compute distances to current endpoints
            start_dist = haversine_m(c_edge['start_coord'], vnode_pos)
            end_dist = haversine_m(c_edge['end_coord'], vnode_pos)
            
            # Use as the closer endpoint
            if start_dist < end_dist:
                c_edge['start_node_id'] = vnode['id']
                c_edge['start_coord'] = vnode_pos
            else:
                c_edge['end_node_id'] = vnode['id']
                c_edge['end_coord'] = vnode_pos
        
        elif len(connected_vnodes) == 2:
            # Two virtual C-nodes: use them as endpoints
            # Project both along the C-edge's major direction
            projections = []
            for vnode_id in connected_vnodes:
                vnode = virtual_cnodes[vnode_id]
                proj = project_to_bearing_m(
                    vnode['position'],
                    c_edge['start_coord'],
                    c_edge['direction_deg']
                )
                projections.append((proj, vnode_id))
            
            # Sort by projection
            projections.sort(key=lambda x: x[0])
            
            # Use the most negative projection as start, most positive as end
            start_vnode_id = projections[0][1]
            end_vnode_id = projections[1][1]
            
            start_vnode = virtual_cnodes[start_vnode_id]
            end_vnode = virtual_cnodes[end_vnode_id]
            
            c_edge['start_node_id'] = start_vnode['id']
            c_edge['start_coord'] = start_vnode['position']
            c_edge['end_node_id'] = end_vnode['id']
            c_edge['end_coord'] = end_vnode['position']

    # Update C-nodes' connected_cedges: replace split originals with split pieces
    for c_edge in c_edges:
        if c_edge.get('is_split', False):
            ce_idx = c_edge['idx']
            piece_indices = c_edge.get('split_pieces', [])
            
            # Build mapping: vnode_id -> set of piece indices it's connected to
            vnode_to_pieces: Dict[int, set] = defaultdict(set)
            for piece_idx in piece_indices:
                piece = None
                for new_ce in new_c_edges:
                    if new_ce['idx'] == piece_idx:
                        piece = new_ce
                        break
                if piece:
                    for vn_id in piece.get('connected_vnodes', []):
                        vnode_to_pieces[vn_id].add(piece_idx)
            
            # Update all C-nodes that reference the original
            for vn_id, vnode in virtual_cnodes.items():
                if ce_idx in vnode['connected_cedges']:
                    vnode['connected_cedges'].remove(ce_idx)
                    # Add references to relevant split pieces (if any)
                    if vn_id in vnode_to_pieces:
                        vnode['connected_cedges'].update(vnode_to_pieces[vn_id])

    # Return original + split pieces
    return c_edges + new_c_edges


def update_c_edge_endpoints(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
) -> None:
    """Store connected C-nodes for each C-edge and update endpoint coordinates.

    Updates endpoint coordinates to match connected C-node positions for visual
    continuity. The direction_deg field is preserved unchanged.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        virtual_cnodes: Dict from create_virtual_cnodes().
    """
    cedge_to_vnodes: Dict[int, List[int]] = {}
    for vnode_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            if ce_idx not in cedge_to_vnodes:
                cedge_to_vnodes[ce_idx] = []
            cedge_to_vnodes[ce_idx].append(vnode_id)

    for c_edge in c_edges:
        ce_idx = c_edge['idx']
        if ce_idx not in cedge_to_vnodes:
            c_edge['connected_vnodes'] = []
            continue

        vnodes = cedge_to_vnodes[ce_idx]

        if len(vnodes) < 2:
            c_edge['connected_vnodes'] = vnodes
            continue

        direction = c_edge['direction_deg']
        origin = c_edge['start_coord']

        projections = []
        for vn_id in vnodes:
            vn = virtual_cnodes[vn_id]
            proj = project_to_bearing_m(vn['position'], origin, direction)
            projections.append((proj, vn_id))

        projections.sort(key=lambda x: x[0])

        start_proj, start_vn_id = projections[0]
        end_proj, end_vn_id = projections[-1]

        c_edge['start_coord'] = virtual_cnodes[start_vn_id]['position']
        c_edge['end_coord'] = virtual_cnodes[end_vn_id]['position']
        c_edge['start_node_id'] = virtual_cnodes[start_vn_id]['id']
        c_edge['end_node_id'] = virtual_cnodes[end_vn_id]['id']
        c_edge['connected_vnodes'] = [start_vn_id, end_vn_id]


def filter_dangling_cedges(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """移除所有断头路（迭代剥离 degree ≤ 1 的 C-nodes）
    
    使用队列驱动的级联删除算法，一次性找出所有断头路。
    当一个 C-node 的度数变为 ≤ 1 时，将其连接的 C-edges 移除，
    并检查受影响的 C-nodes 是否也变成断头。
    
    Args:
        c_edges: C-edge 字典列表
        virtual_cnodes: 虚拟 C-node 字典
    
    Returns:
        过滤后的 C-edge 列表（所有保留的 C-nodes 度数 ≥ 2）
    """
    from collections import deque
    
    # 构建双向映射
    cedge_to_cnodes: Dict[int, set] = defaultdict(set)
    for cn_id, cn in virtual_cnodes.items():
        for ce_idx in cn['connected_cedges']:
            cedge_to_cnodes[ce_idx].add(cn_id)
    
    cn_to_cedges: Dict[int, set] = defaultdict(set)
    for ce_idx, cn_ids in cedge_to_cnodes.items():
        for cn_id in cn_ids:
            cn_to_cedges[cn_id].add(ce_idx)
    
    removed_cedges: set = set()
    removed_cnodes: set = set()
    
    # 第一步：移除连接 < 2 个 C-nodes 的 C-edges
    for ce in c_edges:
        if len(cedge_to_cnodes.get(ce['idx'], set())) < 2:
            removed_cedges.add(ce['idx'])
            # 更新受影响的 C-nodes
            for cn_id in cedge_to_cnodes.get(ce['idx'], set()):
                cn_to_cedges[cn_id].discard(ce['idx'])
    
    # 第二步：初始化队列：所有 degree ≤ 1 的 C-nodes
    queue = deque([cn_id for cn_id, cedgs in cn_to_cedges.items() if len(cedgs) <= 1])
    
    # 迭代剥离
    while queue:
        cn_id = queue.popleft()
        if cn_id in removed_cnodes:
            continue
        
        removed_cnodes.add(cn_id)
        
        # 移除该 C-node 连接的所有 C-edges
        for ce_idx in list(cn_to_cedges[cn_id]):
            if ce_idx in removed_cedges:
                continue
            removed_cedges.add(ce_idx)
            
            # 检查受影响的 C-nodes
            for other_cn_id in cedge_to_cnodes[ce_idx]:
                if other_cn_id == cn_id or other_cn_id in removed_cnodes:
                    continue
                cn_to_cedges[other_cn_id].discard(ce_idx)
                if len(cn_to_cedges[other_cn_id]) <= 1:
                    queue.append(other_cn_id)
    
    # 返回保留的 C-edges
    return [ce for ce in c_edges if ce['idx'] not in removed_cedges]


def find_parallelograms_near_cnodes(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
    edge_features: List[Dict[str, Any]],
    near_threshold_m: float = 50.0,
    max_edge_length_m: float = 25.0,
    min_edge_length_m: float = 1.0,
    parallel_angle_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Find parallelograms formed by raw edges near C-nodes.

    Only searches for parallelograms in the vicinity of C-nodes that connect
    3 or more C-edges (intersection points). This avoids the O(N^4) complexity
    of searching the entire dataset.

    Args:
        c_edges: List of C-edge dicts.
        virtual_cnodes: Dict of C-node dicts from create_virtual_cnodes().
        edge_features: List of edge GeoJSON features.
        near_threshold_m: Distance threshold for C-node clustering (default 50.0).
        max_edge_length_m: Maximum edge length in meters (default 25.0).
        min_edge_length_m: Minimum edge length in meters (default 1.0).
        parallel_angle_threshold: Angle threshold for parallel detection (default 15.0).

    Returns:
        List of parallelogram dicts:
        {
            'vertices': tuple of 4 (lon, lat) coordinates,
            'edges': tuple of 4 edge indices,
            'edge_lengths': tuple of 4 edge lengths in meters,
        }
    """
    search_radius_m = 2 * near_threshold_m

    # Identify intersection C-nodes (3+ connected C-edges)
    intersection_cnodes = []
    for vnode_id, vnode in virtual_cnodes.items():
        if len(vnode.get('connected_cedges', set())) >= 3:
            intersection_cnodes.append(vnode)

    # For each intersection C-node, collect nearby edges
    all_parallelograms = []
    seen_edge_sets = set()

    for vnode in intersection_cnodes:
        vnode_pos = vnode['position']

        # Collect edges within search radius
        local_edges = []
        for idx, ef in enumerate(edge_features):
            coords = ef['geometry']['coordinates']
            mid_lon = (coords[0][0] + coords[-1][0]) / 2
            mid_lat = (coords[0][1] + coords[-1][1]) / 2
            mid_coord = (mid_lon, mid_lat)

            if haversine_m(mid_coord, vnode_pos) <= search_radius_m:
                local_edges.append((idx, ef))

        # Run parallelogram detection on local edges
        local_parallelograms = _find_edge_parallelograms_local(
            local_edges,
            max_edge_length_m=max_edge_length_m,
            min_edge_length_m=min_edge_length_m,
            parallel_angle_threshold=parallel_angle_threshold,
        )

        # Deduplicate across all C-nodes
        for pg in local_parallelograms:
            edge_set = frozenset(pg['edges'])
            if edge_set not in seen_edge_sets:
                seen_edge_sets.add(edge_set)
                all_parallelograms.append(pg)

    return all_parallelograms


def _find_edge_parallelograms_local(
    local_edges: List[tuple],
    max_edge_length_m: float = 25.0,
    min_edge_length_m: float = 1.0,
    parallel_angle_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Find parallelograms in a local set of edges.

    Args:
        local_edges: List of (idx, edge_feature) tuples.
        max_edge_length_m: Maximum edge length in meters.
        min_edge_length_m: Minimum edge length in meters.
        parallel_angle_threshold: Angle threshold for parallel detection.

    Returns:
        List of parallelogram dicts.
    """
    # Build direction groups and spatial grid
    direction_groups: Dict[int, List[int]] = {}
    spatial_grid: Dict[tuple, List[int]] = {}
    cell_size = max_edge_length_m / 111000.0

    # Map from local index to global index
    local_to_global = {}

    for local_idx, (global_idx, ef) in enumerate(local_edges):
        local_to_global[local_idx] = global_idx

        props = ef['properties']
        coords = ef['geometry']['coordinates']
        start = (coords[0][0], coords[0][1])
        end = (coords[-1][0], coords[-1][1])

        length = haversine_m(start, end)
        if length < min_edge_length_m or length > max_edge_length_m:
            continue

        direction = props.get('direction_deg', 0)
        group_key = int(round(direction / parallel_angle_threshold) * parallel_angle_threshold)
        if group_key not in direction_groups:
            direction_groups[group_key] = []
        direction_groups[group_key].append(local_idx)

        # Use start/end coordinates for spatial grid
        for coord in (start, end):
            cell = (int(coord[0] / cell_size), int(coord[1] / cell_size))
            if cell not in spatial_grid:
                spatial_grid[cell] = []
            spatial_grid[cell].append(local_idx)

    def get_nearby_edges(coord: tuple, exclude: set) -> List[int]:
        """Get edges near a coordinate, excluding specified edge indices."""
        cell_x = int(coord[0] / cell_size)
        cell_y = int(coord[1] / cell_size)
        result = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cell = (cell_x + dx, cell_y + dy)
                if cell in spatial_grid:
                    for eidx in spatial_grid[cell]:
                        if eidx not in exclude:
                            result.append(eidx)
        return result

    parallelograms = []
    seen = set()
    group_keys = sorted(direction_groups.keys())

    for i in range(len(group_keys)):
        for j in range(i + 1, len(group_keys)):
            g1_key = group_keys[i]
            g2_key = group_keys[j]
            angle_diff = abs(g1_key - g2_key)
            if angle_diff > 90:
                angle_diff = 180 - angle_diff
            if angle_diff < 30:
                continue

            g1_edges = direction_groups[g1_key]
            g2_edges = direction_groups[g2_key]

            # Check both directions: g1 as "A pair" and g2 as "B pair"
            for group_a, group_b in [(g1_edges, g2_edges), (g2_edges, g1_edges)]:
                for ai in range(len(group_a)):
                    for aj in range(ai + 1, len(group_a)):
                        a1_local = group_a[ai]
                        a2_local = group_a[aj]

                        a1 = local_edges[a1_local][1]
                        a2 = local_edges[a2_local][1]

                        a1_u, a1_v = a1['properties']['u'], a1['properties']['v']
                        a2_u, a2_v = a2['properties']['u'], a2['properties']['v']

                        # A edges must not share nodes
                        if a1_u in (a2_u, a2_v) or a1_v in (a2_u, a2_v):
                            continue

                        # Find B edges near A edge endpoints
                        exclude = {a1_local, a2_local}
                        nearby_b = set()

                        a1_start = (a1['geometry']['coordinates'][0][0], a1['geometry']['coordinates'][0][1])
                        a1_end = (a1['geometry']['coordinates'][-1][0], a1['geometry']['coordinates'][-1][1])
                        a2_start = (a2['geometry']['coordinates'][0][0], a2['geometry']['coordinates'][0][1])
                        a2_end = (a2['geometry']['coordinates'][-1][0], a2['geometry']['coordinates'][-1][1])

                        for coord in (a1_start, a1_end, a2_start, a2_end):
                            for eidx in get_nearby_edges(coord, exclude):
                                if eidx in group_b:
                                    nearby_b.add(eidx)

                        nearby_b = list(nearby_b)

                        for bi in range(len(nearby_b)):
                            for bj in range(bi + 1, len(nearby_b)):
                                b1_local = nearby_b[bi]
                                b2_local = nearby_b[bj]

                                b1 = local_edges[b1_local][1]
                                b2 = local_edges[b2_local][1]

                                b1_u, b1_v = b1['properties']['u'], b1['properties']['v']
                                b2_u, b2_v = b2['properties']['u'], b2['properties']['v']

                                # B edges must not share nodes
                                if b1_u in (b2_u, b2_v) or b1_v in (b2_u, b2_v):
                                    continue

                                # Check if 4 edges form a closed cycle
                                all_nodes = [a1_u, a1_v, a2_u, a2_v, b1_u, b1_v, b2_u, b2_v]
                                unique_nodes = list(set(all_nodes))

                                if len(unique_nodes) != 4:
                                    continue

                                # Each node must appear exactly twice (degree 2)
                                node_count = {}
                                for n in all_nodes:
                                    node_count[n] = node_count.get(n, 0) + 1

                                if any(c != 2 for c in node_count.values()):
                                    continue

                                # Deduplicate using global indices
                                a1_global = local_to_global[a1_local]
                                a2_global = local_to_global[a2_local]
                                b1_global = local_to_global[b1_local]
                                b2_global = local_to_global[b2_local]

                                cycle_key = frozenset([a1_global, a2_global, b1_global, b2_global])
                                if cycle_key in seen:
                                    continue
                                seen.add(cycle_key)

                                # Compute edge lengths and vertices
                                a1_start = (a1['geometry']['coordinates'][0][0], a1['geometry']['coordinates'][0][1])
                                a1_end = (a1['geometry']['coordinates'][-1][0], a1['geometry']['coordinates'][-1][1])
                                a2_start = (a2['geometry']['coordinates'][0][0], a2['geometry']['coordinates'][0][1])
                                a2_end = (a2['geometry']['coordinates'][-1][0], a2['geometry']['coordinates'][-1][1])
                                b1_start = (b1['geometry']['coordinates'][0][0], b1['geometry']['coordinates'][0][1])
                                b1_end = (b1['geometry']['coordinates'][-1][0], b1['geometry']['coordinates'][-1][1])
                                b2_start = (b2['geometry']['coordinates'][0][0], b2['geometry']['coordinates'][0][1])
                                b2_end = (b2['geometry']['coordinates'][-1][0], b2['geometry']['coordinates'][-1][1])

                                len_a1 = haversine_m(a1_start, a1_end)
                                len_a2 = haversine_m(a2_start, a2_end)
                                len_b1 = haversine_m(b1_start, b1_end)
                                len_b2 = haversine_m(b2_start, b2_end)

                                # Convert node_id strings to coordinates
                                vertex_coords = []
                                for node_id in unique_nodes:
                                    parts = node_id.split('_')
                                    lon = float(parts[0])
                                    lat = float(parts[1])
                                    vertex_coords.append((lon, lat))

                                parallelograms.append({
                                    'vertices': tuple(vertex_coords),
                                    'edges': (a1_global, a2_global, b1_global, b2_global),
                                    'edge_lengths': (len_a1, len_a2, len_b1, len_b2),
                                })

    return parallelograms


def cluster_parallelograms(
    parallelograms: List[Dict[str, Any]],
    cluster_radius_m: float = 50.0,
) -> List[Dict[str, Any]]:
    """Cluster nearby parallelograms into crossroad groups.

    Uses Union-Find to group parallelograms whose centers are within
    cluster_radius_m of each other.

    Args:
        parallelograms: List of parallelogram dicts from find_parallelograms_near_cnodes().
        cluster_radius_m: Maximum distance between parallelogram centers to cluster (default 50.0).

    Returns:
        List of crossroad cluster dicts:
        [
            {
                'center': (lon, lat),
                'parallelograms': [...],
            },
            ...
        ]
    """
    if not parallelograms:
        return []

    n = len(parallelograms)

    centers = []
    for pg in parallelograms:
        vertices = pg['vertices']
        cx = sum(v[0] for v in vertices) / len(vertices)
        cy = sum(v[1] for v in vertices) / len(vertices)
        centers.append((cx, cy))

    parent = list(range(n))

    def find(i: int) -> int:
        if parent[i] != i:
            parent[i] = find(parent[i])
        return parent[i]

    def union(i: int, j: int) -> None:
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(n):
        for j in range(i + 1, n):
            if haversine_m(centers[i], centers[j]) < cluster_radius_m:
                union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
        clusters[root].append(i)

    result = []
    for indices in clusters.values():
        cluster_pgs = [parallelograms[i] for i in indices]
        cluster_centers = [centers[i] for i in indices]
        avg_lon = sum(c[0] for c in cluster_centers) / len(cluster_centers)
        avg_lat = sum(c[1] for c in cluster_centers) / len(cluster_centers)
        result.append({
            'center': (avg_lon, avg_lat),
            'parallelograms': cluster_pgs,
        })

    return result


def merge_intersection_cnodes(
    virtual_cnodes: Dict[int, Dict[str, Any]],
    intersection_clusters: List[Dict[str, Any]],
    near_threshold_m: float = 50.0,
) -> Dict[int, Dict[str, Any]]:
    """Merge C-nodes near crossroad clusters into single C-nodes.

    For each crossroad cluster, finds all C-nodes within near_threshold_m
    of the cluster center and merges them into one C-node.

    Args:
        virtual_cnodes: Dict from create_virtual_cnodes().
        intersection_clusters: List of crossroad cluster dicts from cluster_parallelograms().
        near_threshold_m: Distance threshold for C-node merging (default 50.0).

    Returns:
        Updated dict of virtual C-nodes with intersection C-nodes merged and re-numbered.
    """
    if not intersection_clusters:
        return virtual_cnodes

    to_merge: Dict[int, int] = {}

    for cluster_idx, cluster in enumerate(intersection_clusters):
        cluster_center = cluster['center']

        nearby_cnodes = []
        for cn_id, vnode in virtual_cnodes.items():
            if cn_id in to_merge:
                continue
            dist = haversine_m(vnode['position'], cluster_center)
            if dist < near_threshold_m:
                nearby_cnodes.append(cn_id)

        if len(nearby_cnodes) <= 1:
            continue

        merged_cedges = set()
        merged_assoc = set()
        merged_nodes = []
        merged_positions = []

        for cn_id in nearby_cnodes:
            vnode = virtual_cnodes[cn_id]
            merged_cedges.update(vnode['connected_cedges'])
            merged_assoc.update(vnode['c_edge_end_associations'])
            merged_nodes.extend(vnode['original_nodes'])
            merged_positions.append(vnode['position'])

        avg_lon = sum(p[0] for p in merged_positions) / len(merged_positions)
        avg_lat = sum(p[1] for p in merged_positions) / len(merged_positions)

        primary_id = nearby_cnodes[0]
        virtual_cnodes[primary_id]['position'] = (avg_lon, avg_lat)
        virtual_cnodes[primary_id]['connected_cedges'] = merged_cedges
        virtual_cnodes[primary_id]['c_edge_end_associations'] = merged_assoc
        virtual_cnodes[primary_id]['original_nodes'] = list(set(merged_nodes))

        for cn_id in nearby_cnodes[1:]:
            to_merge[cn_id] = primary_id

    for cn_id in to_merge:
        del virtual_cnodes[cn_id]

    new_cnodes: Dict[int, Dict[str, Any]] = {}
    for new_id, (old_id, vnode) in enumerate(sorted(virtual_cnodes.items())):
        vnode['id'] = f'C-node_{new_id}'
        new_cnodes[new_id] = vnode

    return new_cnodes


def find_small_parallelograms(
    c_edges: List[Dict[str, Any]],
    max_edge_length_m: float = 25.0,
    min_edge_length_m: float = 1.0,
    parallel_angle_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Find all parallelograms formed by crossing C-edges with small edge lengths.

    Groups C-edges by direction, then for each pair of non-parallel groups,
    finds parallelograms where all 4 edges are shorter than max_edge_length_m
    and longer than min_edge_length_m (to exclude coincident edges).

    Args:
        c_edges: List of C-edge dicts.
        max_edge_length_m: Maximum edge length in meters (default 25.0).
        min_edge_length_m: Minimum edge length in meters (default 1.0).
        parallel_angle_threshold: Angle threshold for parallel detection (default 15.0).

    Returns:
        List of parallelogram dicts:
        {
            'vertices': tuple of 4 (lon, lat) coordinates,
            'g1_edges': (ce_idx_a, ce_idx_b) - parallel pair from group 1,
            'g2_edges': (ce_idx_c, ce_idx_d) - parallel pair from group 2,
            'edge_lengths': tuple of 4 edge lengths in meters,
        }
    """
    active_edges = [(ce['idx'], ce) for ce in c_edges if not ce.get('is_split', False)]

    direction_groups: Dict[int, List[tuple]] = {}
    for idx, ce in active_edges:
        d = ce['direction_deg']
        group_key = round(d / parallel_angle_threshold) * parallel_angle_threshold
        group_key_int = int(group_key)
        if group_key_int not in direction_groups:
            direction_groups[group_key_int] = []
        direction_groups[group_key_int].append((idx, ce))

    parallelograms = []
    group_keys = sorted(direction_groups.keys())

    for i in range(len(group_keys)):
        for j in range(i + 1, len(group_keys)):
            g1_key = group_keys[i]
            g2_key = group_keys[j]
            angle_diff = abs(g1_key - g2_key)
            if angle_diff > 90:
                angle_diff = 180 - angle_diff
            if angle_diff < 30:
                continue

            g1_edges = direction_groups[g1_key]
            g2_edges = direction_groups[g2_key]
            
            # Skip if either group is too large (performance optimization)
            if len(g1_edges) > 20 or len(g2_edges) > 20:
                continue

            for a in range(len(g1_edges)):
                for b in range(a + 1, len(g1_edges)):
                    idx_a, ce_a = g1_edges[a]
                    idx_b, ce_b = g1_edges[b]

                    for c in range(len(g2_edges)):
                        for d in range(c + 1, len(g2_edges)):
                            idx_c, ce_c = g2_edges[c]
                            idx_d, ce_d = g2_edges[d]

                            p1 = line_intersection_2d(
                                ce_a['start_coord'], ce_a['end_coord'],
                                ce_c['start_coord'], ce_c['end_coord']
                            )
                            p2 = line_intersection_2d(
                                ce_a['start_coord'], ce_a['end_coord'],
                                ce_d['start_coord'], ce_d['end_coord']
                            )
                            p3 = line_intersection_2d(
                                ce_b['start_coord'], ce_b['end_coord'],
                                ce_c['start_coord'], ce_c['end_coord']
                            )
                            p4 = line_intersection_2d(
                                ce_b['start_coord'], ce_b['end_coord'],
                                ce_d['start_coord'], ce_d['end_coord']
                            )

                            if not (p1 and p2 and p3 and p4):
                                continue

                            e1 = haversine_m(p1, p2)
                            e2 = haversine_m(p2, p4)
                            e3 = haversine_m(p4, p3)
                            e4 = haversine_m(p3, p1)

                            if (min_edge_length_m <= e1 <= max_edge_length_m and
                                min_edge_length_m <= e2 <= max_edge_length_m and
                                min_edge_length_m <= e3 <= max_edge_length_m and
                                min_edge_length_m <= e4 <= max_edge_length_m):
                                parallelograms.append({
                                    'vertices': (p1, p2, p4, p3),
                                    'g1_edges': (idx_a, idx_b),
                                    'g2_edges': (idx_c, idx_d),
                                    'edge_lengths': (e1, e2, e3, e4),
                                })

    return parallelograms


def build_walkable_graph(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
    edge_clusters: List[List[int]] | None = None,
    edge_features: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build a walkable graph from C-edges and C-nodes for random path generation.

    Filters out split C-edges (is_split=True) and builds an adjacency list
    structure suitable for random walk algorithms.

    Args:
        c_edges: List of C-edge dicts from the full pipeline.
        virtual_cnodes: Dict of C-node dicts from the full pipeline.
        edge_clusters: Optional list of edge index lists for each C-edge cluster.
            Used to compute highway_level when not present in c_edges.
        edge_features: Optional list of edge GeoJSON features.
            Used to compute highway_level when not present in c_edges.

    Returns:
        Dict with structure:
        {
            'nodes': {node_id: {'position': (lon, lat), 'edges': [edge_idx, ...]}},
            'edges': {edge_idx: {
                'start_node': str, 'end_node': str,
                'length_m': float, 'direction_deg': float,
                'highway_level': int, 'start_coord': tuple, 'end_coord': tuple
            }},
        }
    """
    active_edges = [ce for ce in c_edges if not ce.get('is_split', False)]

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[int, Dict[str, Any]] = {}

    for ce in active_edges:
        ce_idx = ce['idx']
        connected = ce.get('connected_vnodes', [])
        if len(connected) < 2:
            continue

        start_vnode_id = connected[0]
        end_vnode_id = connected[1]

        if start_vnode_id not in virtual_cnodes or end_vnode_id not in virtual_cnodes:
            continue

        start_vnode = virtual_cnodes[start_vnode_id]
        end_vnode = virtual_cnodes[end_vnode_id]

        start_node_id = start_vnode['id']
        end_node_id = end_vnode['id']

        road_length_m = ce.get('road_length_m')
        if road_length_m is None:
            road_length_m = ce.get('length_m')
        if road_length_m is None:
            road_length_m = haversine_m(ce['start_coord'], ce['end_coord'])

        highway_level = ce.get('highway_level')
        if highway_level is None and edge_clusters is not None and edge_features is not None:
            parent_idx = ce.get('parent_idx', ce_idx)
            if parent_idx < len(edge_clusters):
                cluster = edge_clusters[parent_idx]
                levels = [_edge_highway_level(edge_features[idx]) for idx in cluster if idx < len(edge_features)]
                highway_level = min(levels) if levels else 99
            else:
                highway_level = 99
        elif highway_level is None:
            highway_level = 99

        edges[ce_idx] = {
            'start_node': start_node_id,
            'end_node': end_node_id,
            'length_m': road_length_m,
            'direction_deg': ce['direction_deg'],
            'highway_level': highway_level,
            'start_coord': ce['start_coord'],
            'end_coord': ce['end_coord'],
        }

        if start_node_id not in nodes:
            nodes[start_node_id] = {
                'position': start_vnode['position'],
                'edges': [],
            }
        nodes[start_node_id]['edges'].append(ce_idx)

        if end_node_id not in nodes:
            nodes[end_node_id] = {
                'position': end_vnode['position'],
                'edges': [],
            }
        nodes[end_node_id]['edges'].append(ce_idx)

    return {
        'nodes': nodes,
        'edges': edges,
    }

