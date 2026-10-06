# Task Routing V0.1 / Phase 2B

扩展现有 `hpa.capability`，复用 canonical Registry 和 Continue Resolver；Specialist 的纯执行契约保持原状。没有新的执行器。

| Target | 职责 |
| --- | --- |
| SELF | H：低风险项目状态、Registry/Memory/Capsule、文件文档分析；已核验且适用于当前项目的 H Skill 优先 |
| CX | 正式代码开发、复杂或跨文件工程、测试、构建和 Git 收口 |
| WB | 常规公开信息研究、资料报告和简单配置，节省 CX 额度 |
| AC | 用户明确偏好且已有当前项目/机器适用证据的低风险受控开发；复杂工程和 Git 仍交 CX |

`build_task_profile` 将明确动作与对象转换为结构化特征，再接收结构化上下文。裸“代码”“搜索”不会建立需求；未确认字段为 null/UNKNOWN。Profile 表达任务类型、代码/联网/工程/Git/GUI/敏感访问/高上下文/Memory 需求、复杂度、风险、偏好和禁用 Agent。风险、复杂度是分类估计，不是执行安全证明。`route_task` 的策略只使用这些特征与可信项目上下文，不按 Agent 可用性分配工作。

入口：`route_task(text, registry, project_reference=None, characteristics=None, capsule=None, verified_skills=(), ac_suitability=None, acceptance_criteria=(), relevant_paths=(), source_requirements=())`，参数在 `project_reference` 后均为关键字参数。空 text 可读取真实 Project State 的 next_action；非空用户请求优先。Continue Resolver 提供项目上下文；绑定 Capsule 提供限制和引用。不修改 Memory OS 或 Capsule schema。

Skill/AC 适用证据由可信调用方核验后提供：`verified, project_id, machine_id, task_type, evidence_ref`；Skill 还需 `skill_id, low_risk`。这些字段是调用方证据契约，不是 Router 自行确认安装或健康。AC 还要求 profile 中 LOW 复杂度/风险、明确 preferred_agent=AC、deep_engineering=false、git_operations=false。没有证据会阻断，不能为通过测试伪造证据。

Routing Decision：`route_target, decision, confidence, reasons, evidence, blocked_reasons, requires_user_approval, fallback_target, task_profile, recommended_sequence, handoff_package, execution_attempted`。同一输入得到同一结果；当前没有自动 fallback。高风险、未知身份/风险、敏感访问、GUI 范围不明或关键交接信息缺失返回 BLOCKED。研究和正式开发同时出现返回 NEEDS_DECOMPOSITION / BLOCKED，建议顺序 WB → CX。偏好或禁用名单与能力/安全/成本规则冲突时阻断。

Handoff：包含稳定内容摘要 handoff_id、project_id、machine_id、workspace、target_agent、单一 objective、context、current_state、allowed_scope、hard_boundaries、acceptance_criteria、relevant_paths/refs、expected_return_format。开发交接需要最小读取路径和验收条件；路径必须属于当前项目，不接受 .env、.git 或 vault 路径。引用 `$doraemon-local-dev-guard`，不复制整套 Skill。WB 交接要求研究目标、主要来源/链接/日期和输出格式，不做无关开发。

Approval Gate：CX/WB/AC 始终 `requires_user_approval=true`、`execution_allowed=false`；SELF 只推荐，不改变 H 正常执行逻辑。BLOCKED 不生成交接。Router 不启动 Agent、不发送任务、不实现队列、调度或 workflow。

Machine Isolation：先通过 Phase 2A canonical Registry + Continue Resolver 核验真实 H binding、可信当前机器和 workspace。跨机器、失效路径、歧义、损坏 Registry 均阻断，不猜替代目录。

```powershell
Set-Location '<PROJECT_ROOT>'
& '<HERMES_PYTHON>' -X utf8 -B -m hpa.capability '继续开发 h-professional-agent，增加跨文件正式功能并做 Git 收口' --task-route --hermes-home '<HERMES_HOME>' --trusted-root '<PROJECT_ROOT>' --acceptance '相关测试与构建通过' --relevant-path hpa/capability.py --relevant-path tests/test_task_routing.py
& '<HERMES_PYTHON>' -X utf8 -B -m tests.task_routing_e2e --hermes-home '<HERMES_HOME>' --workspace '<PROJECT_ROOT>'
```

CLI 复用 `hpa.capability --task-route`，输出 JSON；不执行 handoff。复杂或无法解析的自然语言需要调用方补充结构化特征，当前没有 LLM 分类或自动 Agent 执行。

验证：新增场景和相关回归测试通过，真实 H/Registry 的自然语言 CX handoff E2E 通过，H 源文件字节未变。全量旧 Scope 测试在当前环境缺少 Capsule 包及 Memory/DHAF 路径配置；本轮不安装依赖或集成 DHAF。
