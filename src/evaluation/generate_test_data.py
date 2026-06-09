"""Generate test prediction data for evaluation script validation.

Creates synthetic predictions with known overlap levels to verify
the evaluation script computes accuracy correctly.

Usage:
    cd src && uv run python evaluation/generate_test_data.py \
        --ground-truth-dir ../ground_truth/changsha \
        --result-dir ../roadmatch_result \
        --data-dir ../resource/changsha
"""
from __future__ import annotations

import argparse
import pickle
import random
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_path_file(path: Path) -> List[Tuple[str, str | None]]:
    """Parse path file into list of (node_id, edge_idx) tuples."""
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


def write_path_file(entries: List[Tuple[str, str | None]], path: Path) -> None:
    """Write path entries to file."""
    with open(path, 'w', encoding='utf-8') as f:
        for node_id, edge_idx in entries:
            if edge_idx is not None:
                f.write(f"{node_id} {edge_idx}\n")
            else:
                f.write(f"{node_id}\n")


def load_graph(cache_dir: Path) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, Tuple[str, str]]]:
    """Load adjacency graph and edge endpoints from C-edge cache.
    
    Args:
        cache_dir: Cache directory
        
    Returns:
        Tuple of (adjacency, endpoints):
        - adjacency: Dict mapping node_id to list of (edge_idx, other_node_id)
        - endpoints: Dict mapping edge_idx to (start_node, end_node)
    """
    cache_file = cache_dir / "filter_dangling.pkl"
    if not cache_file.exists():
        cache_file = cache_dir / "c_edges.pkl"
    
    if not cache_file.exists():
        raise FileNotFoundError(f"No C-edge cache found in {cache_dir}")
    
    with open(cache_file, 'rb') as f:
        c_edges = pickle.load(f)
    
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    endpoints: Dict[str, Tuple[str, str]] = {}
    
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
        
        if start_node not in adjacency:
            adjacency[start_node] = []
        if end_node not in adjacency:
            adjacency[end_node] = []
        
        adjacency[start_node].append((edge_idx, end_node))
        adjacency[end_node].append((edge_idx, start_node))
    
    return adjacency, endpoints


def generate_perfect_prediction(
    gt_entries: List[Tuple[str, str | None]],
) -> List[Tuple[str, str | None]]:
    """Generate perfect prediction (100% overlap)."""
    return gt_entries.copy()


def generate_partial_prediction(
    gt_entries: List[Tuple[str, str | None]],
    adjacency: Dict[str, List[Tuple[str, str]]],
    detour_prob: float,
    seed: int | None = None,
) -> List[Tuple[str, str | None]]:
    """Generate partial prediction by taking detours at intersection nodes.
    
    At nodes with degree >= 3, randomly take a different branch instead of
    following the ground truth.
    
    Args:
        gt_entries: Ground truth path entries
        adjacency: Node adjacency graph
        detour_prob: Probability of taking a detour at intersection nodes
        seed: Random seed
        
    Returns:
        Modified path entries (continuous)
    """
    if seed is not None:
        random.seed(seed)
    
    gt_edge_set = {e[1] for e in gt_entries if e[1] is not None}
    
    result = []
    visited_edges: Set[str] = set()
    
    i = 0
    while i < len(gt_entries):
        node_id, edge_idx = gt_entries[i]
        
        if edge_idx is None:
            result.append((node_id, None))
            break
        
        degree = len(adjacency.get(node_id, []))
        
        if degree >= 3 and random.random() < detour_prob:
            detour_len = random.randint(3, 10)
            detour_path = _take_detour(
                node_id, edge_idx, adjacency, visited_edges, gt_edge_set, detour_len
            )
            
            if detour_path:
                result.extend(detour_path)
                visited_edges.update(e[1] for e in detour_path if e[1] is not None)
                
                last_node = detour_path[-1][0]
                
                for j in range(i + 1, len(gt_entries)):
                    next_node, _ = gt_entries[j]
                    if next_node == last_node:
                        i = j
                        break
                else:
                    i += 1
                continue
        
        result.append((node_id, edge_idx))
        if edge_idx:
            visited_edges.add(edge_idx)
        i += 1
    
    return result


