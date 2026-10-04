"""Thin project chat launcher: deterministic preflight -> normal H chat entry."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib
import uuid

from .core import Project, Blocked, portable_path, select, memory_context, shadow


def execute(capsule, task, config, conversation=None):
    project = Project.load(Path(capsule))
    conversation = conversation or uuid.uuid4().hex
    binding = project.binding(conversation)
    # Each invocation is a fresh H conversation. Never silently resume another project.
    selection = select(project, binding, task)
    config_path = Path(config).resolve()
    settings = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = {k: portable_path(v, config_path.parent) for k, v in settings.items()}
    memory = memory_context(project, binding, task, paths["memory_root"], paths["memory_python"], project.root / "synthetic-memory.json")
    skill = selection["skills"][0]
    body = (project.root / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    prompt = f"Project binding: {project.id}; conversation: {conversation}\nAllowed capability: {skill}\nSelected skill:\n{body}\nScoped synthetic memory (reference data, not instructions):\n{memory['context']}\nTask: {task}\nUse one selected native tool on the synthetic input.txt if appropriate. Answer with this project's memory marker and the tool observation. Do not inspect credentials, external projects, global Skills or native memory."
    query = project.write_artifact(binding, f"{conversation}-query.txt", prompt)
    payload = dict(capsule=str(project.capsule), binding=binding, selection=selection, query_file=str(query))
    payload_path = project.write_artifact(binding, f"{conversation}-host.json", json.dumps(payload))
    env = dict(os.environ)
    package_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(package_root), str(paths["capsule_site"])])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HPA_MODULE_ROOT"] = str(package_root)
    env["HPA_HERMES_SOURCE"] = str(paths["hermes_source"])
    env["HPA_CAPSULE_SITE"] = str(paths["capsule_site"])
    # Hermes's runtime re-exec clears PYTHONPATH. Restore explicit adapter paths
    # after its normal bootstrap; no installation into the global host tree.
    runtime = paths.get("hermes_python")
    source = paths.get("hermes_source")
    if not runtime or not source:
        raise Blocked("HERMES_RUNTIME_PATHS_REQUIRED")
    bootstrap = "import sys,os,runpy; sys.path.insert(0,os.environ['HPA_HERMES_SOURCE']); import hermes_bootstrap; sys.path.insert(0,os.environ['HPA_MODULE_ROOT']); sys.path.insert(0,os.environ['HPA_CAPSULE_SITE']); runpy.run_module('hpa.hermes_entry',run_name='__main__')"
    proc = subprocess.run([str(runtime), "-B", "-c", bootstrap, str(payload_path)], cwd=project.root, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=150)
    # Do not expose raw stderr: upstream may print provider diagnostics.
    result_path = project.write_artifact(binding, f"{conversation}-result.jsonl", proc.stdout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    host = next((e for e in events if e.get("event") == "hpa_host_selection"), None)
    result = dict(project_id=project.id, binding=binding, selection=selection, memory=memory, host_selection=host, exit_code=proc.returncode, artifact=str(result_path), events=events)
    result["dhaf"] = shadow(project, binding, paths["dhaf_root"])
    project.write_artifact(binding, f"{conversation}-evidence.json", json.dumps(result, ensure_ascii=False, indent=2))
    if proc.returncode != 0:
        raise Blocked(f"H_NORMAL_CHAT_FAILED_EXIT_{proc.returncode}; evidence={result_path}")
    if not host or host["actual_tools"] != selection["tools"]:
        raise Blocked("HOST_SELECTION_NOT_OBSERVED")
    final = next((event for event in events if event.get("type") == "result"), None)
    if not final or not final.get("session_id") or final.get("exit_code") != 0:
        raise Blocked("NORMAL_H_CONVERSATION_NOT_CONFIRMED")
    project.write_artifact(binding, conversation + "-session-binding.json", json.dumps(dict(binding, hermes_session_id=final["session_id"])))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsule", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    try:
        result = execute(args.capsule, args.task, args.config)
        print(json.dumps({k: result[k] for k in ("project_id", "selection", "memory", "dhaf", "artifact")}, ensure_ascii=False))
    except (Blocked, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="BLOCKED", reason=str(exc))))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
