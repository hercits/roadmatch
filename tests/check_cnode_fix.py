import sys
sys.path.insert(0, 'src')
import json
from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import *

def build_node_coords(node_features):
    coords = {}
    for f in node_features:
        nid = f['properties']['node_id']
        c = f['geometry']['coordinates']
        coords[nid] = (c[0], c[1])
    return coords

# Load data
with open('resource/miniquad/edges.geojson', encoding='utf-8') as f:
    edge_features = json.load(f)['features']
with open('resource/miniquad/nodes.geojson', encoding='utf-8') as f:
    node_features = json.load(f)['features']

# Run pipeline
edge_features, node_features, _ = split_edges_at_intersections(edge_features, node_features)
edge_clusters, core_edges = cluster_near_parallel_edges(edge_features, near_threshold_m=50.0, parallel_angle_threshold=30.0)
node_coords = build_node_coords(node_features)
c_edges = build_c_edge_graph(edge_clusters, edge_features, node_coords, core_edges_per_cluster=core_edges, near_threshold_m=50.0)
node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
connection_nodes = identify_connection_nodes(node_to_cedges)
clusters = cluster_connection_nodes(connection_nodes, node_coords)
virtual_cnodes = create_virtual_cnodes(clusters, connection_nodes, c_edges, node_coords, edge_clusters, core_edges, edge_features)

# Check C-node 443 and 439
for cnode_id in [443, 439]:
    if cnode_id not in virtual_cnodes:
        print(f'C-node {cnode_id} not found')
        continue
    
    vnode = virtual_cnodes[cnode_id]
    print(f'C-node {cnode_id}:')
    print(f'  Position: {vnode["position"]}')
    print(f'  Connected C-edges: {sorted(vnode["connected_cedges"])}')
    print(f'  Original nodes: {vnode["original_nodes"]}')
    
    # Check endpoint nodes for each connected C-edge
    cedge_endpoint_nodes = {}
    for ce_idx in range(len(c_edges)):
        cedge_endpoint_nodes[ce_idx] = identify_endpoint_nodes_for_cedge(
            ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords
        )
    
    print(f'  Endpoint node analysis:')
    endpoint_cedge_count = 0
    for ce_idx in sorted(vnode["connected_cedges"]):
        endpoint_nodes = cedge_endpoint_nodes[ce_idx]
        cluster_nodes = set(vnode["original_nodes"])
        intersection = cluster_nodes & endpoint_nodes
        print(f'    C-edge {ce_idx}: {len(intersection)} endpoint nodes in cluster')
        print(f'      Endpoint nodes: {endpoint_nodes}')
        print(f'      Cluster nodes: {cluster_nodes}')
        print(f'      Intersection: {intersection}')
        if intersection:
            endpoint_cedge_count += 1
    
    print(f'  endpoint_cedge_count: {endpoint_cedge_count}')
    
    # Check distance to C-edge 430
    ce = c_edges[430]
    dist = point_to_line_distance_m(vnode['position'], ce['start_coord'], ce['end_coord'])
    print(f'  Distance to C-edge 430: {dist:.2f} m')
    print()
