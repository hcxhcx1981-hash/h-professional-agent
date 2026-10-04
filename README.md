# H Professional Agent Architecture V0.1

独立的薄适配仓库，不修改 Hermes、Memory OS 或 DHAF Core。

流程：明确选择 Project Capsule → conversation/project 绑定 → 固定版本 Capsule route → 精确 Memory namespace → 正常 Hermes chat CLI / 原生工具 → 项目产物目录 → DHAF shadow 记录。

## 边界

- allowed_skills / allowed_tools 是 **Host selection**，不是 Runtime security。每进程注册一个明确的原生工具集合，不修改全局设置。
- Skill 正文和任务说明是提示词。H 的原生 read_file/terminal 等通用工具不是项目文件沙箱；本项目只检查自己读写的路径。本次 fixture 验收不证明任意恶意模型无法访问其他文件。
- DHAF 始终 SHADOW_ONLY / NOT_LIVE_READY，建议不会执行；没有强制 live gate。
- V0.1 只接显式 synthetic JSON fixture，不读取私人 Memory、原生 DB、.env 或凭据。真实数据接入不在本轮范围内。
- 必须显式指定 capsule；不会从任务、CWD 或历史猜项目。每次调用新建 H 会话，切换项目不复用旧历史。失效 binding 和 Capsule 漂移立即阻断。
- 路由只给 Capsule 允许候选，最多 1 个 Skill 和该 Skill 所需的 Tools。低分/无结果/分差不足要求澄清。没有 LLM Router，也没有新服务或编排器。
- Capsule 路由输入以英文词汇/显式触发短语为主，中文任务可能低置信；此时阻断，不加 LLM 翻译兜底。
- 这连接正常 **CLI 聊天入口**，不是 Electron Desktop 全局安装。模型和 Provider 使用 H 已有默认值。

## 固定依赖

Capsule commit `6492d704f9d389233038ee35630940acba7c2f2f`，Apache-2.0；调用上游 route，不复制源代码到本仓库。包内 Python 文件完整性在选择前校验，拒绝版本漂移。Memory OS 固定 `e53d43089f288146a089281cfce47403544d6004`，执行前验证 HEAD 和 tracked files。DHAF 使用你已配置的 checkout，Core 不变。

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
