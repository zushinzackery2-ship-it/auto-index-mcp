"""Compact agent responses with complete reports available as resources."""
import time

from ...runtime.control.contracts import SUCCESS_STATES


def build_response(root, status, enabled, started, targets, results=(), blockers=(), record=None):
    removed = sum(row["status"] in ("removed", "unregistered") for row in results)
    result = dict(format="auto_index_purge_v1", operation_id=record["operation_id"] if record else None,
                  status=status, complete=status in SUCCESS_STATES, root=str(root), enabled=enabled,
                  selected_indexes=len(targets), removed_indexes=removed,
                  remaining_indexes=len(targets) - removed,
                  deleted_bytes=sum(row.get("deleted_bytes", 0) for row in results),
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  results=list(results), blockers=list(blockers))
    return result


def compact(result):
    response = dict(result)
    response["results"] = result["results"][:50]
    response["blockers"] = result["blockers"][:50]
    response["participants"] = result.get("participants", [])[:50]
    response["details_truncated"] = any(len(result.get(key, [])) > 50 for key in ("results", "blockers", "participants"))
    operation_id = result.get("operation_id")
    if operation_id and result.get("report_path"):
        response["details_resource"] = f"purge://{operation_id}/0"
    return response
