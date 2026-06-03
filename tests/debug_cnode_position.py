"""Debug script to investigate C-node positioning issues.

Focuses on C-node 306 and 276 to understand why they don't lie on adjacent C-edges.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import (
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_near_parallel_edges,
    create_virtual_cnodes,
    identify_connection_nodes,
    identify_endpoint_nodes_for_cedge,
    point_to_line_distance_m,
    project_to_bearing_m,
    offset_to_coordinate,
    average_position,
    project_to_line,
    line_intersection_2d,
    angular_delta_mod180,
)
from utils.geometry import haversine_m


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


def trace_position_computation(
    nodes: list[str],
    connection_nodes: dict,
    c_edges: list[dict],
    node_coords: dict,
    edge_clusters: list,
    core_edges: dict,
    edge_features: list,
):
    """Trace through the position computation logic for a cluster."""
    print(f"\n  Position computation trace:")
    
    # Collect connected C-edges
    connected_cedges = set()
    for node in nodes:
        connected_cedges.update(connection_nodes[node])
    
    # Identify endpoint nodes
    cedge_endpoint_nodes = {}
    for ce_idx in range(len(c_edges)):
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords
        )
    
    # Count endpoint nodes
    endpoint_node_count = 0
    for node in nodes:
        is_endpoint = False
        for ce_idx in connected_cedges:
            if node in cedge_endpoint_nodes[ce_idx]:
                is_endpoint = True
                break
        if is_endpoint:
            endpoint_node_count += 1
    
    print(f"    Endpoint node count: {endpoint_node_count}")
    
    if endpoint_node_count >= 2:
        print(f"    Decision: Use average position (2+ endpoint nodes)")
        avg_pos = average_position(nodes, node_coords)
        print(f"    Average position: {avg_pos}")
        return
    
    # Compute unique directions
    directions = set()
    for ce_idx in connected_cedges:
        directions.add(round(c_edges[ce_idx]['direction_deg']))
    
    print(f"    Unique directions: {sorted(directions)}")
    
    if len(directions) == 1:
        print(f"    Decision: 1 direction - project to line")
        avg_pos = average_position(nodes, node_coords)
        print(f"    Average position: {avg_pos}")
        ce_idx = list(connected_cedges)[0]
        ce = c_edges[ce_idx]
        projected = project_to_line(avg_pos, ce['start_coord'], ce['end_coord'])
        print(f"    Projected to C-edge {ce_idx}: {projected}")
    elif len(directions) == 2:
        dir_list = sorted(directions)
        angle_diff = angular_delta_mod180(dir_list[0], dir_list[1])
        print(f"    Angle difference: {angle_diff:.1f}°")
        
        if angle_diff < 5.0:
            print(f"    Decision: Nearly parallel - use average position")
            avg_pos = average_position(nodes, node_coords)
            print(f"    Average position: {avg_pos}")
        else:
            print(f"    Decision: 2 directions - compute intersection")
            ce1 = next(
                ce for ce in connected_cedges
                if round(c_edges[ce]['direction_deg']) == dir_list[0]
            )
            ce2 = next(
                ce for ce in connected_cedges
                if round(c_edges[ce]['direction_deg']) == dir_list[1]
            )
            print(f"    C-edge {ce1}: direction {c_edges[ce1]['direction_deg']:.1f}°")
            print(f"    C-edge {ce2}: direction {c_edges[ce2]['direction_deg']:.1f}°")
            intersection = line_intersection_2d(
                c_edges[ce1]['start_coord'],
                c_edges[ce1]['end_coord'],
                c_edges[ce2]['start_coord'],
                c_edges[ce2]['end_coord'],
            )
            print(f"    Intersection: {intersection}")
            if intersection is None:
                print(f"    Fallback: Parallel lines, use average position")
                avg_pos = average_position(nodes, node_coords)
                print(f"    Average position: {avg_pos}")
    else:
        print(f"    Decision: 3+ directions - use average position")
        avg_pos = average_position(nodes, node_coords)
        print(f"    Average position: {avg_pos}")


def analyze_cnode(
    cnode_id: int,
    virtual_cnodes: dict,
    c_edges: list[dict],
    node_coords: dict[str, tuple],
    clusters: list,
    connection_nodes: dict,
    edge_clusters: list,
    core_edges: dict,
    edge_features: list,
):
    """Analyze a specific C-node's position relative to its connected C-edges."""
    if cnode_id not in virtual_cnodes:
        print(f"C-node {cnode_id} not found")
        return

    vnode = virtual_cnodes[cnode_id]
    print(f"\n{'='*60}")
    print(f"=== C-node {cnode_id} Analysis ===")
    print(f"{'='*60}")

    print(f"\nPosition: ({vnode['position'][0]:.7f}, {vnode['position'][1]:.7f})")
    print(f"Original nodes: {len(vnode['original_nodes'])} nodes")
    print(f"  Nodes: {vnode['original_nodes'][:10]}{'...' if len(vnode['original_nodes']) > 10 else ''}")

    print(f"\nConnected C-edges: {len(vnode['connected_cedges'])} edges")
    print(f"  Indices: {sorted(vnode['connected_cedges'])}")

    print(f"\nC-edge end associations:")
    for ce_idx, end_type in sorted(vnode.get('c_edge_end_associations', set())):
        print(f"  C-edge {ce_idx}: {end_type}")

    # Find which cluster this C-node came from
    print(f"\nCluster composition:")
    for cluster_id, nodes in enumerate(clusters):
        if set(nodes) == set(vnode['original_nodes']):
            print(f"  Cluster {cluster_id}: {nodes}")
            print(f"  Connection nodes in cluster:")
            for node in nodes:
                if node in connection_nodes:
                    print(f"    {node}: C-edges {sorted(connection_nodes[node])}")
            
            # Trace position computation
            trace_position_computation(
                nodes, connection_nodes, c_edges, node_coords,
                edge_clusters, core_edges, edge_features
            )
            break

    print(f"\nDistance to connected C-edges:")
    for ce_idx in sorted(vnode['connected_cedges']):
        ce = c_edges[ce_idx]
        if ce.get('is_split', False):
            continue

        start = ce['start_coord']
        end = ce['end_coord']
        direction = ce['direction_deg']

        # Distance to line
        dist = point_to_line_distance_m(vnode['position'], start, end)

        # Projection onto line
        proj = project_to_bearing_m(vnode['position'], start, direction)
        proj_pos = offset_to_coordinate(start, direction, proj, 0.0)

        # Distance to projected point
        dist_to_proj = haversine_m(vnode['position'], proj_pos)

        # Check if projection is within C-edge extent
        start_proj = project_to_bearing_m(start, start, direction)
        end_proj = project_to_bearing_m(end, start, direction)
        min_proj = min(start_proj, end_proj)
        max_proj = max(start_proj, end_proj)
        in_range = min_proj <= proj <= max_proj

        print(f"\n  C-edge {ce_idx}:")
        print(f"    Direction: {direction:.1f}°")
        print(f"    Size: {ce['size']} edges")
        print(f"    Distance to line: {dist:.2f} m")
        print(f"    Projection on line: ({proj_pos[0]:.7f}, {proj_pos[1]:.7f})")
        print(f"    Distance to projection: {dist_to_proj:.2f} m")
        print(f"    Projection in range: {in_range} (proj={proj:.1f}, range=[{min_proj:.1f}, {max_proj:.1f}])")


