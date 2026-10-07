"""Build with deterministic DLL lookup before PyInstaller imports its hooks."""

import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from prepare_distribution import prepare

root = Path(__file__).resolve().parents[1]
os.chdir(root)
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
if not re.fullmatch(r"[0-9A-Za-z.-]+", version):
    raise ValueError("Invalid build version")
destination = root / "dist" / version
prepare()
if not destination.resolve().is_relative_to((root / "dist").resolve()):
    raise ValueError("Build output must remain within dist")
windows = Path(os.environ["WINDIR"])
os.environ["PATH"] = os.pathsep.join(
    str(path) for path in (Path(sys.executable).parent, windows / "System32", windows)
)
os.environ["PYINSTALLER_CONFIG_DIR"] = str(root / ".pyinstaller-cache")

from PyInstaller.__main__ import run  # noqa: E402

run(
    [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--exclude-module", "pytest",
        "--exclude-module", "_pytest",
        "--exclude-module", "pygments",
        "--name",
        "UsageDesk",
        "--icon",
        "src/usagedesk/assets/usagedesk.ico",
        "--distpath",
        str(destination),
        "--paths",
        "src",
        "--add-data",
        "src/usagedesk/assets;usagedesk/assets",
        "packaging/entry.py",
    ]
)

# Keep the raster-widget application and smoke-test backend, not optional Qt stacks.
bundle = destination / "UsageDesk"
qt = bundle / "_internal" / "PySide6"
plugins = {"qgif.dll", "qico.dll", "qjpeg.dll", "qsvg.dll", "qsvgicon.dll",
           "qwindows.dll", "qoffscreen.dll", "qmodernwindowsstyle.dll",
           "qnetworklistmanager.dll", "qschannelbackend.dll"}
for path in qt.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(qt)
    if (path.name == "opengl32sw.dll" or "translations" in relative.parts
            or ("plugins" in relative.parts and path.name not in plugins)):
        if not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("Refusing to trim outside the built bundle")
        path.unlink()
shutil.copytree(root / "licenses", bundle / "licenses", dirs_exist_ok=True)
for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "DISTRIBUTION.md"):
    shutil.copy2(root / name, bundle / name)
shutil.copytree(root / "packaging", bundle / "packaging", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("*.py", "__pycache__"))
