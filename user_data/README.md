# 用户原始数据放置说明

这个目录用于放置你自己的路网数据、监测点/检测事件数据，以及起点终点位置数据。

这里的数据可以保持你的原始格式，不要求立刻符合 Roadmatch 当前代码的输入格式。后续把代码转入生产环境后，可以由生产环境里的 agent 或数据转换脚本，把这些原始数据转换成 Roadmatch 标准格式。

## 推荐目录

```text
user_data/
  raw_road_network/       # 放你的原始路网数据
  raw_monitoring/         # 放你的原始监测点、检测区间、路口判断等数据
  start_end/              # 放起点终点位置数据
  converted/              # 生产环境 agent 转换后的标准输入，建议输出到这里
  README.md               # 本说明
```

## 1. 原始路网数据放哪里

请放到：

```text
user_data/raw_road_network/
```

可以放任意生产侧已有格式，例如：

- GeoJSON
- Shapefile
- CSV
- GeoPackage
- 数据库导出的 JSON/CSV
- 其他内部格式

如果可能，建议文件名里带上区域和日期，例如：

```text
user_data/raw_road_network/shanghai_pudong_roads_2026-05-08.geojson
```

后续生产环境 agent 需要把它转换成 Roadmatch 当前支持的内部路网格式：

```text
user_data/converted/road_graph.json
```

标准 `road_graph.json` 的最小结构如下：

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

转换重点：

- 必须保留可恢复拓扑的节点和边。
- 立交、桥梁、隧道、上下穿越关系尽量用 `attrs.bridge`、`attrs.tunnel`、`attrs.layer`、`attrs.junction` 等字段保留。
- 普通 LineString 如果没有拓扑关系，不能直接等价于可匹配路网。

## 2. 原始监测点/检测数据放哪里

请放到：

```text
user_data/raw_monitoring/
```

可以是你的原始格式，例如：

- 每个路口的检测记录
- 光缆累计长度区间
- 直行/拐弯判断
- 是否经过立交桥/桥梁/隧道
- 检测置信度
- 内部系统导出的表格或 JSON

建议文件名：

```text
user_data/raw_monitoring/cable_detection_case_001.json
user_data/raw_monitoring/cable_detection_case_001.csv
```

后续生产环境 agent 需要转换成 Roadmatch 标准检测输入：

```text
user_data/converted/detections.json
```

标准 `detections.json` 的最小结构如下：

```json
{
  "start": {
    "name": "start",
    "lon": 121.3188,
    "lat": 31.1947
  },
  "end": {
    "name": "end",
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

字段转换建议：

- `interval_m`：检测事件在光缆累计长度上的范围，单位米。
- `movement`：转换成 `straight`、`turn` 或 `unknown`。
- `interchange`：转换成 `true`、`false` 或 `unknown`。
- 如果原始数据只有点位没有区间，可以按业务误差给一个区间，例如 `[distance - 50, distance + 50]`。

## 3. 起点终点位置放哪里

请放到：

```text
user_data/start_end/
```

可以放原始格式，也可以直接放标准 JSON，例如：

```json
{
  "start": {
    "name": "A端",
    "lon": 121.3188,
    "lat": 31.1947
  },
  "end": {
    "name": "Z端",
    "lon": 121.507,
    "lat": 31.2397
  }
}
```

注意：

- 坐标建议统一为 WGS84。
- 字段顺序使用 `lon`、`lat`，不要写反。
- 如果生产数据是 GCJ-02、BD-09 或本地投影坐标，生产环境 agent 需要先转换坐标系。

## 4. 转换后怎么运行

生产环境 agent 转换完成后，建议得到：

```text
user_data/converted/road_graph.json
user_data/converted/detections.json
```

然后新建或复制一份配置，例如：

```text
configs/user_case.yaml
```

关键配置示例：

```yaml
project:
  name: user_case

paths:
  data_dir: user_data/converted
  output_dir: outputs/user_case
  road_graph_json: road_graph.json
  graphml: graph.graphml
  nodes_geojson: nodes.geojson
  edges_geojson: edges.geojson
```

运行匹配：

```bash
roadmatch match \
  --config configs/user_case.yaml \
  --detections user_data/converted/detections.json
```

结果会输出到：

```text
outputs/user_case/
  match_report.json
  candidates.geojson
  map.html
```

## 5. 给生产环境 agent 的转换任务说明

生产环境 agent 接手时，目标不是修改 Roadmatch 算法，而是做数据适配：

1. 读取 `user_data/raw_road_network/` 中的原始路网。
2. 构建带拓扑的节点/边结构。
3. 输出 `user_data/converted/road_graph.json`。
4. 读取 `user_data/raw_monitoring/` 和 `user_data/start_end/`。
5. 统一坐标系、字段名、单位。
6. 输出 `user_data/converted/detections.json`。
7. 复制或生成 `configs/user_case.yaml`。
8. 调用 `roadmatch match` 生成结果。

转换时优先保证：

- 起点终点能吸附到路网附近。
- 路网拓扑正确，尤其是立交上下穿越不要误连通。
- 所有长度单位统一为米。
- 检测事件按光缆累计长度单调递增。
- `movement` 和 `interchange` 不确定时使用 `unknown`，不要强行猜成确定值。
