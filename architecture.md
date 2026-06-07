# Roadmatch 架构参考

## 项目结构

```
src/
├── cli.py                   # CLI 入口（fetch-data 子命令）
│
├── mock/                    # 路网数据处理与简化
│   ├── data_fetcher.py      #   从 OSM 拉取路网数据
│   ├── edge_splitter.py     #   边分裂：在交叉点处拆分边
│   └── graph_simplifier.py  #   C-edge 图构建：平行边聚类、节点识别
│
├── roadgraphmodel/          # 路网数据模型
├── roadmatch/               # 检测数据匹配算法
├── evaluation/              # 评估与可视化
├── utils/                   # 通用工具（geometry, osm, types, errors）
│
└── old/                     # 旧代码归档（不修改）

tests/                       # 可视化脚本（非单元测试）
├── plot_c_graph.py          #   C-edge 图可视化
├── plot_edge_clusters.py    #   边聚类可视化
├── plot_road_network.py     #   路网可视化
└── plot_split_edges.py      #   边分裂可视化

resource/<city>/             # 缓存的路网数据
├── nodes.geojson            #   节点
├── edges.geojson            #   边
├── raw/                     #   原始数据
└── c_edge_graph.html        #   C-edge 图输出
```

## C-edge 图流水线

```
原始边 → split_edges_at_intersections → cluster_near_parallel_edges
     → build_c_edge_graph → filter_small_link_cedges
     → build_node_to_cedges_map → filter_spur_core_edges
     → identify_connection_nodes → cluster_connection_nodes
     → create_virtual_cnodes → merge_t_junction_cnodes
     → find_parallelograms_near_cnodes → cluster_parallelograms
     → merge_intersection_cnodes → update_c_edge_endpoints
     → split_c_edges_at_intersection_nodes
```

运行：
```bash
uv run python tests/plot_c_graph.py --data-dir resource/miniquad --near-threshold 50 --parallel-angle-threshold 30
```

## 模块依赖关系

```
utils              ──→  (无内部依赖)
mock               ──→  utils
roadgraphmodel     ──→  utils
roadmatch          ──→  roadgraphmodel, utils
evaluation         ──→  roadgraphmodel, utils
cli.py             ──→  mock, utils
```

## 关键约定

- 各模块文件夹内的 `__init__.py` 只做空文件或简单重导出，不写业务逻辑
- 旧代码 `src/old/` 不做修改，重构时按需重新实现
- 模型文件以 `_model` 后缀命名（如 `event_model.py`），逻辑文件以功能命名
- 坐标系统：WGS84，`lon, lat` 顺序

## Pipeline 缓存数据结构

缓存目录：`cache/{dataset_name}/`，使用 pickle 序列化，支持 `--cache-dir` 和 `--resume-from` 参数。

### 总览

| # | 缓存名 | 数据结构 | 说明 |
|---|--------|----------|------|
| 1 | `crop` | `(edge_features, node_features)` | 裁剪后的 GeoJSON Feature 列表 |
| 2 | `split` | `(edge_features, node_features)` | 交叉口拆分后的 GeoJSON Feature 列表 |
| 3 | `cluster` | `(edge_clusters, core_edges)` | 平行边聚类结果 |
| 4 | `c_edges` | `List[Dict]` | C-edge 列表 |
| 5 | `filter_link` | `List[Dict]` | 剔除小型纯 link C-edge 后的列表 |
| 6 | `node_to_cedges` | `Dict[str, set]` | node_id → C-edge 索引集合 |
| 7 | `filter_spur` | `(core_edges, c_edges)` | 过滤毛刺后的核心边和更新后的 C-edge |
| 8 | `connection_nodes` | `Dict[str, set]` | 连接节点 (属于 2+ C-edge 的节点) |
| 9 | `cluster_connection` | `List[List[str]]` | 连接节点聚类 |
| 10 | `virtual_cnodes` | `Dict[int, Dict]` | 虚拟 C-node |
| 11 | `merge_t_junction` | `Dict[int, Dict]` | T 型合并后的 C-node |
| 12 | `parallelograms` | `List[Dict]` | 平行四边形列表 |
| 13 | `crossroads` | `List[Dict]` | 十字路口聚类 |
| 14 | `merge_intersection` | `Dict[int, Dict]` | 交叉口合并后的 C-node |
| 15 | `update_endpoints` | `List[Dict]` | 更新端点后的 C-edge |
| 16 | `split_c_edges` | `List[Dict]` | 最终拆分后的 C-edge |

### 详细结构

#### `edge_features` — `List[Dict]`

