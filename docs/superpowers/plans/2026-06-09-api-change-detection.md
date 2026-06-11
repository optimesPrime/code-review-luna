# API 变更检测 — OpenAPI / Protobuf 破坏性变更识别 ✅ 已完成

**目标：** 自动检测 diff 中 OpenAPI（swagger）或 Protobuf 文件的变更，精确识别破坏性变更（删除接口、改参数类型、删字段），无需 LLM 即可给出高置信度风险判断。

---

## 支持的文件类型

| 格式 | 文件特征 |
|------|----------|
| OpenAPI 3.x / Swagger 2.x | `swagger.json`、`openapi.yaml`、`openapi.json`、`api-docs.json`、`api-docs.yaml` |
| Protobuf | `*.proto` |

---

## 产品行为

终端审查点矩阵新增"API 契约"行：

```
  API 契约     🚨高   DELETE /orders/{id} 接口被删除   openapi.yaml:142   manual
```

完整报告新增"API 变更"节：

```markdown
## API 变更风险

### `DELETE /orders/{id}` 接口被删除 — `openapi.yaml:142`
- 风险: **high** · 破坏性变更
- 原因: 删除已发布的接口会导致调用方 404。需确认所有客户端已完成迁移。
- 建议: 先标记为 deprecated，保留至少一个版本周期后再删除。

### `amount` 字段类型 `string` → `number` — `openapi.yaml:88`
- 风险: **high** · 类型不兼容
- 原因: 已有客户端按 string 解析，改为 number 会导致反序列化失败。
```

---

## 风险分级规则

### OpenAPI

| 变更类型 | 风险 | 说明 |
|----------|------|------|
| 删除 path / endpoint | high | 调用方 404 |
| 删除 request body 字段（必填） | high | 服务端报 400 |
| 删除 response 字段 | high | 客户端解析异常 |
| 改字段类型 | high | 序列化不兼容 |
| 新增必填 request 字段（无 default） | high | 旧客户端发送的请求会被拒绝 |
| 改 HTTP method（GET→POST） | high | 路由不匹配 |
| 删除 optional request 字段 | medium | 旧客户端发送的多余字段被忽略（通常无害，但需确认） |
| 新增 optional request 字段 | low | 向后兼容 |
| 新增 response 字段 | low | 向后兼容 |
| 新增 path / endpoint | low | 纯增量 |

### Protobuf

| 变更类型 | 风险 | 说明 |
|----------|------|------|
| 修改字段编号 | high | 二进制序列化不兼容，数据损坏 |
| 删除字段（未保留 field number） | high | 解码时忽略变为使用旧数据 |
| 改字段类型 | high | wire type 不兼容 |
| 删除 enum 值 | high | 旧数据反序列化失败 |
| 重用已删除的字段编号 | high | 严重的序列化冲突 |
| 重命名字段（编号不变） | low | 仅影响 JSON 模式，二进制兼容 |
| 新增字段 | low | 向后兼容 |
| 新增 enum 值 | low | 向后兼容 |

---

## 架构

```
luna.py
  └── pipeline（检测到 API schema 文件时）
        └── api_change_detector.py
              ├── detect_schema_files(diff) → list[str]
              ├── openapi_analyzer.analyze(old_content, new_content) → list[APIChangeItem]
              └── proto_analyzer.analyze(diff_hunk) → list[APIChangeItem]
```

**OpenAPI diff 方式：** 用 `git show HEAD:{path}` 拿旧版本，与工作区版本对比，用 Python dict 深度 diff（不依赖外部工具）。

**Protobuf diff 方式：** 直接解析 unified diff hunk，用正则检测字段编号变化、类型变化、字段删除。

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| Create | `phases/api_change_detector.py` | 检测 schema 文件；分发到 openapi/proto 分析器；汇总结果 |
| Create | `phases/openapi_analyzer.py` | 解析 OpenAPI YAML/JSON；深度 diff；风险分级 |
| Create | `phases/proto_analyzer.py` | 解析 .proto diff；检测字段编号/类型/删除变更 |
| Modify | `reporter.py` | `ReviewReport` 新增 `api_change_items: list` |
| Modify | `luna.py` | pipeline 中调用 api_change_detector |
| Modify | `terminal_renderer.py` | checkpoint 矩阵新增"API 契约"行 |
| Modify | `config.py` | 新增 `APIChangeConfig(enabled=True)` |
| Create | `tests/test_openapi_analyzer.py` | 测试各类 OpenAPI 变更的检测和分级 |
| Create | `tests/test_proto_analyzer.py` | 测试 Protobuf 字段编号变化等 |

---

## 核心数据模型

