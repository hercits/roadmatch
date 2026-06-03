"""Debug visualization for C-edges 430, 431, 893, 894 and their C-nodes.

Shows how C-node positions are computed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import plotly.graph_objects as go
from mock.edge_splitter import split_edges_at_intersections
from mock.graph_simplifier import (
    _compute_cnode_position,
    build_c_edge_graph,
    build_node_to_cedges_map,
    cluster_connection_nodes,
    cluster_near_parallel_edges,
    create_virtual_cnodes,
    identify_connection_nodes,
    identify_endpoint_nodes_for_cedge,
    average_position,
    line_intersection_2d,
    angular_delta_mod180,
    project_to_line,
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


def trace_position_computation(
    nodes: list[str],
    connected_cedges: set,
    c_edge_end_associations: set,
    c_edges: list[dict],
    node_coords: dict[str, tuple],
) -> dict:
    """Trace how a C-node position is computed."""
    info = {
        "nodes": nodes,
        "connected_cedges": sorted(connected_cedges),
        "c_edge_end_associations": sorted(c_edge_end_associations),
    }
    
    # Count C-edges ending here
    cedges_ending_here = set(ce_idx for ce_idx, _ in c_edge_end_associations)
    info["cedges_ending_count"] = len(cedges_ending_here)
    info["cedges_ending_here"] = sorted(cedges_ending_here)
    
    # Compute unique directions
    directions = set()
    for ce_idx in connected_cedges:
        directions.add(round(c_edges[ce_idx]['direction_deg']))
    info["directions"] = sorted(directions)
    
    # Determine decision path
    if info["cedges_ending_count"] >= 2:
        info["decision"] = "average_position (2+ C-edges end here)"
        info["position"] = average_position(nodes, node_coords)
    elif len(directions) == 1:
        info["decision"] = "project_to_line (1 direction)"
        avg_pos = average_position(nodes, node_coords)
        ce_idx = list(connected_cedges)[0]
        ce = c_edges[ce_idx]
        info["position"] = project_to_line(avg_pos, ce['start_coord'], ce['end_coord'])
    elif len(directions) == 2:
        dir_list = sorted(directions)
        angle_diff = angular_delta_mod180(dir_list[0], dir_list[1])
        info["angle_diff"] = angle_diff
        
        if angle_diff < 5.0:
            info["decision"] = f"average_position (nearly parallel, diff={angle_diff:.1f}°)"
            info["position"] = average_position(nodes, node_coords)
        else:
            info["decision"] = f"line_intersection (2 directions, diff={angle_diff:.1f}°)"
            ce1 = next(ce for ce in connected_cedges if round(c_edges[ce]['direction_deg']) == dir_list[0])
            ce2 = next(ce for ce in connected_cedges if round(c_edges[ce]['direction_deg']) == dir_list[1])
            info["intersection_cedges"] = (ce1, ce2)
            info["position"] = line_intersection_2d(
                c_edges[ce1]['start_coord'], c_edges[ce1]['end_coord'],
                c_edges[ce2]['start_coord'], c_edges[ce2]['end_coord'],
            )
    else:
        info["decision"] = f"average_position ({len(directions)} directions)"
        info["position"] = average_position(nodes, node_coords)
    
    return info


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
    
    # Target C-edges
    target_cedges = {430, 431, 893, 894}
    
    # Find C-nodes connected to target C-edges
    relevant_cnodes = {}
    for cnode_id, vnode in virtual_cnodes.items():
        if vnode['connected_cedges'] & target_cedges:
            relevant_cnodes[cnode_id] = vnode
    
    print(f"\nFound {len(relevant_cnodes)} C-nodes connected to C-edges {sorted(target_cedges)}")
    
    # Trace position computation for each C-node
    cnode_traces = {}
    for cnode_id, vnode in relevant_cnodes.items():
        trace = trace_position_computation(
            vnode['original_nodes'],
            vnode['connected_cedges'],
            vnode['c_edge_end_associations'],
            c_edges,
            node_coords
        )
        cnode_traces[cnode_id] = trace
        print(f"\nC-node {cnode_id}:")
        print(f"  Position: {vnode['position']}")
        print(f"  Connected C-edges: {trace['connected_cedges']}")
        print(f"  C-edges ending here: {trace['cedges_ending_here']} (count={trace['cedges_ending_count']})")
        print(f"  Directions: {trace['directions']}")
        print(f"  Decision: {trace['decision']}")
    
    # Create plotly figure
    fig = go.Figure()
    
    # Color map for C-edges
    colors = {
        430: "#e63946",
        431: "#457b9d",
        893: "#2a9d8f",
        894: "#f4a261",
    }
    
    # Plot C-edges
    for ce_idx in sorted(target_cedges):
        ce = c_edges[ce_idx]
        
        # Plot representative line
        fig.add_trace(go.Scatter(
            x=[ce['start_coord'][0], ce['end_coord'][0]],
            y=[ce['start_coord'][1], ce['end_coord'][1]],
            mode='lines+markers',
            line=dict(color=colors[ce_idx], width=3),
            marker=dict(size=8),
            name=f"C-edge {ce_idx} (dir={ce['direction_deg']:.1f}°)",
        ))
        
        # Plot all edges in cluster
        for edge_idx in edge_clusters[ce_idx]:
            edge = edge_features[edge_idx]
            coords = edge['geometry']['coordinates']
            fig.add_trace(go.Scatter(
                x=[c[0] for c in coords],
                y=[c[1] for c in coords],
                mode='lines',
                line=dict(color=colors[ce_idx], width=1, dash='dot'),
                opacity=0.3,
                showlegend=False,
                hoverinfo='name',
                name=f"C-edge {ce_idx} edge {edge_idx}",
            ))
    
    # Plot C-nodes
    for cnode_id, vnode in relevant_cnodes.items():
        pos = vnode['position']
        trace = cnode_traces[cnode_id]
        
        # Determine marker color based on decision
        if "intersection" in trace['decision']:
            marker_color = "#00ff00"  # green for intersection
            marker_symbol = "star"
        elif "average" in trace['decision']:
            marker_color = "#ff00ff"  # magenta for average
            marker_symbol = "square"
        else:
            marker_color = "#ffff00"  # yellow for projection
            marker_symbol = "diamond"
        
        # Build hover text
        hover = f"C-node {cnode_id}<br>"
        hover += f"Position: ({pos[0]:.7f}, {pos[1]:.7f})<br>"
        hover += f"Connected: {trace['connected_cedges']}<br>"
        hover += f"Ending here: {trace['cedges_ending_here']}<br>"
        hover += f"Directions: {trace['directions']}<br>"
        hover += f"Decision: {trace['decision']}"
        
        fig.add_trace(go.Scatter(
            x=[pos[0]],
            y=[pos[1]],
            mode='markers+text',
            marker=dict(size=15, color=marker_color, symbol=marker_symbol, line=dict(width=2, color='black')),
            text=[f"C-node {cnode_id}"],
            textposition="top center",
            name=f"C-node {cnode_id}",
            hovertext=hover,
            hoverinfo="text",
        ))
        
        # Plot original nodes
        for node in trace['nodes']:
            if node in node_coords:
                npos = node_coords[node]
                fig.add_trace(go.Scatter(
                    x=[npos[0]],
                    y=[npos[1]],
                    mode='markers',
                    marker=dict(size=6, color='gray', symbol='circle'),
                    showlegend=False,
                    hovertext=f"Original node: {node}",
                    hoverinfo="text",
                ))
    
    # Add legend for decision types
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(size=15, color="#00ff00", symbol='star'),
        name="Intersection (2 directions)",
        showlegend=True,
    ))
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(size=15, color="#ff00ff", symbol='square'),
        name="Average position",
        showlegend=True,
    ))
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(size=15, color="#ffff00", symbol='diamond'),
        name="Projection to line",
        showlegend=True,
    ))
    
    # Update layout
    fig.update_layout(
        title=f"C-edges {sorted(target_cedges)} and their C-nodes",
        xaxis_title="Longitude",
        yaxis_title="Latitude",
        showlegend=True,
        hovermode='closest',
        width=1200,
        height=800,
    )
    
    # Save
    output_path = Path("resource/miniquad/debug_cnode_plot.html")
    fig.write_html(str(output_path))
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
