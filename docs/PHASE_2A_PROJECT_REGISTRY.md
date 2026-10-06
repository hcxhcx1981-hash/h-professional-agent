# Phase 2A Project Registry Bootstrap

复用 `hpa/registry.py`，canonical 配置为 `hpa/project-registry.local.json`，只登记当前现场核验的 H 项目。没有新数据库、依赖或调度。

Source of Truth：Registry 管项目身份、别名、机器和外部引用；H 管任务状态；Git origin 管仓库地址；本机文件系统管路径是否可用。Registry 不赋予执行权限。

静态配置：project_id、display_name、aliases、machine_id、workspace、git_remote、h_project_id、project_type、tags，以及显式 Memory 引用。memory_namespace 沿用 Capsule 契约，未验证绑定时为 null，不猜测 namespace。status、last_active_at、next_action 是无 H 数据时的配置基线；有 H 数据时使用 H 运行时状态。IDLE 映射 UNKNOWN，不假装任务正在执行。

运行时核验：Device Context 的可信机器身份、目录存在且在 trusted roots 内、真实 H ID 与 workspace 对应关系。machine_id 使用已有 Device Context 的 H install identity + hostname + user 摘要；另一台机器返回 NOT_ON_THIS_MACHINE，路径不可用返回 UNAVAILABLE。机器标签不能替代机器身份。

默认 Continue CLI 读取 canonical Registry；支持精确 ID、名称、别名、workspace、H ID 和 GitHub remote；自然语言名称沿用现有匹配。未知引用 NOT_FOUND，歧义 BLOCKED，损坏配置或重复身份 fail closed。既有 `--registry` 是只读多源发现视图，不会自动写入 canonical 配置。

```powershell
Set-Location <PROJECT_ROOT>
& '<HERMES_PYTHON>' -X utf8 -B -m hpa.continuation p_example --hermes-home '<HERMES_HOME>' --trusted-root '<PROJECT_ROOT>'
& '<HERMES_PYTHON>' -X utf8 -B -m unittest discover -s tests -p 'test_*.py' -v
```

可用 `--registry-file` 指定配置，损坏配置不会回退到猜项目。H 读取继续使用已有临时快照和 query_only，不打开源数据库写连接；没有直接 SQL write。

真实无 mock 验收：`python -X utf8 -B -m tests.registry_e2e --hermes-home <H目录> --workspace <PROJECT_ROOT>`。通过 H 官方 `get_project` 在只读临时快照内核验，检查真实 Git remote、机器身份及五种解析，并对比 H 源数据库文件哈希。无需模型调用。
