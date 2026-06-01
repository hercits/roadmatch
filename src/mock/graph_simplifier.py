from __future__ import annotations

from typing import Any, Dict, List

import geopandas as gpd
from shapely.geometry import LineString, MultiPoint, Point

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


def find_crossroad_nodes(
    c_edges: List[Dict[str, Any]],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    parallel_angle_threshold: float = 15.0,
) -> Dict[str, Dict[str, Any]]:
    """Find nodes that are potential crossroads.
    
    A crossroad node:
    1. Appears in at least 2 C-edges
    2. Is an endpoint of at least one C-edge
    3. At least one pair of C-edges containing it has angle difference > threshold
    
    Args:
        c_edges: List of C-edge dicts
        edge_clusters: List of edge index lists for each C-edge
        edge_features: Original edge GeoJSON features
        parallel_angle_threshold: Angle threshold in degrees
        
    Returns:
        Dict mapping node_id -> {
            'c_edges': list of C-edge indices,
            'is_endpoint': bool,
            'max_angle_diff': float
        }
    """
    from utils.geometry import angular_delta_mod180
    
    # Step 1: Build node-to-C-edges mapping
    node_to_cedges: Dict[str, List[int]] = {}
    
    for ce_idx, c_edge in enumerate(c_edges):
        # Get all nodes from this C-edge's cluster
        cluster_edges = edge_clusters[ce_idx]
        for edge_idx in cluster_edges:
            props = edge_features[edge_idx]['properties']
            u, v = props['u'], props['v']
            
            if u not in node_to_cedges:
                node_to_cedges[u] = []
            if ce_idx not in node_to_cedges[u]:
                node_to_cedges[u].append(ce_idx)
            
            if v not in node_to_cedges:
                node_to_cedges[v] = []
            if ce_idx not in node_to_cedges[v]:
                node_to_cedges[v].append(ce_idx)
    
    # Step 2: Filter candidates (2+ C-edges, endpoint of at least one)
    candidates = {}
    for node_id, ce_indices in node_to_cedges.items():
        if len(ce_indices) < 2:
            continue
        
        # Check if endpoint of at least one C-edge
        is_endpoint = False
        for ce_idx in ce_indices:
            ce = c_edges[ce_idx]
            if ce['start_node_id'] == node_id or ce['end_node_id'] == node_id:
                is_endpoint = True
                break
        
        if not is_endpoint:
            continue
        
        candidates[node_id] = ce_indices
    
    # Step 3: Check angle differences
    crossroad_nodes = {}
    for node_id, ce_indices in candidates.items():
        max_angle_diff = 0.0
        
        # Check all pairs
        for i in range(len(ce_indices)):
            for j in range(i + 1, len(ce_indices)):
                ce_i = c_edges[ce_indices[i]]
                ce_j = c_edges[ce_indices[j]]
                angle_diff = angular_delta_mod180(
                    ce_i['direction_deg'],
                    ce_j['direction_deg']
                )
                max_angle_diff = max(max_angle_diff, angle_diff)
        
        # If max angle diff > threshold, it's a crossroad
        if max_angle_diff > parallel_angle_threshold:
            crossroad_nodes[node_id] = {
                'c_edges': ce_indices,
                'is_endpoint': True,
                'max_angle_diff': max_angle_diff
            }
    
    return crossroad_nodes
