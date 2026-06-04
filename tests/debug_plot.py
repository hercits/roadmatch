"""通用 C-edge/C-node 调试绘图脚本

用法:
    uv run python tests/debug_plot.py --cnodes 232 234
    uv run python tests/debug_plot.py --cedges 944 945 946
    uv run python tests/debug_plot.py --cnodes 232 --cedges 944
    uv run python tests/debug_plot.py --cnodes 232 234 --cedges 944 945 --output custom.html
"""
import argparse
import json
import sys
from pathlib import Path

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mock.graph_simplifier import (
    cluster_near_parallel_edges,
    build_c_edge_graph, build_node_to_cedges_map, identify_connection_nodes,
    cluster_connection_nodes, create_virtual_cnodes, update_c_edge_endpoints,
    split_c_edges_at_intersection_nodes, filter_spur_core_edges,
    recompute_c_edge_geometry, identify_endpoint_nodes_for_cedge,
    merge_t_junction_cnodes,
)
from utils.geometry import project_to_bearing_m
from mock.edge_splitter import split_edges_at_intersections


def load_geojson(path: Path) -> list[dict]:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('features', [])


def build_node_coords(node_features: list[dict]) -> dict[str, tuple]:
    coords = {}
    for feat in node_features:
        node_id = feat['properties']['node_id']
        lon, lat = feat['geometry']['coordinates']
        coords[node_id] = (lon, lat)
    return coords


