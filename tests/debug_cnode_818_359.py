"""Debug C-node 818 and 359 positioning issues."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import (
    _compute_cnode_position,
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_near_parallel_edges,
    create_virtual_cnodes,
    identify_connection_nodes,
    average_position,
    line_intersection_2d,
    angular_delta_mod180,
)
from utils.geometry import haversine_m, point_to_line_distance_m


def load_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]


def build_node_coords(node_features: list[dict]) -> dict[str, tuple]:
    coords: dict[str, tuple] = {}
    for f in node_features:
        nid = f["properties"]["node_id"]
        c = f["geometry"]["coordinates"]
        coords[nid] = (c[0], c[1])
    return coords


def point_to_segment_distance_m(point: tuple, seg_start: tuple, seg_end: tuple) -> float:
    """Calculate distance from point to line segment."""
    from utils.geometry import project_point_to_line, haversine_m
    
    # Project point onto infinite line
    proj = project_point_to_line(point, seg_start, seg_end)
    
    # Check if projection is within segment
    seg_len = haversine_m(seg_start, seg_end)
    if seg_len == 0:
        return haversine_m(point, seg_start)
    
    # Parameter t along segment
    dx = seg_end[0] - seg_start[0]
    dy = seg_end[1] - seg_start[1]
    t = ((proj[0] - seg_start[0]) * dx + (proj[1] - seg_start[1]) * dy) / (dx * dx + dy * dy)
    
    if t < 0:
        return haversine_m(point, seg_start)
    elif t > 1:
        return haversine_m(point, seg_end)
    else:
        return haversine_m(point, proj)


def main():
    # Load data
    edges_path = Path("resource/miniquad/edges.geojson")
    nodes_path = Path("resource/miniquad/nodes.geojson")
    
    print("Loading data...")
    edge_features = load_geojson(edges_path)
    node_features = load_geojson(nodes_path)
    
    print("Splitting edges...")
    edge_features, node_features, _ = split_edges_at_intersections(
        edge_features, node_features
    )
    
    print("Clustering edges...")
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features, near_threshold_m=50.0, parallel_angle_threshold=30.0
    )
    
    node_coords = build_node_coords(node_features)
    
    print("Building C-edge graph...")
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords, core_edges
    )
    
    print("Building node-to-C-edges map...")
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
    
    print("Identifying connection nodes...")
    connection_nodes = identify_connection_nodes(node_to_cedges)
    
    print("Clustering connection nodes...")
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    
    print("Creating virtual C-nodes...")
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features
    )
    
    # Target C-nodes
    target_cnodes = [818, 359]
    
    print("\n" + "=" * 70)
    print("C-node analysis: 818, 359")
    print("=" * 70)
    
    for cnode_id in target_cnodes:
        if cnode_id not in virtual_cnodes:
            print(f"\nC-node {cnode_id}: NOT FOUND")
            continue
        
        vnode = virtual_cnodes[cnode_id]
        print(f"\n{'=' * 60}")
        print(f"C-node {cnode_id}")
        print(f"{'=' * 60}")
        print(f"Position: {vnode['position']}")
        print(f"Connected C-edges: {sorted(vnode['connected_cedges'])}")
        print(f"C-edge end associations: {sorted(vnode['c_edge_end_associations'])}")
        
        # Decision path analysis
        cedges_ending_here = set(ce_idx for ce_idx, _ in vnode['c_edge_end_associations'])
        cedges_ending_count = len(cedges_ending_here)
        
        directions = set()
        for ce_idx in vnode['connected_cedges']:
            directions.add(round(c_edges[ce_idx]['direction_deg']))
        
        print(f"\nDecision path:")
        print(f"  cedges_ending_count: {cedges_ending_count} (C-edges: {sorted(cedges_ending_here)})")
        print(f"  directions: {sorted(directions)}")
        
        if cedges_ending_count >= 2:
            print(f"  Decision: average_position (2+ C-edges end here)")
            avg_pos = average_position(vnode['original_nodes'], node_coords)
            print(f"  Average position: {avg_pos}")
        elif len(directions) == 2:
            dir_list = sorted(directions)
            angle_diff = angular_delta_mod180(dir_list[0], dir_list[1])
            print(f"  angle_diff: {angle_diff:.1f}")
            if angle_diff < 5.0:
                print(f"  Decision: average_position (nearly parallel)")
            else:
                print(f"  Decision: line_intersection (2 directions)")
                ce1 = next(ce for ce in vnode['connected_cedges'] if round(c_edges[ce]['direction_deg']) == dir_list[0])
                ce2 = next(ce for ce in vnode['connected_cedges'] if round(c_edges[ce]['direction_deg']) == dir_list[1])
                intersection = line_intersection_2d(
                    c_edges[ce1]['start_coord'], c_edges[ce1]['end_coord'],
                    c_edges[ce2]['start_coord'], c_edges[ce2]['end_coord']
                )
                print(f"  Intersection: {intersection}")
        
        print(f"\nOriginal nodes ({len(vnode['original_nodes'])}):")
        for node in sorted(vnode['original_nodes']):
            pos = node_coords.get(node, "N/A")
            print(f"  {node} @ {pos}")
        
        # Distance to connected C-edges' core edges
        print(f"\nDistance to connected C-edges' core edges:")
        for ce_idx in sorted(vnode['connected_cedges']):
            ce = c_edges[ce_idx]
            core_edge_indices = core_edges.get(ce_idx, set())
            
            print(f"\n  C-edge {ce_idx} (dir={ce['direction_deg']:.1f}, size={ce['size']}, core={len(core_edge_indices)})")
            print(f"    start_coord: {ce['start_coord']}")
            print(f"    end_coord:   {ce['end_coord']}")
            
            # Distance from C-node to representative line
            dist_to_line = point_to_line_distance_m(
                vnode['position'], ce['start_coord'], ce['end_coord']
            )
            print(f"    Distance to representative line: {dist_to_line:.1f}m")
            
            # Calculate distance from C-node to each core edge
            min_dist = float('inf')
            min_edge_idx = None
            for edge_idx in sorted(core_edge_indices):
                edge = edge_features[edge_idx]
                coords = edge['geometry']['coordinates']
                
                # Distance to each segment
                for i in range(len(coords) - 1):
                    p1 = (coords[i][0], coords[i][1])
                    p2 = (coords[i + 1][0], coords[i + 1][1])
                    dist = point_to_segment_distance_m(vnode['position'], p1, p2)
                    if dist < min_dist:
                        min_dist = dist
                        min_edge_idx = edge_idx
            
            if min_edge_idx is not None:
                print(f"    Min distance to core edge {min_edge_idx}: {min_dist:.1f}m")
            else:
                print(f"    No core edges found")
            
            # Also check distance from original nodes to core edges
            print(f"    Distance from original nodes to core edges:")
            for node in sorted(vnode['original_nodes']):
                if node not in node_coords:
                    continue
                node_pos = node_coords[node]
                min_node_dist = float('inf')
                for edge_idx in core_edge_indices:
                    edge = edge_features[edge_idx]
                    coords = edge['geometry']['coordinates']
                    for i in range(len(coords) - 1):
                        p1 = (coords[i][0], coords[i][1])
                        p2 = (coords[i + 1][0], coords[i + 1][1])
                        dist = point_to_segment_distance_m(node_pos, p1, p2)
                        if dist < min_node_dist:
                            min_node_dist = dist
                print(f"      {node}: {min_node_dist:.1f}m")


if __name__ == "__main__":
    main()
