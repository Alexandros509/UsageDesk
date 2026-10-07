# Service logos

Application icon: usagedesk-icon.png / usagedesk.ico, based on the user's Gemini
revision jy7cf4jy7cf4jy7c. Background extracted with the built-in imagegen tool;
multi-resolution ICO packaged with scripts/package_icon.py. Provenance, prompt,
and attribution: [Third-party notices](../../../THIRD_PARTY_NOTICES.md).
This is separate from the service logos.

Current vector source: lobehub/lobe-icons, commit 82e641b4fece9d1028a127149af9ded00df5ac0c.

- claude.svg: packages/static-svg/icons/claude.svg
- openai.svg: packages/static-svg/icons/openai.svg
- grok.svg: packages/static-svg/icons/grok.svg (white mark on a black tile)

MIT copyright (c) 2023 LobeHub; license in licenses/Lobe-Icons-LICENSE.txt.
Original SVG paths are preserved. The Qt UI renders the marks in white on rounded
orange (Claude) and dark teal (ChatGPT/OpenAI) tiles to match the user's reference.
Codex uses the ChatGPT/OpenAI knot mark as explicitly requested; the service label
and data still refer to Codex usage. No official affiliation is implied.

Replaced in 0.1.0a6: the prior dotted Claude and blue-clock Codex images came from
f-is-h/Usage4Claude commit e0030f1ada6014eadce3c58c785ecb4039a74e52,
Usage4Claude/Resources/Assets.xcassets/AppIconReverse.imageset/icon.reverse@2x.png
and CodexIconReverse.imageset/icon.codex.reverse@2x.png. They were a mistaken match
for the user's intended service logos and are no longer used.
