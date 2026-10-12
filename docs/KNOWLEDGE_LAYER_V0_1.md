# 专业知识层 V0.1 — 知识管理设计

状态：DESIGN_DRAFT / NOT_IMPLEMENTED / NOT_VALIDATED。

本文定义 HPA 未来可选的专业知识层架构，吸收 LLM Wiki（nashsu/llm_wiki，GPLv3）的知识管理设计模式。不复制其代码，不安装其依赖，不 vendor 其源码。

本文不修改 Hermes Core，不修改 Memory OS，不修改 S5 冻结工程，不修改运行配置，不安装依赖。

## 1. 定位与边界

### 1.1 HPA 是专业工程 Agent 的决策与上下文层

HPA 现有核心职责不变：项目注册、Capsule 绑定、能力路由、任务推荐、证据判断、人工审批。专业知识层是 HPA 未来**可选**的能力扩展，不是新 Core，不在当前实施范围内。

### 1.2 职责分离

| 层 | 负责方 | 说明 |
| --- | --- | --- |
| 项目与任务决策 | HPA Core | 现有能力，不变 |
| Agent 长期记忆 | Memory OS | 独立组件，不合并到 HPA 知识层 |
| 专业领域知识 | 本层（Future） | 结构化知识维护，可选启用 |
| 市场监管 Agent | S5 工程（冻结） | 不改写；仅可作为未来应用场景参考 |

### 1.3 原始证据不可被替代

LLM 生成的知识条目不能替代原始证据。每条知识必须保留 `source_refs` 指向可信来源。查询结果须同时呈现知识摘要与来源引用，供调用方独立核验。不引用来源的 LLM 生成内容不得作为已验证结论。

### 1.4 写入安全

知识层写入必须经过：

- **权限**：仅可信写入者可操作，与 Action Policy 一致。Agent 本身不因需要知识维护而获得写权限。
- **验证**：写入前校验 `source_refs` 有效性、身份与 scope 匹配。
- **审计**：所有写操作生成不可变记录（`wiki/log.md` 风格），不静默覆盖。

## 2. 三层架构（Raw → Wiki → Schema）

```
raw/
  sources/       # 不可变原始材料（项目文档、研究摘录、监管法规、市场数据）
  assets/        # 本地图片与附件

wiki/
  index.md       # 知识目录，LLM 导航入口
  log.md         # 操作历史（append-only）
  overview.md    # 全局摘要（自动维护）
  entities/      # 人物、组织、产品、法规
  concepts/      # 理论、方法、技术、模式
  sources/       # 来源摘要（每来源一页）
  synthesis/     # 跨来源综合
  comparisons/   # 对比分析

schema/
  purpose.md     # 目标、关键问题、研究范围
  schema.md      # 页面类型规则、结构规范、生命周期
```

**与 HPA 现有架构的映射：**

- `raw/sources/` 等价于 HPA 项目内受控文件与外部研究材料。HPA 的 Evidence Verification 模块继续作为"原始证据判断"的唯一入口。
- `wiki/` 是 LLM 维护的专业知识层。每条内容必须携带 YAML frontmatter（`project_id`、`source_refs`、`confidence`、`version`、`created_at`、`expires_at`）。
- `schema/` 是 HPA 项目专属规则：页面类型定义、冲突解决策略、过期检测规则、MCP Adapter 配置。
- **与 Memory OS 隔离**：`wiki/` 存储专业领域知识，Memory OS 存储 Agent 自身长期记忆。两者通过 Capsule `memory_namespace` 隔离，不双写、不自动同步。

## 3. Ingest → Query → Lint 维护流程

### 3.1 Ingest

1. 将新来源放入 `raw/sources/` 或通过可信写入者注入。
2. LLM 执行两步 Chain-of-Thought：(a) 分析来源结构与关键事实；(b) 生成或更新 wiki 页面，附带 `source_refs`。
3. 增量缓存：同一来源内容摘要不变时跳过重复处理。
4. 来源变更检测：自动发现 `raw/sources/` 外部修改，触发增量 ingest 或清理。

### 3.2 Query

1. 任务上下文触发知识检索，优先从 `wiki/index.md` 导航。
2. 支持 **Read Sources Only** 模式：仅从 `raw/sources/` 原文回答，不引用 LLM 生成内容。
3. 检索结果同时呈现知识摘要与 `source_refs`，调用方可核验。
4. 向量语义搜索为可选扩展，不作为默认路径。

### 3.3 Lint