```python
{
    "type": "Feature",
    "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
    "properties": {
        "edge_id": str,           # 格式 "{original_id}_{segment_idx}"
        "u": str,                 # 起点 node_id，格式 "{lon:.7f}_{lat:.7f}"
        "v": str,                 # 终点 node_id
        "length": float,          # haversine 长度 (米)
        "direction_deg": float,   # 方向角 (mod 180)
        "highway": str | list     # OSM highway 标签
    }
}
```

#### `node_features` — `List[Dict]`

```python
{
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [lon, lat]},
    "properties": {"node_id": str}  # 格式 "{lon:.7f}_{lat:.7f}"
}
```

#### `edge_clusters` — `List[List[int]]`

```python
[[0, 5, 12, 33], [1], [2, 7], ...]  # 每个聚类是一组 edge_features 索引
```

#### `core_edges` — `Dict[int, set]`

```python
{0: {0, 5, 12}, 1: {1}, ...}  # cluster_idx → 核心边索引集合
```

#### `c_edges` — `List[Dict]`

```python
{
    'idx': int,                    # C-edge 索引
    'parent_idx': int,             # 父 C-edge 索引
    'start_coord': (lon, lat),     # 起点坐标
    'end_coord': (lon, lat),       # 终点坐标
    'direction_deg': float,        # 主方向 (度)
    'start_node_id': str | None,   # 起点 node_id
    'end_node_id': str | None,     # 终点 node_id
    'size': int,                   # 聚类中的边数量
    # update_endpoints 后新增:
    'connected_vnodes': List[int], # 连接的 C-node 索引列表
    # split 后新增:
    'is_split': bool,              # 是否被拆分
    'split_pieces': List[int],     # 拆分后的 C-edge 索引列表
    'split_idx': int               # 在拆分序列中的位置
}
```

#### `node_to_cedges` — `Dict[str, set]`

```python
{node_id: {ce_idx_1, ce_idx_2, ...}}  # str → set of int
```

#### `connection_nodes` — `Dict[str, set]`

```python
{node_id: {ce_idx_1, ce_idx_2, ...}}  # 仅包含属于 2+ C-edge 的节点
```

#### `cluster_connection` — `List[List[str]]`

```python
[
    ["120.1234567_30.1234567", "120.1234568_30.1234568"],  # 聚类 0
    ["120.2345678_30.2345678"],                            # 聚类 1
    ...
]
```

#### `virtual_cnodes` — `Dict[int, Dict]`

```python
{
    cluster_id: {
        'id': str,                              # "C-node_{id}"
        'position': (lon, lat),                 # 几何交点位置
        'connected_cedges': set[int],           # 连接的 C-edge 索引
        'original_nodes': list[str],            # 原始 node_id 列表
        'c_edge_end_associations': set[tuple]   # {(ce_idx, 'start'|'end'), ...}
    }
}
```

#### `parallelograms` — `List[Dict]`

```python
{
    'vertices': ((lon,lat), (lon,lat), (lon,lat), (lon,lat)),  # 4 个顶点
    'edges': (idx_a1, idx_a2, idx_b1, idx_b2),                # 4 条边索引
    'edge_lengths': (float, float, float, float)               # 4 条边长度 (米)
}
```

#### `crossroads` — `List[Dict]`

```python
{
    'center': (lon, lat),                    # 聚类中心 (平行四边形中心平均值)
    'parallelograms': List[Dict]             # 该聚类包含的平行四边形列表
}
```

### 数据流图

```
edge_features, node_features (GeoJSON)
        ↓ [split_edges_at_intersections]
edge_features, node_features (拆分后)
        ↓ [cluster_near_parallel_edges]
edge_clusters, core_edges
        ↓ [build_c_edge_graph]
c_edges
        ↓ [filter_small_link_cedges]
c_edges (剔除小型 link)
        ↓ [build_node_to_cedges_map]
node_to_cedges
        ↓ [filter_spur_core_edges + recompute_c_edge_geometry]
core_edges, c_edges (更新)
        ↓ [identify_connection_nodes]
connection_nodes
        ↓ [cluster_connection_nodes]
clusters
        ↓ [create_virtual_cnodes]
virtual_cnodes
        ↓ [merge_t_junction_cnodes]
virtual_cnodes (T 型合并)
        ↓ [find_parallelograms_near_cnodes]
parallelograms
        ↓ [cluster_parallelograms]
crossroads
        ↓ [merge_intersection_cnodes]
virtual_cnodes (交叉口合并)
        ↓ [update_c_edge_endpoints]
c_edges (端点更新)
        ↓ [split_c_edges_at_intersection_nodes]
c_edges (最终拆分)
```
