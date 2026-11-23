"""Account for MCP path arguments and filesystem callers, including delegated admission."""
import ast
from pathlib import Path
import subprocess


PATH_ENTRIES = {
    "g6_review_patch", "g6_review_until_clean", "g6_premortem", "g6_confirm_behaviour",
    "g6_mission_create", "g6_mission_submit", "g6_improve_start", "g6_improve_stop", "g6_improve_status",
    "g6_improve_enrol", "g6_improve_resume", "g6_improve_demote", "g6_knowledge_add",
    "g6_workflow_export", "g6_workflow_correct", "g6_workflow_delete", "g6_suggestion_decide", "g6_feedback",
}
# Each request route is exercised in test_learning_fu2_admission.py. The knowledge
# walker uses storage.contained's existing name gate; its route has a separate probe.
# Fixed package/state paths and already admitted roots are recorded as well, so a
# new filesystem caller cannot silently bypass this review.
FILESYSTEM_OWNERS = {
    "behaviour.py": {"_review_candidates", "enforced", "confirm"},
    "catalogue.py": {"<module>", "main", "shipped"},
    "cloud_consent.py": {"_source_function"},  # inspect.Signature.replace, no filesystem access
    "improve.py": {"_log", "_environment_path", "_cannot_act", "_stop_entry_on_record", "_stop_before",
                   "_is_a_loop", "_pinned_python", "_settings", "_import_path", "status"},
    "mission.py": {"register", "_here", "work"},  # _here: str.replace
    "registrations.py": {"guidance", "package_root", "inputs", "server_sources", "identifiers"},
    "review_binding.py": {"write", "matches"},
    "review_loop.py": {"_asked", "_step"},
    "review_reply.py": {"save", "record", "carried"},
    "workflow.py": {"feedback", "_project"},
}
FS_CALLS = {"resolve", "is_dir", "is_file", "exists", "stat", "lstat", "open", "mkdir", "read_text", "read_bytes",
            "write_text", "write_bytes", "iterdir", "scandir", "unlink", "replace", "rename", "rmdir", "fdopen"}


def test_mcp_filesystem_and_path_argument_census():
    root = Path(__file__).parents[2]
    files = subprocess.run(["git", "ls-files", "src/g6/mcp/*.py"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    entries, owners = set(), {}
    for rel in files:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("g6_"):
                if any(arg.arg in {"repo", "folder", "path"} for arg in node.args.args):
                    entries.add(node.name)
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in FS_CALLS:
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            owners.setdefault(Path(rel).name, set()).add(owner.name if owner else "<module>")
    assert entries == PATH_ENTRIES
    assert owners == FILESYSTEM_OWNERS
