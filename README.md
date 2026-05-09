# Roadmatch

Roadmatch 是一个 Python 项目，用来从有噪声的光缆检测信息中，还原光缆最可能沿着真实路网经过的路径。

默认 demo 使用 OpenStreetMap 路网，借助 OSMnx 下载上海道路拓扑，然后模拟“光缆长度偏长、路口漏检、直行/拐弯误判”的检测数据，并输出 Top-K 候选路径、置信度、GeoJSON 和 HTML 地图。上海 demo 默认生成 20 条候选并输出 Top-5；需要更充分搜索时可以调大 `matching.generated_paths` 和 `matching.top_k`。

## 目录结构

```text
Roadmatch/
  configs/
    demo_shanghai.yaml        # 默认上海 demo 配置，换路主要改这里
  data/
    shanghai/
      graph.graphml           # OSMnx 原始拓扑缓存
      road_graph.json         # Roadmatch 内部轻量路网缓存
      nodes.geojson           # 路网节点，用于 GIS 检查
      edges.geojson           # 路网边，用于 GIS 检查
  outputs/
    demo/
      detections.json         # 模拟出来的检测输入
      match_report.json       # 匹配报告，Top-K、置信度、对齐解释
      candidates.geojson      # 候选路径 GeoJSON
      map.html                # 可视化地图
  user_data/
    README.md                 # 你的原始路网/监测点/起终点数据放置说明
    raw_road_network/         # 放生产侧原始路网数据
    raw_monitoring/           # 放生产侧原始监测点数据
    start_end/                # 放生产侧起点终点数据
    converted/                # 放转换后的 Roadmatch 标准输入
  src/roadmatch/
    data.py                   # 下载/加载路网
    simulator.py              # 虚构检测数据
    matcher.py                # Top-K 匹配入口
    scoring.py                # 长度评分和路口动态规划对齐
    graph.py                  # 轻量无向路网、最短路、候选路径
    visualize.py              # GeoJSON/HTML 输出
    cli.py                    # roadmatch 命令行
  tests/                      # 离线测试
  run_demo.py                 # 带 main() 的本地调试入口
  environment.yml             # conda 环境
  pyproject.toml              # Python 包配置
```

## 环境安装

项目要求 Python 3.11+。推荐使用 Python 3.12。

### Conda 环境

推荐用 conda-forge 安装地理空间依赖，最稳：

```bash
conda env create -f environment.yml
conda activate roadmatch
pip install --no-deps -e .
```

验证环境：

```bash
python -m pytest
roadmatch --help
```

### venv 环境

如果不用 conda，也可以用 venv：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

验证：

```bash
python -m pytest
roadmatch --help
```

## 快速运行

推荐直接跑带 `main()` 的入口文件：

```bash
python run_demo.py
```

如果已经有 `data/shanghai/road_graph.json` 或 `data/shanghai/graph.graphml`，不希望触发 OSM 下载：

```bash
python run_demo.py --skip-fetch
```

指定随机种子：

```bash
python run_demo.py --seed 7
```

也可以用 CLI 跑同样流程：

```bash
roadmatch run-demo --config configs/demo_shanghai.yaml --seed 42
```

完整流程会做 4 件事：

1. 读取或下载上海 bbox 内的 OSM 路网，生成 `data/shanghai/graph.graphml` 和 `data/shanghai/road_graph.json`。
2. 从配置里的起点和多个终点候选中，选择一条接近 20km 的最短路作为 demo 的真实光缆路径。
3. 按噪声参数虚构检测数据，写入 `outputs/demo/detections.json`。
4. 执行路网匹配，输出 `match_report.json`、`candidates.geojson`、`map.html`。

## 测试命令

跑单元测试和离线集成测试：

```bash
python -m pytest
```

当前测试不依赖网络，覆盖几何计算、转向分类、长度评分、动态规划对齐、小型手工路网 Top-1 匹配。

代码检查：

```bash
ruff check .
```

小规模评估：

```bash
roadmatch evaluate --config configs/demo_shanghai.yaml --seeds 1
```

完整评估可以调大 seed 数：

```bash
roadmatch evaluate --config configs/demo_shanghai.yaml --seeds 50
```

