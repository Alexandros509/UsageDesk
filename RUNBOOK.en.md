[한국어](RUNBOOK.md) | [**English**](RUNBOOK.en.md)

# UsageDesk release runbook

Prepare, verify and publish a Windows x64 portable release, then recover if an update fails.
Run commands in PowerShell at the repository root. Adding or pushing this document does not publish a GitHub Release.

## 1. Freeze the release source

- Prepare the development environment with `setup.ps1` on Windows 11 x64. Skip this if it is already installed.
- For application changes, align the version in `pyproject.toml`, `uv.lock`, `src/usagedesk/__init__.py` and both READMEs. Do not replace an already published version's ZIP with different contents.
- Update Korean and English documentation, examples and limitations. Generate screenshots with `scripts/render_readme.py --language ko` and `--language en`, using synthetic data instead of real accounts.
- If dependencies changed, review `packaging/sources.json`, the runtime inventory and third-party notices. See [DISTRIBUTION.md](DISTRIBUTION.md) (Korean) for the distribution requirements maintained by this project.
- Complete the publication review below, commit using Conventional Commits, push, and identify the exact release commit.

```powershell
git status --short
git log -1 --oneline
$releaseCommit = git rev-parse HEAD
$releaseVersion = & .\.venv\Scripts\python.exe -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read release version' }
$releaseDir = Join-Path 'dist' $releaseVersion
```

Do not proceed with a dirty working tree or a target commit that has not been pushed.

## 2. Review public files and privacy

```powershell
git ls-files
git check-ignore -- language.json hotkeys.json display.ini config.json secrets/claude.dpapi .test-data/example.json
```

- Inspect tracked files and commit diffs for tokens, passwords, callback URLs, real account identifiers, personal paths/email addresses and real usage screenshots. These commands alone are not a content audit.
- `.gitignore` does not remove already tracked files. Remove accidentally tracked private files from the index before publication. Revoke and replace credentials that were already exposed; follow [SECURITY.md](SECURITY.md).
- `public_files()` in `scripts/package_release.py` is the source ZIP allowlist. Do not add authentication files, local settings, logs, `.test-data` or live user directories.
- `%LOCALAPPDATA%\UsageDesk` and `%USERPROFILE%\.grok` are not distribution inputs. Keep private settings backups out of public archives too.

## 3. Run automated checks and build

```powershell
.\scripts\test.ps1
if (-not $?) { throw 'Tests or lint failed' }
.\build.ps1
if (-not $?) { throw 'Build or packaged smoke test failed' }
git status --short
```

`test.ps1` runs pytest and Ruff. `build.ps1` verifies dependency source hashes, prepares notices, builds the required Qt components into the EXE bundle, and checks startup/shutdown with isolated settings.
If the build modifies tracked files, review and commit those changes, update the target commit, and rebuild. Do not package a failed build.

## 4. Verify EXE behavior

Use a unique test directory that cannot change real user settings.

```powershell
$smokeData = Join-Path (Get-Location) ('.test-data\release-' + [guid]::NewGuid().ToString('N'))
$releaseExe = Join-Path $releaseDir 'UsageDesk\UsageDesk.exe'
& $releaseExe --data-dir $smokeData --window
```

- Confirm that the gear button and `⋯ → Settings` open the same management window.
- Check Korean/English switching and restart, Light/Dark window themes, and readable menus and dropdowns.
- Switch bar widths back and forth; confirm top/bottom/floating placement survives tray mode and restart.
- Check quota selection, hiding entire services, used/remaining percentages, reset times and hover details, long program names and the `+N` menu.
- Register and open sample files; check hotkey assignment, conflicts and clearing. Do not use files containing personal information.
- If live account verification is needed, use a separately authorized account. Confirm missing values are not treated as 0% usage, previous data is labeled, and a valid response restores normal display. Keep test credentials out of distribution files.
- Exit fully with `⋯ → Exit`, then verify restart and second-instance handoff. The window's X button is not a full exit.

## 5. Generate corresponding sources and checksums

```powershell
& .\.venv\Scripts\python.exe scripts/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Get-Content (Join-Path $releaseDir 'SHA256SUMS.txt')
Get-FileHash -Algorithm SHA256 -LiteralPath `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-windows-x64.zip"), `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-sources.zip")
```

Compare the calculated hashes with `SHA256SUMS.txt` and extract both ZIPs to separate directories for inspection.

- EXE ZIP: include the executable, `_internal`, `licenses`, distribution notices and `BUNDLE-INVENTORY.json`.
- Source ZIP: include matching application sources, build scripts, bilingual documentation and corresponding dependency archives under `third-party-sources`.
- Run the extracted EXE with clean test settings. Stop publication for archive failures, missing notices/sources or included private data.

## 6. Create and publish the GitHub Release

The following command creates a **draft only**. If the tag or Release already exists, inspect its state first rather than overwriting it.

```powershell
gh auth status
if ($LASTEXITCODE -ne 0) { throw 'GitHub authentication required' }
gh release create "v$releaseVersion" `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-windows-x64.zip") `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-sources.zip") `
  (Join-Path $releaseDir 'SHA256SUMS.txt') `
  --repo Alexandros509/UsageDesk --target $releaseCommit `
  --title "UsageDesk $releaseVersion" --draft --prerelease `
  --notes 'Draft: add reviewed Korean and English release notes before publishing.'
if ($LASTEXITCODE -ne 0) { throw 'Release draft creation failed' }
```

Write reviewed Korean and English notes covering changes, installation/update instructions and known limitations.
Recheck the target commit, version, three attached files, hashes, notices and corresponding sources.
Identify the build as an unsigned alpha, then explicitly publish it on GitHub after review.
Treat documentation pushes and Release publication as separate operations.

## 7. Replace an installation and roll back

1. Exit through `⋯ → Exit`. Stop replacement if executable files are still locked.
2. Back up the existing application folder under a dated/versioned name. Before moving or deleting files, verify resolved absolute paths remain inside the intended installation location.
3. Install the **entire UsageDesk folder** from the new ZIP. Do not replace only the EXE or mix `_internal` files from different versions.
4. Preserve `%LOCALAPPDATA%\UsageDesk` and the Grok CLI authentication directory. Verify language, placement, hotkeys and connection state.
5. If the update fails, exit the new app and restore the previous application folder. If the old version cannot read a newer settings format, keep private settings intact and investigate with an isolated `--data-dir`.
6. Record the version, commit, verification results, hashes and known issues in the Release record. Do not include credentials or personal paths.
