# UsageDesk Windows 배포 안내

UsageDesk 자체 코드는 MIT입니다. 이 프로그램은 Qt / PySide6 / shiboken6
6.11.2를 동적 링크하여 사용하며, 해당 라이브러리에는 **LGPL-3.0**이 적용됩니다.
MIT가 포함 라이브러리의 라이선스를 대체하지 않습니다.

## 받는 파일

- `UsageDesk-<version>-windows-x64.zip`: 압축을 풀고 UsageDesk.exe를 실행합니다.
  `_internal` 폴더를 포함한 전체 폴더를 함께 유지하세요.
- `UsageDesk-<version>-sources.zip`: 동일 버전의 프로젝트 소스, 빌드 스크립트,
  QtBase / QtSvg / PySide 및 기타 의존성의 원본 소스 아카이브입니다.
- `SHA256SUMS.txt`: 두 ZIP의 무결성 확인용 해시입니다.

배포자는 위 파일을 같은 Release에 함께 게시해야 합니다. 외부 링크만으로
소스 제공을 대신하지 않습니다. 현재 버전은 서명하지 않은 Windows x64 알파
빌드입니다. SmartScreen 평판이나 코드 서명을 획득했다고 주장하지 않습니다.

## 라이선스와 교체 권리

EXE 옆의 `licenses/`에 원본 라이선스와 저작권 고지가 있습니다.
Qt LGPL 전문은 `licenses/bundled/qtbase/LICENSES/LGPL-3.0-only.txt`,
이를 보완하는 GPL 전문은 같은 폴더의 `GPL-3.0-only.txt`입니다.
각 Qt 소스의 `qt_attribution.json`과 참조 고지도 보존했습니다. 소스 트리의
보수적인 고지 모음에는 이 Windows 빌드에 사용되지 않은 구성 요소도 포함됩니다.

사용자는 LGPL 라이브러리를 수정·교체하고 그 수정 사항을 디버깅하기 위해
프로그램을 역공학할 수 있습니다. UsageDesk는 이러한 권리를 제한하지 않으며,
교체를 차단하는 서명 검사·DRM·라이브러리 해시 강제를 사용하지 않습니다.

1. 배포 폴더를 복사하고 UsageDesk를 종료합니다.
2. sources ZIP의 QtBase, QtSvg, PySide 소스 아카이브를 풉니다.
   각 프로젝트에 포함된 빌드 문서를 따라 MSVC 2022 x64, CMake/Ninja 및
   CPython 3.13에 호환되는 **shared / release** 라이브러리를 빌드합니다.
   PySide 소스에는 shiboken도 들어 있습니다. Qt 6.11.2 / PySide 6.11.2
   ABI와 Python 3.13 x64 구성을 맞추세요.
3. 복사한 배포 폴더의 `_internal/PySide6/Qt6*.dll`, 관련 플러그인 및
   필요하면 PySide/shiboken 바인딩을 호환되는 수정 빌드로 교체합니다.
4. 복사본의 EXE를 실행합니다. 또는 sources ZIP의 프로젝트에서
   `setup.ps1`을 실행하고 수정 라이브러리를 가상환경에 설치한 뒤
   `run.cmd`로 실행하거나 `build.ps1`로 다시 패키징합니다.

배포본에 포함된 Qt/PySide 소스는 수정하지 않았습니다. 전체 앱 소스와
패키징 절차도 제공하므로 라이브러리 교체 후 재빌드할 수 있습니다.
동일 바이트의 재현 빌드나 임의 버전 간 ABI 호환성을 보장하지 않습니다.

## 구성 및 배포자 점검

`packaging/runtime-inventory.json`에 Python 패키지 버전,
`packaging/sources.json`에 원본 URL·버전·SHA256을 기록합니다.
EXE의 `BUNDLE-INVENTORY.json`에는 최종 포함 파일의 크기와 해시가 있습니다.
Python, OpenSSL, pywin32, HTTP 라이브러리, certifi(MPL-2.0), Microsoft 런타임
등의 고지도 `licenses/bundled/`에 포함합니다. Microsoft 런타임은 Python/Qt가
제공한 재배포 파일이며 별도의 Microsoft 조건을 유지합니다.
PyInstaller의 GPL 예외는 생성된 앱을 자체 라이선스로 배포하도록 허용합니다.

`python scripts/prepare_distribution.py`는 원본 해시를 확인하고 고지를 수집합니다.
`build.ps1`로 빌드·실행 검사 후 `python scripts/package_release.py`로 두 ZIP과
체크섬을 생성합니다. 버전/의존성을 바꾸면 원본 매니페스트와 고지를 다시
검토해야 합니다. Qt 소프트웨어 OpenGL DLL, 사용하지 않는 이미지 플러그인,
번역과 pytest/pygments는 배포본에서 제외합니다.

계정 설정, 토큰, 실행 기록, 실제 사용량 데이터는 ZIP에 넣지 않습니다.
프로젝트 소스는 명시적인 파일 목록으로 묶으며 사용자 데이터 폴더는 읽지 않습니다.
배포 고지는 법률 자문이나 제3자의 보증을 의미하지 않습니다.

참고: [Qt의 LGPL 의무 안내](https://www.qt.io/development/open-source-lgpl-obligations),
[PyInstaller 라이선스 및 예외](https://pyinstaller.org/en/stable/license.html).