上海真实路网较大，评估 50 个 seed 会比较耗时。

## 输出怎么看

### `outputs/demo/map.html`

浏览器打开即可看候选路径。HTML 有两种渲染方式：

- 有网络时，加载 Leaflet 和 OpenStreetMap 底图。
- Leaflet CDN 加载失败时，自动使用内嵌 SVG 画候选路径，仍能看到线路。

颜色含义：

- 蓝色：Top-1 候选路径。
- 橙色/绿色等：其他候选路径。
- 紫色点：匹配到的检测路口事件。

### `outputs/demo/match_report.json`

重点看 `candidates`：

```json
{
  "rank": 1,
  "path_id": "path_001",
  "confidence": 0.2001,
  "score": 0.9457,
  "length_m": 21421.7,
  "expected_observed_length_m": 24636.0,
  "length_delta_m": -93.7,
  "length_score": 0.9999,
  "event_score": 0.9014,
  "matched_event_count": 40,
  "event_alignment": []
}
```

字段说明：

- `rank`：候选排名。
- `confidence`：Top-K 内归一化置信度。多条候选非常接近时，每条置信度会接近平均值，这是正常现象。
- `score`：综合评分，越高越好。
- `length_m`：候选道路路径长度。
- `expected_observed_length_m`：按光缆偏长系数换算后的预期检测长度。
- `length_delta_m`：观测长度与预期长度的差。
- `event_alignment`：每个检测路口和候选路口的匹配解释。

### `outputs/demo/candidates.geojson`

给 GIS 工具或前端系统用。每个 Feature 是一条候选路径，属性里带 `rank`、`confidence`、`score`、`length_m`。

### `outputs/demo/detections.json`

这是模拟出来的检测输入。以后其他部门给真实检测数据时，可以按这个格式替换。

## 输入检测格式

`detections.json` 的核心结构如下：

```json
{
  "start": {
    "name": "Shanghai Hongqiao Railway Station",
    "lon": 121.3188,
    "lat": 31.1947
  },
  "end": {
    "name": "Lujiazui",
    "lon": 121.507,
    "lat": 31.2397
  },
  "observed_length_m": 23000.0,
  "events": [
    {
      "interval_m": [4200.0, 4360.0],
      "movement": "straight",
      "interchange": false
    }
  ]
}
```

字段说明：

- `start` / `end`：光缆起终点，经纬度为 WGS84，字段顺序是 `lon`、`lat`。
- `observed_length_m`：估计出来的光缆长度，单位米。
- `events[].interval_m`：检测到某个路口事件所在的光缆累计长度区间，单位米。
- `events[].movement`：`straight`、`turn` 或 `unknown`。
- `events[].interchange`：`true`、`false` 或 `unknown`。

注意：`interval_m` 是按光缆观测长度计，不是道路路径长度。当前默认认为光缆长度比道路路径长约 15%，匹配时会使用 `noise.length_multiplier_mean` 做换算。

## CLI 命令

分步执行：

```bash
roadmatch fetch-data --config configs/demo_shanghai.yaml
roadmatch simulate --config configs/demo_shanghai.yaml --seed 42
roadmatch match --config configs/demo_shanghai.yaml --detections outputs/demo/detections.json
```

一键 demo：

```bash
roadmatch run-demo --config configs/demo_shanghai.yaml --seed 42
```

评估：

```bash
roadmatch evaluate --config configs/demo_shanghai.yaml --seeds 50
```

## 技术实现路径

### 1. 路网数据

入口：`src/roadmatch/data.py`

- 使用 OSMnx 从 OpenStreetMap 下载 `drive` 路网。
- 保存 OSMnx 原始图到 `graph.graphml`。
- 转换成 Roadmatch 内部轻量无向图，保存到 `road_graph.json`。
- 同时导出 `nodes.geojson` 和 `edges.geojson` 方便人工检查。

为什么转成无向图：光缆沿道路敷设，不受机动车单行方向约束，因此匹配时使用无向路网。

立交/桥梁识别：读取 OSM 属性 `bridge`、`tunnel`、`layer`、`junction`、`highway`，在 `src/roadmatch/graph.py` 中推断是否疑似立交/桥梁/隧道。

