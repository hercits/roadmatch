"""Debug: show which nodes C-nodes 439, 442, 443 contain and why."""
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
)


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
    
    edge_features = load_geojson(edges_path)
    node_features = load_geojson(nodes_path)
    
    edge_features, node_features, _ = split_edges_at_intersections(
        edge_features, node_features
    )
    
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features, near_threshold_m=50.0, parallel_angle_threshold=30.0
    )
    
    node_coords = build_node_coords(node_features)
    
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords, core_edges
    )
    
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
    connection_nodes = identify_connection_nodes(node_to_cedges)
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features
    )
    
    # Identify endpoint nodes for target C-edges
    target_cedges = [430, 431, 893, 894]
    cedge_endpoint_nodes = {}
    for ce_idx in target_cedges:
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords
        )
    
    print("=" * 70)
    print("C-edge endpoint nodes")
    print("=" * 70)
    for ce_idx in target_cedges:
        ce = c_edges[ce_idx]
        print(f"\nC-edge {ce_idx} (dir={ce['direction_deg']:.1f}°, size={ce['size']})")
        print(f"  start_node_id: {ce['start_node_id']}")
        print(f"  end_node_id:   {ce['end_node_id']}")
        print(f"  start_coord:   {ce['start_coord']}")
        print(f"  end_coord:     {ce['end_coord']}")
        print(f"  endpoint nodes ({len(cedge_endpoint_nodes[ce_idx])}):")
        for nid in sorted(cedge_endpoint_nodes[ce_idx]):
            if nid in node_coords:
                print(f"    {nid} -> {node_coords[nid]}")
    
    print("\n" + "=" * 70)
    print("C-node analysis: C-edges 430, 431, 893, 894")
    print("=" * 70)
    
    # Find all C-nodes connected to target C-edges
    relevant_cnodes = []
    for cnode_id, vnode in virtual_cnodes.items():
        if vnode['connected_cedges'] & set(target_cedges):
            relevant_cnodes.append(cnode_id)
    relevant_cnodes.sort()
    print(f"\nC-nodes connected to {target_cedges}: {relevant_cnodes}")
    
    for cnode_id in relevant_cnodes:
        vnode = virtual_cnodes[cnode_id]
        print(f"\n{'─' * 60}")
        print(f"C-node {cnode_id}")
        print(f"{'─' * 60}")
        print(f"Position: {vnode['position']}")
        print(f"Connected C-edges: {sorted(vnode['connected_cedges'])}")
        print(f"C-edge end associations: {sorted(vnode['c_edge_end_associations'])}")
        print(f"Original nodes ({len(vnode['original_nodes'])}):")
        
        for node in sorted(vnode['original_nodes']):
            pos = node_coords.get(node, "N/A")
            # Check which C-edges this node is an endpoint of
            is_ep_of = []
            for ce_idx in target_cedges:
                if node in cedge_endpoint_nodes[ce_idx]:
                    is_ep_of.append(ce_idx)
            ep_str = f"  <- endpoint of C-edge {is_ep_of}" if is_ep_of else "  <- NOT an endpoint of any target C-edge"
            print(f"  {node} @ {pos}{ep_str}")
        
        # Check: which target C-edges are connected but have NO endpoint node in this cluster?
        print(f"\nEndpoint coverage:")
        for ce_idx in sorted(vnode['connected_cedges']):
            if ce_idx not in target_cedges:
                continue
            cluster_nodes = set(vnode['original_nodes'])
            ep_nodes = cedge_endpoint_nodes[ce_idx]
            intersection = cluster_nodes & ep_nodes
            if intersection:
                print(f"  C-edge {ce_idx}: [OK] has {len(intersection)} endpoint node(s) in cluster")
            else:
                print(f"  C-edge {ce_idx}: [MISSING] NO endpoint node in cluster")
                # Find the nearest endpoint node
                vnode_pos = vnode['position']
                from utils.geometry import haversine_m
                nearest_ep = None
                nearest_dist = float('inf')
                for ep_nid in ep_nodes:
                    if ep_nid in node_coords:
                        dist = haversine_m(vnode_pos, node_coords[ep_nid])
                        if dist < nearest_dist:
                            nearest_dist = dist
                            nearest_ep = ep_nid
                if nearest_ep:
                    print(f"    Nearest endpoint: {nearest_ep} @ {node_coords[nearest_ep]} ({nearest_dist:.1f}m away)")


if __name__ == "__main__":
    main()
