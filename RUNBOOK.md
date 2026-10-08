[**한국어**](RUNBOOK.md) | [English](RUNBOOK.en.md)

# UsageDesk 배포 런북

Windows x64 포터블 배포를 준비·검증·게시하고 문제가 생겼을 때 복구하는 절차입니다.
명령은 저장소 루트의 PowerShell에서 실행합니다. 이 문서를 추가하거나 Git에 푸시하는 것만으로 Release가 게시되지는 않습니다.

## 1. 배포할 소스 확정

- Windows 11 x64에서 `setup.ps1`로 개발 환경을 준비합니다. 이미 준비했다면 다시 실행할 필요가 없습니다.
- 기능 변경 시 `pyproject.toml`, `uv.lock`, `src/usagedesk/__init__.py`, 두 README의 버전을 맞춥니다. 이미 게시한 버전의 ZIP을 다른 내용으로 교체하지 않습니다.
- 한국어·영어 설명, 예시 화면, 알려진 제한을 최신 동작과 맞춥니다. 화면은 실제 계정 대신 `scripts/render_readme.py --language ko`와 `--language en`으로 생성합니다.
- 의존성을 바꿨다면 `packaging/sources.json`, 런타임 목록과 제3자 고지를 검토합니다. 배포 구성 기준은 [DISTRIBUTION.md](DISTRIBUTION.md)에 있습니다.
- 아래 공개 검수 후 변경을 Conventional Commits 형식으로 커밋·푸시하고, 배포 대상 커밋을 확정합니다.

```powershell
git status --short
git log -1 --oneline
$releaseCommit = git rev-parse HEAD
$releaseVersion = & .\.venv\Scripts\python.exe -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read release version' }
$releaseDir = Join-Path 'dist' $releaseVersion
```

작업 트리가 깨끗하지 않거나 원격에 대상 커밋이 없으면 다음 단계로 넘어가지 않습니다.

## 2. 공개 파일·개인정보 검수

```powershell
git ls-files
git check-ignore -- language.json hotkeys.json display.ini config.json secrets/claude.dpapi .test-data/example.json
```

- 추적 파일과 커밋 diff에 토큰, 비밀번호, 콜백 URL, 실제 계정 식별자, 개인 경로·이메일, 실제 사용량 화면이 없는지 확인합니다. 위 명령만으로 내용 검수가 끝나는 것은 아닙니다.
- `.gitignore`는 이미 추적 중인 파일을 제거하지 않습니다. 잘못 추적한 파일은 공개 전에 인덱스에서 제외합니다. 이미 노출된 자격 증명은 폐기·재발급하고 [SECURITY.md](SECURITY.md)를 따릅니다.
- `scripts/package_release.py`의 `public_files()`가 소스 ZIP의 허용 목록입니다. 인증 파일, 로컬 설정, 로그, `.test-data`, 실행 중인 사용자 폴더를 추가하지 않습니다.
- `%LOCALAPPDATA%\UsageDesk`와 `%USERPROFILE%\.grok`는 배포 입력이 아닙니다. 개인 설정 백업도 공개 ZIP에 넣지 않습니다.

## 3. 자동 검사와 빌드

```powershell
.\scripts\test.ps1
if (-not $?) { throw 'Tests or lint failed' }
.\build.ps1
if (-not $?) { throw 'Build or packaged smoke test failed' }
git status --short
```

`test.ps1`은 pytest와 Ruff를 실행합니다. `build.ps1`은 의존성 소스 해시·고지를 준비하고, 필요한 Qt 구성으로 EXE를 빌드한 뒤 격리된 설정으로 실행·종료를 검사합니다.
빌드가 추적 파일을 변경했다면 검수·커밋 후 대상 커밋을 다시 확정하고 빌드를 반복합니다. 실패를 무시하고 패키징하지 않습니다.

## 4. EXE 기능 확인

실제 사용자 설정을 건드리지 않는 고유한 시험 폴더를 사용합니다.

```powershell
$smokeData = Join-Path (Get-Location) ('.test-data\release-' + [guid]::NewGuid().ToString('N'))
$releaseExe = Join-Path $releaseDir 'UsageDesk\UsageDesk.exe'
& $releaseExe --data-dir $smokeData --window
```