### 2. demo 检测数据模拟

入口：`src/roadmatch/simulator.py`

模拟规则来自需求：

- 真实路径：起点到终点候选的最短路，优先选择接近 20km 的终点。
- 光缆长度：道路长度乘以约 `1.15`，并加入少量随机扰动。
- 路口检出率：默认只保留约 `30%` 的真实路口事件。
- 直行判断：默认准确率 `90%`。
- 拐弯判断：默认准确率 `40%`。
- 每个检测事件输出一个长度区间 `interval_m`。

这些参数都在 `configs/demo_shanghai.yaml` 的 `noise` 段里。

### 3. 候选路径生成

入口：`src/roadmatch/matcher.py` 和 `src/roadmatch/graph.py`

流程：

1. 起点终点吸附到最近路网节点。
2. 根据 `observed_length_m / length_multiplier` 估计道路路径长度。
3. 用“长度走廊”裁剪路网，只保留可能构成合理长度路径的节点和边。
4. 在裁剪后的子图中生成 K-shortest simple paths。
5. 对候选路径按长度窗口过滤。

关键配置：

- `matching.generated_paths`：生成多少条候选路径。
- `matching.top_k`：最终输出多少条。
- `matching.length_window_ratio`：长度窗口容忍比例。

如果你发现运行太慢，先降低 `generated_paths`。如果你想搜索更充分，调大 `generated_paths` 和 `top_k`。

### 4. 路口事件提取

入口：`src/roadmatch/graph.py`

每条候选路径会被转换成事件序列：

- 当前路口累计道路距离。
- 进入边和离开边的 bearing。
- 根据夹角判断 `straight` 或 `turn`。
- 根据 OSM 属性判断是否疑似立交/桥梁/隧道。

转向阈值在配置里：

```yaml
matching:
  turn_threshold_degrees: 35
```

### 5. 评分和动态规划对齐

入口：`src/roadmatch/scoring.py`

评分由两部分组成：

- 长度评分：候选道路长度乘以光缆偏长系数后，与 `observed_length_m` 比较。
- 事件评分：用动态规划把检测事件和候选路口事件做单调对齐。

动态规划允许：

- 候选路径中存在很多未检出的路口。
- 检测区间有位置误差。
- 直行/拐弯判断有误差。
- 立交标记缺失或不确定。

权重在配置里：

```yaml
matching:
  weights:
    length: 0.45
    events: 0.55
    event_position: 0.50
    event_movement: 0.35
    event_interchange: 0.15
```

### 6. 可视化

入口：`src/roadmatch/visualize.py`

输出：

- `candidates.geojson`：标准 GeoJSON。
- `map.html`：嵌入候选路径和检测点。

`map.html` 会优先使用 Leaflet + OSM 底图；如果本地无法访问 CDN，会自动 fallback 到 SVG 静态线路图。

## 如果需要换路线

最常见场景是：仍然使用 OSM 数据，只是换一个城市、区域、起点终点。

### 方式一：直接改 `configs/demo_shanghai.yaml`

你需要改 4 个地方。

1. 改输出目录，避免覆盖旧结果：

```yaml
paths:
  data_dir: data/my_area
  output_dir: outputs/my_area
```

2. 改 OSM 下载范围：

```yaml
osm:
  bbox: [west, south, east, north]
```

注意顺序是 `[左, 下, 右, 上]`，也就是 `[west, south, east, north]`。起点、终点、候选路径都应该落在这个 bbox 里。bbox 不要太大，否则下载和候选搜索都会变慢。

3. 改起点：

```yaml
demo:
  start:
    name: My Start
    lon: 121.3188
    lat: 31.1947
```

4. 改终点候选：

```yaml
demo:
  end_candidates:
    - name: My End A
      lon: 121.5070
      lat: 31.2397
    - name: My End B
      lon: 121.4750
      lat: 31.2304
```

demo 会从这些终点候选里，选择路径长度最接近 `target_length_m` 的一个。

5. 改目标长度：

```yaml
demo:
  target_length_m: 20000
  target_min_m: 18000
  target_max_m: 24000
```

然后运行：

```bash
python run_demo.py --config configs/demo_shanghai.yaml
```

### 方式二：复制一份新配置

