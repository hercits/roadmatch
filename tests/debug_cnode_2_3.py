"""Debug C-node 2 and 3 positioning issues."""
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
    update_c_edge_endpoints,
    split_c_edges_at_intersection_nodes,
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
    
    # Debug: Check C-edge 355's start_node_id before create_virtual_cnodes
    print(f"\nDEBUG: C-edge 355 start_node_id before create_virtual_cnodes: {c_edges[355]['start_node_id']}")
    print(f"DEBUG: C-edge 355 end_node_id before create_virtual_cnodes: {c_edges[355]['end_node_id']}")
    
    # Debug: Check which cluster contains node '121.2871923_31.2696396'
    target_node = '121.2871923_31.2696396'
    for cluster_id, nodes in enumerate(clusters):
        if target_node in nodes:
            print(f"DEBUG: Node {target_node} is in cluster {cluster_id}")
            print(f"DEBUG: Cluster {cluster_id} nodes: {nodes}")
            print(f"DEBUG: Cluster {cluster_id} connected C-edges:")
            connected_cedges = set()
            for node in nodes:
                connected_cedges.update(connection_nodes.get(node, set()))
            print(f"DEBUG:   {sorted(connected_cedges)}")
            
            # Debug: Check if target_node is in cedge_endpoint_nodes for C-edge 355
            from mock.graph_simplifier import identify_endpoint_nodes_for_cedge
            ep_nodes_355 = identify_endpoint_nodes_for_cedge(
                355, c_edges, edge_clusters, core_edges, edge_features, node_coords,
                near_threshold_m=50.0
            )
            print(f"DEBUG: C-edge 355 endpoint nodes: {sorted(ep_nodes_355)}")
            print(f"DEBUG: Is {target_node} in C-edge 355 endpoint nodes? {target_node in ep_nodes_355}")
            
            # Debug: Check C-edge 355's core edges
            core_edges_355 = core_edges.get(355, set())
            print(f"DEBUG: C-edge 355 core edges: {sorted(core_edges_355)}")
            print(f"DEBUG: C-edge 355 all edges: {sorted(edge_clusters[355])}")
            
            # Debug: Check node counts for target_node
            all_edge_indices = edge_clusters[355]
            core_edge_indices = core_edges.get(355, set())
            non_core_edge_indices = set(all_edge_indices) - core_edge_indices
            
            core_count = 0
            non_core_count = 0
            for edge_idx in core_edge_indices:
                edge = edge_features[edge_idx]
                if edge['properties']['u'] == target_node or edge['properties']['v'] == target_node:
                    core_count += 1
            for edge_idx in non_core_edge_indices:
                edge = edge_features[edge_idx]
                if edge['properties']['u'] == target_node or edge['properties']['v'] == target_node:
                    non_core_count += 1
            
            print(f"DEBUG: Node {target_node} appears in {core_count} core edges and {non_core_count} non-core edges")
            
            # Debug: Check projection
            from utils.geometry import project_to_bearing_m
            ce_355 = c_edges[355]
            start_proj = project_to_bearing_m(ce_355['start_coord'], ce_355['start_coord'], ce_355['direction_deg'])
            end_proj = project_to_bearing_m(ce_355['end_coord'], ce_355['start_coord'], ce_355['direction_deg'])
            mid_proj = (start_proj + end_proj) / 2
            node_proj = project_to_bearing_m(node_coords[target_node], ce_355['start_coord'], ce_355['direction_deg'])
            print(f"DEBUG: C-edge 355 start_proj: {start_proj:.2f}")
            print(f"DEBUG: C-edge 355 end_proj: {end_proj:.2f}")
            print(f"DEBUG: C-edge 355 mid_proj: {mid_proj:.2f}")
            print(f"DEBUG: Node {target_node} proj: {node_proj:.2f}")
            print(f"DEBUG: Node is on start side? {node_proj < mid_proj}")
            break
    
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features
    )
    
    print("Updating C-edge endpoints...")
    update_c_edge_endpoints(c_edges, virtual_cnodes)
    
    print("Splitting C-edges at intersection nodes...")
    c_edges = split_c_edges_at_intersection_nodes(
        c_edges, virtual_cnodes, edge_clusters, edge_features,
        parallel_angle_threshold=30.0
    )
    
    # Target C-nodes
    target_cnodes = [2, 3]
    target_cedges = [1, 308, 309, 355]
    
    print("\n" + "=" * 70)
    print("C-node analysis: 2, 3")
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
        print(f"Original nodes ({len(vnode['original_nodes'])}):")
        for node in sorted(vnode['original_nodes']):
            pos = node_coords.get(node, "N/A")
            print(f"  {node} @ {pos}")
    
    print("\n" + "=" * 70)
    print("C-edge analysis: 1, 308, 309, 355")
    print("=" * 70)
    
    for ce_idx in target_cedges:
        if ce_idx >= len(c_edges):
            print(f"\nC-edge {ce_idx}: NOT FOUND")
            continue
        
        ce = c_edges[ce_idx]
        print(f"\n{'=' * 60}")
        print(f"C-edge {ce_idx}")
        print(f"{'=' * 60}")
        print(f"Direction: {ce['direction_deg']:.1f}°")
        print(f"Size: {ce['size']}")
        print(f"start_coord: {ce['start_coord']}")
        print(f"end_coord:   {ce['end_coord']}")
        print(f"start_node_id: {ce.get('start_node_id', 'N/A')}")
        print(f"end_node_id:   {ce.get('end_node_id', 'N/A')}")
        print(f"connected_vnodes: {ce.get('connected_vnodes', [])}")
        print(f"is_split: {ce.get('is_split', False)}")
        
        # Check distance from target C-nodes to this C-edge
        print(f"\nDistance from target C-nodes:")
        for cnode_id in target_cnodes:
            if cnode_id not in virtual_cnodes:
                continue
            vnode = virtual_cnodes[cnode_id]
            dist = point_to_line_distance_m(
                vnode['position'], ce['start_coord'], ce['end_coord']
            )
            print(f"  C-node {cnode_id}: {dist:.1f}m")
    
    # Check which C-edges have C-node 2 and 3 as endpoints
    print("\n" + "=" * 70)
    print("C-edges with C-node 2 or 3 as endpoints")
    print("=" * 70)
    
    for ce_idx, ce in enumerate(c_edges):
        if ce.get('is_split', False):
            continue
        
        start_node = ce.get('start_node_id', '')
        end_node = ce.get('end_node_id', '')
        
        if start_node in ['C-node_2', 'C-node_3'] or end_node in ['C-node_2', 'C-node_3']:
            print(f"\nC-edge {ce_idx}:")
            print(f"  start_node_id: {start_node}")
            print(f"  end_node_id:   {end_node}")
            print(f"  start_coord: {ce['start_coord']}")
            print(f"  end_coord:   {ce['end_coord']}")


if __name__ == "__main__":
    main()
