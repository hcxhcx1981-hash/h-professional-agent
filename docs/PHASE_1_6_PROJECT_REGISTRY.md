# Phase 1.6: ephemeral Project Registry

Phase 2A supersedes continuation identity resolution: both CLI entry points now use `hpa/project-registry.local.json`. This ephemeral view remains discovery-only; it cannot register projects or replace canonical identity. See `PHASE_2A_PROJECT_REGISTRY.md`.

The registry is an in-memory view, with no persistent store or queue. ProjectRecord fields are defined in `hpa/registry.py`. Authority remains H for task state, GitHub for repository metadata, Memory OS for structured long-term facts and the local filesystem for present workspace availability. Sources do not imply permission to execute.

## Read interfaces

- H: existing `read_states` with disposable DB/WAL snapshots and SQLite query_only. No source database handles or writes.
- GitHub: one existing authenticated gh repo list request; no login, retry or mutations. A 1000-result limit is explicitly TRUNCATED. Repository update time is metadata, not task activity. Latest commit is not claimed.
- Local: explicit trusted roots, one level, at most 200 children per root, `.git` or manifest plus README evidence. Only remote.origin.url is queried; no file bodies or Git mutation. A local path is availability evidence, never a permission grant.
- Memory: existing Memory(read_only=True).load for namespace discovery and search for lifecycle-aware retrieval. No copying retrieval/lifecycle policy. Only `metadata.project_registry` with explicit canonical_id, repo_url or workspace is accepted. Free prose and bare namespaces are not identity evidence. Recognized machine labels: WIN10_ADMIN, WIN11_LENOVO, MAC_WIN10. Unknown labels never imply online/available. Structured aliases and status may supplement identity. Credential-shaped projections and explicitly synthetic/test records are excluded.

`metadata.project_registry` optional keys: canonical_id, canonical_name, repo_url, aliases, workspace, status, next_action, not_on_github. This is a projection contract, not a new Memory schema or an instruction to write Memory.

## Merge and queries

Exact normalized GitHub URL, explicit stable ID, or exact workspace plus trusted machine merges sources. Names never merge identities; collisions remain AMBIGUOUS. H state wins over Memory claims and differences enter conflicts. A completed task does not imply a completed project. Missing remote does not prove NOT_ON_GITHUB; that state requires explicit structured evidence.

Methods: list_projects, get_project (list to preserve ambiguity), get_available_projects, get_projects_waiting_for_device, resolve_project_reference. Registry continue_request keeps nonlocal identities RESOLVED while returning no local workspace, WAITING_FOR_DEVICE and NO_VALID_AGENT. Local execution recommendation still requires existing H and Capsule binding. Host trust/freshness is checked first.

## CLI

Use existing Python, no dependency installation:

```powershell
& '<HERMES_PYTHON>' -X utf8 -B -m hpa.registry --hermes-home '<HERMES_HOME>' --root '<WORKSPACE>' --github-owner hcxhcx1981-hash --memory-root '<MEMORY_OS_ROOT>' --memory-store '<MEMORY_OS_ROOT>\storage\hermes-memory.json' --continue-text '继续 h-professional-agent'
```

Existing continuation CLI can opt in with --registry --github-owner and --registry-memory-store (with --memory-root/--memory-python). Default Phase 1 behavior is preserved.

## Acceptance evidence and open blocker

32 targeted tests pass, including four-source fixture merge, remote-device RESOLVED without execution, collision ambiguity, source precedence and readonly checks. Real H/GitHub/local merge resolves p_example at <PROJECT_ROOT>; protected H DB/WAL/SHM and Memory JSON hashes remain unchanged. GitHub-only examples: example-research, example-tools. Local manifest/README candidates exist, but absence of a remote is UNKNOWN, not proof of absence from GitHub.

Real Memory currently has no structured identity link for h-professional-agent. Real Win11/Mac project claims and NOT_ON_GITHUB identities were not established. Four-source merge is proven with isolated fixtures only. Real four-source acceptance remains BLOCKED; no Memory facts or fake projects were created to satisfy it. The acceptance baseline has not been reduced.
