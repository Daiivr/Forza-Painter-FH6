<p align="center">
  <img src="docs/screenshots/forza-painter-fh6-showcase.png" alt="Forza-Painter FH6 쇼케이스">
</p>

# Forza-Painter FH6

**Forza Horizon 6용 비닐 가져오기 도구.** 이미지를 Forza 호환 비닐 geometry로 변환하고, 결과를 미리 본 뒤, 하나의 데스크톱 앱에서 FH6 Vinyl Group Editor로 가져옵니다.

<p>
  <a href="README.md">English</a> |
  <a href="README.es-ES.md">Español</a> |
  <a href="README.es-MX.md">Español MX</a> |
  <a href="README.zh-CN.md">中文</a> |
  <a href="README.ko-KR.md">한국어</a>
</p>

<p>
  <code>v1.8.4</code> <code>Windows</code> <code>Forza Horizon 6</code> <code>GPU/OpenCL</code> <code>단일 파일 EXE</code>
</p>

## 주요 기능

Forza-Painter FH6는 현재 FH6 비닐 작업 흐름에 맞춰 만들어졌습니다.

- PNG, JPG, BMP 이미지에서 geometry JSON을 생성합니다.
- 가져오기 전에 생성된 JSON을 미리 봅니다.
- geometry JSON을 그룹 해제된 FH6 비닐 템플릿으로 가져옵니다.
- 앱 내 Market에서 커뮤니티 프리셋을 둘러보고 다운로드합니다.
- Region Paint로 중요한 영역을 더 자세하게 다듬습니다.
- 연구용으로 실험적인 full-shape/type-code JSON을 내보내고 가져옵니다.
- 로그, 런타임 데이터, 미리보기, 가져오기 진단을 로컬 앱 폴더에 보관합니다.

일반 사용자는 [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases)에서 EXE를 다운로드하면 됩니다. 프로젝트를 개발하지 않는다면 Python, 가상 환경, 소스 ZIP은 필요 없습니다.

## 현재 앱

아래 스크린샷은 현재 `v1.8.4` 데스크톱 UI에서 캡처했습니다.

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-generate-json-current.png" alt="JSON 생성 화면">
      <strong>JSON 생성</strong><br>
      원본 이미지를 추가하고, 품질 프리셋을 고르고, 생성 설정과 진행 상황을 확인합니다.
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-import-current.png" alt="가져오기 화면">
      <strong>가져오기</strong><br>
      FH6 프로세스를 선택하고, 정확한 템플릿 레이어 수를 입력하고, JSON을 미리 본 뒤 가져옵니다.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-region-paint-current.png" alt="Region Paint 화면">
      <strong>Region Paint</strong><br>
      기본 패스를 만든 뒤 핵심 영역을 선택하고, 디테일이 필요한 곳에 레이어를 더 사용합니다.
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-full-shapes-current.png" alt="내보내기 화면">
      <strong>내보내기</strong><br>
      full-shape JSON 연구를 위한 실험적인 FH6 shape word 내보내기/가져오기 도구입니다.
    </td>
  </tr>
</table>

## 게임 내 작업 흐름

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/fh6-template-ready.png" alt="FH6 템플릿 준비됨">
      <strong>템플릿 준비</strong><br>
      FH6 Vinyl Group Editor를 열고 sphere 레이어 템플릿을 불러온 뒤 그룹을 해제합니다.
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-import-result.png" alt="FH6 가져오기 결과">
      <strong>JSON 가져오기</strong><br>
      앱이 편집 가능한 레이어 테이블을 찾아 디자인을 쓰는 동안 편집기를 열린 상태로 유지합니다.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-import-preview.png" alt="앱 JSON 미리보기">
      <strong>미리보기 확인</strong><br>
      JSON 미리보기는 레이어 확인에 유용하지만, 최종 기준은 게임 안의 결과입니다.
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-car-applied.png" alt="FH6 차량 적용 결과">
      <strong>차량에 적용</strong><br>
      가져오고 저장한 뒤에는 다른 FH6 디자인처럼 비닐 그룹을 사용하면 됩니다.
    </td>
  </tr>
</table>

## 빠른 시작

1. [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases)에서 `forza-painter-fh6-v1.8.4.exe`를 다운로드합니다.
2. EXE를 `Desktop\forza-painter-fh6` 같은 일반 쓰기 가능 폴더에 둡니다.
3. EXE를 실행합니다. Windows 프로세스 권한 때문에 가져오기가 실패하면 관리자 권한으로 실행하세요.
4. FH6에서 `Create Vinyl Group` / `Vinyl Group Editor`를 엽니다.
5. sphere 템플릿을 불러오고 그룹을 해제한 뒤, 게임에 표시되는 정확한 레이어 수를 기록합니다.
6. Forza-Painter FH6에서 JSON을 생성하거나 추가하고, FH6 프로세스를 선택하고, 템플릿 레이어 수를 입력한 뒤 가져옵니다.

## JSON 생성

JSON 생성 페이지는 내장 GPU/OpenCL 생성기를 사용해 이미지를 Forza 친화적인 geometry 파일로 변환합니다.

1. 이미지를 하나 이상 추가합니다.
2. 품질 프리셋을 선택합니다.
3. 필요하면 `품질 설정`을 열어 레이어 수, 해상도, 무작위 샘플 등 고급 값을 조정합니다.
4. 생성을 시작하고 미리보기와 로그가 갱신될 때까지 기다립니다.
5. 템플릿에 맞는 가장 높은 레이어 JSON을 사용합니다.

