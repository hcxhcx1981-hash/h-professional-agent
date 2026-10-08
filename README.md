# H Professional Agent

独立的薄适配仓库，不修改 Hermes、Memory OS 或 DHAF Core。

当前包括 Phase 2A Canonical Project Registry 和 Phase 2B Task Router / Handoff Contract。项目身份解析与任务路由只做判断和推荐，`AUTO_DELEGATION=NO`。

## 当前能力

- **Project Registry / Machine Isolation**：一个 canonical 项目身份关联 H 项目、Git remote、别名、workspace 和 machine_id；跨机器、冲突、失效路径或损坏配置阻断。
- **Continue / Project Resolution**：复用真实 H 的只读 Project State，按项目 ID、名称、别名、workspace、H ID 解析；不猜项目，也不写 H 数据库。
- **Capability Router**：按任务、绑定 Capsule、工具声明和限制判断 H 内部能力；工具已登记不等于执行健康已确认。
- **SELF / CX / WB / AC Task Router**：结构化 Task Profile 产生确定性推荐；H 低风险能力优先，常规研究交 WB，正式工程/Git 交 CX，AC 需要明确适用证据。混合研究与开发要求分阶段。
- **Handoff Contract / Human Approval Gate**：外部交接携带项目、机器、范围、边界和验收；必须人工批准，不自动启动或发送任务给外部 Agent。
- **Specialist / Evidence Verification**：已有单一注册 Specialist 的受控纯执行契约和证据判断，不是通用多 Agent 执行平台。

这不是全自动多 Agent orchestration。外部 Agent delegation 当前仍需人工批准。项目尚未指定许可证，仓库公开不代表授予开源许可；上游依赖的许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 本机 Registry

公开仓库只跟踪 `hpa/project-registry.example.json`。真实配置使用忽略的 `hpa/project-registry.local.json`，wheel 只包含示例，不包含本机 Registry。

```powershell
Set-Location '<PROJECT_ROOT>'
Copy-Item hpa/project-registry.example.json hpa/project-registry.local.json
python -B -m hpa.registry --hermes-home '<HERMES_HOME>' --root '<WORKSPACE>'
```

首次配置时编辑 local 文件：通过已有 H 登记接口核验 project_id / h_project_id、真实 Git remote 和 workspace；`machine_id` 必须与 `hpa.continuation.local_device` 生成的 Device Context 一致，不能照抄示例。Registry 不创建 H 项目，不读取或保存凭据。缺少 local 文件时 fail closed，不回退到示例；也可用 `--registry-file` 指定本机文件。安装 wheel 后请通过该参数指定自己的配置位置。

使用说明：[Project Registry](docs/PHASE_2A_PROJECT_REGISTRY.md)、[Task Routing](docs/TASK_ROUTING.md)。下面是早期 synthetic Capsule/chat 适配层的独立运行流程。

真实 H 工具暴露、独立 Browser 与外部 Memory OS 接入核准见 [Tool Reliability](docs/TOOL_RELIABILITY.md)。

## 边界

- allowed_skills / allowed_tools 是 **Host selection**，不是 Runtime security。每进程注册一个明确的原生工具集合，不修改全局设置。
- Skill 正文和任务说明是提示词。H 的原生 read_file/terminal 等通用工具不是项目文件沙箱；本项目只检查自己读写的路径。本次 fixture 验收不证明任意恶意模型无法访问其他文件。
- DHAF 始终 SHADOW_ONLY / NOT_LIVE_READY，建议不会执行；没有强制 live gate。
- 早期 chat/Memory 适配层只接显式 synthetic JSON fixture，不读取私人 Memory 或凭据；Phase 2A/2B 可只读核验真实 H 项目元数据，不读取原始任务内容。
- 正常 chat 入口必须显式指定 capsule；Registry/Continue 可解析已登记项目，缺失 Capsule 不构成执行许可。每次 chat 新建 H 会话，切换项目不复用旧历史。失效 binding 和 Capsule 漂移阻断。
- Capsule Skill 路由最多选择 1 个 Skill 和所需 Tools；低分/无结果/分差不足阻断。Task Router 另按结构化任务特征给 Agent 推荐，不执行交接。没有 LLM Router、新服务或编排器。
- Capsule 路由输入以英文词汇/显式触发短语为主，中文任务可能低置信；此时阻断，不加 LLM 翻译兜底。
- 这连接正常 **CLI 聊天入口**，不是 Electron Desktop 全局安装。模型和 Provider 使用 H 已有默认值。