def main():
    parser = argparse.ArgumentParser(description="Debug C-node positioning")
    parser.add_argument("--data-dir", type=Path, default=Path("resource/miniquad"))
    parser.add_argument("--cnode-ids", type=int, nargs="+", default=[306, 276])
    parser.add_argument("--near-threshold", type=float, default=50.0)
    parser.add_argument("--parallel-angle-threshold", type=float, default=30.0)
    args = parser.parse_args()

    edges_path = args.data_dir / "edges.geojson"
    nodes_path = args.data_dir / "nodes.geojson"

    print("Loading data...")
    edge_features = load_geojson(edges_path)
    node_features = load_geojson(nodes_path)
    print(f"Original: {len(edge_features)} edges, {len(node_features)} nodes")

    print("\nSplitting edges at intersections...")
    edge_features, node_features, split_indices = split_edges_at_intersections(
        edge_features, node_features
    )
    print(f"After splitting: {len(edge_features)} edges, {len(node_features)} nodes")

    print("\nClustering edges...")
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features,
        near_threshold_m=args.near_threshold,
        parallel_angle_threshold=args.parallel_angle_threshold,
    )
    print(f"Clusters: {len(edge_clusters)}")

    node_coords = build_node_coords(node_features)

    print("\nBuilding C-edge graph...")
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords,
        core_edges_per_cluster=core_edges,
        near_threshold_m=args.near_threshold,
    )
    print(f"C-edges: {len(c_edges)}")

    print("\nBuilding node-to-C-edges mapping...")
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)

    print("Identifying connection nodes...")
    connection_nodes = identify_connection_nodes(node_to_cedges)
    print(f"Connection nodes: {len(connection_nodes)}")

    print("Clustering connection nodes...")
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    print(f"Connection node clusters: {len(clusters)}")

    print("\nCreating virtual C-nodes...")
    
    # Manually run create_virtual_cnodes with debug output
    from mock.graph_simplifier import identify_endpoint_nodes_for_cedge
    
    # Identify endpoint nodes for all C-edges (cached)
    cedge_endpoint_nodes = {}
    for ce_idx in range(len(c_edges)):
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords
        )
    
    virtual_cnodes_initial = {}
    for cluster_id, nodes in enumerate(clusters):
        # Collect all C-edges connected to this cluster
        connected_cedges = set()
        for node in nodes:
            connected_cedges.update(connection_nodes[node])
        
        # Count endpoint nodes
        endpoint_node_count = 0
        for node in nodes:
            is_endpoint = False
            for ce_idx in connected_cedges:
                if node in cedge_endpoint_nodes[ce_idx]:
                    is_endpoint = True
                    break
            if is_endpoint:
                endpoint_node_count += 1
        
        # Track C-edge end associations
        c_edge_end_associations = set()
        for ce_idx in connected_cedges:
            ce = c_edges[ce_idx]
            start_node = ce['start_node_id']
            end_node = ce['end_node_id']
            for node in nodes:
                if node == start_node:
                    c_edge_end_associations.add((ce_idx, 'start'))
                if node == end_node:
                    c_edge_end_associations.add((ce_idx, 'end'))
        
        # Compute position (simplified - just use intersection for 2 directions)
        directions = set()
        for ce_idx in connected_cedges:
            directions.add(round(c_edges[ce_idx]['direction_deg']))
        
        if len(directions) == 2 and endpoint_node_count < 2:
            dir_list = sorted(directions)
            angle_diff = angular_delta_mod180(dir_list[0], dir_list[1])
            if angle_diff >= 5.0:
                ce1 = next(ce for ce in connected_cedges if round(c_edges[ce]['direction_deg']) == dir_list[0])
                ce2 = next(ce for ce in connected_cedges if round(c_edges[ce]['direction_deg']) == dir_list[1])
                virtual_pos = line_intersection_2d(
                    c_edges[ce1]['start_coord'], c_edges[ce1]['end_coord'],
                    c_edges[ce2]['start_coord'], c_edges[ce2]['end_coord'],
                )
                if cluster_id in args.cnode_ids:
                    print(f"  Cluster {cluster_id}: Initial position (intersection) = {virtual_pos}")
            else:
                virtual_pos = average_position(nodes, node_coords)
        else:
            virtual_pos = average_position(nodes, node_coords)
        
        virtual_cnodes_initial[cluster_id] = {
            'id': f'C-node_{cluster_id}',
            'position': virtual_pos,
            'connected_cedges': connected_cedges,
            'original_nodes': nodes,
            'c_edge_end_associations': c_edge_end_associations,
        }
    
    # Now run the actual create_virtual_cnodes
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features
    )
    print(f"Virtual C-nodes: {len(virtual_cnodes)}")
    
    # Compare initial vs final positions
    print("\nPosition comparison (initial vs final):")
    for cnode_id in args.cnode_ids:
        if cnode_id in virtual_cnodes_initial and cnode_id in virtual_cnodes:
            initial_pos = virtual_cnodes_initial[cnode_id]['position']
            final_pos = virtual_cnodes[cnode_id]['position']
            print(f"  C-node {cnode_id}:")
            print(f"    Initial (intersection): {initial_pos}")
            print(f"    Final (after merging):  {final_pos}")
            if initial_pos and final_pos:
                dist = haversine_m(initial_pos, final_pos)
                print(f"    Distance: {dist:.2f} m")

    # Debug: Check merging for specific C-nodes
    print("\n" + "="*60)
    print("Checking cluster merging...")
    print("="*60)
    
    # Build end_to_clusters mapping
    end_to_clusters = {}
    for cluster_id, vnode in virtual_cnodes.items():
        for (ce_idx, end_type) in vnode.get('c_edge_end_associations', set()):
            key = (ce_idx, end_type)
            if key not in end_to_clusters:
                end_to_clusters[key] = []
            end_to_clusters[key].append(cluster_id)
    
    # Check for C-node 306 and 276
    for cnode_id in args.cnode_ids:
        if cnode_id not in virtual_cnodes:
            continue
        vnode = virtual_cnodes[cnode_id]
        print(f"\nC-node {cnode_id}:")
        print(f"  C-edge end associations: {vnode.get('c_edge_end_associations', set())}")
        for (ce_idx, end_type) in vnode.get('c_edge_end_associations', set()):
            key = (ce_idx, end_type)
            if key in end_to_clusters:
                other_clusters = [cid for cid in end_to_clusters[key] if cid != cnode_id]
                if other_clusters:
                    print(f"  Shares (C-edge {ce_idx}, {end_type}) with clusters: {other_clusters}")
                    for other_id in other_clusters:
                        other_vnode = virtual_cnodes[other_id]
                        print(f"    Cluster {other_id}: {len(other_vnode['original_nodes'])} nodes")

    # Analyze requested C-nodes
    for cnode_id in args.cnode_ids:
        analyze_cnode(
            cnode_id, virtual_cnodes, c_edges, node_coords,
            clusters, connection_nodes, edge_clusters, core_edges, edge_features
        )


if __name__ == "__main__":
    main()
