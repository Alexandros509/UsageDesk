[한국어](README.md) | [**English**](README.en.md)

<p align="center">
  <img src="src/usagedesk/assets/usagedesk-icon.png" width="92" alt="UsageDesk icon">
</p>

# UsageDesk

**AI usage limits and your favorite programs and files, in one Windows toolbar.**

UsageDesk is a Windows desktop utility that shows Claude, Codex and Grok usage and reset times, and opens programs, HTML pages and documents from the same bar.

Windows 11 · Python 3.13 · PySide6 · **0.1.0a16 / Alpha** · [MIT](LICENSE)

![UsageDesk toolbar in dark mode](docs/images/toolbar-dark.png)

> Screenshots show real application widgets with **synthetic example data**. They contain no real account usage, personal file paths or credentials. Percentages show remaining quota.
>
> The application UI and screenshots are currently in Korean. This page provides English documentation; it does not switch the application's language. Korean labels are included below to help you find the controls.

## Features

- **Three services in one place** — View limits for Claude, Codex associated with a ChatGPT account, and a Grok CLI account.
- **Used or remaining quota** — Switch both percentages and rings together.
- **Time until reset** — Separate the quota window from the countdown, for example `7d │ 초기화 23hr 44min` (`초기화` means reset). Hover for the exact reset date, time and fetch status.
- **Floating, top-docked, bottom-docked or tray-only** — Drag the floating bar, reserve desktop work area with a docked bar, or keep only the tray icon.
- **Launch programs and files** — Register EXE, BAT and Python scripts, HTML pages, PDFs, documents and images.
- **Two-line program names** — Adjust spacing and font size, use spare width, then put overflow items in a `+N` menu.
- **Display preferences** — Choose size, colors, light/dark appearance, visible quotas and programs.

