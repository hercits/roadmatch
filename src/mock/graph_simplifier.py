from __future__ import annotations

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
            'parent_idx': ce_idx,
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
        # Skip original C-edges that have been split
        if c_edge.get('is_split', False):
            continue
        
        # Get all nodes from this C-edge's cluster
        # For split pieces, use the parent's edge cluster
        parent_idx = c_edge.get('parent_idx', ce_idx)
        cluster_edges = edge_clusters[parent_idx]
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


def compute_crossroad_positions(
    crossroad_nodes: Dict[str, Dict[str, Any]],
    c_edges: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    parallel_angle_threshold: float = 15.0,
) -> Dict[str, tuple]:
    """Compute intersection positions for crossroad nodes.

    For each crossroad node:
    - Get all C-edges it belongs to
    - Compute pairwise intersections of perpendicular C-edges only
    - Average all intersection points

    Only computes intersections between C-edges with angle difference
    greater than parallel_angle_threshold to avoid meaningless intersections
    between parallel roads (e.g., dual carriageways).

    Args:
        crossroad_nodes: Dict from find_crossroad_nodes().
        c_edges: List of C-edge dicts.
        node_coords: Dict of node_id -> (lon, lat) for fallback.
        parallel_angle_threshold: Angle threshold in degrees (default 15.0).

    Returns:
        Dict mapping node_id -> (lon, lat) intersection position.
    """
    from utils.geometry import angular_delta_mod180, line_intersection_2d

    crossroad_positions = {}

    for node_id, info in crossroad_nodes.items():
        ce_indices = info['c_edges']
        intersections = []

        # Compute pairwise intersections (perpendicular only)
        for i in range(len(ce_indices)):
            for j in range(i + 1, len(ce_indices)):
                ce_i = c_edges[ce_indices[i]]
                ce_j = c_edges[ce_indices[j]]

                # Skip parallel C-edges
                angle_diff = angular_delta_mod180(
                    ce_i['direction_deg'],
                    ce_j['direction_deg']
                )
                if angle_diff <= parallel_angle_threshold:
                    continue

                # Line 1: C-edge i
                p1 = ce_i['start_coord']
                p2 = ce_i['end_coord']

                # Line 2: C-edge j
                p3 = ce_j['start_coord']
                p4 = ce_j['end_coord']

                intersection = line_intersection_2d(p1, p2, p3, p4)
                if intersection is not None:
                    intersections.append(intersection)

        # Average all intersection points
        if intersections:
            avg_lon = sum(p[0] for p in intersections) / len(intersections)
            avg_lat = sum(p[1] for p in intersections) / len(intersections)
            crossroad_positions[node_id] = (avg_lon, avg_lat)
        else:
            # Fallback: keep original position (should never happen)
            crossroad_positions[node_id] = node_coords[node_id]

    return crossroad_positions


def cluster_crossroad_nodes(
    crossroad_positions: Dict[str, tuple],
    crossroad_nodes: Dict[str, Dict[str, Any]],
    clustering_distance_m: float = 60.0,
) -> Dict[str, tuple]:
    """Cluster closely positioned crossroad nodes.

    Merges crossroad nodes that are within clustering_distance_m of each other
    by computing the centroid of each cluster.

    Args:
        crossroad_positions: Dict mapping node_id -> (lon, lat) from
            compute_crossroad_positions().
        crossroad_nodes: Dict from find_crossroad_nodes().
        clustering_distance_m: Maximum distance to cluster nodes (default 60m).

    Returns:
        Updated crossroad_positions with clustered nodes.
    """
    from utils.geometry import haversine_m

    # Build list of (node_id, position) pairs
    nodes = list(crossroad_positions.items())

    # Use simple greedy clustering
    clustered_positions = {}
    used = set()

    for i, (node_id_i, pos_i) in enumerate(nodes):
        if node_id_i in used:
            continue

        # Find all nodes within clustering_distance_m
        cluster = [node_id_i]
        used.add(node_id_i)

        for j, (node_id_j, pos_j) in enumerate(nodes[i + 1:], start=i + 1):
            if node_id_j in used:
                continue

            dist = haversine_m(pos_i, pos_j)
            if dist <= clustering_distance_m:
                cluster.append(node_id_j)
                used.add(node_id_j)

        # Compute centroid of cluster
        if len(cluster) == 1:
            clustered_positions[node_id_i] = pos_i
        else:
            # Average positions
            avg_lon = sum(crossroad_positions[nid][0] for nid in cluster) / len(cluster)
            avg_lat = sum(crossroad_positions[nid][1] for nid in cluster) / len(cluster)
            centroid = (avg_lon, avg_lat)

            # All nodes in cluster get the centroid position
            for nid in cluster:
                clustered_positions[nid] = centroid

    return clustered_positions


