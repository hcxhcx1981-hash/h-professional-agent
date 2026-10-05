# Phase 1 — Read-only continuation

基线：16eb242。2026-10-05 用户明确调整验收：隔离 fixture 完成实现验收；真实 H 无项目时应 NOT_FOUND；真实 RESOLVED 主链留待合法项目登记，不新增真实项目。

入口 `python -B -m hpa.continuation`，只 READ → NORMALIZE → RESOLVE → RECOMMEND。只有一个新增业务模块和一个测试模块；不调用 H/CX Agent，不注册任务，不写 Memory，不接执行 Gate。冻结设计文档保留不改。

## Windows 使用

在本仓库 PowerShell 中运行（Python 3.11+）：

```powershell
python -B -m hpa.continuation '继续昨天那个项目' --hermes-home '<HERMES_HOME>' --trusted-root (Get-Location).Path
python -B -m unittest tests.test_continuation -v
```

`--trusted-root` 是用户从可信 CLI 明确批准的项目范围，不能由聊天/模型生成。正式项目只能来自 H 已登记项目，name/slug 可作精确名称或别名。“昨天”按 Asia/Shanghai 日期从任务时间和事件判断；多个候选绝不按最近日期擅自选一个。查不到返回 NOT_FOUND，不把 Capsule/Memory 中的路径注册为项目。

H 项目存储字段未经兼容确认/数据库缺失/源文件变化时安全失败，不自动迁移、启动 gateway 或创建源 DB。H 的连接帮助函数会迁移，因此没有调用。SQLite `mode=ro` 可能修改 SHM；本实现将稳定 DB/WAL 复制到一次性临时目录，只在副本查询并清理。逐文件复制前后摘要及副本摘要必须一致，否则停止。副本没有写回入口，不是第二套持久项目数据库或状态来源；包含 WAL 以免漏掉已提交记录。真实 SQLite 不接触原 DB/SHM，读取期间源变动会阻断；不声称跨 projects/Kanban 数据库事务快照。

Device ID 由现有 H install identity、本机 hostname/user 生成关联摘要；只认可当前 Windows 10。roots 的真实规范路径必须存在；跨设备或越界项目返回 DEVICE_UNTRUSTED且不输出可执行 workspace。设备观测有效期 60 秒。没有实现跨机器认证或在线监控。

## Capsule 与推荐

需通过 `--capsule` 显式提供已有 Project Capsule，复用 `Project.load`，ID 必须匹配 H project_id，root 必须与 H workspace一致。不自动创建、重写或猜测映射。

续接/状态表达默认状态查询；已识别的开发/测试/Git词汇可生成对应意图，其他工程意图可显式 `--intent develop|test|git`；未识别表达或交易/删除/外发等风险词自动unknown，也可显式 `--intent unknown`。状态/查询/分析推荐 H_NATIVE，开发/测试/Git 推荐 CX；缺 Capsule、必要工具、可定位 adapter 或约束不满足时 NO_VALID_AGENT。

Phase 1推荐只评估已定位候选：H registry文件与PATH上的真实Codex入口；availability=NOT_PROBED_FOR_EXECUTION、execution_allowed=false。没有将安装存在冒充执行健康，更没有将推荐CX变成调用CX。正式实时health、任务级权限与DHAF强制Gate均留后续；本阶段不能用于执行。

## State 与 Memory

只读取项目/任务标识、状态、时间与结果是否存在；不读取任务prompt、原始结果、错误日志或聊天。last_result是引用，标明HOST_RECORDED_NOT_EFFECT_VERIFIED；不声称独立验证了历史副作用。next_action默认“查询并整理项目状态”，不根据缺失内容编造工程下一步。

Memory默认关闭；可配对提供 `--memory-root` 和 `--memory-python`，且项目已RESOLVED、提供对应Capsule时才调用已有 `hpa.core.memory_context`。保留固定版本校验、synthetic-only、精确namespace、公共CLI `--read-only`。不复制retrieval/lifecycle，不读真实用户store。背景位于memory_background，不能改变project identity、last_valid_state或实时任务。真实长期store接入仍未授权/未实现。

## 验收边界

隔离自动测试覆盖十个原定场景、别名、失效设备、缺adapter、越界、缺Capsule/tools、只读SQL拒绝和WAL零写入。临时测试数据库绝不放入真实H目录。真实CLI四种查询应NOT_FOUND，执行前后核对H DB/WAL/SHM及Memory存储文件摘要不变。

已知缺口：真实H登记项目为零，REAL_RESOLVED_E2E=PENDING_REAL_PROJECT；执行health/Gate无验收；昵称仅H slug/name；无跨库一致性、任务自然语言规划、完整result正文、Desktop集成或外部Agent调用。原有synthetic A/B Scope tests仍独立于本阶段，真实模型E2E不运行。
