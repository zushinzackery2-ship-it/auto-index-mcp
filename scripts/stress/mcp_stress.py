"""Full-stack stress: real `python -m auto_index_mcp.server` stdio processes
driven over JSON-RPC, N at once against one big synthetic project.

Measures wall time of initialize and every tools/call auto_index_enable
(rebuild=false). A call with no response within CALL_CAP seconds marks the
server HUNG; the driver then kills it and reports. This is the layer the
agents actually talk to, so a hang here == the user-visible symptom.

Usage: mcp_stress.py [servers] [files] [rounds] [scenario A|B]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
CALL_CAP = 45.0


def build_project(root: Path, files: int) -> None:
    for i in range(files):
        sub = root / f"pkg{i % 40}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"mod_{i}.py").write_text(
            f'"""module {i}"""\n\n'
            f"def func_a_{i}(x):\n"
            f"    if x > {i}:\n"
            f"        return helper_{i}(x)\n"
            f"    return x\n\n"
            f"def helper_{i}(x):\n"
            f"    total = 0\n"
            f"    for k in range(x):\n"
            f"        total += k\n"
            f"    return total\n\n"
            f"class Thing{i}:\n"
            f"    def method_one(self):\n"
            f"        return func_a_{i}({i})\n",
            encoding="utf-8",
        )


class McpClient:
    def __init__(self, name: str, env: dict, errlog_path: Path) -> None:
        self.name = name
        self.errlog = open(errlog_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(PY), "-m", "auto_index_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errlog,
            env=env,
            cwd=str(REPO),
        )
        self.pending: dict[int, threading.Event] = {}
        self.results: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.next_id = 0
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                continue
            with self.lock:
                self.results[msg_id] = msg
                event = self.pending.pop(msg_id, None)
            if event is not None:
                event.set()

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict, timeout: float) -> tuple[float, dict | None]:
        with self.lock:
            self.next_id += 1
            msg_id = self.next_id
            event = threading.Event()
            self.pending[msg_id] = event
        t0 = time.monotonic()
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        ok = event.wait(timeout)
        dt = time.monotonic() - t0
        if not ok:
            return dt, None
        with self.lock:
            return dt, self.results.pop(msg_id, None)

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
        self.errlog.close()


def call_status(response: dict | None) -> str:
    if response is None:
        return "TIMEOUT"
    if "error" in response:
        return f"rpc-error:{response['error'].get('message', '?')[:60]}"
    result = response.get("result") or {}
    if result.get("isError"):
        content = result.get("content") or [{}]
        return f"tool-error:{str(content[0].get('text', ''))[:80]}"
    structured = result.get("structuredContent") or {}
    return str(structured.get("status") or ("enabled" if structured.get("enabled") else "?"))


def drive(client: McpClient, proj: str, rounds: int, out: list[str]) -> None:
    dt, resp = client.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stress", "version": "0"},
        },
        CALL_CAP,
    )
    out.append(f"{client.name} initialize {dt:.2f}s {'ok' if resp else 'TIMEOUT'}")
    if resp is None:
        return
    client.notify("notifications/initialized")
    for round_no in range(rounds):
        dt, resp = client.request(
            "tools/call",
            {"name": "auto_index_enable", "arguments": {"root_path": proj, "rebuild": False}},
            CALL_CAP,
        )
        out.append(f"{client.name} enable#{round_no} {dt:.2f}s {call_status(resp)}")
        if resp is None:
            return
        time.sleep(1.0)


def main() -> None:
    servers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    files = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    scenario = sys.argv[4] if len(sys.argv) > 4 else "B"

    tmp = Path(tempfile.mkdtemp(prefix=f"aidx_mcp_{scenario}_"))
    proj = tmp / "proj"
    proj.mkdir()
    build_project(proj, files)
    print(f"[driver] servers={servers} files={files} rounds={rounds} scenario={scenario} project={proj}", flush=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    if scenario == "B":
        env["AUTO_INDEX_EMBEDDING_MODEL"] = str(REPO / "models" / "minilm-onnx")
    else:
        env["AUTO_INDEX_EMBEDDING_MODEL"] = str(tmp / "no-model-here")

    clients = [McpClient(f"s{i}", env, tmp / f"s{i}.stderr.log") for i in range(servers)]
    outputs: list[list[str]] = [[] for _ in clients]
    threads = [
        threading.Thread(target=drive, args=(client, str(proj), rounds, outputs[i]), daemon=True)
        for i, client in enumerate(clients)
    ]
    for thread in threads:
        thread.start()
        time.sleep(0.4)
    deadline = time.monotonic() + CALL_CAP + rounds * (CALL_CAP + 1.5)
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    print("\n===== RESULTS =====", flush=True)
    hung = False
    for lines in outputs:
        for line in lines:
            print(line, flush=True)
            if "TIMEOUT" in line:
                hung = True
    for client in clients:
        client.kill()
    print(f"[driver] hung={hung} artifacts={tmp}", flush=True)


if __name__ == "__main__":
    main()