Inspired by [Usage4Claude](https://github.com/f-is-h/Usage4Claude), this is an independent Windows application written in Python/Qt. It is not an official Windows port and is not affiliated with Anthropic, OpenAI or xAI.

## Screenshots

**Light mode**

![Light mode](docs/images/toolbar-light.png)

**More items — a single-row bar with a `+N` overflow menu**

![Overflow handling in a narrow bar](docs/images/toolbar-overflow.png)

<details>
<summary><strong>Display settings</strong></summary>
<p><img src="docs/images/display-settings.png" width="490" alt="Display settings for quota mode, reset countdown and placement"></p>
</details>

<details>
<summary><strong>Program and file registration</strong></summary>
<p><img src="docs/images/file-launcher.png" width="850" alt="File launcher with example HTML, Python and PDF entries"></p>
</details>

## Getting started

You can run from source or build a **Windows x64 portable alpha**. No public Release has been published yet, and no signed installer is provided. Binary distributions are prepared with an EXE ZIP, a corresponding source ZIP and SHA256 checksums. See the [distribution and licensing guide](DISTRIBUTION.md) (Korean).

Requirements: **Windows 11 x64** and [uv](https://github.com/astral-sh/uv).

Download and extract the repository, or clone it, then open PowerShell in the project directory:

```powershell
.\setup.ps1
.\run.cmd
```

`setup.ps1` installs Python 3.13 and the dependencies locally using `uv.lock`. After setup, you can also double-click `run.cmd`.

Click a gauge to open the details window. Selecting tray-only mode under display settings → placement hides both the bar and the details window, and persists across restarts. Choose `사용량 바 표시` (Show usage bar) from the tray menu to restore the previous placement and position, including top/bottom docking. Closing the details window returns to the bar, or leaves the app in the tray when tray-only mode is enabled. To exit completely, choose `⋯ → 종료` (Exit) on the bar or `종료` in the tray menu.

```powershell
# Start with the details window visible
.\.venv\Scripts\python.exe -m usagedesk.app --window

# Use a separate settings directory for testing
.\.venv\Scripts\python.exe -m usagedesk.app --data-dir .dev-data
```

## Connecting accounts

**Claude / Codex**

1. In the details window's `사용량` (Usage) tab, select the service's `로그인 / 다시 연결` (Sign in / Reconnect).
2. Select `브라우저 로그인 시작` (Start browser sign-in), then sign in and authorize in your browser.
3. If automatic connection fails, follow the app's instructions to enter the callback URL for the current sign-in attempt. Claude also offers an alternative sign-in flow.

Codex values represent **Codex usage limits associated with your ChatGPT account**, not the remaining message allowance for all regular ChatGPT conversations.

**Grok**

Connect the default personal account of the official [Grok Build CLI](https://github.com/xai-org/grok-build). The default executable path is `%USERPROFILE%\.grok\bin\grok.exe`. Open CLI sign-in from the Grok connection dialog, then select `현재 CLI 계정 연결` (Connect current CLI account). If the CLI token expires, sign in through the CLI and reconnect.

Grok shows the account's combined usage limit. It does not sum per-conversation token counts or API spending.

These integrations are experimental and depend on each service's authentication and response formats. Service changes, account types or plans may limit authentication or usage retrieval.

## Usage tips

### Quotas and reset times

- Choose used or remaining quota in `표시 설정 → 비율 기준` (Display settings → Percentage basis).
- Enable `초기화까지` (Time until reset) to show the quota type and countdown on the second line.
- `5h` and `7d` identify the **quota window**; text after the separator is the **time until reset**.
- If the server supplies no reset time, the bar shows `시간 미제공` (Time not provided). After the reported time passes, it shows `초기화 확인 중` (Checking reset).
- Usage is normally fetched about every three minutes. Manual refresh also respects server wait periods. Errors or stale values are marked with `!` and explained in the tooltip.

**What if Claude's weekly quota is exhausted but its five-hour quota still has capacity?**

While the account-wide weekly quota is exhausted, the five-hour gauge is adjusted to **0% subscription quota remaining / 100% exhausted**, with `5h │ 주간 한도 소진` (Weekly quota exhausted). The tooltip retains the original server value and explains the adjustment. Normal display resumes when a response reports available weekly quota. Extra-usage activation is indicated separately.

### Custom hotkeys

Open the **단축키 설정** (Hotkey settings) tab in the details window or select it from the tray menu. All shortcuts are unassigned and disabled by default.

- Assign separate combinations for toggling the bar, opening usage details, opening the program menu and refreshing usage.
- Click an input, press Ctrl or Alt with a letter, digit or F1–F11, then select **저장** (Save). Shift may also be included.
- Duplicate combinations, Windows registration failures or file-write failures preserve the previous configuration. The Windows key and F12 are excluded.
- Actions are suppressed while the hotkey settings window is active or a modal dialog is open. Holding a key does not repeatedly trigger its action.
- Hotkeys work in tray mode and are restored on restart. Toggling the bar preserves its previous placement and position.
- Uncheck `전역 단축키 사용` (Enable global hotkeys) and save to disable all shortcuts. Individual clearing and a reset-to-defaults option are also available.
- If another application has already registered a combination, registration may fail at startup. The hotkey settings page shows the reason.

<details>
<summary>Hotkey settings screenshot</summary>

![Hotkey settings](docs/images/hotkey-settings.png)

</details>

### Registering programs and files

Choose a file under `내 프로그램 → 추가` (My programs → Add), or drag it onto the window. Registration alone does not run it. Choose which entries appear on the bar under `표시 설정 → 내 프로그램` (Display settings → My programs).

- Executables: `.exe`, `.com`, `.cmd`, `.bat`
- Scripts and tools: `.py`, `.pyw`, `.ps1`, `.jar`, `.vbs`, `.vbe`, `.js`, `.jse`, `.wsf`, `.wsh`, `.msc`, `.hta`, `.ahk`
- Shortcuts: `.lnk`, `.url`, `.appref-ms`
- General files: HTML, PDF, Office documents, text, images, music, video and more. Use `모든 파일` (All files) for extensions not listed in the picker.

Python scripts prefer the project's `.venv`. Required interpreters and file associations must be installed on your PC. `.js` files run through Windows Script Host. General files open in their Windows default application.

Long names use up to two lines. When space runs out, the bar **reduces padding → reduces the font by one step → uses spare bar width → moves overflow to `+N`**. Short names remain on one line.

## Privacy and local storage

- Application data is stored in `%LOCALAPPDATA%\UsageDesk`.
- Claude and Codex credentials are encrypted with Windows DPAPI for the current user, with restricted file permissions.
- Grok usage requests read the current access token from the CLI's authentication file. UsageDesk does not separately copy, store or refresh that token; it stores only the connection preference.
- Requests go directly to the respective service. UsageDesk has no collection server or analytics telemetry.
- Disconnecting removes the local connection. It does not revoke server tokens or sign out of the Grok CLI.

Do not attach tokens, callback URLs, authentication files or screenshots containing personal paths to reports. See [SECURITY.md](SECURITY.md) for details.

## Development and verification

```powershell
.\scripts\test.ps1
.\build.ps1
```

Current validation: **258 automated tests** (including 51 bar tests), Ruff and Windows EXE startup/shutdown checks. Coverage includes switching bar lengths back and forth, preserving reset-time display, top/bottom docking and restoration from tray mode. This does not guarantee operation across every account, plan or PC environment.

Build output is written to `dist\0.1.0a16\UsageDesk\`. **Move the entire folder, not just the EXE.** Python/Qt notices and library replacement instructions are included. Run `python scripts/package_release.py` to produce binary and corresponding source ZIPs with checksums. This is an unsigned alpha build.

Regenerate README screenshots with the following command. It uses only an isolated directory and synthetic data:

```powershell
.\.venv\Scripts\python.exe scripts/render_readme.py
```

### Scope and limitations

- Windows only. Top/bottom docking uses a separate AppBar; it does not insert the bar into the existing Windows taskbar.
- UNC paths, network drives, reparse paths and folder registration are not supported.
- Applications opened through file associations may not support process tracking or duplicate-launch prevention.
- Automatic startup, quota alerts, settings import/export, an enterprise proxy settings UI and an installer are not implemented.
- Not every combination of mixed-DPI monitors, full-screen applications and authentication expiration/refresh has been tested in real environments.

## References and acknowledgments

- **[f-is-h/Usage4Claude](https://github.com/f-is-h/Usage4Claude)** — Reference for the menu-bar monitor concept, Claude/Codex authentication protocols and response models. MIT notice preserved.
- **[xai-org/grok-build](https://github.com/xai-org/grok-build)** — Reference for personal CLI account authentication and the billing response protocol. Apache-2.0 notice included.
- **[lobehub/lobe-icons](https://github.com/lobehub/lobe-icons)** — Source of the Claude, OpenAI and Grok SVG logos. MIT notice included.

Pinned commits, reference files and dependency notices are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Service names and marks belong to their respective owners. Codex uses the OpenAI knot logo, and its values refer to Codex limits.

## License

UsageDesk's own code is provided under the [MIT License](LICENSE). Third-party code, logos and dependencies retain their respective licenses. The app icon was prepared from an AI-generated concept supplied by the project owner, with a transparent background and multiple ICO resolutions.