## 固定依赖

Capsule commit `6492d704f9d389233038ee35630940acba7c2f2f`，Apache-2.0；调用上游 route，不复制源代码到本仓库。包内 Python 文件完整性在选择前校验，拒绝版本漂移。Memory OS 固定 `2a75f1dc56053e07516318aea69716f0333810f5`，执行前验证 HEAD 和 tracked files。DHAF 使用你已配置的 checkout，Core 不变。

## 另一台 Win10 / Mac-Win10 Boot Camp

Boot Camp 下仍运行 Windows，下面命令相同。先装好 H、Python 3.11+、Git，并准备 Memory OS 和 DHAF checkout。不要复制当前电脑的绝对路径。

```powershell
git clone https://github.com/hcxhcx1981-hash/h-professional-agent.git
cd h-professional-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
Copy-Item local.example.toml local.toml
```

在本机选择正确路径，然后设置这些非秘密变量；尖括号是要替换的说明，不要照抄为实际路径：

```powershell
$env:MEMORY_OS_ROOT = '<本机 agent-memory-os checkout>'
$env:MEMORY_OS_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:DHAF_ROOT = '<本机 DHAF checkout>'
$env:HERMES_SOURCE = '<本机 hermes-agent checkout>'
$env:HERMES_PYTHON = '<H 已有 Python 可执行文件>'
$env:CAPSULE_SITE = (Resolve-Path '.venv\Lib\site-packages').Path
git -C $env:MEMORY_OS_ROOT rev-parse HEAD
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -B -m tests.e2e local.toml
```

对照确认 Memory OS HEAD 与固定版本一致；不要在有未提交改动的 checkout 强制 reset。网络安装失败时先保留错误，不修改代理/DNS。local.toml 只存非秘密路径，已 gitignore；路径可以相对于配置文件或 `${ENV_NAME}`，Capsule 项目根和产物路径相对解析。

为一键验收，可先激活本仓库环境，再双击 verify.bat；失败窗口保留。

```powershell
.\.venv\Scripts\Activate.ps1
```

## 明确绑定项目并发起正常 H 聊天

```powershell
.\.venv\Scripts\python.exe -B -m hpa.chat --config local.toml --capsule fixtures/A/project-capsule.json --task 'inspect meridian report'
.\.venv\Scripts\python.exe -B -m hpa.chat --config local.toml --capsule fixtures/B/project-capsule.json --task 'inspect cobalt ledger'
```

--capsule 是本次 conversation 明确的项目选择。launcher 先路由和检索，再调用 H 原有 bootstrap 及 hermes_cli.main.main 的正常 chat 路径。选中 Skill 正文通过用户消息传入；--ignore-rules 阻止原生私人记忆/规则/全局 Skill 预加载。只注册每进程 hpa-selected toolset，H 的真实模型调用可见集合由该 toolset 决定。

示例 A 仅有 meridian/read_file，B 仅有 cobalt/search_files。原生工具读取各自 synthetic input.txt，最终答复保存为各自 artifacts/result.jsonl；另有 host payload、路由/记忆证据、真实 H session 绑定和 DHAF shadow。全部产物 gitignore，仓库只包含代码/schema/测试/文档/模板与公开虚构数据。

不传 --capsule 时参数校验阻断；无关任务和同分候选在启动 H 前阻断。一般启动命令不自动重试模型；e2e 明确跑 A/B 两个新会话。

原生 macOS 尚未真实验证，DHAF 也未声明支持；不要把 Boot Camp 验收等同于 macOS 验收。新机器必须重新执行正常入口 E2E。
