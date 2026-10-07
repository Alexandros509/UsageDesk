"""Bounded package smoke test; never opens a console or touches user settings."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

sys.stdout.reconfigure(encoding="utf-8")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
name = sys.argv[1] if len(sys.argv) > 1 else "UsageDesk"
version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
executable = Path("dist") / version / name / f"{name}.exe"
data = Path(".test-data") / f"package-{uuid4().hex}"
process = subprocess.Popen(
    [str(executable.resolve()), "--data-dir", str(data.resolve()), "--smoke-ms", "800"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
try:
    output, _ = process.communicate(timeout=20)
    print(output.decode(errors="replace"))
    print(f"{name}: exit {process.returncode}")
    if process.returncode == 0 and (not data.is_dir() or (data / "instance.lock").exists()):
        print("Application startup or lock cleanup was not confirmed.")
        raise SystemExit(1)
    raise SystemExit(process.returncode)
except subprocess.TimeoutExpired:
    process.kill()  # Only the test process created above, never a registered program.
    output, _ = process.communicate()
    print(output.decode(errors="replace"))
    print(f"{name}: timeout")
    raise SystemExit(1) from None