def update_c_edges_for_crossroads(
    c_edges: List[Dict[str, Any]],
    crossroad_positions: Dict[str, tuple],
) -> None:
    """Update C-edge endpoints to match crossroad positions.

    For each C-edge:
    - If start_node_id is a crossroad, update start_coord
    - If end_node_id is a crossroad, update end_coord

    Modifies c_edges in-place.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        crossroad_positions: Dict from compute_crossroad_positions().
    """
    for c_edge in c_edges:
        start_node = c_edge['start_node_id']
        end_node = c_edge['end_node_id']

        # Update start endpoint if it's a crossroad
        if start_node in crossroad_positions:
            c_edge['start_coord'] = crossroad_positions[start_node]

        # Update end endpoint if it's a crossroad
        if end_node in crossroad_positions:
            c_edge['end_coord'] = crossroad_positions[end_node]


def connect_shared_nodes(
    c_edges: List[Dict[str, Any]],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    crossroad_nodes: Dict[str, Dict[str, Any]],
) -> None:
    """Connect C-edges that share non-crossroad nodes.

    For nodes that appear in multiple C-edges but are not crossroads
    (angle difference <= 15°), compute the average position of all
    C-edge endpoints at that node and update all C-edges to use it.

    Modifies c_edges in-place.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.
        node_coords: Dict of node_id -> (lon, lat).
        crossroad_nodes: Dict from find_crossroad_nodes() (to exclude crossroads).
    """
    # Build node-to-C-edges mapping (same as find_crossroad_nodes)
    node_to_cedges: Dict[str, List[int]] = {}

    for ce_idx, c_edge in enumerate(c_edges):
        # Skip original C-edges that have been split
        if c_edge.get('is_split', False):
            continue
        
        # Get all nodes from this C-edge's cluster
        # For split pieces, use the parent's edge cluster
        parent_idx = c_edge.get('parent_idx', ce_idx)
        cluster_edges = edge_clusters[parent_idx]
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

    # Process nodes that appear in 2+ C-edges but are NOT crossroads
    for node_id, ce_indices in node_to_cedges.items():
        if len(ce_indices) < 2:
            continue

        # Skip if this is a crossroad
        if node_id in crossroad_nodes:
            continue

        # Compute average position of all C-edge endpoints at this node
        positions = []
        for ce_idx in ce_indices:
            ce = c_edges[ce_idx]
            if ce['start_node_id'] == node_id:
                positions.append(ce['start_coord'])
            if ce['end_node_id'] == node_id:
                positions.append(ce['end_coord'])

        if not positions:
            continue

        # Average positions
        avg_lon = sum(p[0] for p in positions) / len(positions)
        avg_lat = sum(p[1] for p in positions) / len(positions)
        avg_pos = (avg_lon, avg_lat)

        # Update all C-edges to use this position
        for ce_idx in ce_indices:
            ce = c_edges[ce_idx]
            if ce['start_node_id'] == node_id:
                ce['start_coord'] = avg_pos
            if ce['end_node_id'] == node_id:
                ce['end_coord'] = avg_pos


