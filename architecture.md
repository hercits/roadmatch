# Roadmatch 架构参考

## 项目结构

```
src/
├── cli.py                   # CLI 入口
├── __init__.py              # 包声明
├── __main__.py              # python -m roadmatch 入口
│
│
├── mock/                    # 模拟真实路径和检测事件
├── roadgraphmodel/          # 路网数据模型
├── roadmatch/               # 检测数据匹配算法
├── evaluation/              # 评估与可视化
├── utils/                   # 通用工具
│
└── old/                     # 旧代码归档
```

## 模块依赖关系

```
utils              ──→  (无内部依赖)
roadgraphmodel     ──→  utils
mock               ──→  roadgraphmodel, utils
roadmatch          ──→  roadgraphmodel, utils
evaluation         ──→  roadgraphmodel, utils
cli.py             ──→  所有模块
```

## 关键约定

- 各模块文件夹内的 `__init__.py` 只做空文件或简单重导出，不写业务逻辑
- 旧代码 `src/old/` 不做修改，重构时按需重新实现
- 模型文件以 `_model` 后缀命名（如 `event_model.py`），逻辑文件以功能命名
