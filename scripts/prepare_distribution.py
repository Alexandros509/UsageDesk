"""Collect pinned source notices; never read account data or extract archive paths."""

import hashlib
import importlib.metadata as metadata
import json
import posixpath
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / ".release-cache" / "sources"
NOTICES = ROOT / "licenses" / "bundled"


def write_notice(component, name, data):
    relative = Path(name)
    target = (NOTICES / component / relative).resolve()
    if not target.is_relative_to(NOTICES.resolve()):
        raise ValueError("Unsafe notice path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def prepare():
    manifest = json.loads((ROOT / "packaging" / "sources.json").read_text("utf-8"))
    SOURCES.mkdir(parents=True, exist_ok=True)
    for row in manifest:
        path = SOURCES / row["file"]
        if not path.exists():
            with urllib.request.urlopen(row["url"], timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError(f"Source checksum mismatch: {row['id']}")
            path.write_bytes(data)
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Source checksum mismatch: {row['id']}")
        with tarfile.open(path) as archive:
            members = {m.name: m for m in archive.getmembers() if m.isfile()}
            selected = set()
            for name in members:
                base = posixpath.basename(name).lower()
                if (base.startswith(("license", "licence", "copying", "copyright", "notice"))
                        or "/LICENSES/" in name or base == "qt_attribution.json"
                        or name.endswith("Doc/license.rst")):
                    selected.add(name)
                if base == "qt_attribution.json":
                    records = json.load(archive.extractfile(members[name]), strict=False)
                    for record in records if isinstance(records, list) else [records]:
                        files = record.get("LicenseFile", record.get("LicenseFiles", []))
                        for file in files if isinstance(files, list) else [files]:
                            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), file))
                            if resolved not in members:
                                raise ValueError(f"Missing attribution license: {resolved}")
                            selected.add(resolved)
            for name in sorted(selected):
                write_notice(row["id"], name.split("/", 1)[-1],
                             archive.extractfile(members[name]).read())
    inventory = []
    for name in ("PySide6_Essentials", "shiboken6", "httpx", "httpcore", "h11", "anyio",
                 "idna", "certifi", "typing_extensions", "packaging", "pywin32", "pyinstaller"):
        dist = metadata.distribution(name)
        notices = []
        for file in dist.files or []:
            if any(word in file.name.lower() for word in ("license", "copying", "notice")):
                path = Path(dist.locate_file(file))
                if path.is_file():
                    safe = str(file).replace("../", "parent/").replace("\\", "/")
                    write_notice(name, safe, path.read_bytes())
                    notices.append(safe)
        inventory.append({"name": name, "version": dist.version,
                          "license": dist.metadata.get("License-Expression") or
                          dist.metadata.get("License", "See bundled original notices"),
                          "notices": notices})
    write_notice("Python", "LICENSE.txt", (Path(sys.base_prefix) / "LICENSE.txt").read_bytes())
    (ROOT / "packaging" / "runtime-inventory.json").write_text(
        json.dumps(inventory, indent=2) + "\n", "utf-8")
    print(f"Verified {len(manifest)} source archives; notices prepared.")


if __name__ == "__main__":
    prepare()