생성된 파일은 원본 이미지 옆에 저장됩니다. 한 이미지에서 `image.500.json`, `image.1000.json`, `image.3000.json` 같은 checkpoint와 최종 `image.json`이 만들어질 수 있습니다.

## JSON 가져오기

가져오기 페이지는 생성된 geometry를 현재 FH6 Vinyl Group Editor 세션에 씁니다.

- 가져오기 전에 FH6 템플릿은 그룹 해제되어 있어야 합니다.
- 앱에 입력한 레이어 수는 게임과 정확히 일치해야 합니다.
- 가져오는 동안 FH6를 Vinyl Group Editor에 유지하세요.
- 앱이 스캔하거나 쓰는 동안 메뉴를 전환하지 마세요.
- Windows가 프로세스 접근을 막으면 앱을 관리자 권한으로 다시 시작하세요.

FH6는 저장과 적용 범위를 올바르게 처리하기 위해 몇 개의 추가 경계 레이어가 필요합니다. 예를 들어 1000 레이어 JSON은 최소 1004 레이어 템플릿을 사용해야 하며, 3000 레이어 템플릿은 보통 약 2996개의 그릴 수 있는 레이어를 남깁니다.

## Region Paint

Region Paint는 특정 부분에 더 많은 디테일이 필요한 이미지에 사용합니다. 첫 패스를 생성하고, 사각형이나 타원을 선택한 뒤 선택한 영역에만 추가 레이어를 사용합니다.

현재 도구:

- 첫 패스와 영역 패스 레이어 예산.
- 사각형 및 타원 선택.
- 드래그, 크기 조절, 회전, 마우스 휠 컨트롤.
- 미리보기와 히트맵 탭.
- 패스 기록, 남은 레이어 추적, 결과 JSON 내보내기.

## Market

가져오기 페이지에는 painter6.com 프리셋을 위한 앱 내 Market 버튼이 있습니다. 디자인을 둘러보고, 선택한 프리셋을 미리 보고, geometry JSON을 다운로드한 뒤 가져오기 목록에 바로 추가할 수 있습니다.

## 내보내기

내보내기는 실험적인 기능입니다. 일반 생성기가 만든 ellipse geometry가 아니라, 내보낸 FH6 type-code JSON이나 직접 만든 JSON을 위한 기능입니다.

- 레이어 오프셋 `0x7A`의 16비트 FH6 shape word를 사용합니다.
- 위치, 크기, 회전, 기울기, 색상, mask/banner 데이터, shape word 같은 안정적인 시각 필드를 내보냅니다.
- `0xA8` 같은 변동성 리소스 포인터는 복사하지 않습니다.
- 가능할 때 포함된 FH6 비닐 리소스로 미리보기를 생성합니다.

일반 생성 geometry JSON은 표준 가져오기 페이지를 사용하세요.

## 런타임 폴더

단일 파일 EXE는 내부 파일을 임시로 추출하고, 일반 앱 데이터를 EXE 옆에 씁니다.

- `runtime/`: 로그, 생성 미리보기, Region Paint 세션, Market 다운로드, 임시 파일.
- `webui-data/`: 로컬 설정과 FH6 probe/session 캐시.

앱을 닫은 상태에서 이 폴더들을 삭제하면 로컬 런타임 데이터를 초기화할 수 있습니다.

## 문제 해결

- **가져오기가 시작되지 않음:** 앱을 관리자 권한으로 실행하고 FH6 편집기가 열려 있는지 확인하세요.
- **템플릿을 찾을 수 없음:** 템플릿 그룹을 해제하고 정확한 레이어 수를 입력한 뒤 스캔 중에는 편집기에 머무르세요.
- **결과가 흐릿함:** 출력 레이어와 `Random samples`를 높이세요. `200000` 이상은 보통 최종 선명도를 개선합니다.
- **미리보기가 FH6와 다름:** 현재 JSON 미리보기는 근사치입니다. FH6는 앱 미리보기가 단순화하는 소수점 ellipse 크기를 유지합니다.
- **GPU/OpenCL 오류:** NVIDIA, AMD 또는 Intel 그래픽 드라이버를 업데이트하세요.
- **디버깅 도움이 필요함:** `상세 로그 내보내기`를 사용하고 issue를 열 때 로그를 첨부하세요.

## 개발

소스 실행은 주로 개발과 테스트용입니다.

```powershell
install_dependencies.bat
start_app.bat
```

유용한 프로젝트 파일:

- `src/app.py`: 데스크톱 UI와 작업 흐름.
- `src/generator_backend.py`: 생성기 명령/빌드 통합.
- `src/import_readiness.py`: 가져오기 전 검사.
- `src/region_painter/`: Region Paint 작업 흐름 모듈.
- `scripts/make_exe_release.ps1`: 릴리스 패키징.
- `CHANGELOG.md`: 전체 버전 기록.

## 리소스

- Releases: [github.com/Daiivr/Forza-Painter-FH6/releases](https://github.com/Daiivr/Forza-Painter-FH6/releases)
- 가져오기 walkthrough 비디오: [bilibili.com/video/BV1hG5Z6nENZ](https://www.bilibili.com/video/BV1hG5Z6nENZ)
- 포함된 GPU 생성기 참고: [zjl88858/forza-painter-geometrize-gpu](https://github.com/zjl88858/forza-painter-geometrize-gpu)
- 전체 changelog: [CHANGELOG.md](CHANGELOG.md)

## 라이선스

[LICENSE](LICENSE), [LICENSE.custom-importer](LICENSE.custom-importer), [LICENSE.kloudys-custom-importer](LICENSE.kloudys-custom-importer)를 참고하세요.
