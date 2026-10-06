# H Tool Reliability

Use the installed Hermes entry, not a second Project or Memory implementation.
Discover the actual executable with `where.exe hermes`, then use
`hermes --version` and `hermes --print-runtime-command` to identify its interpreter
and source. Do not upgrade Hermes to perform these checks.

## Project resolution

Hermes registers `desktop_project` in `tools/project_tools.py`, under the
`project` toolset. The default `hermes-cli` toolset excludes it. Registration in
the source/catalog does not mean the current chat exposes the tool.

For an explicit project-tool chat, include `project` in `-t`; keep the caller's
other selected toolsets. Use `desktop_project(action="list")` for read-only
workspace resolution. Do not create/switch a project merely to resolve its ID.
The equivalent normal CLI entry is:

```text
hermes project list
hermes project show <REGISTERED_PROJECT_ID>
```

The existing HPA Registry and Capsule resolver remain unchanged. A tool error
from another live session still requires checking that session's actual tool
selection; a successful fresh CLI process does not establish its configuration.

## Independent browser

Back up the local runtime configuration privately before changing it. Use the
existing Hermes configuration command:

```text
hermes config set browser.use_real_profile false
```

For the existing `browser-use` backend, Hermes' `_resolve_backend_cdp` routes to
managed Chromium through agent-browser when there is no explicit CDP override
or cloud provider. It avoids the user's installed Chrome profile. Do not pass
`local=true`: that option explicitly requests the real-profile route.
Do not set a user Chrome directory, copy login data, or close the user's Chrome.
Inspect only the relevant non-secret browser settings before claiming the
managed route: a CDP override or cloud provider changes the effective backend.

Verify in a new normal H chat with the `browser` toolset. Use one named
`browser_exec` session, navigate to `https://example.com/`, and read
`document.body.innerText`. Keep the user's Chrome running. Let Hermes perform its
normal session cleanup; do not kill unrelated browser processes.

## Agent Memory OS

Agent Memory OS is already integrated through its official
`agent-memory-os-router` Skill and public CLI. It is separate from Hermes native
memory and session history. Installing a Python wheel alone does not bind this
Skill to Hermes.

The official template is `adapters/hermes/ROUTER_SKILL.md` in the existing Memory
OS checkout. Resolve `<AGENT_MEMORY_OS_ROOT>` and `<MEMORY_OS_PYTHON>` locally;
never commit the generated machine-specific Skill. Preserve local additions
when aligning an existing installation. Pin the command to a verified Python
interpreter rather than relying on the terminal's `python` lookup.

The current public Router requires all three read arguments:

```text
<MEMORY_OS_PYTHON> -B <AGENT_MEMORY_OS_ROOT>/adapters/hermes/router.py read --project <PROJECT> --machine <MACHINE> --task <QUERY>
```

Quote paths with spaces. PowerShell requires `&` before a quoted executable.
The dedicated normal entry is:

```text
hermes chat -s agent-memory-os-router -t terminal,skills
```

Keep these capabilities distinct:

| Capability | Actual interface/source |
| --- | --- |
| Session history search | `session_search`; not Memory OS retrieval |
| Native Hermes memory | Native `memory` writes; MEMORY.md/USER.md context injection |
| Agent Memory OS retrieval | Official Router `read` -> `smart-retrieve` + `smart-inject` |
| Agent Memory OS write | Official Router/public CLI with its confirmation and gate |
| External reference injection | Bounded context returned by the Router; Skill driven, not a mandatory hook |

Prove retrieval with `MEMORY_RETRIEVAL_TRIGGERED=true`, the expected
`INJECTED_MEMORY_IDS`, and actual H terminal-call evidence. An answer alone is
insufficient. The old HPA `memory_context` adapter remains synthetic-only; it
must not be described as the production Memory OS connection.

For a disposable E2E probe, use the public CLI and Router's existing `--store`
and `--trace` arguments, with an isolated temporary `hermes-memory.json` store.
Do not export or query historical records. The CLI has `retire`, not physical
record deletion; remove only the expressly authorized disposable probe files
after closing the test and verify they are absent. This validates the existing
Router/runtime interface, not access to production historical records.
