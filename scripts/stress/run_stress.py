"""Multi-process enable(rebuild=False) stress driver (service layer).

Usage: run_stress.py <scenario> [agents] [duration_seconds] [files]
  scenario A : embedding disabled (isolates index.db + BuildLock behavior)
  scenario B : real bundled ONNX embedding (full multi-agent storm)
  scenario K2: agent a0 wins the build lock then dies mid-build with a
               compressed 12s stale window - regression scenario for the
               dead-owner lock reclaim (production window was 120s)

Spawns N child_agent.py processes against one synthetic project, streams their
JSON output live, and reports per-agent worst-case enable latency. Any child
still alive past the global cap is killed and flagged HUNG with its
faulthandler stack (stderr log).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"


def build_project(root: Path, files: int = 600) -> None:
    for i in range(files):
        sub = root / f"pkg{i % 20}"
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


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "A"
    agents = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 35.0
    files = int(sys.argv[4]) if len(sys.argv) > 4 else 600

    tmp = Path(tempfile.mkdtemp(prefix=f"aidx_stress_{scenario}_"))
    proj = tmp / "proj"
    proj.mkdir()
    build_project(proj, files)
    print(f"[driver] scenario={scenario} agents={agents} duration={duration}s files={files} project={proj}", flush=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    if scenario == "B":
        env["AUTO_INDEX_EMBEDDING_MODEL"] = str(REPO / "models" / "minilm-onnx")
    else:
        env["AUTO_INDEX_EMBEDDING_MODEL"] = str(tmp / "no-model-here")
    if scenario == "K2":
        env["AIDX_STALE"] = "12"
        env["AIDX_KILL_AFTER_a0"] = "4"

    stats: dict[str, dict] = {}
    stats_lock = threading.Lock()

    def pump(name: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            print(f"[{name}] {line}", flush=True)
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            with stats_lock:
                entry = stats.setdefault(name, {"max_s": 0.0, "statuses": {}, "errors": []})
                if rec.get("op") == "enable":
                    entry["max_s"] = max(entry["max_s"], float(rec.get("seconds") or 0.0))
                    key = str(rec.get("status"))
                    entry["statuses"][key] = entry["statuses"].get(key, 0) + 1
                    if rec.get("error"):
                        entry["errors"].append(rec["error"])

    procs: list[tuple[str, subprocess.Popen, object]] = []
    threads: list[threading.Thread] = []
    for i in range(agents):
        name = f"a{i}"
        errlog = open(tmp / f"{name}.stderr.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [str(PY), str(Path(__file__).parent / "child_agent.py"), str(proj), name, str(duration)],
            stdout=subprocess.PIPE,
            stderr=errlog,
            text=True,
            encoding="utf-8",
            env=env,
        )
        procs.append((name, proc, errlog))
        thread = threading.Thread(target=pump, args=(name, proc), daemon=True)
        thread.start()
        threads.append(thread)
        time.sleep(0.25)

    cap = time.monotonic() + duration + 30.0
    hung: list[str] = []
    for name, proc, _ in procs:
        remaining = max(0.0, cap - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            hung.append(name)
            proc.kill()
    for thread in threads:
        thread.join(timeout=5.0)
    for _, _, errlog in procs:
        errlog.close()

    print("\n===== SUMMARY =====", flush=True)
    for name in sorted(stats):
        entry = stats[name]
        print(f"{name}: max_enable={entry['max_s']:.2f}s statuses={entry['statuses']} errors={entry['errors'][:3]}", flush=True)
    if hung:
        print(f"HUNG agents (killed at cap): {hung}", flush=True)
        for name in hung:
            log = (tmp / f"{name}.stderr.log").read_text(encoding="utf-8", errors="replace")
            print(f"--- {name} faulthandler stderr ---\n{log[-4000:]}", flush=True)
    else:
        print("no hung agents", flush=True)
    stderr_notes = []
    for name, _, _ in procs:
        text = (tmp / f"{name}.stderr.log").read_text(encoding="utf-8", errors="replace").strip()
        if text:
            stderr_notes.append((name, text))
    for name, text in stderr_notes:
        print(f"--- {name} stderr (non-empty) ---\n{text[-3000:]}", flush=True)
    print(f"[driver] artifacts in {tmp}", flush=True)
    if not hung and not stderr_notes:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
