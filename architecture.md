# Roadmatch 架构参考

## 项目结构

```
src/
├── cli.py                   # CLI 入口（fetch-data 子命令）
│
├── mock/                    # 路网数据处理与模拟
│   ├── data_fetcher.py      #   从 OSM 拉取路网数据
│   ├── edge_splitter.py     #   边分裂：在交叉点处拆分边
│   ├── graph_simplifier.py  #   C-edge 图构建：平行边聚类、节点识别
│   ├── random_path_generator.py  #   随机路径生成：在 C-edge 图上生成满足约束的随机路径
│   ├── event_simulator.py   #   事件点检测模拟：模拟光缆检测的路口、拐弯、长度
│   ├── event_simulator_config.py  #   事件模拟器配置参数
│   └── graph_exporter.py    #   C-edge 图导出为 GeoJSON
│
├── roadgraphmodel/          # 路网数据模型
├── roadmatch/               # 检测数据匹配算法
├── evaluation/              # 评估与可视化
├── utils/                   # 通用工具（geometry, osm, types, errors）
│
└── old/                     # 旧代码归档（不修改）

tests/                       # 可视化与生成脚本（非单元测试）
├── plot_c_graph.py          #   C-edge 图可视化（含道路等级着色）
├── plot_edge_clusters.py    #   边聚类可视化
├── plot_random_path.py      #   随机路径可视化
├── plot_road_network.py     #   路网可视化
├── plot_split_edges.py      #   边分裂可视化
└── generate_paths.py        #   批量生成路径与检测结果

resource/<city>/             # 路网数据
├── nodes.geojson            #   C-edge 图节点（简化路网）
├── edges.geojson            #   C-edge 图边（简化路网）
├── c_edge_graph.html        #   C-edge 图可视化输出
└── raw/                     #   原始与中间数据
    ├── raw_nodes.geojson    #     原始 OSM 节点
    ├── raw_edges.geojson    #     原始 OSM 边
    ├── nodes.geojson        #     分割后的节点
    └── edges.geojson        #     分割后的边

outputs/<city>/              # 生成结果
└── path_N/                  #   第 N 条路径
    ├── path.html            #     路径可视化
    └── detection.json       #     检测结果

ground_truth/<city>/         # 标准答案（用于评估）
└── path_N/                  #   第 N 条路径（N = seed 值）
    ├── ground_truth.txt     #     点边序列（node_id edge_id，末行仅 node_id）
    ├── stats.json           #     路径统计信息
    └── path.html            #     路径可视化
```

## C-edge 图流水线

```
原始边 → split_edges_at_intersections → cluster_near_parallel_edges
     → build_c_edge_graph → filter_small_link_cedges
     → build_node_to_cedges_map → filter_spur_core_edges
     → identify_connection_nodes → cluster_connection_nodes
     → create_virtual_cnodes → merge_t_junction_cnodes
     → merge_intermediate_t_junctions
     → find_parallelograms_near_cnodes → cluster_parallelograms
     → merge_intersection_cnodes → split_c_edges_at_intersection_nodes
     → update_c_edge_endpoints → filter_dangling_cedges
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
- 数值精度：角度和坐标统一保留小数点后 7 位

## Ground Truth 数据格式

`ground_truth/<city>/path_<seed>/` 目录下包含标准答案文件：

### `ground_truth.txt`

点边交替序列，每行格式：
- 中间行：`<node_id> <edge_id>` — 节点 ID 和后续边的 ID
- 末行：`<node_id>` — 终点节点 ID

节点 ID 不含 `C-node_` 前缀，直接使用数字部分。

### `stats.json`

路径统计信息，包含：
- `total_length_m`：总长度（米）
- `turn_count`：拐弯次数
- `main_road_ratio`：大路占比
- `forward_ratio` / `lateral_ratio` / `backward_ratio`：方向分布
- `turns`：拐弯详情列表，每项包含：
  - `node_id`：拐弯节点 ID（不含前缀）
  - `angle`：拐弯角度（度，保留 7 位小数）
  - `position`：拐弯位置坐标 `[lon, lat]`（保留 7 位小数）

### 生成命令

```bash
uv run python tests/plot_random_path.py \
    --data-dir resource/changsha \
    --total-length 20000 \
    --num-turns 12 \
    --main-road-ratio 0.85 \
    --seed 1
```

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
| 12 | `merge_intermediate_t` | `Dict[int, Dict]` | 中间 T 型合并后的 C-node |
| 13 | `parallelograms` | `List[Dict]` | 平行四边形列表 |
| 14 | `crossroads` | `List[Dict]` | 十字路口聚类 |
| 15 | `merge_intersection` | `Dict[int, Dict]` | 交叉口合并后的 C-node |
| 16 | `split_c_edges` | `List[Dict]` | 交叉口拆分后的 C-edge |
| 17 | `update_endpoints` | `List[Dict]` | 更新端点后的 C-edge |
| 18 | `filter_dangling` | `List[Dict]` | 剔除断头后的 C-edge |

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
        ↓ [merge_t_junction_cnodes] (排除平行边)
virtual_cnodes (T 型合并)
        ↓ [merge_intermediate_t_junctions]
virtual_cnodes (中间 T 型合并)
        ↓ [find_parallelograms_near_cnodes]
parallelograms
        ↓ [cluster_parallelograms]
crossroads
        ↓ [merge_intersection_cnodes]
virtual_cnodes (交叉口合并)
        ↓ [split_c_edges_at_intersection_nodes]
c_edges (交叉口拆分)
        ↓ [update_c_edge_endpoints]
c_edges (端点更新)
        ↓ [filter_dangling_cedges]
c_edges (剔除断头)
```

