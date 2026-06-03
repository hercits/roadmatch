"""Check if C-node 442 is correctly positioned after the fix."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.geometry import point_to_line_distance_m
from mock.graph_simplifier import (
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_near_parallel_edges,
    create_virtual_cnodes,
    identify_connection_nodes,
)
from mock.edge_splitter import split_edges_at_intersections


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
    
    # Split edges
    edge_features, node_features, _ = split_edges_at_intersections(
        edge_features, node_features
    )
    
    # Cluster edges
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features, near_threshold_m=50.0, parallel_angle_threshold=30.0
    )
    
    # Build node coords
    node_coords = build_node_coords(node_features)
    
    # Build C-edge graph
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords, core_edges
    )
    
    # Build node-to-C-edges map
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
    
    # Identify connection nodes
    connection_nodes = identify_connection_nodes(node_to_cedges)
    
    # Cluster connection nodes
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    
    # Create virtual C-nodes
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features
    )
    
    # Check C-node 442
    cnode_id = 442
    if cnode_id not in virtual_cnodes:
        print(f"C-node {cnode_id} not found")
        return
    
    vnode = virtual_cnodes[cnode_id]
    print(f"C-node {cnode_id}:")
    print(f"  Position: {vnode['position']}")
    print(f"  Connected C-edges: {sorted(vnode['connected_cedges'])}")
    print(f"  C-edge end associations: {vnode['c_edge_end_associations']}")
    
    # Check distance to C-edge 430
    ce_430 = c_edges[430]
    dist = point_to_line_distance_m(
        vnode['position'],
        ce_430['start_coord'],
        ce_430['end_coord']
    )
    print(f"  Distance to C-edge 430: {dist:.2f} m")
    
    # Check distance to C-edge 893
    ce_893 = c_edges[893]
    dist = point_to_line_distance_m(
        vnode['position'],
        ce_893['start_coord'],
        ce_893['end_coord']
    )
    print(f"  Distance to C-edge 893: {dist:.2f} m")


if __name__ == "__main__":
    main()