- 톱니바퀴와 `⋯ → 설정`이 같은 관리 창을 여는지 확인합니다.
- 한국어/English 변경 후 재시작, 창 테마 밝게/어둡게 전환, 메뉴·드롭다운 가독성을 확인합니다.
- 바 길이를 왕복 변경하고, 상단·하단·떠 있는 배치가 트레이 전환 및 재실행 후 유지되는지 확인합니다.
- 한도 선택·서비스 전체 숨김, 사용량/잔량, 초기화 시간과 호버, 긴 프로그램 이름과 `+N` 메뉴를 확인합니다.
- 시험용 파일을 등록해 실행하고 단축키 지정·충돌·해제를 확인합니다. 개인정보가 있는 파일로 테스트하지 않습니다.
- 실제 계정 확인이 필요하면 별도로 승인한 계정으로 진행합니다. 값 누락이 사용량 0%로 바뀌지 않는지, 이전 데이터 안내·정상 응답 후 복구가 동작하는지 확인합니다. 시험 인증정보는 배포물에 넣지 않습니다.
- `⋯ → 종료`로 완전히 종료한 뒤 재실행과 중복 실행 전달을 확인합니다. 창의 X는 완전 종료가 아닙니다.

## 5. 대응 소스와 체크섬 생성

```powershell
& .\.venv\Scripts\python.exe scripts/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Get-Content (Join-Path $releaseDir 'SHA256SUMS.txt')
Get-FileHash -Algorithm SHA256 -LiteralPath `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-windows-x64.zip"), `
  (Join-Path $releaseDir "UsageDesk-$releaseVersion-sources.zip")
```

출력 해시를 `SHA256SUMS.txt`와 비교하고 두 ZIP을 별도 폴더에 풀어 확인합니다.

- EXE ZIP: 실행 파일, `_internal`, `licenses`, 배포 고지와 `BUNDLE-INVENTORY.json`을 포함해야 합니다.
- 소스 ZIP: 같은 버전의 앱 소스·빌드 스크립트·양언어 문서와 `third-party-sources`의 대응 의존성 소스를 포함해야 합니다.
- 추출한 EXE를 깨끗한 시험 설정으로 실행합니다. ZIP 검사 실패, 누락된 고지·소스, 개인정보 포함이 있으면 게시를 중단합니다.

## 6. GitHub Release 초안과 게시

아래 명령은 **초안만 생성**합니다. 기존 태그/Release가 있으면 먼저 상태를 확인하고 덮어쓰지 않습니다.

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

초안에서 변경 사항·설치/업데이트 방법·알려진 제한을 한국어와 영어로 작성합니다.
대상 커밋, 버전, 첨부 파일 3개, 해시, 고지와 대응 소스를 재확인합니다.
서명하지 않은 알파 배포라는 점을 명시하고, 검토가 끝난 뒤 GitHub에서 명시적으로 게시합니다.
문서 푸시와 Release 게시를 별개 작업으로 취급합니다.

## 7. 설치 교체와 롤백

1. `⋯ → 종료`로 앱을 종료합니다. 실행 파일이 잠겨 있으면 교체를 중단합니다.
2. 기존 실행 폴더를 날짜·버전이 표시된 별도 폴더로 백업합니다. 이동·삭제 전 절대 경로가 의도한 설치 위치 안에 있는지 확인합니다.
3. 새 ZIP의 **전체 UsageDesk 폴더**를 설치합니다. EXE만 교체하거나 서로 다른 버전의 `_internal`을 섞지 않습니다.
4. `%LOCALAPPDATA%\UsageDesk`와 Grok CLI 인증 폴더는 유지합니다. 언어·배치·단축키·연결 상태를 확인합니다.
5. 문제가 생기면 새 앱을 종료하고 백업한 이전 실행 폴더로 복구합니다. 구버전이 새로운 설정 형식을 지원하지 않으면 개인 설정을 지우지 말고 격리된 `--data-dir`로 확인합니다.
6. 배포 버전·커밋·검증 결과·해시·알려진 문제를 Release 기록에 남깁니다. 인증정보나 개인 경로는 기록하지 않습니다.