## 随机路径生成

### 概述

`random_path_generator.py` 在 C-edge 图上生成满足多种约束的随机路径，用于模拟真实的路网行驶轨迹。

### 约束条件

| 约束 | 说明 | 容差 |
|------|------|------|
| 总长度 | 路径总长度（米） | ±20% |
| 拐弯次数 | 行进方向变化超过 60° 的次数 | ±20% |
| 大路占比 | highway_level ≤ 6 的边长度占比 | ±20% |
| 方向约束 | forward ≥60%, lateral ≤40%, backward ≤10% | ±20% |

方向定义（相对于随机生成的默认方向）：
- **forward**: 夹角 < 60°
- **lateral**: 夹角 60°~120°
- **backward**: 夹角 > 120°

### 算法流程

```
1. 选择起点（度数 ≥ 2 的随机节点）
2. 生成随机默认方向 default_dir ∈ [0°, 360°)
3. 随机游走（每步选边）：
   ├── 计算候选边权重：
   │   ├── 方向偏好（forward/lateral/backward 动态调整）
   │   ├── 拐弯控制（冷却机制 + 理想间距）
   │   ├── 回头惩罚（避免原路返回）
   │   └── 大路/小路偏好（根据当前比例动态调整）
   ├── 按权重随机选择下一条边
   ├── 更新统计量（长度、拐弯、方向分布）
   └── 检查终止条件（总长度 ∈ [0.8L, 1.2L]）
4. 验证所有约束
5. 不满足则重试（最多 max_retries 次）
```

### 拐弯均匀分布

为避免连续拐弯或长时间不拐弯，使用冷却机制：
- `ideal_gap = est_edges / num_turns`：理想拐弯间距
- `cooldown = ideal_gap * 0.5`：最小冷却步数
- 步数 < cooldown 时强制不拐弯
- 步数 > ideal_gap * 1.5 时偏向拐弯

### 使用方法

```bash
cd src && uv run python tests/plot_random_path.py \
    --data-dir ../resource/miniquad \
    --total-length 2000 \
    --num-turns 5 \
    --main-road-ratio 0.6 \
    --seed 42
```

参数：
- `--total-length`: 目标总长度（米）
- `--num-turns`: 目标拐弯次数
- `--main-road-ratio`: 大路长度占比（0~1）
- `--main-road-level`: 大路判定阈值（默认 6，即 secondary 及以上）
- `--turn-angle`: 拐弯判定角度（默认 60°）
- `--max-retries`: 最大重试次数（默认 100）
- `--seed`: 随机种子（可选，用于复现）

### 数据结构

#### `walkable_graph` — `Dict[str, Any]`

```python
{
    'nodes': {
        node_id: {
            'position': (lon, lat),
            'edges': [edge_idx, ...]
        }
    },
    'edges': {
        edge_idx: {
            'start_node': str,
            'end_node': str,
            'length_m': float,
            'direction_deg': float,
            'highway_level': int,
            'start_coord': (lon, lat),
            'end_coord': (lon, lat)
        }
    }
}
```

#### `path` — `List[Dict]`

点/边交替出现的连续路径：

```python
[
    {'type': 'node', 'id': str, 'position': (lon, lat)},
    {'type': 'edge', 'idx': int, 'length_m': float, 'direction_deg': float, 'highway_level': int},
    {'type': 'node', ...},
    ...
]
```

## graph_simplifier 变更

### 新增字段

`build_c_edge_graph` 和 `split_c_edges_at_intersection_nodes` 输出的 C-edge dict 新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `length_m` | float | 边长（米），haversine 计算 |
| `highway_level` | int | 道路等级（取聚类中最小值，越小等级越高） |

### 新增函数

#### `build_walkable_graph(c_edges, virtual_cnodes) -> Dict`

从 C-edge 图构建可行走的邻接表结构，用于随机路径生成。
- 过滤掉 `is_split=True` 的死边
- 从 `connected_vnodes` 构建节点邻接关系
- 补算缺失的 `length_m`

## utils/geometry 变更

### 新增函数

#### `angular_delta_mod360(a, b) -> float`

计算两个有向 bearing（[0°, 360°)）之间的最小角度差，返回值范围 [0°, 180°]。

用于随机路径生成中的行进方向比较（区别于 `angular_delta_mod180` 用于无向边方向比较）。

## 事件点检测模拟

### 概述

