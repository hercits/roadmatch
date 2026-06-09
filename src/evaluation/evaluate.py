"""Evaluate path matching algorithm accuracy.

Compares predicted path against ground truth and computes accuracy
as the percentage of overlapping edge length relative to ground truth length.

Usage:
    cd src && uv run python evaluation/evaluate.py \
        --ground-truth-dir ../ground_truth/changsha \
        --result-dir ../roadmatch_result \
        --data-dir ../resource/changsha \
        --output ../evaluation_report.html

Dependencies: plotly>=6.7.0
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.geometry import haversine_m


def parse_path_file(path: Path) -> List[Tuple[str, str | None]]:
    """Parse path file into list of (node_id, edge_idx) tuples.
    
    Args:
        path: Path to ground_truth.txt or prediction.txt
        
    Returns:
        List of (node_id, edge_idx) tuples. Last entry has edge_idx=None.
    """
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2:
                node_id, edge_idx = parts
                entries.append((node_id, edge_idx))
            elif len(parts) == 1:
                entries.append((parts[0], None))
    return entries


def load_node_positions(cache_dir: Path) -> Dict[str, Tuple[float, float]]:
    """Load C-node positions from cache.
    
    Args:
        cache_dir: Cache directory (e.g., cache/changsha/)
        
    Returns:
        Dict mapping node_id (str) to (lon, lat) position.
    """
    cache_file = cache_dir / "merge_intersection.pkl"
    if not cache_file.exists():
        cache_file = cache_dir / "virtual_cnodes.pkl"
    
    if not cache_file.exists():
        raise FileNotFoundError(f"No virtual_cnodes cache found in {cache_dir}")
    
    with open(cache_file, 'rb') as f:
        vcnodes = pickle.load(f)
    
    positions = {}
    for vnode in vcnodes.values():
        node_id = vnode['id']
        if node_id.startswith('C-node_'):
            node_id = node_id.replace('C-node_', '')
        positions[node_id] = vnode['position']
    
    return positions


def load_edge_endpoints(cache_dir: Path) -> Dict[str, Tuple[str, str]]:
    """Load C-edge endpoint mappings from cache.
    
    Args:
        cache_dir: Cache directory (e.g., cache/changsha/)
        
    Returns:
        Dict mapping edge_idx (str) to (start_node_id, end_node_id).
    """
    cache_file = cache_dir / "filter_dangling.pkl"
    if not cache_file.exists():
        cache_file = cache_dir / "c_edges.pkl"
    
    if not cache_file.exists():
        raise FileNotFoundError(f"No C-edge cache found in {cache_dir}")
    
    with open(cache_file, 'rb') as f:
        c_edges = pickle.load(f)
    
    endpoints = {}
    for ce in c_edges:
        if ce.get('is_split', False):
            continue
        edge_idx = str(ce['idx'])
        start_node = ce.get('start_node_id', '')
        end_node = ce.get('end_node_id', '')
        if start_node.startswith('C-node_'):
            start_node = start_node.replace('C-node_', '')
        if end_node.startswith('C-node_'):
            end_node = end_node.replace('C-node_', '')
        endpoints[edge_idx] = (start_node, end_node)
    
    return endpoints


def validate_path_continuity(
    path_entries: List[Tuple[str, str | None]],
    edge_endpoints: Dict[str, Tuple[str, str]],
    path_name: str = "path",
) -> None:
    """Validate that path entries form a continuous path.
    
    Args:
        path_entries: List of (node_id, edge_idx) tuples
        edge_endpoints: Dict mapping edge_idx to (start_node, end_node)
        path_name: Name for error messages
        
    Raises:
        ValueError: If path is discontinuous
    """
    for i in range(len(path_entries) - 1):
        node_id, edge_idx = path_entries[i]
        next_node_id, _ = path_entries[i + 1]
        
        if edge_idx is None:
            continue
        
        if edge_idx not in edge_endpoints:
            raise ValueError(
                f"{path_name}: Edge {edge_idx} not found in graph (at position {i})"
            )
        
        start_node, end_node = edge_endpoints[edge_idx]
        
        if node_id not in (start_node, end_node):
            raise ValueError(
                f"{path_name}: Node {node_id} is not an endpoint of edge {edge_idx} "
                f"(endpoints: {start_node}, {end_node}) at position {i}"
            )
        
        if next_node_id not in (start_node, end_node):
            raise ValueError(
                f"{path_name}: Next node {next_node_id} is not an endpoint of edge {edge_idx} "
                f"(endpoints: {start_node}, {end_node}) at position {i}"
            )


def compute_edge_lengths(
    path_entries: List[Tuple[str, str | None]],
    node_positions: Dict[str, Tuple[float, float]],
) -> Dict[str, float]:
    """Compute length of each edge in the path.
    
    Args:
        path_entries: List of (node_id, edge_idx) tuples
        node_positions: Dict mapping node_id to (lon, lat)
        
    Returns:
        Dict mapping edge_idx to length in meters.
    """
    edge_lengths = {}
    
    for i in range(len(path_entries) - 1):
        node_id, edge_idx = path_entries[i]
        next_node_id, _ = path_entries[i + 1]
        
        if edge_idx is None:
            continue
        
        if node_id not in node_positions or next_node_id not in node_positions:
            continue
        
        pos1 = node_positions[node_id]
        pos2 = node_positions[next_node_id]
        length = haversine_m(pos1, pos2)
        edge_lengths[edge_idx] = length
    
    return edge_lengths


def compute_accuracy(
    gt_entries: List[Tuple[str, str | None]],
    pred_entries: List[Tuple[str, str | None]],
    node_positions: Dict[str, Tuple[float, float]],
) -> Tuple[float, float, float, set[str]]:
    """Compute accuracy between ground truth and prediction.
    
    Args:
        gt_entries: Ground truth path entries
        pred_entries: Predicted path entries
        node_positions: Node position mapping
        
    Returns:
        Tuple of (accuracy, gt_length, overlap_length, overlapping_edges)
    """
    gt_edge_lengths = compute_edge_lengths(gt_entries, node_positions)
    pred_edge_indices = {entry[1] for entry in pred_entries if entry[1] is not None}
    
    gt_length = sum(gt_edge_lengths.values())
    
    overlapping_edges = set()
    overlap_length = 0.0
    
    for edge_idx, length in gt_edge_lengths.items():
        if edge_idx in pred_edge_indices:
            overlapping_edges.add(edge_idx)
            overlap_length += length
    
    accuracy = overlap_length / gt_length if gt_length > 0 else 0.0
    
    return accuracy, gt_length, overlap_length, overlapping_edges


def visualize_paths(
    gt_entries: List[Tuple[str, str | None]],
    pred_entries: List[Tuple[str, str | None]],
    overlapping_edges: set[str],
    node_positions: Dict[str, Tuple[float, float]],
    accuracy: float,
    path_name: str,
    output_path: Path,
) -> None:
    """Visualize ground truth, prediction, and overlap.
    
    Args:
        gt_entries: Ground truth path entries
        pred_entries: Predicted path entries
        overlapping_edges: Set of overlapping edge indices
        node_positions: Node position mapping
        accuracy: Computed accuracy
        path_name: Name for the path (e.g., "path_1")
        output_path: Output HTML file path
    """
    traces = []
    
    gt_lons, gt_lats = [], []
    for node_id, _ in gt_entries:
        if node_id in node_positions:
            pos = node_positions[node_id]
            gt_lons.append(pos[0])
            gt_lats.append(pos[1])
    
    if gt_lons:
        traces.append(go.Scattermap(
            lon=gt_lons, lat=gt_lats,
            mode='lines',
            line=dict(width=3, color='#457b9d'),
            name='Ground Truth',
        ))
    
    pred_lons, pred_lats = [], []
    for node_id, _ in pred_entries:
        if node_id in node_positions:
            pos = node_positions[node_id]
            pred_lons.append(pos[0])
            pred_lats.append(pos[1])
    
    if pred_lons:
        traces.append(go.Scattermap(
            lon=pred_lons, lat=pred_lats,
            mode='lines',
            line=dict(width=3, color='#e63946'),
            name='Prediction',
        ))
    
    overlap_lons, overlap_lats = [], []
    for i in range(len(gt_entries) - 1):
        node_id, edge_idx = gt_entries[i]
        next_node_id, _ = gt_entries[i + 1]
        
        if edge_idx in overlapping_edges:
            if node_id in node_positions and next_node_id in node_positions:
                pos1 = node_positions[node_id]
                pos2 = node_positions[next_node_id]
                overlap_lons.extend([pos1[0], pos2[0], None])
                overlap_lats.extend([pos1[1], pos2[1], None])
    
    if overlap_lons:
        traces.append(go.Scattermap(
            lon=overlap_lons, lat=overlap_lats,
            mode='lines',
            line=dict(width=5, color='#2a9d8f'),
            name=f'Overlap ({accuracy:.1%})',
        ))
    
    if gt_lons:
        traces.append(go.Scattermap(
            lon=[gt_lons[0]], lat=[gt_lats[0]],
            mode='markers',
            marker=dict(size=12, color='#2a9d8f', symbol='circle'),
            name='Start',
        ))
        traces.append(go.Scattermap(
            lon=[gt_lons[-1]], lat=[gt_lats[-1]],
            mode='markers',
            marker=dict(size=12, color='#e76f51', symbol='circle'),
            name='End',
        ))
    
    title_text = f"{path_name}: Accuracy = {accuracy:.1%}"
    
    fig = go.Figure(data=traces)
    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lon=gt_lons[0] if gt_lons else 0, lat=gt_lats[0] if gt_lats else 0),
            zoom=14,
        ),
        title=dict(text=title_text, x=0.5),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        dragmode="pan",
    )
    
    fig.write_html(str(output_path))
    print(f"Saved visualization to {output_path}")


def evaluate_single_path(
    gt_file: Path,
    pred_file: Path,
    node_positions: Dict[str, Tuple[float, float]],
    edge_endpoints: Dict[str, Tuple[str, str]],
    output_dir: Path | None = None,
) -> Dict[str, float | str]:
    """Evaluate a single path prediction against ground truth.
    
    Args:
        gt_file: Ground truth file path
        pred_file: Prediction file path
        node_positions: Node position mapping
        edge_endpoints: Edge endpoint mapping
        output_dir: Optional directory to save visualization
        
    Returns:
        Dict with evaluation metrics
        
    Raises:
        ValueError: If prediction path is discontinuous
    """
    gt_entries = parse_path_file(gt_file)
    pred_entries = parse_path_file(pred_file)
    
    validate_path_continuity(gt_entries, edge_endpoints, f"ground_truth/{gt_file.parent.name}")
    validate_path_continuity(pred_entries, edge_endpoints, f"prediction/{pred_file.stem}")
    
    accuracy, gt_length, overlap_length, overlapping_edges = compute_accuracy(
        gt_entries, pred_entries, node_positions
    )
    
    result = {
        'path_name': gt_file.parent.name,
        'accuracy': accuracy,
        'gt_length_m': gt_length,
        'overlap_length_m': overlap_length,
        'gt_edge_count': len([e for e in gt_entries if e[1] is not None]),
        'pred_edge_count': len([e for e in pred_entries if e[1] is not None]),
        'overlap_edge_count': len(overlapping_edges),
    }
    
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        pred_name = pred_file.stem
        viz_path = output_dir / f"{result['path_name']}_{pred_name}_comparison.html"
        display_name = f"{result['path_name']} ({pred_name})"
        visualize_paths(
            gt_entries, pred_entries, overlapping_edges,
            node_positions, accuracy, display_name, viz_path
        )
    
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate path matching accuracy")
    parser.add_argument("--ground-truth-dir", type=Path, required=True,
                        help="Directory containing ground truth paths")
    parser.add_argument("--result-dir", type=Path, required=True,
                        help="Directory containing prediction results")
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="Data directory (e.g., resource/changsha)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output HTML report path")
    args = parser.parse_args()
    
    cache_dir = args.data_dir.parent.parent / "cache" / args.data_dir.name
    print(f"Loading node positions from {cache_dir}...")
    node_positions = load_node_positions(cache_dir)
    print(f"Loaded {len(node_positions)} nodes")
    
    print(f"Loading edge endpoints from {cache_dir}...")
    edge_endpoints = load_edge_endpoints(cache_dir)
    print(f"Loaded {len(edge_endpoints)} edges")
    
    gt_paths = sorted(args.ground_truth_dir.glob("path_*/ground_truth.txt"))
    print(f"Found {len(gt_paths)} ground truth paths")
    
    results = []
    output_dir = args.result_dir / "visualizations"
    
    for gt_file in gt_paths:
        path_name = gt_file.parent.name
        pred_dir = args.result_dir / path_name
        
        if not pred_dir.exists():
            print(f"Warning: No prediction directory found for {path_name}, skipping")
            continue
        
        pred_files = sorted(pred_dir.glob("*.txt"))
        if not pred_files:
            print(f"Warning: No prediction files found in {pred_dir}, skipping")
            continue
        
        for pred_file in pred_files:
            pred_name = pred_file.stem
            full_name = f"{path_name}_{pred_name}"
            
            print(f"\nEvaluating {full_name}...")
            try:
                result = evaluate_single_path(gt_file, pred_file, node_positions, edge_endpoints, output_dir)
                result['prediction_name'] = pred_name
                results.append(result)
                
                print(f"  Accuracy: {result['accuracy']:.1%}")
                print(f"  GT length: {result['gt_length_m']:.1f}m ({result['gt_edge_count']} edges)")
                print(f"  Overlap: {result['overlap_length_m']:.1f}m ({result['overlap_edge_count']} edges)")
            except ValueError as e:
                print(f"  ERROR: {e}")
                continue
    
    if results:
        avg_accuracy = sum(r['accuracy'] for r in results) / len(results)
        print(f"\n{'='*60}")
        print(f"Overall average accuracy: {avg_accuracy:.1%}")
        print(f"Evaluated {len(results)} predictions")
        
        by_type = {}
        for r in results:
            pred_name = r.get('prediction_name', 'unknown')
            if pred_name not in by_type:
                by_type[pred_name] = []
            by_type[pred_name].append(r['accuracy'])
        
        print(f"\nAccuracy by prediction type:")
        for pred_name in sorted(by_type.keys()):
            accs = by_type[pred_name]
            avg = sum(accs) / len(accs)
            print(f"  {pred_name}: {avg:.1%} (n={len(accs)})")
        
        if args.output:
            report = {
                'average_accuracy': avg_accuracy,
                'prediction_count': len(results),
                'by_type': {k: {'avg_accuracy': sum(v)/len(v), 'count': len(v)} 
                           for k, v in by_type.items()},
                'results': results,
            }
            report_file = args.output.with_suffix('.json')
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2)
            print(f"Saved report to {report_file}")


if __name__ == "__main__":
    main()
