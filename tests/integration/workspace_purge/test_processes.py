"""The full transport exercises cooperation across actual interpreter processes."""
import json
from pathlib import Path
import subprocess
import sys


def test_five_stdio_processes_cooperate_and_keep_independent_workspace():
    script = Path(__file__).resolve().parents[3] / "scripts" / "verify_workspace_purge.py"
    result = subprocess.run([sys.executable, "-B", str(script)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["stopped_instances"] == 4
    assert report["process_and_lease_cleanup"]
