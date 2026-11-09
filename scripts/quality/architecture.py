"""Check dependency direction and ownership, independently of file length."""
import ast
from pathlib import Path

LOWER_LAYERS = {"storage", "languages", "workspace", "search", "quality"}
ENTRY_LAYERS = {"application", "core", "mcp_api", "commands"}
OWNED_FIELDS = {"store", "embedding_store", "root_path", "index_root", "session_lease", "enabled",
                "quiescing", "binding_generation", "mount_child_indexes", "background", "embedding_background"}
REVIEWED_RESPONSIBILITIES = {}


def architecture_findings(source: Path):
    findings = []
    package = source / "auto_index_mcp"
    if not package.is_dir():
        return findings
    for path in package.rglob("*.py"):
        relative = path.relative_to(package)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            reason = _violation(node, relative)
            if reason:
                findings.append(dict(path=path.relative_to(source).as_posix(), line=node.lineno, reason=reason))
    return findings


def _violation(node, relative):
    layer = relative.parts[0]
    if isinstance(node, ast.ImportFrom) and layer in LOWER_LAYERS:
        parts = list(relative.parts[:-1])
        base = parts[:len(parts) - node.level + 1] if node.level else []
        module = base + (node.module or "").split(".")
        if module and module[0] == "auto_index_mcp":
            module = module[1:]
        if module and module[0] in ENTRY_LAYERS:
            return "lower layer imports an application entry layer"
    targets = node.targets if isinstance(node, ast.Assign) else (
        [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
    for target in targets:
        for member in ast.walk(target):
            if (isinstance(member, ast.Attribute) and member.attr in OWNED_FIELDS
                    and isinstance(member.ctx, ast.Store) and isinstance(member.value, ast.Attribute)
                    and member.value.attr == "project"):
                return f"project.{member.attr} must be changed by its owner"
    return None


def responsibility_reviews(findings):
    return [dict(finding, state="reviewed" if finding["path"] in REVIEWED_RESPONSIBILITIES else "review-required",
                 reason=REVIEWED_RESPONSIBILITIES.get(finding["path"], "assess whether this file owns multiple responsibilities"))
            for finding in findings]