```python
@dataclass
class APIChangeItem:
    file: str
    line: int
    change_type: str    # "removed_endpoint", "changed_field_type", "added_required_field", ...
    path: str           # OpenAPI path 或 proto message.field
    risk: str           # "high" | "medium" | "low"
    reason: str
    suggestion: str
    needs_human_review: bool = True
```

---

## Task 1：OpenAPI 分析器

**文件：** `phases/openapi_analyzer.py`

- [ ] 写失败测试：
  - `test_detects_removed_endpoint`
  - `test_detects_changed_field_type`
  - `test_detects_new_required_field`
  - `test_new_optional_field_is_low_risk`
  - `test_new_endpoint_is_low_risk`
  - `test_handles_yaml_and_json`
- [ ] 确认测试失败
- [ ] 实现 `analyze(old_content: str, new_content: str, file_path: str) -> list[APIChangeItem]`
  - `yaml.safe_load` 或 `json.loads` 解析两版本
  - 用递归 diff 比较 `paths`、`components/schemas`
  - 检测 path 增删、method 增删、`required` 字段增删、`type` 变更
  - 为每条变更生成 `APIChangeItem`，按规则表定 risk
- [ ] 确认测试通过
- [ ] `pytest tests/test_openapi_analyzer.py -v` 全绿
- [ ] commit：`feat: openapi_analyzer — detect breaking changes in OpenAPI specs`

---

## Task 2：Protobuf 分析器

**文件：** `phases/proto_analyzer.py`

- [ ] 写失败测试：
  - `test_detects_field_number_change`
  - `test_detects_field_type_change`
  - `test_detects_removed_field`
  - `test_detects_enum_value_removal`
  - `test_field_rename_is_low_risk`
  - `test_new_field_is_low_risk`
- [ ] 确认测试失败
- [ ] 实现 `analyze(diff_hunk: str, file_path: str) -> list[APIChangeItem]`
  - 从 diff 中提取 `-` 行（删除）和 `+` 行（新增）
  - 正则解析字段声明：`(repeated )?\w+ \w+ = (\d+)`
  - 比对同名字段的编号变化、类型变化
  - 检测有 `-` 无对应 `+` 的字段（字段被删除）
- [ ] 确认测试通过
- [ ] `pytest tests/test_proto_analyzer.py -v` 全绿
- [ ] commit：`feat: proto_analyzer — detect breaking changes in Protobuf definitions`

---

## Task 3：`api_change_detector.py` 总入口

**文件：** `phases/api_change_detector.py`

- [ ] 写失败测试：`test_detect_schema_files_finds_openapi_and_proto`
- [ ] 确认测试失败
- [ ] 实现 `detect_schema_files(diff: str) -> list[str]` — 按文件名特征匹配
- [ ] 实现 `analyze(diff: str, project_root: str) -> list[APIChangeItem]`
  - OpenAPI 文件：`git show HEAD:{path}` 拿旧版本（subprocess），与当前文件对比
  - Proto 文件：直接把 diff hunk 传给 `proto_analyzer`
  - `git show` 失败（新文件）→ 跳过，新增文件无破坏性变更
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: api_change_detector — route OpenAPI and proto files to correct analyzer`

---

## Task 4：接入主流程 + 终端渲染

**文件：** `luna.py`、`terminal_renderer.py`、`reporter.py`、`config.py`

- [ ] `config.py` 新增 `APIChangeConfig(enabled: bool = True)`
- [ ] `reporter.py` `ReviewReport` 新增 `api_change_items: list = field(default_factory=list)`
- [ ] `luna.py` pipeline 末尾调 `api_change_detector.analyze()`，结果赋 `report.api_change_items`
- [ ] `terminal_renderer.py` checkpoint 矩阵新增"API 契约"行：有变更时展示最高风险，无变更时显示 `✅ 无 API 契约变更`
- [ ] 写测试：`test_api_change_items_appear_in_checkpoint_matrix`
- [ ] 确认测试通过
- [ ] `pytest -q` 全绿
- [ ] commit：`feat: wire api_change_detector into luna pipeline and checkpoint matrix`

---

## Task 5：验证

```bash
pytest -q
```

手动验证：

```bash
# 修改 openapi.yaml，删除一个 endpoint，然后运行
luna --staged
```

验收标准：
- 删除 endpoint 标记 high 风险，出现在审查点矩阵"API 契约"行
- 新增 endpoint 标记 low，不触发高风险
- proto 字段编号变更标记 high
- 无 schema 文件变更时矩阵显示 ✅

---

## Non-Goals（本阶段不做）

- GraphQL schema 变更检测
- AsyncAPI（消息队列 schema）
- 自动生成客户端迁移代码
- 与 API Gateway 联动验证（需要网络连接）