`event_simulator.py` 模拟光缆检测算法的输出，基于真实路径生成带有噪声的检测结果，包括：
- **路口检测**：识别度 ≥ 3 的节点，区分大路口（≥ 2 条大路）和小路口
- **拐弯检测**：检测路径在路口处的转向行为
- **长度检测**：模拟盘留（cable slack）导致的路径长度膨胀

### 检测误差模型

#### 盘留（Cable Slack）

光缆每隔一段距离需要做盘留，导致检测长度比实际更长：
- **常规盘留间隔**：截断指数分布，范围 [50m, 250m]，均值 150m
- **路口盘留**：度 ≥ 3 的节点有 40% 概率产生盘留（若距前一盘留 < 50m 则合并）
- **基础长度**：均匀分布 [10m, 20m]
- **超大盘留**：概率 = 平均间距 × 0.05 / 100，命中时长度翻倍

#### 路口检测

| 节点类型 | 召回率 |
|----------|--------|
| 大路口（≥ 2 条大路） | 80% |
| 小路口（< 2 条大路） | 30% |
| 度 = 2（误检） | 10% |

#### 路口范围

路口宽度 = 基础宽度 + 经过边的宽度之和：
- **基础宽度**：均匀分布 [15m, 50m]
- **边宽度**：每条相邻边有 50% 概率被经过
  - motorway: [30m, 70m]
  - trunk: [20m, 60m]
  - primary: [15m, 40m]
  - secondary: [10m, 30m]
  - tertiary: [5m, 25m]
  - 其他: [5m, 15m]

#### 拐弯检测

| 置信度 | 占比 | 直行精准率 | 拐弯精准率 |
|--------|------|------------|------------|
| medium | 20% | 80% | 60% |
| medium_high | 60% | 90% | 75% |
| high | 20% | 97% | 90% |

### 输出格式

```json
{
  "path_length(meter)": 33362,
  "turning": {
    "1": {
      "confidence_of_turning_types": "medium",
      "turning_directions": ["straight"]
    },
    "2": {
      "confidence_of_turning_types": "medium_high",
      "turning_directions": ["left_or_right"]
    }
  },
  "intersec_after_redund(meter)_l_m_s": [[
    {"id": 1, "start_m": 150, "end_m": 492, "center_m": 321},
    {"id": 2, "start_m": 770, "end_m": 972, "center_m": 871}
  ]]
}
```

### 使用方法

```bash
uv run python tests/generate_paths.py \
    --data-dir resource/changsha \
    --city changsha \
    --max-retries 300
```

参数：
- `--data-dir`: 路网数据目录
- `--city`: 城市名称（用于输出子目录）
- `--output-dir`: 输出根目录（默认 `outputs`）
- `--main-road-level`: 大路判定阈值（默认 6）
- `--turn-angle`: 拐弯判定角度（默认 60°）
- `--max-retries`: 最大重试次数（默认 100）

## C-edge 图导出

### 概述

`graph_exporter.py` 将 walkable graph 导出为标准 GeoJSON 格式，便于可视化和外部工具使用。

### 输出文件

#### `edges.geojson`

```json
{
  "type": "FeatureCollection",
  "name": "edges",
  "features": [{
    "type": "Feature",
    "properties": {
      "id": "0",
      "highway": "secondary",
      "highway_level": 6,
      "line_length": 98.7214839,
      "direction_deg": 179.15,
      "start_node": "587",
      "end_node": "0"
    },
    "geometry": {
      "type": "LineString",
      "coordinates": [[112.9754535, 28.1985242], [112.9754458, 28.1976364]]
    }
  }]
}
```

#### `nodes.geojson`

```json
{
  "type": "FeatureCollection",
  "name": "nodes",
  "features": [{
    "type": "Feature",
    "properties": {
      "id": "587",
      "degree": 3,
      "connected_edges": ["0", "2836", "8048"]
    },
    "geometry": {
      "type": "Point",
      "coordinates": [112.9754535, 28.1985242]
    }
  }]
}
```

### 命名约定

C-edge 图是路网简化的中间产物，构建过程中使用 `C-edge`（聚类边）和 `C-node`（聚类节点）的术语以区分原始 OSM 数据。但一旦完成构建并导出为 GeoJSON，简化路网就是最终的路网表示，不再使用 C-edge/C-node 的命名：

- **构建阶段**（`graph_simplifier.py` 内部）：使用 `C-edge`、`C-node`、`C-node_587` 等术语
- **导出后**（`nodes.geojson`、`edges.geojson`、`endpoints.json`）：仅使用 `node`、`edge`、`587` 等通用术语
- **节点 ID 格式**：去除 `C-node_` 前缀，仅保留数字（如 `C-node_587` → `587`）

这一约定适用于所有输出文件，包括检测结果（`detection.json`）和端点信息（`endpoints.json`）。

### 数据规范

- **节点 ID**：去除 `C-node_` 前缀，仅保留数字
- **坐标精度**：最多 7 位小数（约 1cm 精度）
- **长度精度**：最多 7 位小数
- **坐标顺序**：经度在前，纬度在后（WGS84）

### 自动导出

`generate_paths.py` 在构建 walkable graph 后自动调用导出函数，输出到 `resource/<city>/` 目录。
