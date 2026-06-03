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
     → build_c_edge_graph → find_crossroad_nodes → compute_crossroad_positions
     → cluster_crossroad_nodes → connect_shared_nodes → align_parallel_c_edges
     → update_c_edges_for_crossroads
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