def main():
    parser = argparse.ArgumentParser(description='C-edge/C-node 调试绘图')
    parser.add_argument('--data-dir', type=str, default='resource/miniquad',
                        help='数据目录 (default: resource/miniquad)')
    parser.add_argument('--cnodes', type=int, nargs='+', default=[],
                        help='要绘制的 C-node ID 列表')
    parser.add_argument('--cedges', type=int, nargs='+', default=[],
                        help='要绘制的 C-edge ID 列表')
    parser.add_argument('--output', type=str, default=None,
                        help='输出文件路径 (default: resource/<data-dir>/debug_plot.html)')
    parser.add_argument('--near-threshold', type=float, default=50.0,
                        help='聚类距离阈值 (default: 50.0)')
    parser.add_argument('--angle-threshold', type=float, default=15.0,
                        help='平行角度阈值 (default: 15.0)')
    parser.add_argument('--show-endpoints', type=int, default=None,
                        help='显示指定 C-edge 的端点节点 (起点-三角形, 终点-菱形)')
    
    args = parser.parse_args()
    
    if not args.cnodes and not args.cedges:
        parser.error('至少需要指定 --cnodes 或 --cedges 之一')
    
    data_dir = Path(args.data_dir)
    edges_path = data_dir / "edges.geojson"
    nodes_path = data_dir / "nodes.geojson"
    
    if not edges_path.exists():
        print(f"错误: 找不到 {edges_path}")
        sys.exit(1)
    
    print(f"加载数据: {data_dir}")
    edge_features = load_geojson(edges_path)
    node_features = load_geojson(nodes_path) if nodes_path.exists() else []
    
    print(f"原始边数: {len(edge_features)}, 节点数: {len(node_features)}")
    
    edge_features, node_features, _ = split_edges_at_intersections(edge_features, node_features)
    node_coords = build_node_coords(node_features)
    
    print(f"分裂后边数: {len(edge_features)}, 节点数: {len(node_features)}")
    
    edge_clusters, core_edges = cluster_near_parallel_edges(
        edge_features, near_threshold_m=args.near_threshold, parallel_angle_threshold=args.angle_threshold,
    )
    
    c_edges = build_c_edge_graph(
        edge_clusters, edge_features, node_coords,
        core_edges_per_cluster=core_edges, near_threshold_m=args.near_threshold,
    )
    
    print(f"C-edge 数: {len(c_edges)}")
    
    node_to_cedges = build_node_to_cedges_map(c_edges, edge_clusters, edge_features)
    
    core_edges = filter_spur_core_edges(core_edges, edge_clusters, edge_features)
    recompute_c_edge_geometry(c_edges, core_edges, edge_clusters, edge_features, node_coords, near_threshold_m=args.near_threshold)
    
    connection_nodes = identify_connection_nodes(node_to_cedges)
    clusters = cluster_connection_nodes(connection_nodes, node_coords)
    
    virtual_cnodes = create_virtual_cnodes(
        clusters, connection_nodes, c_edges, node_coords,
        edge_clusters, core_edges, edge_features, near_threshold_m=args.near_threshold,
        parallel_angle_threshold=args.angle_threshold,
    )
    
    virtual_cnodes = merge_t_junction_cnodes(virtual_cnodes, near_threshold_m=args.near_threshold)
    
    update_c_edge_endpoints(c_edges, virtual_cnodes)
    c_edges = split_c_edges_at_intersection_nodes(
        c_edges, virtual_cnodes, edge_clusters, edge_features,
        parallel_angle_threshold=args.angle_threshold,
    )
    
    print(f"分裂后 C-edge 数: {len(c_edges)}, C-node 数: {len(virtual_cnodes)}")
    
    target_ce_indices = set(args.cedges)
    target_cnode_ids = set(args.cnodes)
    
    for cn_id in list(target_cnode_ids):
        if cn_id in virtual_cnodes:
            target_ce_indices.update(virtual_cnodes[cn_id]['connected_cedges'])
    
    adjacent_cnode_ids = set(target_cnode_ids)
    for ce in c_edges:
        if ce.get('is_split', False):
            continue
        if ce.get('parent_idx', ce['idx']) in target_ce_indices:
            for key in ('start_node_id', 'end_node_id'):
                nid = ce.get(key, '')
                if nid and nid.startswith('C-node_'):
                    cn_id = int(nid.split('_')[1])
                    adjacent_cnode_ids.add(cn_id)
    
    for cn_id in adjacent_cnode_ids:
        if cn_id in virtual_cnodes:
            target_ce_indices.update(virtual_cnodes[cn_id]['connected_cedges'])
    
    related_ce_indices = sorted(target_ce_indices)
    
    print(f"目标 C-nodes: {sorted(target_cnode_ids)}")
    print(f"相邻 C-nodes: {sorted(adjacent_cnode_ids)}")
    print(f"相关 C-edges: {related_ce_indices}")
    
    ce_colors = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
                 '#42d4f4', '#f032e6', '#bfef45', '#fabed4', '#469990',
                 '#dcbeff', '#aaffc3', '#808000', '#008080']
    
    traces = []
    
    for i, ce_idx in enumerate(related_ce_indices):
        if ce_idx >= len(c_edges):
            continue
        color = ce_colors[i % len(ce_colors)]
        ce = c_edges[ce_idx]
        parent_idx = ce.get('parent_idx', ce['idx'])
        cluster_edge_indices = edge_clusters[parent_idx] if parent_idx < len(edge_clusters) else []
        
        for edge_idx in cluster_edge_indices:
            edge = edge_features[edge_idx]
            coords = edge['geometry']['coordinates']
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            
            traces.append(go.Scatter(
                x=lons,
                y=lats,
                mode='lines',
                line=dict(width=2, color=color),
                name=f'C-edge {parent_idx} (edge {edge_idx})',
                showlegend=False,
                hoverinfo='name',
            ))
        
        if 'split_idx' in ce:
            ce_label = f'C-edge {parent_idx}-{ce["split_idx"]} ({ce["direction_deg"]:.1f}°)'
        else:
            ce_label = f'C-edge {parent_idx} ({ce["direction_deg"]:.1f}°)'
        
        traces.append(go.Scatter(
            x=[ce['start_coord'][0], ce['end_coord'][0]],
            y=[ce['start_coord'][1], ce['end_coord'][1]],
            mode='lines+markers',
            line=dict(width=4, color=color),
            marker=dict(size=8, color=color, symbol='diamond'),
            name=ce_label,
            hoverinfo='name',
        ))
    
    cn_colors = ['#ff0000', '#0000ff', '#00aa00', '#ff00ff', '#00aaaa',
                 '#aa5500', '#5500aa', '#aa0055']
    for i, cn_id in enumerate(sorted(adjacent_cnode_ids)):
        if cn_id not in virtual_cnodes:
            continue
        vnode = virtual_cnodes[cn_id]
        color = cn_colors[i % len(cn_colors)]
        
        pos = vnode['position']
        traces.append(go.Scatter(
            x=[pos[0]],
            y=[pos[1]],
            mode='markers+text',
            marker=dict(size=16, color=color, symbol='star', line=dict(width=2, color='black')),
            text=[vnode['id']],
            textposition='top center',
            name=f'{vnode["id"]} (position)',
            hovertext=(
                f'{vnode["id"]}<br>'
                f'pos=({pos[0]:.7f}, {pos[1]:.7f})<br>'
                f'connected_cedges={sorted(vnode["connected_cedges"])}<br>'
                f'end_associations={sorted(vnode["c_edge_end_associations"])}'
            ),
            hoverinfo='text',
        ))
        
        for node_id in vnode['original_nodes']:
            if node_id in node_coords:
                nc = node_coords[node_id]
                traces.append(go.Scatter(
                    x=[nc[0]],
                    y=[nc[1]],
                    mode='markers+text',
                    marker=dict(size=10, color=color, symbol='circle', line=dict(width=1, color='black')),
                    text=[node_id],
                    textposition='bottom center',
                    textfont=dict(size=8),
                    name=f'{vnode["id"]} node: {node_id}',
                    hovertext=(
                        f'Original node: {node_id}<br>'
                        f'pos=({nc[0]:.7f}, {nc[1]:.7f})<br>'
                        f'belongs to {vnode["id"]}'
                    ),
                    hoverinfo='text',
                ))
    
    # 绘制指定 C-edge 的端点节点
    if args.show_endpoints is not None:
        ce_idx = args.show_endpoints
        if ce_idx < len(c_edges):
            ce = c_edges[ce_idx]
            parent_idx = ce.get('parent_idx', ce['idx'])
            
            # 获取端点节点集合
            ep_nodes = identify_endpoint_nodes_for_cedge(
                ce_idx, c_edges, edge_clusters, core_edges, edge_features, node_coords,
                near_threshold_m=args.near_threshold
            )
            
            # 投影到 C-edge 方向，区分起点和终点
            start_proj = project_to_bearing_m(ce['start_coord'], ce['start_coord'], ce['direction_deg'])
            end_proj = project_to_bearing_m(ce['end_coord'], ce['start_coord'], ce['direction_deg'])
            mid_proj = (start_proj + end_proj) / 2
            
            start_nodes = []
            end_nodes = []
            
            for node_id in ep_nodes:
                if node_id in node_coords:
                    node_pos = node_coords[node_id]
                    proj = project_to_bearing_m(node_pos, ce['start_coord'], ce['direction_deg'])
                    if proj < mid_proj:
                        start_nodes.append((node_id, node_pos))
                    else:
                        end_nodes.append((node_id, node_pos))
            
            # 绘制起点节点（三角形）
            if start_nodes:
                start_lons = [pos[0] for _, pos in start_nodes]
                start_lats = [pos[1] for _, pos in start_nodes]
                start_texts = [node_id for node_id, _ in start_nodes]
                
                traces.append(go.Scatter(
                    x=start_lons,
                    y=start_lats,
                    mode='markers+text',
                    marker=dict(size=12, color='#00ff00', symbol='triangle-up', line=dict(width=2, color='black')),
                    text=start_texts,
                    textposition='top center',
                    textfont=dict(size=8),
                    name=f'C-edge {parent_idx} start endpoints ({len(start_nodes)})',
                    hovertext=[f'Start endpoint: {node_id}<br>pos=({pos[0]:.7f}, {pos[1]:.7f})' for node_id, pos in start_nodes],
                    hoverinfo='text',
                ))
            
            # 绘制终点节点（菱形）
            if end_nodes:
                end_lons = [pos[0] for _, pos in end_nodes]
                end_lats = [pos[1] for _, pos in end_nodes]
                end_texts = [node_id for node_id, _ in end_nodes]
                
                traces.append(go.Scatter(
                    x=end_lons,
                    y=end_lats,
                    mode='markers+text',
                    marker=dict(size=12, color='#ff00ff', symbol='diamond', line=dict(width=2, color='black')),
                    text=end_texts,
                    textposition='top center',
                    textfont=dict(size=8),
                    name=f'C-edge {parent_idx} end endpoints ({len(end_nodes)})',
                    hovertext=[f'End endpoint: {node_id}<br>pos=({pos[0]:.7f}, {pos[1]:.7f})' for node_id, pos in end_nodes],
                    hoverinfo='text',
                ))
            
            print(f"C-edge {parent_idx} 端点节点: 起点 {len(start_nodes)} 个, 终点 {len(end_nodes)} 个")
    
    title_parts = []
    if args.cnodes:
        title_parts.append(f'C-nodes {args.cnodes}')
    if args.cedges:
        title_parts.append(f'C-edges {args.cedges}')
    title = ' & '.join(title_parts) + ' Debug'
    
    fig = go.Figure(data=traces)
    fig.update_layout(
        title=title,
        xaxis_title='Longitude',
        yaxis_title='Latitude',
        showlegend=True,
        legend=dict(y=0.99, x=1.01, font=dict(size=9)),
        width=1200,
        height=800,
        plot_bgcolor='white',
    )
    fig.update_xaxes(scaleanchor='y', scaleratio=1)
    
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = data_dir / "debug_plot.html"
    
    fig.write_html(str(output_path))
    print(f"已保存到: {output_path}")


if __name__ == "__main__":
    main()