def align_parallel_c_edges(
    c_edges: List[Dict[str, Any]],
    parallel_angle_threshold: float = 15.0,
) -> None:
    """Align parallel C-edges that share endpoints.

    For groups of parallel C-edges (angle diff < threshold) that share
    endpoints, align them to the majority direction and create virtual
    nodes for proper head-to-tail connections.

    Modifies c_edges in-place.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        parallel_angle_threshold: Angle threshold for parallel detection (degrees).
    """
    from utils.geometry import angular_delta_mod180

    # Step 1: Build adjacency graph for parallel C-edges
    # Two C-edges are connected if they share an endpoint AND are parallel
    n = len(c_edges)
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

    # Check all pairs
    for i in range(n):
        ce_i = c_edges[i]
        # Skip original C-edges that have been split
        if ce_i.get('is_split', False):
            continue

        for j in range(i + 1, n):
            ce_j = c_edges[j]
            # Skip original C-edges that have been split
            if ce_j.get('is_split', False):
                continue

            # Check if they share an endpoint
            shared = False
            if ce_i['start_node_id'] and ce_j['start_node_id'] == ce_i['start_node_id']:
                shared = True
            elif ce_i['start_node_id'] and ce_j['end_node_id'] == ce_i['start_node_id']:
                shared = True
            elif ce_i['end_node_id'] and ce_j['start_node_id'] == ce_i['end_node_id']:
                shared = True
            elif ce_i['end_node_id'] and ce_j['end_node_id'] == ce_i['end_node_id']:
                shared = True

            if not shared:
                continue

            # Check if they are parallel
            angle_diff = angular_delta_mod180(
                ce_i['direction_deg'],
                ce_j['direction_deg']
            )
            if angle_diff <= parallel_angle_threshold:
                union(i, j)

    # Step 2: Group C-edges by component
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        root = find(i)
        if root not in groups:
            groups[root] = []
        groups[root].append(i)

    # Step 3: Process each group
    virtual_node_counter = 0
    for root, indices in groups.items():
        if len(indices) < 2:
            continue  # Single C-edge, no alignment needed

        # Find majority direction (weighted by size)
        direction_weights: Dict[int, int] = {}
        for idx in indices:
            ce = c_edges[idx]
            direction = ce['direction_deg']
            size = ce['size']

            # Round direction to nearest degree for grouping
            rounded_dir = round(direction)
            if rounded_dir not in direction_weights:
                direction_weights[rounded_dir] = 0
            direction_weights[rounded_dir] += size

        majority_direction = float(max(direction_weights.keys(),
                                       key=lambda d: direction_weights[d]))

        # Step 4: Align all C-edges to majority direction
        for idx in indices:
            c_edges[idx]['direction_deg'] = majority_direction

        # Step 5: Create virtual nodes for head-to-tail connections
        # For each pair of C-edges that should connect but have overlap/gap,
        # create a virtual node at the midpoint
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                ce_i = c_edges[indices[i]]
                ce_j = c_edges[indices[j]]

                # Check if they share an endpoint
                shared_node = None
                if ce_i['start_node_id'] == ce_j['start_node_id']:
                    shared_node = ce_i['start_node_id']
                elif ce_i['start_node_id'] == ce_j['end_node_id']:
                    shared_node = ce_i['start_node_id']
                elif ce_i['end_node_id'] == ce_j['start_node_id']:
                    shared_node = ce_i['end_node_id']
                elif ce_i['end_node_id'] == ce_j['end_node_id']:
                    shared_node = ce_i['end_node_id']

                if not shared_node:
                    continue

                # Get the coordinates of the shared node from both C-edges
                if ce_i['start_node_id'] == shared_node:
                    coord_i = ce_i['start_coord']
                else:
                    coord_i = ce_i['end_coord']

                if ce_j['start_node_id'] == shared_node:
                    coord_j = ce_j['start_coord']
                else:
                    coord_j = ce_j['end_coord']

                # Compute midpoint
                mid_lon = (coord_i[0] + coord_j[0]) / 2
                mid_lat = (coord_i[1] + coord_j[1]) / 2
                mid_coord = (mid_lon, mid_lat)

                # Create virtual node ID
                virtual_node_id = f"virtual_{virtual_node_counter}"
                virtual_node_counter += 1

                # Update both C-edges to use the virtual node
                if ce_i['start_node_id'] == shared_node:
                    ce_i['start_node_id'] = virtual_node_id
                    ce_i['start_coord'] = mid_coord
                if ce_i['end_node_id'] == shared_node:
                    ce_i['end_node_id'] = virtual_node_id
                    ce_i['end_coord'] = mid_coord

                if ce_j['start_node_id'] == shared_node:
                    ce_j['start_node_id'] = virtual_node_id
                    ce_j['start_coord'] = mid_coord
                if ce_j['end_node_id'] == shared_node:
                    ce_j['end_node_id'] = virtual_node_id
                    ce_j['end_coord'] = mid_coord


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

    for ce_idx, cluster in enumerate(edge_clusters):
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
    # Stage 1: Basic clusters by C-edge pairs
    pair_clusters = []
    cedge_list = list(set(ce for cedges in connection_nodes.values() for ce in cedges))

    for i, ce1 in enumerate(cedge_list):
        for ce2 in cedge_list[i + 1:]:
            # Find shared nodes between ce1 and ce2
            shared = [
                node
                for node, cedges in connection_nodes.items()
                if ce1 in cedges and ce2 in cedges
            ]
            if shared:
                pair_clusters.append(set(shared))

    # Stage 2: Merge clusters that share nodes (transitive closure)
    merged = True
    while merged:
        merged = False
        new_clusters = []
        used = set()

        for i, cluster1 in enumerate(pair_clusters):
            if i in used:
                continue

            current = set(cluster1)
            for j, cluster2 in enumerate(pair_clusters[i + 1:], start=i + 1):
                if j in used:
                    continue

                if current & cluster2:  # Share at least one node
                    current |= cluster2
                    used.add(j)
                    merged = True

            new_clusters.append(current)
            used.add(i)

        pair_clusters = new_clusters

    # Convert sets to lists and remove duplicates
    return [list(cluster) for cluster in pair_clusters]


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
) -> set:
    """Identify endpoint nodes for a C-edge.

    A node is an endpoint node if:
    a) It appears in only one core edge of this C-edge, OR
    b) It doesn't appear in any core edge, but appears in only one non-core edge,
       and the nearest core node is an endpoint node.

    Args:
        c_edge_idx: Index of the C-edge.
        c_edges: List of C-edge dicts.
        edge_clusters: List of edge index lists for each C-edge.
        core_edges: Dict mapping C-edge index to set of core edge indices.
        edge_features: List of edge GeoJSON features.
        node_coords: Dict of node_id -> (lon, lat).

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

    return endpoint_nodes


def create_virtual_cnodes(
    clusters: List[List[str]],
    connection_nodes: Dict[str, set],
    c_edges: List[Dict[str, Any]],
    node_coords: Dict[str, tuple],
    edge_clusters: List[List[int]],
    core_edges: Dict[int, set],
    edge_features: List[Dict[str, Any]],
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
    cedge_endpoint_nodes: Dict[int, set] = {}
    for ce_idx in range(len(c_edges)):
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords
        )

    virtual_cnodes: Dict[int, Dict[str, Any]] = {}

    for cluster_id, nodes in enumerate(clusters):
        # Collect all C-edges connected to this cluster
        connected_cedges = set()
        for node in nodes:
            connected_cedges.update(connection_nodes[node])

        # Count how many nodes in this cluster are endpoint nodes
        endpoint_node_count = 0
        for node in nodes:
            is_endpoint = False
            for ce_idx in connected_cedges:
                if node in cedge_endpoint_nodes[ce_idx]:
                    is_endpoint = True
                    break
            if is_endpoint:
                endpoint_node_count += 1

        # Track which end of each C-edge this cluster is associated with
        c_edge_end_associations = set()
        for ce_idx in connected_cedges:
            ce = c_edges[ce_idx]
            start_node = ce['start_node_id']
            end_node = ce['end_node_id']
            
            # Check if any node in cluster is the start or end of this C-edge
            for node in nodes:
                if node == start_node:
                    c_edge_end_associations.add((ce_idx, 'start'))
                if node == end_node:
                    c_edge_end_associations.add((ce_idx, 'end'))

        # If 2+ nodes are endpoint nodes, use average position
        if endpoint_node_count >= 2:
            virtual_pos = average_position(nodes, node_coords)
            if virtual_pos is None:
                # Fallback: use first valid node's position
                for node in nodes:
                    if node in node_coords:
                        virtual_pos = node_coords[node]
                        break
        else:
            # Use direction-based logic
            # Compute unique directions (rounded to 1°)
            directions = set()
            for ce_idx in connected_cedges:
                directions.add(round(c_edges[ce_idx]['direction_deg']))

            # Compute position based on number of directions
            if len(directions) == 1:
                # 1 direction: average position, project to line
                avg_pos = average_position(nodes, node_coords)
                if avg_pos is None:
                    # Fallback: use first valid node's position
                    for node in nodes:
                        if node in node_coords:
                            avg_pos = node_coords[node]
                            break
                # Use first C-edge to define the line
                ce_idx = list(connected_cedges)[0]
                ce = c_edges[ce_idx]
                virtual_pos = project_to_line(avg_pos, ce['start_coord'], ce['end_coord'])
            elif len(directions) == 2:
                # 2 directions: check if they are too similar (nearly parallel)
                dir_list = sorted(directions)
                angle_diff = angular_delta_mod180(dir_list[0], dir_list[1])
                
                if angle_diff < 5.0:
                    # Nearly parallel lines with different endpoints, use average position
                    # to avoid extremely far intersections
                    virtual_pos = average_position(nodes, node_coords)
                    if virtual_pos is None:
                        # Fallback: use first valid node's position
                        for node in nodes:
                            if node in node_coords:
                                virtual_pos = node_coords[node]
                                break
                else:
                    # Compute intersection
                    ce1 = next(
                        ce
                        for ce in connected_cedges
                        if round(c_edges[ce]['direction_deg']) == dir_list[0]
                    )
                    ce2 = next(
                        ce
                        for ce in connected_cedges
                        if round(c_edges[ce]['direction_deg']) == dir_list[1]
                    )
                    virtual_pos = line_intersection_2d(
                        c_edges[ce1]['start_coord'],
                        c_edges[ce1]['end_coord'],
                        c_edges[ce2]['start_coord'],
                        c_edges[ce2]['end_coord'],
                    )
                    if virtual_pos is None:
                        # Parallel lines (shouldn't happen with 2 different directions), use average
                        virtual_pos = average_position(nodes, node_coords)
                        if virtual_pos is None:
                            # Fallback: use first valid node's position
                            for node in nodes:
                                if node in node_coords:
                                    virtual_pos = node_coords[node]
                                    break
            else:
                # 3+ directions: average position
                # TODO: improve with better algorithm (e.g., weighted by C-edge importance)
                virtual_pos = average_position(nodes, node_coords)
                if virtual_pos is None:
                    # Fallback: use first valid node's position
                    for node in nodes:
                        if node in node_coords:
                            virtual_pos = node_coords[node]
                            break

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
    for new_id, (root, old_cluster_ids) in enumerate(merged_clusters.items()):
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
        
        # Use average position
        virtual_pos = average_position(all_nodes, node_coords)
        if virtual_pos is None:
            # Fallback: use first valid node's position
            for node in all_nodes:
                if node in node_coords:
                    virtual_pos = node_coords[node]
                    break
        
        new_virtual_cnodes[new_id] = {
            'id': f'C-node_{new_id}',
            'position': virtual_pos,
            'connected_cedges': all_connected_cedges,
            'original_nodes': all_nodes,
            'c_edge_end_associations': all_c_edge_end_associations,
        }

    return new_virtual_cnodes


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
    # Build reverse mapping: C-edge index -> list of connected C-node indices
    cedge_to_vnodes: Dict[int, List[int]] = {}
    for vnode_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            if ce_idx not in cedge_to_vnodes:
                cedge_to_vnodes[ce_idx] = []
            cedge_to_vnodes[ce_idx].append(vnode_id)

    new_c_edges = []
    next_idx = max(ce['idx'] for ce in c_edges) + 1

    for ce_idx, c_edge in enumerate(c_edges):
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

            # Check if connected to at least one parallel C-edge
            for other_ce_idx in vnode['connected_cedges']:
                if other_ce_idx == ce_idx:
                    continue
                angle_diff = angular_delta_mod180(
                    c_edge['direction_deg'],
                    c_edges[other_ce_idx]['direction_deg']
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
            }
            split_pieces.append(piece)
            next_idx += 1

        # Mark original C-edge as split
        c_edge['is_split'] = True
        c_edge['split_pieces'] = [p['idx'] for p in split_pieces]

        new_c_edges.extend(split_pieces)

    # Handle C-edges with 1 or 2 connected C-nodes (no splitting needed)
    for ce_idx, c_edge in enumerate(c_edges):
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

    # Return original + split pieces
    return c_edges + new_c_edges


def update_c_edge_endpoints(
    c_edges: List[Dict[str, Any]],
    virtual_cnodes: Dict[int, Dict[str, Any]],
) -> None:
    """Store connected C-nodes for each C-edge.

    Modifies c_edges in-place. Stores all connected C-nodes in
    c_edge['connected_vnodes'] for later processing by
    split_c_edges_at_intersection_nodes.

    Args:
        c_edges: List of C-edge dicts (modified in-place).
        virtual_cnodes: Dict from create_virtual_cnodes().
    """
    # Build reverse mapping: C-edge index -> list of virtual C-nodes it's connected to
    cedge_to_vnodes: Dict[int, List[int]] = {}
    for vnode_id, vnode in virtual_cnodes.items():
        for ce_idx in vnode['connected_cedges']:
            if ce_idx not in cedge_to_vnodes:
                cedge_to_vnodes[ce_idx] = []
            cedge_to_vnodes[ce_idx].append(vnode_id)

    for ce_idx, c_edge in enumerate(c_edges):
        if ce_idx in cedge_to_vnodes:
            c_edge['connected_vnodes'] = cedge_to_vnodes[ce_idx]
        else:
            c_edge['connected_vnodes'] = []