1. 按策略触发，扫描 wiki/ 页面：
   - 来源缺失或 `source_refs` 不可解析
   - 知识冲突（同一实体在不同页面矛盾描述）
   - 过期标记（`expires_at` 已过期或 TTL 超期）
   - 孤立页面（无入站链接）
   - 与 `schema/schema.md` 规则不一致
2. Lint 结果生成为 Review 条目，由人工裁定后修复，不自动修改内容。
3. Lint 操作记录写入 `wiki/log.md`。

## 4. 增量更新与来源追溯

### 4.1 增量更新

- 知识条目按 `source_hash` 追踪：来源不变时不重复 ingest。
- 来源更新后仅重新处理受影响页面。
- `wiki/log.md` 是 append-only 操作记录，格式可解析（时间戳 + 操作类型 + 页面 + 摘要）。

### 4.2 来源追溯

每条 wiki 内容必须携带 YAML frontmatter：

```yaml
---
project_id: <HPA project_id>
source_refs:
  - type: file | url | session
    ref: <路径或 URL>
    version: <commit 或 content hash>
confidence: 0..1
created_at: ISO-8601
updated_at: ISO-8601
expires_at: ISO-8601 | null
supersedes: <旧条目引用> | null
---
```

- 查询结果必须展示 `source_refs`，不允许仅返回 LLM 生成的汇总文本。
- 与 HPA 现有 `source_refs` 字段语义保持一致（见 Control Plane V0.1 §4 身份与引用）。

## 5. 知识冲突与过期检测

### 5.1 冲突检测

- 同一实体或概念在不同页面出现矛盾描述时，Lint 标记 CONFLICT。
- 冲突页面在 `index.md` 中标记，查询时同时呈现冲突双方的 `source_refs`，由调用方裁定。
- 与 HPA Evidence 模块的 CONFLICT 判定保持一致语义。

### 5.2 过期检测

- `expires_at` 字段驱动：过期条目自动标记 STALE。
- TTL 由 `schema/schema.md` 定义（按项目类型配置）。
- 过期条目不删除，标记后进入 Review 队列。
- 与 Memory OS 的 supersedes/retire 机制独立运行，不互相触发。

## 6. 可选 MCP Knowledge Adapter

### 6.1 定位

未来可选组件：通过 MCP 协议接入外部知识 wiki（如 LLM Wiki 本地实例），为 HPA 提供**只读**知识检索。

### 6.2 约束

- 默认只读：MCP Adapter 不提供写入能力；写入走 HPA 权限链路（Action Policy + DHAF）。
- 不安装 LLM Wiki 或其依赖：MCP 端点须已独立运行，HPA 仅消费其 API。
- 不复制 GPLv3 代码：HPA 通过 MCP 协议调用，不 fork、不 vendor LLM Wiki 源码。
- 与现有 Capsule / Tool 路由兼容：MCP 知识检索作为一个可选 Skill 注册，由 Capability Router 按需选择。
- 查询结果必须保留来源引用，不得仅返回 LLM 生成的汇总文本。

### 6.3 市场监管 Agent 未来场景（不改 S5）

市场监管 Agent（S5）当前已冻结。若未来需要专业知识层支撑监管场景（法规跟踪、市场数据摘要、竞品分析），本层的 `wiki/` 与 MCP Adapter 可作为扩展入口，但不得修改 S5 已有工程。新增监管知识页面须通过正常 Ingest 流程进入，由人工裁定后生效。

## 7. 实施状态

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 三层架构定义 | DESIGN_ONLY | 本文档定义，无实现 |
| Ingest / Query / Lint 流程 | DESIGN_ONLY | 本文档定义，无实现 |
| 增量更新与来源追溯字段契约 | DESIGN_ONLY | 字段定义，无实现 |
| 冲突 / 过期检测策略 | DESIGN_ONLY | 策略定义，无实现 |
| MCP Knowledge Adapter | FUTURE_OPTIONAL | 可选未来能力，未设计具体接口 |
| 代码实现 | NOT_STARTED | 不在本任务范围 |
| 测试 | NOT_STARTED | 不在本任务范围 |
| 运行验收 | NOT_APPLICABLE | 设计文档，无运行验收 |

本文档仅补充 HPA 架构设计，不改变任何现有代码、配置、Hermes Core、Memory OS 或 S5 工程。

## 参考

- LLM Wiki（nashsu/llm_wiki）：GPLv3，20.2k stars。本文仅吸收其知识管理设计模式（Raw→Wiki→Schema 三层、Ingest→Query→Lint 流程、source traceability、YAML frontmatter、MCP 只读接入），不复制代码，不安装依赖。
- Karpathy LLM Wiki 模式（原始设计文档）：抽象设计模式，本文是其在 HPA 专业工程语境下的具体映射。