更推荐复制一份，避免破坏上海 demo：

```bash
cp configs/demo_shanghai.yaml configs/demo_my_area.yaml
```

然后修改：

- `paths.data_dir`
- `paths.output_dir`
- `osm.bbox`
- `demo.start`
- `demo.end_candidates`
- `demo.target_length_m`

运行：

```bash
python run_demo.py --config configs/demo_my_area.yaml
```

或者：

```bash
roadmatch run-demo --config configs/demo_my_area.yaml --seed 42
```

## 如果需要添加真实检测数据

真实检测数据不需要放在固定目录，但建议放到对应输出目录下，例如：

```text
outputs/my_area/real_detections.json
```

格式必须和 `outputs/demo/detections.json` 一致，至少包含：

- `start`
- `end`
- `observed_length_m`
- `events`

然后运行匹配：

```bash
roadmatch match \
  --config configs/demo_my_area.yaml \
  --detections outputs/my_area/real_detections.json
```

输出会写到配置里的 `paths.output_dir`：

- `match_report.json`
- `candidates.geojson`
- `map.html`

如果真实数据的起点终点和原 demo 不一致，也要同步修改配置里的 `osm.bbox`，确保路网覆盖真实起终点和中间可能经过的区域。

## 如果需要添加自己的路网数据

当前主流程推荐用 OSMnx 自动下载 OSM 路网。如果要接入外部道路数据，需要满足一个原则：必须能恢复拓扑。

只给普通道路 LineString GeoJSON 通常不够，因为它可能无法表达：

- 哪些线段真的连通。
- 哪些线段只是立交上下穿过但不连通。
- 边的长度和方向。
- 桥梁、隧道、layer 等属性。

当前代码读取路网的优先级是：

1. `paths.road_graph_json` 指向的 `road_graph.json`
2. `paths.graphml` 指向的 OSMnx `graph.graphml`

如果你已经有外部路网，最稳的做法是转换成 `road_graph.json`，放到配置指定的位置：

```text
data/my_area/road_graph.json
```

最小结构示例：

```json
{
  "nodes": [
    {
      "node_id": "a",
      "lon": 121.0,
      "lat": 31.0,
      "attrs": {}
    }
  ],
  "edges": [
    {
      "edge_id": "a-b",
      "u": "a",
      "v": "b",
      "length_m": 120.5,
      "coords": [[121.0, 31.0], [121.001, 31.001]],
      "attrs": {
        "highway": "primary",
        "bridge": "yes",
        "layer": "1"
      }
    }
  ]
}
```

然后在配置里指向它：

```yaml
paths:
  data_dir: data/my_area
  road_graph_json: road_graph.json
```

再运行：

```bash
roadmatch match --config configs/demo_my_area.yaml --detections outputs/my_area/real_detections.json
```

## 常见问题

### 1. `map.html` 只有左上角信息，看不到路径

重新生成最新地图：

```bash
python run_demo.py --skip-fetch
```

新版 `map.html` 已有 SVG fallback。如果浏览器缓存旧文件，强制刷新页面。

### 2. 第一次运行很慢

第一次会下载 OSM 路网并生成缓存。后续有 `data/.../road_graph.json` 后会直接读缓存。

如果匹配仍然慢，可以降低：

```yaml
matching:
  generated_paths: 10
  top_k: 3
```

### 3. 换城市后下载失败

检查：

- 网络是否能访问 OpenStreetMap Overpass API。
- `osm.bbox` 是否过大。
- 起点终点是否在 bbox 内。

可以先把 bbox 缩小，只覆盖起点终点之间的区域。

### 4. Top-K 置信度都差不多

这是正常的。真实路网中可能存在多条几乎等长、检测事件也相似的候选路径。此时要重点看：

- `score`
- `length_delta_m`
- `event_alignment`
- `candidates.geojson` 在地图上的空间差异

## 数据源说明

- OSMnx: https://pypi.org/project/osmnx/
- OSMnx 文档: https://osmnx.readthedocs.io/en/stable/getting-started.html
- Geofabrik/OpenStreetMap 数据: https://www.geofabrik.de/data/
- Overture Maps 交通数据: https://docs.overturemaps.org/guides/transportation/
