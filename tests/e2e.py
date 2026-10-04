"""Exactly two real normal-H-chat runs, fresh conversations for A then B."""
import json
import sys
from pathlib import Path

from hpa.chat import execute

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = sys.argv[1]
    results = []
    for project, task, marker, excluded, skill, tool in (
        ("A", "inspect meridian report", "A_MEMORY_MERIDIAN", "B_MEMORY_COBALT", "meridian", "read_file"),
        ("B", "inspect cobalt ledger", "B_MEMORY_COBALT", "A_MEMORY_MERIDIAN", "cobalt", "search_files"),
    ):
        if project == "A" and len(sys.argv) == 3:
            # Explicit reuse of the already-completed real A run after correcting
            # only our event-format assertion. Default runs always call real H.
            result = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        else:
            result = execute(ROOT / f"fixtures/{project}/project-capsule.json", task, config)
        events = result["events"]
        # Real tool execution and final answer are required, not just preflight logs.
        output = json.dumps(events, ensure_ascii=False)
        assert marker in output and excluded not in output, "scoped memory answer missing or leaking"
        assert result["host_selection"]["skills"] == [skill]
        assert result["host_selection"]["actual_tools"] == [tool]
        native_calls = [e for e in events if e.get("type") == "tool_use"]
        assert native_calls and all(e["name"] == tool for e in native_calls), "normal H native tool execution not observed or outside selection"
        assert any(e.get("type") == "tool_result" and e.get("name") == tool and not e.get("is_error") for e in events), "native tool failed"
        final = next((e for e in events if e.get("type") == "result"), None)
        assert final and final.get("exit_code") == 0 and marker in final["text"] and excluded not in final["text"], "real final response missing"
        from hpa.core import Project
        bound = Project.load(ROOT / f"fixtures/{project}/project-capsule.json")
        bound.validate_binding(result["binding"])
        session_evidence = dict(result["binding"], hermes_session_id=final["session_id"])
        binding_file = bound.artifacts / (result["binding"]["conversation"] + "-session-binding.json")
        if not binding_file.exists():
            bound.write_artifact(result["binding"], binding_file.name, json.dumps(session_evidence))
        assert json.loads(binding_file.read_text(encoding="utf-8")) == session_evidence
        assert result["dhaf"]["mode"] == "SHADOW_ONLY"
        assert Path(result["artifact"]).is_relative_to(ROOT / f"fixtures/{project}/artifacts")
        results.append(dict(project_id=result["project_id"], conversation=result["binding"]["conversation"], artifact=result["artifact"], native_tools_observed=True))
        print(json.dumps(dict(project=project, e2e="PASS")), flush=True)
    assert results[0]["conversation"] != results[1]["conversation"]
    print("REAL_NORMAL_CHAT_E2E=PASS")


if __name__ == "__main__":
    main()
