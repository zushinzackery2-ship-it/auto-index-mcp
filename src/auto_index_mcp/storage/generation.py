from __future__ import annotations

import json
import time
import uuid

POLICY_KEYS = ("runtime_ignore_patterns", "auto_ignore_patterns", "privileged_ignore_patterns")


def metadata(conn) -> dict:
    return dict((row["key"], json.loads(row["value"])) for row in conn.execute("SELECT key,value FROM metadata"))


def set_value(conn, key, value) -> None:
    conn.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


def update_policy(conn, values: dict) -> int:
    current = metadata(conn)
    changed = any(key in values and list(values[key]) != current.get(key, []) for key in POLICY_KEYS)
    generation = int(current.get("policy_generation", 0)) + int(changed)
    for key in POLICY_KEYS:
        if key in values:
            set_value(conn, key, values[key])
    set_value(conn, "policy_generation", generation)
    return generation


def publish(conn, parser_version: int) -> None:
    current = metadata(conn)
    set_value(conn, "index_epoch", current.get("index_epoch") or uuid.uuid4().hex)
    set_value(conn, "source_generation", int(current.get("source_generation", 0)) + 1)
    set_value(conn, "applied_policy_generation", int(current.get("policy_generation", 0)))
    set_value(conn, "parser_version", parser_version)
    set_value(conn, "updated_at", time.time())


def revision(values: dict) -> str | None:
    if values.get("updated_at") is None:
        return None
    return f"{values.get('index_epoch', '')}:{values.get('source_generation', 0)}"
