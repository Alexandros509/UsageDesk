# Third-party notices

UsageDesk is an independent Python/Qt implementation. These references do not
imply sponsorship or API compatibility guarantees.

## Usage4Claude — MIT

- Repository: https://github.com/f-is-h/Usage4Claude
- Reviewed commit: `e0030f1ada6014eadce3c58c785ecb4039a74e52`
- Reference: `Usage4Claude/Services/ClaudeOAuth/` and `CodexOAuth/`, API services and response models.
- Use: menu-bar usage-monitor concept, OAuth configuration/flow and quota protocol.
- Copyright (c) 2025-2026 f-is-h.
- Original license: [Usage4Claude-MIT.txt](licenses/Usage4Claude-MIT.txt).

## Grok Build — Apache-2.0

- Repository: https://github.com/xai-org/grok-build
- Reviewed commit: `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`
- Reference: [billing.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/extensions/billing.rs), CLI login configuration and credential storage.
- Use: Python adapter written against the CLI billing protocol and default personal OAuth credential format. No CLI executable or Rust source tree is included.
- Copyright 2023-2026 SpaceXAI, as stated in the referenced license.
- Original license: [Grok-Build-Apache-2.0.txt](licenses/Grok-Build-Apache-2.0.txt). No root NOTICE file was found at this commit.

## Lobe Icons — MIT

- Repository: https://github.com/lobehub/lobe-icons
- Reviewed commit: `82e641b4fece9d1028a127149af9ded00df5ac0c`
- Files: `packages/static-svg/icons/claude.svg`, `openai.svg`, `grok.svg`.
- Use: original SVG paths; white marks rendered on colored rounded tiles in the UI.
- Copyright (c) 2023 LobeHub.
- Original license: [Lobe-Icons-LICENSE.txt](licenses/Lobe-Icons-LICENSE.txt).
- Brand names and marks belong to their respective owners. The license does not imply endorsement.

## Runtime dependencies and compiled distribution

Dependency versions and hashes are pinned in [uv.lock](uv.lock). The source
checkout contains no installed dependencies or Python runtime. PySide6 Essentials
and shiboken6 expose LGPL/GPL alternatives in their package metadata; httpx and
pywin32 carry their own notices. UsageDesk's MIT license does not replace them.

The portable alpha bundle includes original notices in `licenses/bundled/` and a
prominent Qt/PySide LGPL-3.0 notice in its tray menu. See [DISTRIBUTION.md](DISTRIBUTION.md)
for source delivery and library replacement instructions. Paired release archives
include the exact pinned QtBase, QtSvg and PySide source distributions, plus other
runtime/build sources. [packaging/sources.json](packaging/sources.json) records URLs
and SHA256; [packaging/runtime-inventory.json](packaging/runtime-inventory.json)
records installed package versions. Conservative source notice sets may also list
components not compiled into this Windows bundle.

The bundle dynamically links QtCore, QtGui, QtWidgets, QtNetwork and QtSvg. It
excludes unused Qt image/platform/TLS plugins, translations and software OpenGL.
The CPython runtime is 3.13.15. Starting with 0.1.0a19, local Windows builds use
the signed Python Software Foundation distribution. Earlier builds used
python-build-standalone build 20260901. Redistribution notices, including
Microsoft runtime terms and conservative notices from earlier builds, are preserved.
PyInstaller's license includes an exception for generated applications. Windows
fonts used for screenshots are not bundled. Pillow is only an icon-authoring tool.

## Application icon and screenshots

The app icon is based on an AI-generated concept supplied by the project owner,
refined with imagegen for a transparent silhouette and packaged into ICO. The
project contribution is covered by the root license to the extent rights can be
granted; no exclusive rights in AI-generated output or service marks are asserted.

README screenshots render real Qt widgets with synthetic quota values, fictional
paths and an isolated data directory. They contain no real account credentials
or usage records. Regenerate with `scripts/render_readme.py`.
