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


def filter_spur_core_edges(
    core_edges_per_cluster: Dict[int, set],
    edge_clusters: List[List[int]],
    edge_features: List[Dict[str, Any]],
) -> Dict[int, set]:
    """Filter out spur core edges (edges where both endpoints only belong to current C-edge).

    Spur edges are branches that don't connect to other C-edges. Removing them
    improves C-edge geometry by eliminating directional bias from spurs.

    Args:
        core_edges_per_cluster: Dict mapping cluster index to set of core edge indices.
        edge_clusters: List of edge index lists for each C-edge.
        edge_features: Original edge GeoJSON features.

    Returns:
        Filtered core_edges_per_cluster with spur edges removed.
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

    for ce_idx, c_edge in enumerate(c_edges):
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
    ce = c_edges[c_edge_idx]
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
    # Group C-edges by direction using parallel_angle_threshold
    direction_groups: List[List[int]] = []
    group_representatives: List[float] = []
    
    for ce_idx in connected_cedges:
        ce_dir = c_edges[ce_idx]['direction_deg']
        
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
            ce = c_edges[ce_idx]
            proj = project_to_line(pos, ce['start_coord'], ce['end_coord'])
            dist = haversine_m(pos, proj)
            if dist < min_dist:
                min_dist = dist
                best_proj = proj
        return best_proj
    
    # Helper to validate intersection is within reasonable distance
    def validate_intersection(intersection, ce1_idx, ce2_idx):
        ce1 = c_edges[ce1_idx]
        ce2 = c_edges[ce2_idx]
        
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
        ce = c_edges[ce_idx]
        return project_to_line(avg_pos, ce['start_coord'], ce['end_coord'])
    
    elif num_groups == 2:
        # Two non-parallel groups: compute intersection
        ce1_idx = direction_groups[0][0]
        ce2_idx = direction_groups[1][0]
        ce1 = c_edges[ce1_idx]
        ce2 = c_edges[ce2_idx]
        
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
                ce1 = c_edges[ce1_idx]
                ce2 = c_edges[ce2_idx]
                
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
    cedge_endpoint_nodes: Dict[int, set] = {}
    for ce_idx in range(len(c_edges)):
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords,
            near_threshold_m=near_threshold_m
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
            ce = c_edges[ce_idx]
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
                dist_to_start = haversine_m(node_coords[ep_node], ce['start_coord'])
                dist_to_end = haversine_m(node_coords[ep_node], ce['end_coord'])
                if dist_to_start > near_threshold_m and dist_to_end > near_threshold_m:
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
                ce = c_edges[ce_idx]
                dist_to_start = haversine_m(avg_pos, ce['start_coord'])
                dist_to_end = haversine_m(avg_pos, ce['end_coord'])
                
                # Project cluster onto C-edge direction
                start_proj = project_to_bearing_m(ce['start_coord'], ce['start_coord'], ce['direction_deg'])
                end_proj = project_to_bearing_m(ce['end_coord'], ce['start_coord'], ce['direction_deg'])
                mid_proj = (start_proj + end_proj) / 2
                cluster_proj = project_to_bearing_m(avg_pos, ce['start_coord'], ce['direction_deg'])
                
                if cluster_proj < mid_proj and dist_to_start <= near_threshold_m:
                    c_edge_end_associations.add((ce_idx, 'start'))
                elif cluster_proj >= mid_proj and dist_to_end <= near_threshold_m:
                    c_edge_end_associations.add((ce_idx, 'end'))
                # else: T-junction (middle of C-edge), no association

        # Count how many C-edges actually end at this cluster
        cedges_ending_here = set(ce_idx for ce_idx, _ in c_edge_end_associations)
        cedges_ending_count = len(cedges_ending_here)
        
        # Compute position using helper function
        virtual_pos = _compute_cnode_position(
            nodes, connected_cedges, c_edge_end_associations, c_edges, node_coords,
            near_threshold_m=near_threshold_m,
            parallel_angle_threshold=parallel_angle_threshold
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
                parallel_angle_threshold=parallel_angle_threshold
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
    near_threshold_m: float = 50.0,
) -> Dict[int, Dict[str, Any]]:
    """Merge T-junction C-nodes into their corresponding endpoint C-nodes.

    A T-junction occurs when two C-nodes share a C-edge, but one connects to
    the endpoint (has end association) while the other connects to the middle
    (no end association). These C-nodes are geometrically close but not merged
    by the standard merge logic.

    This function identifies such pairs and merges the T-junction C-node into
    the endpoint C-node, transferring its unique C-edges and associations.

    Args:
        virtual_cnodes: Dict from create_virtual_cnodes().
        near_threshold_m: Distance threshold for considering C-nodes as close (default 50.0).

    Returns:
        Updated dict of virtual C-nodes with T-junctions merged and re-numbered.
    """
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
