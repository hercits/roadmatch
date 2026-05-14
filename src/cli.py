from __future__ import annotations

import argparse
from pathlib import Path

from mock.data_fetcher import fetch_city_road_network
from utils.errors import OSMFetchError


def _resolve_resource_root() -> Path:
    """返回与 src 目录同级的 resource 目录路径。"""
    src_dir = Path(__file__).resolve().parent.parent
    return src_dir.parent / "resource"


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置完成的 ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        description="Roadmatch — 从噪声光纤检测中恢复道路网络路线。"
    )
    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    # fetch-data 子命令
    fetch_parser = subparsers.add_parser("fetch-data", help="从 OSM 拉取指定城市的路网数据")
    fetch_parser.add_argument(
        "--city",
        type=str,
        default="default",
        help="城市名称，用作 resource 中的子目录名（默认: default）",
    )
    fetch_parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("LEFT", "BOTTOM", "RIGHT", "TOP"),
        help="边界框坐标: left bottom right top (即 west south east north)",
    )
    fetch_parser.add_argument(
        "--network-type",
        type=str,
        default="drive",
        help="路网类型（默认: drive）",
    )
    fetch_parser.add_argument(
        "--no-simplify",
        action="store_false",
        dest="simplify",
        help="不简化路网几何",
    )
    fetch_parser.add_argument(
        "--retain-all",
        action="store_true",
        help="保留所有连通分量",
    )

    return parser


def main() -> None:
    """Roadmatch CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch-data":
        resource_root = _resolve_resource_root()
        try:
            city_dir = fetch_city_road_network(
                city_name=args.city,
                bbox=args.bbox,
                resource_root=resource_root,
                network_type=args.network_type,
                simplify=args.simplify,
                retain_all=args.retain_all,
            )
            nodes_file = city_dir / "nodes.geojson"
            edges_file = city_dir / "edges.geojson"
            print(f"路网数据已保存至: {city_dir}")
            print(f"  节点文件: {nodes_file} ({_human_size(nodes_file)})")
            print(f"  边文件:   {edges_file} ({_human_size(edges_file)})")
        except OSMFetchError as exc:
            print(f"错误: {exc}")
            raise SystemExit(1) from exc
    else:
        parser.print_help()


def _human_size(path: Path) -> str:
    """返回文件的可读大小字符串。"""
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


if __name__ == "__main__":
    main()
