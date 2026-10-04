"""Per-process exact toolset then the unchanged normal Hermes chat main.

Called by Hermes's existing --run-module launcher. No source/config modifications.
"""
import json
import os
import sys
from pathlib import Path


def main():
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    from hpa.core import Project, Blocked
    project = Project.load(Path(payload["capsule"]))
    project.validate_binding(payload["binding"])
    selection = payload["selection"]
    if selection.get("project_id") != project.id or len(selection.get("skills", [])) != 1 or not set(selection["skills"]) <= set(project.data["allowed_skills"]):
        raise Blocked("HOST_SKILL_SELECTION_INVALID")
    from hpa.core import beneath
    beneath(Path(payload["query_file"]), project.artifacts)
    tools = payload["selection"]["tools"]
    if not tools or not set(tools) <= set(project.data["allowed_tools"]):
        raise Blocked("HOST_TOOL_SELECTION_INVALID")
    from toolsets import create_custom_toolset, BUILTIN_TOOL_NAMES
    if not set(tools) <= BUILTIN_TOOL_NAMES:
        raise Blocked("UNKNOWN_NATIVE_TOOL")
    create_custom_toolset("hpa-selected", "Ephemeral project selection", tools=tools)
    from model_tools import _select_tool_names
    actual = _select_tool_names(["hpa-selected"], [], True)
    if actual != set(tools):
        raise Blocked("HOST_SELECTION_MISMATCH")
    print(json.dumps({"event": "hpa_host_selection", "project_id": project.id, "skills": payload["selection"]["skills"], "actual_tools": sorted(actual)}), flush=True)
    os.chdir(project.root)
    # Prevent native private memory/rules and global Skill preload. Selected text
    # arrives through the ordinary user query, not through a global Skill install.
    sys.argv = ["hermes", "chat", "--cli", "--ignore-rules", "--in", str(project.root), "-t", "hpa-selected", "--query-file", payload["query_file"], "--oneshot", "--format", "stream-json", "--max-turns", "4", "--run-budget", "90"]
    from hermes_cli.main import main as hermes_main
    return hermes_main()


if __name__ == "__main__":
    sys.exit(main())
