# V0.1 acceptance

Date: 2026-10-04 (Asia/Shanghai).

## Results

- Private repository created through the existing GitHub CLI absolute executable; no remote initial files, login changes, or new tools.
- Unit/regression: 10/10 PASS.
- Python wheel build: PASS (no dependency installation during final validation).
- Real normal Hermes CLI chat: Project A PASS; Project B PASS. Route/namespace preflight precedes the unchanged normal chat main, and selected native tools execute in real model conversations.
- A: only meridian/read_file selected; successful read_file tool result; final response contains A_MEMORY_MERIDIAN and SYNTHETIC_NATIVE_A, no B_MEMORY_COBALT.
- B: only cobalt/search_files selected; successful search_files result; final response contains B_MEMORY_COBALT, no A_MEMORY_MERIDIAN.
- Fresh conversations on project switch; actual Hermes session IDs are saved in project-scoped binding evidence. No history resume across projects.
- Invalid/missing/stale binding, unknown task, equal-score ambiguity, foreign skill/tool, artifact escape, foreign/private store and dependency drift are blocked in deterministic preflight tests.
- Memory CLI uses --read-only; exact namespace filtering drops other projects and unscoped global records. Only public synthetic fixtures used.
- DHAF: SHADOW_ONLY / NOT_LIVE_READY; recommendations recorded but never executed.
- Hermes, Memory OS and DHAF tracked worktrees remain clean.

## Evidence and interpretation

Evidence lives only under fixtures/A/artifacts and fixtures/B/artifacts, excluded from Git. The first real A run was retained and revalidated after correcting our assertion from tool_call to Hermes's actual tool_use event. B was additionally run after adding final host checks/session persistence. No model call was retried because of an incorrect answer.

The current wrapper's project-session binding, per-process exact toolset and routed Skill are host selection. Prompt instructions and allowed_* declarations are not runtime security isolation. The artifact writer enforces its own canonical containment checks; native H tools are not replaced with a project sandbox. Successful synthetic tasks do not prove arbitrary adversarial model behavior cannot read other directories.

This release connects the normal H CLI chat entry through a thin launcher, not Electron Desktop global routing. All H defaults remain unchanged. Source/pinned dependency paths are explicit local configuration. Only an external installed Capsule package is used, not vendored source.

## Another machine

Use README instructions: clone, install pinned project dependencies in an isolated environment, configure local paths, run unit tests and the real A/B chat E2E. Mac-Win10 Boot Camp uses the same Windows path resolution. A second physical Boot Camp machine and native macOS were not available for this validation; cross-machine acceptance must be repeated there. V0.2 is out of scope.