def _take_detour(
    start_node: str,
    gt_edge: str,
    adjacency: Dict[str, List[Tuple[str, str]]],
    visited_edges: Set[str],
    gt_edge_set: Set[str],
    max_length: int,
) -> List[Tuple[str, str | None]] | None:
    """Take a detour from start_node, avoiding the ground truth edge.
    
    Args:
        start_node: Starting node
        gt_edge: Ground truth edge to avoid
        adjacency: Node adjacency graph
        visited_edges: Already visited edges
        gt_edge_set: All ground truth edges
        max_length: Maximum detour length
        
    Returns:
        Path entries or None if no detour possible
    """
    if start_node not in adjacency:
        return None
    
    candidates = [
        (e, n) for e, n in adjacency[start_node]
        if e != gt_edge and e not in visited_edges and e not in gt_edge_set
    ]
    
    if not candidates:
        candidates = [
            (e, n) for e, n in adjacency[start_node]
            if e != gt_edge and e not in visited_edges
        ]
    
    if not candidates:
        return None
    
    path = []
    current = start_node
    
    edge_idx, next_node = random.choice(candidates)
    path.append((current, edge_idx))
    visited_edges.add(edge_idx)
    current = next_node
    
    for _ in range(max_length - 1):
        if current not in adjacency:
            break
        
        candidates = [
            (e, n) for e, n in adjacency[current]
            if e not in visited_edges and e not in gt_edge_set
        ]
        
        if not candidates:
            candidates = [
                (e, n) for e, n in adjacency[current]
                if e not in visited_edges
            ]
        
        if not candidates:
            break
        
        edge_idx, next_node = random.choice(candidates)
        path.append((current, edge_idx))
        visited_edges.add(edge_idx)
        current = next_node
    
    if path:
        path.append((current, None))
    
    return path


def generate_low_overlap_prediction(
    gt_entries: List[Tuple[str, str | None]],
    adjacency: Dict[str, List[Tuple[str, str]]],
    seed: int | None = None,
) -> List[Tuple[str, str | None]]:
    """Generate low overlap prediction by taking many detours.
    
    Args:
        gt_entries: Ground truth path entries
        adjacency: Node adjacency graph
        seed: Random seed
        
    Returns:
        Path entries with low overlap (continuous)
    """
    return generate_partial_prediction(gt_entries, adjacency, detour_prob=0.9, seed=seed)


def generate_test_data(
    gt_dir: Path,
    result_dir: Path,
    cache_dir: Path,
) -> None:
    """Generate test predictions for all ground truth paths.
    
    Args:
        gt_dir: Ground truth directory
        result_dir: Output directory for predictions
        cache_dir: Cache directory for C-edge graph
    """
    print(f"Loading graph from {cache_dir}...")
    adjacency, endpoints = load_graph(cache_dir)
    print(f"Loaded {len(adjacency)} nodes, {len(endpoints)} edges")
    
    gt_paths = sorted(gt_dir.glob("path_*/ground_truth.txt"))
    print(f"Found {len(gt_paths)} ground truth paths")
    
    for gt_file in gt_paths:
        path_name = gt_file.parent.name
        print(f"\nProcessing {path_name}...")
        
        gt_entries = parse_path_file(gt_file)
        output_dir = result_dir / path_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        perfect = generate_perfect_prediction(gt_entries)
        write_path_file(perfect, output_dir / "perfect.txt")
        print(f"  Generated perfect.txt (100% overlap)")
        
        partial = generate_partial_prediction(
            gt_entries, adjacency, detour_prob=0.5, seed=42
        )
        write_path_file(partial, output_dir / "partial.txt")
        print(f"  Generated partial.txt (~60% overlap)")
        
        low = generate_low_overlap_prediction(
            gt_entries, adjacency, seed=123
        )
        write_path_file(low, output_dir / "low.txt")
        print(f"  Generated low.txt (~20% overlap)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test prediction data")
    parser.add_argument("--ground-truth-dir", type=Path, required=True,
                        help="Directory containing ground truth paths")
    parser.add_argument("--result-dir", type=Path, required=True,
                        help="Output directory for predictions")
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="Data directory (e.g., resource/changsha)")
    args = parser.parse_args()
    
    cache_dir = args.data_dir.parent.parent / "cache" / args.data_dir.name
    
    generate_test_data(
        gt_dir=args.ground_truth_dir,
        result_dir=args.result_dir,
        cache_dir=cache_dir,
    )
    
    print(f"\n{'='*60}")
    print(f"Test data generated in {args.result_dir}")
    print(f"Run evaluation with:")
    print(f"  cd src && uv run python evaluation/evaluate.py \\")
    print(f"      --ground-truth-dir {args.ground_truth_dir} \\")
    print(f"      --result-dir {args.result_dir} \\")
    print(f"      --data-dir {args.data_dir}")


if __name__ == "__main__":
    main()
