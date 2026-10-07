"""Produce paired portable/source archives from an explicit public-file allowlist."""

import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def public_files():
    names = (".gitignore", ".gitattributes", "README.md", "README.en.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
             "DISTRIBUTION.md", "SECURITY.md", "pyproject.toml", "uv.lock", "setup.ps1",
             "build.ps1", "run.cmd")
    files = {ROOT / name for name in names}
    for pattern in ("src/**/*.py", "src/**/*.svg", "src/**/*.png", "src/**/*.ico",
                    "src/**/README.md", "tests/**/*.py", "scripts/*.py", "scripts/*.ps1",
                    "packaging/*.py", "packaging/*.json", "licenses/**/*", "docs/images/*.png"):
        files.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(files)


def digest(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def package():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    output = ROOT / "dist" / version
    bundle = output / "UsageDesk"
    if not (bundle / "UsageDesk.exe").is_file():
        raise ValueError("Build and smoke-test this version first")
    inventory = [{"path": p.relative_to(bundle).as_posix(), "size": p.stat().st_size,
                  "sha256": digest(p)} for p in sorted(bundle.rglob("*"))
                 if p.is_file() and p.name != "BUNDLE-INVENTORY.json"]
    (bundle / "BUNDLE-INVENTORY.json").write_text(json.dumps(inventory, indent=2) + "\n", "utf-8")
    binaries = output / f"UsageDesk-{version}-windows-x64.zip"
    sources = output / f"UsageDesk-{version}-sources.zip"
    with zipfile.ZipFile(binaries, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(bundle.rglob("*")):
            if p.is_file():
                archive.write(p, "UsageDesk/" + p.relative_to(bundle).as_posix())
    with zipfile.ZipFile(sources, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in public_files():
            archive.write(p, "UsageDesk/" + p.relative_to(ROOT).as_posix())
        manifest = json.loads((ROOT / "packaging/sources.json").read_text("utf-8"))
        for row in manifest:
            p = ROOT / ".release-cache/sources" / row["file"]
            if digest(p) != row["sha256"]:
                raise ValueError(f"Source hash mismatch: {row['id']}")
            archive.write(p, "third-party-sources/" + row["file"], compress_type=zipfile.ZIP_STORED)
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(p)}  {p.name}\n" for p in (binaries, sources)), "utf-8")
    for path in (binaries, sources):
        with zipfile.ZipFile(path) as archive:
            if archive.testzip():
                raise ValueError("Corrupt release archive")
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    package()
