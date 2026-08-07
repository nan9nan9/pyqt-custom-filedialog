# filedialog-widget

`QFileDialog` 를 감싼 **경로 선택 위젯**입니다. 입력창 + 찾아보기 버튼 한 줄로
파일/폴더를 고르게 해 주며, `qtpy` 를 사용하여 **PyQt5 / PyQt6 / PySide2 / PySide6**
모두에서 동작합니다.

```
┌─────────────────────────────────────────────┐
│ 입력 파일: [/home/user/data.csv      ] [...] │
│ 출력 폴더: [/home/user/out           ] [...] │
└─────────────────────────────────────────────┘
        → [...] 클릭 시 QFileDialog 팝업
```

## 특징

- **네 가지 선택 모드** — `open_file`(파일 1개) · `open_files`(여러 개) ·
  `save_file`(저장할 이름) · `directory`(폴더). 모드에 맞는 `QFileDialog` 가 열립니다.
- **파이썬스러운 필터 지정** — `[("이미지", ["png", "jpg"])]` 처럼 쓰면
  `"이미지 (*.png *.jpg);;모든 파일 (*)"` 로 알아서 변환됩니다.
- **유효성 표시** — 존재하지 않는 경로/파일 자리에 폴더 등은 입력창 테두리가
  빨갛게 바뀌고 툴팁에 사유가 나옵니다. 모드별로 기준이 다릅니다
  (저장 모드는 "아직 없는 파일"이 정상).
- **드래그 & 드롭** — 파일 탐색기에서 끌어다 놓으면 경로가 채워집니다.
  폴더 모드에 파일을 떨어뜨리면 그 파일이 든 폴더로 받아 줍니다.
- **경로 자동완성** — 입력창에 직접 타이핑할 때 실제 파일시스템 기준으로 완성됩니다.
- **최근 경로 히스토리** — `history=10` 을 주면 `▾` 드롭다운이 생기고,
  `settings_key` 까지 주면 `QSettings` 에 저장되어 프로그램을 다시 켜도 유지됩니다.
- **똑똑한 시작 위치** — 다이얼로그는 *현재 값 → `start_dir` → 직전에 쓴 폴더 →
  현재 작업 디렉터리* 순으로 위치를 정합니다.
- **저장 확장자 자동 부착** — 저장 모드에서 확장자를 빼고 입력하면
  `default_suffix` 또는 선택된 필터의 확장자를 붙여 줍니다.
- **사이드바 커스터마이즈** — 다이얼로그 왼쪽 목록을 **홈 · 현재 위치 · 최근 파일 ·
  북마크**로 구성하거나, 원하는 폴더 목록으로 통째로 교체할 수 있습니다.
- **우클릭 메뉴** — 파일 목록에서 우클릭해 **즐겨찾기에 추가**,
  사이드바에서 우클릭해 분류 삭제 / 최근 목록 비우기 / 항목 제거.
- **최근 파일** — 최근에 고른 파일을 사이드바 항목 하나로 자동으로 모읍니다 (옵션).
- **즐겨찾기** — 흩어져 있는 **파일·폴더**를 분류별로 모아 사이드바에 **별표(★)** 로
  띄우고, 클릭 한 번으로 그 목록에서 바로 고릅니다 (`FavoritesStore`).
- **죽은 네트워크 경로 방어** — NFS 서버가 응답하지 않아도 GUI 가 멈추지 않도록
  마운트 판별 + 소켓 프로브 + 타임아웃을 조합합니다. **기본으로 켜져 있고**,
  로컬 경로에는 부담이 없습니다 (`path_timeout`).
- **나열하면 안 되는 자리 차단** — `/user` 처럼 아래에 마운트가 잔뜩 달린 경로를
  등록해 두면 그 자리는 열지 않고 하위 경로만 쓰게 합니다 (`guarded_roots`).
- **폼 헬퍼** — `FilePathForm` 으로 여러 줄을 라벨 정렬해 묶고 `values()` 로 한 번에 꺼냅니다.
- **패키지 / 소스 모두 사용 가능** — `pip install` 하거나 `src/` 를 그대로 import

## 설치

```bash
# 소스에서
pip install .

# Qt 바인딩까지 함께 (원하는 것 하나 선택)
pip install ".[pyqt5]"     # 또는 pyqt6 / pyside2 / pyside6
```

`qtpy` 만 필수 의존성이며, 실제 Qt 바인딩은 이미 설치된 것을 자동 사용합니다.
소스 그대로 쓰려면 `src/` 를 `sys.path` 에 추가하면 됩니다.

## 사용법

```python
from qtpy.QtWidgets import QApplication, QVBoxLayout, QWidget
from file_dialog_widget import FilePathEdit

app = QApplication([])
window = QWidget()
layout = QVBoxLayout(window)

# 파일 열기
edit = FilePathEdit(
    mode="open_file",
    label="입력 파일:",
    filters=[("CSV", ["csv"]), ("엑셀", ["xlsx", "xls"])],
    required=True,          # 비어 있으면 무효로 표시
)
layout.addWidget(edit)

edit.pathChanged.connect(lambda path: print("선택됨:", path))

window.show()
app.exec()

print(edit.path())          # "/home/user/data.csv"
print(edit.is_valid())      # True
```

데모 실행:

```bash
python examples/demo.py
```

기능별 상자를 **두 단으로 한 화면에** 펼쳐 놓았습니다(넘치면 스크롤).

| 왼쪽 | 오른쪽 |
| --- | --- |
| 선택 모드 · 필터 · 유효성 · 히스토리 · 폼 | 사이드바 · 즐겨찾기·최근 · 안전 |

"즐겨찾기 · 최근 파일" 상자에서는 두 기능을 **하나의 위젯에 함께 걸어** 실제 사용
형태 그대로 확인할 수 있고, 사이드바 순서가 실시간으로 표시됩니다. 맨 위 공통
옵션(네이티브 다이얼로그 / 드래그&드롭)은 모든 위젯에 한꺼번에 적용되고, 맨 아래
로그 창에서 시그널 발생을 확인할 수 있습니다(경계를 끌어 높이 조절).

## 선택 모드

| mode | 여는 다이얼로그 | 값 | 존재해야 유효? |
| --- | --- | --- | --- |
| `"open_file"` | `getOpenFileName` | 경로 1개 | O |
| `"open_files"` | `getOpenFileNames` | 경로 여러 개 (`paths()`) | O |
| `"save_file"` | `getSaveFileName` | 경로 1개 | X (상위 폴더만 있으면 됨) |
| `"directory"` | `getExistingDirectory` | 폴더 1개 | O |

```python
from file_dialog_widget import SelectMode

FilePathEdit(mode=SelectMode.DIRECTORY)   # 문자열 "directory" 와 동일
```

`open_files` 모드는 여러 경로를 한 줄에 `"; "` 로 이어서 표시하고,
`paths()` 로 리스트를 꺼냅니다. 경로에 `;` 가 들어갈 수 있는 환경이라면
`separator` 인자로 다른 구분자를 지정하세요.

## 필터 지정

`filters` 는 아래 어떤 형태로 줘도 됩니다.

```python
filters=[("이미지", ["png", "jpg"])]        # (설명, 확장자 목록)  ← 권장
filters=[("이미지", "*.png *.jpg")]         # (설명, 패턴 문자열)
filters={"이미지": ["png"], "문서": ["txt"]} # dict (선언 순서 유지)
filters=["*.png", "*.txt"]                  # 패턴만
filters="이미지 (*.png);;모든 파일 (*)"      # 이미 Qt 필터 문자열이면 그대로
```

확장자는 `"png"` / `".png"` / `"*.png"` 아무 형태나 됩니다.
기본적으로 `"모든 파일 (*)"` 이 목록 끝에 자동으로 붙으며,
`add_all_files_filter=False` 로 끌 수 있습니다.

## 주요 API

### `FilePathEdit`

| 메서드 / 프로퍼티 | 설명 |
| --- | --- |
| `.path()` / `.set_path(p)` | 경로 하나 읽기/쓰기 |
| `.paths()` / `.set_paths([...])` | 경로 리스트 읽기/쓰기 (`open_files` 용) |
| `.text()` | 입력창 원본 텍스트 |
| `.clear()` | 비우기 |
| `.browse()` | 다이얼로그를 직접 열기 (선택한 경로 리스트 반환, 취소 시 `[]`) |
| `.is_valid()` / `.invalid_reason()` | 유효 여부와 그 사유 |
| `.history_items()` | 최근 경로 목록 (최신순) |
| `.mode()` / `.set_mode(m)` | 선택 모드 |
| `.set_filters(f)` / `.name_filter()` | 필터 변경 / 실제 Qt 필터 문자열 확인 |
| `.set_start_dir(d)` · `.set_caption(c)` | 다이얼로그 시작 폴더 / 제목 |
| `.set_required(b)` · `.set_must_exist(b)` · `.set_validate_enabled(b)` | 유효성 기준 |
| `.set_native(b)` | OS 네이티브 다이얼로그 사용 여부 |
| `.set_path_timeout(t)` / `.path_timeout()` | 죽은 네트워크 경로 방어 |
| `.set_sidebar_urls([...])` / `.sidebar_urls()` | 다이얼로그 왼쪽 사이드바 항목 |
| `.set_fixed_sidebar_urls([...])` / `.fixed_sidebar_urls()` | 사이드바에서 제거 못 하게 할 위치 |
| `.effective_sidebar_urls()` | 즐겨찾기·최근 파일까지 합친 최종 사이드바 목록 |
| `.set_favorites(store)` / `.favorites()` | 즐겨찾기 저장소 지정 |
| `.set_recent_files(s)` / `.recent_files()` / `.recent_items()` | 최근 파일 저장소 / 목록 |
| `.set_favorites_icon(icon)` | 즐겨찾기 분류 아이콘 (`True`=별표 / `QIcon` / `False`) |
| `.set_read_only(b)` · `.set_drag_drop_enabled(b)` | 직접 편집 / 드롭 허용 |
| `.set_label(t)` · `.set_tooltip(t)` | 라벨 텍스트 / 입력창 기본 툴팁 |
| `.line_edit` · `.browse_button` · `.label_widget` | 내부 위젯 (스타일 커스터마이즈용) |

시그널:

| 시그널 | 발생 시점 |
| --- | --- |
| `pathChanged(str)` | 경로가 바뀔 때마다 (직접 입력 포함) |
| `pathsChanged(list)` | 위와 같으나 리스트로 전달 |
| `validityChanged(bool)` | 유효/무효 상태가 **바뀔 때만** |
| `browsed(list)` | 다이얼로그에서 실제로 선택을 마쳤을 때 |
| `editingFinished()` | 입력창 편집 종료 (Enter / 포커스 아웃) |

### 생성자 옵션

- `mode` : 선택 모드 (기본 `"open_file"`)
- `label` : 입력창 왼쪽 라벨. `None` 이면 라벨 없음
- `caption` : 다이얼로그 제목 (기본: 모드별 한국어 제목)
- `filters` / `add_all_files_filter` : 파일 필터 (위 "필터 지정" 참고)
- `default_suffix` : 저장 모드에서 확장자가 없을 때 붙일 확장자
- `start_dir` : 다이얼로그가 처음 열릴 폴더
- `placeholder` : 입력창 안내 문구 (기본: 모드별 문구)
- `button_text` / `button_icon` : 버튼 텍스트(기본 `"..."`) 또는 폴더 아이콘
- `must_exist` : 경로가 존재해야 유효한지 (`None` = 모드별 기본값)
- `required` : 비어 있는 것도 오류로 볼지 (기본 `False`)
- `validate` : 유효성 표시(테두리/툴팁) 사용 (기본 `True`)
- `drag_drop` : 드래그&드롭 (기본 `True`)
- `completer` : 경로 자동완성 (기본 `True`)
- `history` : 최근 경로 개수. `0` 이면 드롭다운 없음 (기본 `0`)
- `settings_key` / `settings` : `QSettings` 저장 키 / 저장소 인스턴스
- `native` : OS 네이티브 다이얼로그 사용 (기본 `True`)
- `path_timeout` : 죽은 네트워크 경로 방어 제한 시간(초). 기본 `1.0`(켜짐), `None`=끔
- `sidebar_urls` : 다이얼로그 왼쪽 사이드바 항목 (기본 `None` = 손대지 않음)
- `fixed_sidebar_urls` : 우클릭 "제거"를 막을 위치 (기본 `None` = 홈만 보호)
- `favorites` : `FavoritesStore`. 분류가 사이드바에 덧붙고 선택 경로가 자동 복원됨
- `favorites_icon` : 분류 아이콘. `True`=별표(기본) / `QIcon` / `False`=기본 폴더
- `recent_files` : 최근 파일 항목. `False`(기본) / `True` / `RecentStore`
- `recent_max` : `recent_files=True` 로 자동 생성할 때의 개수 (기본 20)
- `read_only` : 입력창 직접 편집 금지 (기본 `False`)
- `clear_button` : 입력창 안 X 버튼 (기본 `True`)
- `separator` : `open_files` 모드 구분자 (기본 `"; "`)

## 여러 줄을 폼으로 묶기 — `FilePathForm`

```python
from file_dialog_widget import FilePathForm

form = FilePathForm()
form.add_path("input",  "입력 파일:", mode="open_file",
              filters=[("CSV", ["csv"])], required=True)
form.add_path("outdir", "출력 폴더:", mode="directory", required=True)
form.add_path("extra",  "추가 파일:", mode="open_files")

form.add_row("메모:", QLineEdit())      # 경로가 아닌 위젯도 같은 정렬로 추가 가능

# 값 꺼내기
form.values()          # {"input": "...", "outdir": "...", "extra": ["...", "..."]}
form.value("input")    # 개별 접근
form.edit("input")     # 해당 FilePathEdit 자체

# 실행 전에 한 번에 검사
if not form.is_valid():
    for key, reason in form.invalid_items():
        print(key, reason)
```

`add_path(key, label, **kwargs)` 의 `**kwargs` 는 그대로 `FilePathEdit` 생성자로
전달됩니다. 라벨은 `QFormLayout` 이 정렬하므로 `label=` 을 따로 넘길 필요가 없습니다.

시그널: `valueChanged(str key, str path)`, `validityChanged(bool)`

## 사이드바(왼쪽 즐겨찾기) 커스터마이즈

### 항목 순서

즐겨찾기·최근 파일을 쓰면 사이드바가 이 순서로 구성됩니다:

```
홈  →  현재 위치  →  최근 파일  →  북마크 분류(이름순)
```

고정된 자리를 위에 두고, **계속 쌓이는 항목(최근 파일 · 북마크)을 맨 아래에**
붙입니다. 여기서 "북마크"는 즐겨찾기(`FavoritesStore`)입니다.

- **Computer 는 넣지 않습니다.** 경로가 없어 실제로 열어 볼 일이 거의 없는
  Qt 기본 항목입니다.
- **현재 위치**는 다이얼로그가 열리는 폴더입니다. 홈에서 열면 홈과 겹치므로
  하나만 남습니다.
- `sidebar_urls` 로 기준 목록을 직접 주면 **그 목록을 그대로 존중**합니다
  (홈·현재 위치를 끼워 넣지 않습니다). 최근 파일·북마크는 그 뒤에 붙습니다.

최종 결과는 `edit.effective_sidebar_urls()` 로 확인할 수 있습니다.

```python
edit = FilePathEdit(mode="open_file", favorites=store, recent_files=True)
edit.effective_sidebar_urls()
# [홈, 현재 위치, 최근 파일, 북마크/분류A, 북마크/분류B]
```

### 기준 목록 직접 지정

기본 구성 대신 원하는 폴더 목록을 쓸 수 있습니다.

```python
edit = FilePathEdit(
    mode="open_file",
    sidebar_urls=["~", "~/프로젝트", "/mnt/data"],   # 통째로 교체
)

# 실행 중 변경
edit.set_sidebar_urls(["/srv/입력", "/srv/출력"])
edit.set_sidebar_urls([])        # 사이드바 비우기
edit.set_sidebar_urls(None)      # 커스터마이즈 끄기
```

기존 항목을 남기고 뒤에 덧붙이려면:

```python
from file_dialog_widget import current_sidebar_urls

edit.set_sidebar_urls(current_sidebar_urls() + ["/mnt/data"])
```

경로 문자열과 `QUrl` 을 섞어 줄 수 있고, `~` 는 홈 디렉터리로 펼쳐집니다.
`current_sidebar_urls()` 는 Qt가 사이드바를 저장하는 설정 키를 **읽기만** 하므로
부작용이 없습니다(저장된 값이 없으면 Qt 기본값인 Computer + 홈을 돌려줍니다).
반환값의 `QUrl("file:")` 항목이 사이드바의 **Computer** 입니다.

> **꼭 알아 둘 두 가지**
>
> 1. **네이티브 다이얼로그에서는 불가능합니다.** OS가 그리는 창이라 Qt가 사이드바를
>    바꿀 수 없습니다. 그래서 `sidebar_urls` 를 지정하면 `native` 설정과 무관하게
>    **Qt 자체 다이얼로그**로 열립니다(정적 메서드 대신 `QFileDialog` 인스턴스를 사용).
> 2. **Qt가 사이드바를 영구 저장합니다.** 리눅스 기준 `~/.config/QtProject.conf` 의
>    `[FileDialog] shortcuts` 에 기록되어, 한 번 지정하면 프로그램을 다시 켜도,
>    나아가 같은 설정을 공유하는 **다른 Qt 앱에서도** 그 항목이 보입니다.
>    `set_sidebar_urls(None)` 로 되돌려도 이미 저장된 항목은 남습니다.
>    (사용자가 사이드바에 폴더를 끌어다 놓는 Qt 기본 동작과 같은 저장소입니다.)
>
>    같은 파일의 `sidebarWidth` 키가 있는 상태에서 `shortcuts` 만 없어지면
>    **사이드바가 빈 채로 뜹니다.** 설정을 손으로 손보다가 이 상태가 되면
>    `QSettings(QSettings.Scope.UserScope, "QtProject")` 로
>    `FileDialog/shortcuts` 에 `[QUrl("file:"), QUrl.fromLocalFile(QDir.homePath())]`
>    를 다시 써 주면 기본값(Computer, 홈)으로 돌아옵니다.
>
> 테스트에서 실제 사용자 설정을 건드리고 싶지 않다면 `QApplication` 생성 전에
> `QSettings.setPath()` 로 저장 위치를 임시 폴더로 돌리세요
> (`tests/test_basic.py` 상단 참고).

사이드바 항목의 **표시 이름은 폴더 이름 그대로**입니다 (`/mnt/data` → `data`).
Qt가 임의 라벨 지정을 지원하지 않으므로, 이름을 바꾸려면 그 이름의 심볼릭 링크를
만들어 그 경로를 넣는 방법을 씁니다.

## 즐겨찾기 — 파일·폴더를 분류별로 모아 두기

사이드바는 **디렉터리만** 받습니다(파일 URL 을 넣으면 Qt 가 조용히 버립니다).
그래서 `FavoritesStore` 는 분류마다 실제 폴더를 만들고 그 안에 대상들의
**심볼릭 링크**를 모읍니다. 분류가 사이드바에 뜨고, 클릭하면 오른쪽 목록에
등록해 둔 파일·폴더가 함께 나옵니다.

```python
from file_dialog_widget import FavoritesStore, FilePathEdit

store = FavoritesStore()                        # 앱 데이터 폴더 아래에 생성
store.add("설계", "/proj/a/설계도.csv")           # 파일
store.add("설계", "/proj/b/산출물")               # 폴더도 가능
store.add("보고서", "/proj/b/보고서.md")

edit = FilePathEdit(mode="open_file", favorites=store)
edit.browse()          # 사이드바에 "설계", "보고서" 가 추가됨
edit.path()            # -> /proj/a/설계도.csv  (링크가 아니라 원본 경로)
```

```
┌─────────────┬─────────────────────┐
│ 📁 jekai    │ 📁 산출물            │
│ 📁 작업      │ 📄 설계도.csv        │
│ ★ 보고서     │                      │
│ ★ 설계       │                      │
└─────────────┴─────────────────────┘
   ↑ 홈 · 현재 위치      ↑ 그 안의 링크들
     그 아래가 분류(별표)
```

즐겨찾기 분류는 폴더 아이콘 대신 **별표**로 표시되어 일반 폴더와 구분됩니다.

실제로 만들어지는 구조는 이렇습니다:

```
<base_dir>/
    설계/
        설계도.csv  ->  /proj/a/설계도.csv
        산출물      ->  /proj/b/산출물
    보고서/
        보고서.md   ->  /proj/b/보고서.md
    .index.json          # 링크 -> 원본 매핑 (하드링크 폴백용)
```

### `FavoritesStore` API

| 메서드 | 설명 |
| --- | --- |
| `FavoritesStore(base_dir=None, create=True, link_mode="auto")` | 저장소 생성 |
| `.add(category, path, name=None)` | 파일/폴더 등록 (만들어진 링크 경로 반환) |
| `.remove(category, path_or_name)` | 항목 제거 (원본은 그대로) |
| `.items(category)` / `.entries(category)` | 원본 경로 목록 / `(표시이름, 원본)` 목록 |
| `.categories()` / `.category_dir(name)` | 분류 목록 / 분류 폴더 경로 |
| `.add_category(name)` / `.remove_category(name)` | 분류 추가 / 삭제 |
| `.contains(category, path)` / `.link_for(category, path)` | 등록 여부 / 링크 경로 |
| `.clear(category=None)` | 분류 하나 또는 전체 비우기 |
| `.sidebar_urls()` | 분류 폴더들의 `QUrl` 목록 |
| `.resolve(path)` / `.resolve_all(paths)` | 링크 경로 → 원본 경로 복원 |

- **디렉터리 구조가 곧 저장소**라 별도 영속화가 필요 없습니다. 다음 실행에도 그대로 남습니다.
- 같은 대상을 다시 등록하면 중복을 만들지 않고 기존 링크를 돌려줍니다.
  이름만 겹치면 `설계도 (2).csv` 처럼 번호가 붙습니다.
- `remove` / `clear` 는 링크만 지웁니다. **원본 파일은 절대 건드리지 않습니다.**

### 저장 위치 정하기

분류 폴더가 만들어질 위치는 세 단계로 정해집니다.

```python
from file_dialog_widget import FavoritesStore, configure_favorites, default_base_dir

# 1) 저장소 하나만 다른 곳에 두기 — 생성자 인자가 가장 우선
store = FavoritesStore(base_dir="/srv/공용/즐겨찾기")

# 2) 앱 전체 기본 위치 — 시작할 때 한 번 부르면 이후 FavoritesStore() 가 모두 여기로
configure_favorites("~/문서/내앱-즐겨찾기")
store = FavoritesStore()          # -> ~/문서/내앱-즐겨찾기

# 3) 아무것도 안 하면 OS 표준 앱 데이터 폴더
default_base_dir()                # -> ~/.local/share/<조직>/<앱>/favorites
```

| 함수 | 설명 |
| --- | --- |
| `configure_favorites(path)` | 앱 전체 기본 위치 지정. `None` 이면 지정 해제. 적용된 위치를 반환 |
| `configured_base_dir()` | 위에서 지정한 위치 (지정 안 했으면 `None`) |
| `default_base_dir()` | 지금 `FavoritesStore()` 가 실제로 쓸 위치 |
| `store.base_dir` | 그 저장소가 쓰는 위치 |

- `~` 표기를 쓸 수 있고, 없는 폴더는 만들어 줍니다(`create=False` 로 끌 수 있음).
- 위치를 바꾸면 **그 위치의 즐겨찾기만** 보입니다. 기존 것을 옮기려면 폴더째
  복사하세요(링크 안의 대상 경로는 그대로 유효합니다).
- 실행 중에 바꾸려면 새 저장소를 만들어 갈아 끼우면 됩니다:
  `edit.set_favorites(FavoritesStore(base_dir=새경로))` — 데모의 "위치 변경..." 버튼이
  이 방식입니다.

### 별표 아이콘

분류에는 기본적으로 별표(★) 아이콘이 붙습니다. 외부 이미지 없이 `QPainter` 로
그리므로 별도 리소스 파일이 필요 없습니다.

```python
# 기본 = 별표
edit = FilePathEdit(mode="open_file", favorites=store)

# 다른 아이콘으로
edit = FilePathEdit(mode="open_file", favorites=store,
                    favorites_icon=QIcon("/path/to/icon.png"))

# 끄기 (Qt 기본 폴더 아이콘)
edit = FilePathEdit(mode="open_file", favorites=store, favorites_icon=False)

# 실행 중 변경
edit.set_favorites_icon(True)
```

색이나 크기를 바꾸려면 `star_icon()` 을 직접 부르면 됩니다:

```python
from file_dialog_widget import star_icon
edit.set_favorites_icon(star_icon(color="#1565c0", sizes=(16, 24, 32)))

# 별 크기 조절: inset 은 반지름에서 빼는 픽셀 수라 지름은 그 두 배만큼 작아진다
edit.set_favorites_icon(star_icon(inset=0))    # 픽스맵을 꽉 채움 (기본보다 2px 큼)
edit.set_favorites_icon(star_icon(inset=2))    # 기본보다 2px 더 작게
```

내부적으로는 `CategoryIconProvider` 가 `QFileDialog.setIconProvider()` 로 걸려,
**분류 폴더에만** 별표를 씌우고 나머지 경로는 Qt 기본 아이콘을 그대로 씁니다.
이 역시 네이티브 다이얼로그에서는 불가능하므로 Qt 자체 다이얼로그로 열립니다.

### 고른 항목의 실제 위치 보여 주기

분류 목록에 보이는 건 심볼릭 링크라, 그대로 두면 상단 "Look in" 에 저장 위치만
뜹니다. 그래서 **항목을 고르면 Qt 기본 콤보의 표시를 그 항목의 원본 경로로 바꿉니다.**

```
설계 › 설계도.csv 클릭  →  Look in: /proj/a/설계도.csv
설계 › 산출물 클릭      →  Look in: /proj/b/산출물
```

- **폴더를 옮기지 않습니다.** 목록은 분류에 그대로 머물러, 이어서 다른 항목을
  고를 수 있습니다.
- 콤보를 다른 위젯으로 갈아 끼우지 않고 **기존 콤보의 텍스트만** 바꿉니다.
- 폴더를 이동하면 Qt 가 콤보를 다시 채우므로 표시도 저절로 되돌아옵니다.

링크로 등록한 **폴더에 실제로 들어가면**(더블클릭) 원본 폴더로 옮겨 가므로,
그 뒤로는 평범한 실제 경로로 탐색이 이어집니다.

직접 걸려면:

```python
from file_dialog_widget import Places, install_hooks

places = Places(favorites=favorites, recent=recent)
install_hooks(dialog, places)   # 링크 추적 + 우클릭 메뉴 + 차단 경로 방어
```

`FilePathEdit` 은 이것을 자동으로 겁니다.

### 우클릭 메뉴

**파일 목록**에서 파일이나 폴더를 우클릭하면 맨 위에 `즐겨찾기에 추가 ▸` 가 붙습니다.
기존 분류를 고르거나 새 분류를 만들 수 있고, 그 아래에는 **Qt 기본 항목이 그대로**
따라붙습니다(이름 변경 · 삭제 · 숨김 파일 · 새 폴더).

```
설계도.csv 우클릭
 ├─ 즐겨찾기에 추가 ▸
 │    ├ 설계          ← 이미 등록돼 있으면 비활성
 │    ├ ─────
 │    └ 새 분류...     ← 이름을 물어보고 만든다
 ├─ ─────
 ├─ Rename / Delete
 └─ Show hidden files / New Folder
```

새 분류를 만들면 사이드바에도 바로 나타납니다. 분류 폴더 **안의 링크**에는 이
메뉴가 뜨지 않습니다(이미 등록된 것이므로).

**사이드바**에서 우클릭하면 항목 종류에 맞는 메뉴가 나옵니다. Qt 기본 메뉴("Remove")를
대신하되, 일반 항목에는 같은 제거 기능을 그대로 제공합니다.

```
★설계        우클릭 → "'설계' 즐겨찾기에서 삭제"   (분류 폴더째 제거)
🕘최근 파일    우클릭 → "'최근 파일' 목록 비우기"   (항목은 남기고 안만 비움)
끌어다 놓은 폴더 우클릭 → "사이드바에서 제거"        (목록에서만 빠짐)
jekai (홈)   우클릭 → "사이드바에서 제거" (비활성)  (기본 보호 위치)
```

**보호 위치** — 실수로 빼면 곤란한 항목은 `fixed_sidebar_urls` 로 잠급니다.
기본값(`None`)은 **사용자 홈만** 보호합니다.

```python
edit = FilePathEdit(mode="open_file")                                # 홈 보호(기본)
edit = FilePathEdit(mode="open_file", fixed_sidebar_urls=["~", "/srv/공용"])
edit = FilePathEdit(mode="open_file", fixed_sidebar_urls=[])         # 보호 없음
edit.set_fixed_sidebar_urls(["~"])                                   # 실행 중 변경
```

| 값 | 동작 |
| --- | --- |
| `None` (기본) | 사용자 홈만 제거 불가 (`Places().fixed_urls()`) |
| `[경로, …]` | 나열한 위치만 제거 불가 (홈을 지키려면 함께 넣습니다) |
| `[]` | 아무것도 보호하지 않음 (홈도 뺄 수 있음) |

어느 쪽이든 **원본 파일·폴더는 건드리지 않습니다**. 직접 제어하려면:

```python
from file_dialog_widget import FavoritesMenus, Places

places = Places(favorites=favorites, recent=recent, fixed_urls=None)  # None = 홈만 보호
menus = FavoritesMenus(dialog, places, confirm=True, add_menu=True)
menus.install()                       # 네이티브 다이얼로그면 False 를 반환
menus.favoriteAdded.connect(on_added)         # (분류, 경로)
menus.categoryRemoved.connect(on_removed)
menus.recentCleared.connect(on_cleared)
menus.sidebarEntryRemoved.connect(on_unpinned)   # 일반 항목을 사이드바에서 뺐을 때
menus.add_to_favorites("/proj/a/x.csv", "설계")   # 코드에서 직접 등록
```

`add_menu=False` 를 주면 파일 목록 메뉴는 건드리지 않고 사이드바 메뉴만 겁니다.

### 경로 복원

즐겨찾기에서 고르면 다이얼로그는 **링크 경로**를 돌려줍니다.
`FilePathEdit(favorites=store)` 는 이를 자동으로 원본으로 되돌리므로 `path()` 에는
항상 원본이 담깁니다. 직접 `exec_file_dialog()` 를 쓴다면 `places.resolve_all()` 을
한 번 통과시키세요. 즐겨찾기 폴더 밖의 경로는 그대로 통과하므로 일괄 적용해도 안전합니다.

### 플랫폼 주의

`link_mode="auto"`(기본)는 심볼릭 링크를 먼저 시도하고, 실패하면 **파일은 하드링크,
폴더는 정션(junction)** 으로 폴백합니다. 윈도우에서 심볼릭 링크는 개발자 모드나
관리자 권한이 필요할 수 있어서입니다. 모두 실패하면 `FavoritesError` 가 납니다
(이 저장소는 리눅스에서만 검증했습니다). 하드링크는 `realpath` 로 원본을 되찾을 수
없으므로 `.index.json` 에 기록해 둔 매핑으로 복원합니다.

## 최근 파일 — 사이드바에 자동으로 쌓기

즐겨찾기와 **같은 방식**(분류 폴더 + 심볼릭 링크)으로, 최근에 고른 파일을 모아 둔
`최근 파일` 항목을 사이드바에 하나 더 띄웁니다. 기본은 **꺼져 있고** 옵션으로 켭니다.

```python
from file_dialog_widget import FilePathEdit, RecentStore

# 1) 가장 간단하게 — 기본 위치에 저장소를 자동으로 만든다
edit = FilePathEdit(mode="open_file", recent_files=True, recent_max=20)

# 2) 저장소를 직접 만들어 여러 위젯이 같은 목록을 공유
recent = RecentStore(max_items=20)
edit_in = FilePathEdit(mode="open_file", recent_files=recent)
edit_out = FilePathEdit(mode="save_file", recent_files=recent)

edit.recent_items()          # 최신순 원본 경로 목록
edit.set_recent_files(False) # 실행 중 끄기
```

```
┌────────────┬─────────────────────────────┐
│ jekai      │ 📄 나.csv                    │   ← 홈
│ 작업        │ 📄 가.csv                    │   ← 현재 위치
│ 🕘최근 파일  │                             │   ← 시계 아이콘
│ ★설계       │                             │   ← 즐겨찾기(별표)
└────────────┴─────────────────────────────┘
```

- **고를 때마다 자동으로 기록**됩니다(`browse()` 성공 시). 직접 넣으려면 `recent.record(path)`.
- 같은 파일을 다시 고르면 중복되지 않고 **맨 앞으로 올라옵니다**.
- `max_items` 를 넘기면 **오래된 것부터** 지워집니다. 지워지는 건 링크뿐, 원본은 그대로입니다.
- **폴더는 기록하지 않습니다**(파일만). 즐겨찾기·최근 파일 안의 링크를 다시 고르면
  원본 경로로 복원해서 기록합니다.
- 사이드바에서 우클릭하면 **"목록 비우기"** 메뉴가 나옵니다 — 즐겨찾기의 "삭제"와
  달리 **항목 자체는 남습니다**.

### `RecentStore` API

`RecentStore` 는 `FavoritesStore` 를 물려받아 분류가 `최근 파일` 하나뿐인 저장소입니다.
그래서 사이드바 등록·경로 복원·아이콘·정리 메뉴가 모두 그대로 동작합니다.

| 메서드 | 설명 |
| --- | --- |
| `RecentStore(base_dir=None, name="최근 파일", max_items=20)` | 저장소 생성 |
| `.record(path)` / `.record_all(paths)` | 기록 (파일만, 이미 있으면 맨 앞으로) |
| `.items()` / `.entries()` / `.links()` | 원본 경로 / `(이름, 원본)` / 링크 경로 — 모두 최신순 |
| `.clear()` | 목록만 비우기 (사이드바 항목은 유지) |
| `.set_max_items(n)` | 개수 제한 변경 (넘치면 즉시 정리) |
| `.sidebar_urls()` / `.resolve(path)` | `FavoritesStore` 와 동일 |
| `default_recent_dir()` | 기본 저장 위치 (즐겨찾기 폴더 옆) |

순서는 **심볼릭 링크 자신의 수정 시각**(만든 시각)으로 판단합니다. 별도 상태 파일이
필요 없고, 다시 고르면 링크를 지웠다 새로 만들어 맨 앞으로 올립니다.

> 참고: 목록의 **순서는 `items()` 기준**입니다. 다이얼로그 오른쪽 파일 목록은 Qt 가
> 자기 정렬 기준(기본은 이름순)으로 보여 주므로 최신순으로 나오지 않습니다.
> 최신순으로 보려면 다이얼로그에서 "Date Modified" 열로 정렬하세요.

## 최근 경로 기억하기

```python
from file_dialog_widget import FilePathEdit, configure_settings

# 앱 시작 시 한 번 — settings_key 를 쓰는 위젯들이 공유할 저장 위치
configure_settings("회사이름", "앱이름")

edit = FilePathEdit(
    mode="open_file",
    history=10,                     # ▾ 드롭다운에 최근 10개
    settings_key="input_file",      # QSettings 에 저장 -> 재실행해도 유지
)
```

- `history` 만 주면 프로그램이 살아 있는 동안만 메모리에 유지됩니다.
- `settings_key` 를 주면 최근 경로와 **직전에 열었던 폴더**가 함께 저장되어,
  다음에 다이얼로그를 열 때 그 폴더에서 시작합니다.
- `QApplication` 에 `setOrganizationName()` / `setApplicationName()` 을 설정했다면
  `configure_settings()` 없이도 그 값이 쓰입니다.

## 죽은 네트워크 경로에서 멈추지 않기 (NFS 등)

NFS 하드 마운트에서 서버가 응답하지 않으면 `os.stat()` 이 커널 안에서
**중단 불가능 대기(D 상태)** 로 들어갑니다. 시그널로도 깨울 수 없어서 그 호출을 한
스레드는 마운트가 살아날 때까지 돌아오지 않고, GUI 는 그대로 멈춥니다.
**타임아웃만으로는 막을 수 없어서** 세 가지를 겹쳐 씁니다.

| 단계 | 하는 일 | 파일시스템 접근 |
| --- | --- | --- |
| 1. 마운트 판별 | `/proc/self/mountinfo` 로 원격 여부·서버를 알아냄 | **없음** |
| 2. 소켓 프로브 | 서버에 TCP 연결만 시도 (막혀 있으면 즉시 판별) | **없음** |
| 3. 스레드 + 타임아웃 | 위를 통과했을 때만 실제 `os.stat()` | 있음 (제한 시간) |

1·2 단계는 파일시스템을 전혀 건드리지 않으므로 **절대 멈추지 않습니다**. 3 단계에서
스레드가 못 돌아와도 블로킹 I/O 중에는 GIL 이 풀려 있어 GUI 는 계속 움직입니다
(프로세스를 죽이는 방식은 D 상태에서 `SIGKILL` 조차 밀려서 쓰지 않습니다).
판정은 **마운트 단위로 캐시**해 죽은 서버를 매번 두드리지 않습니다.

### 나열하면 안 되는 자리 — `guarded_roots`

`/user` 처럼 **아래에 마운트가 잔뜩 달린 자리**는 목록을 읽는 것만으로 전부
마운트되면서 시스템이 주저앉습니다. 입력창에 `/user` 만 쳐도 자동완성이 바로 그
일을 합니다. 그런 경로를 등록해 두면 **그 자리 자체는 열지 않고**, 한 단계라도
아래인 경로만 쓰게 됩니다.

```python
safety.configure(guarded_roots=["/user", "/mnt/nfs", "/net"])
```

데모의 **"안전" 상자**에서 임시 폴더로 `/user` 상황을 재현해 켜고 끄며 확인할 수 있습니다
(차단하면 "폴더를 실제로 읽었나: 아니오", 하위 경로는 정상).

```
/user              →  접근 안 함 (자동완성도 목록을 읽지 않음)
/user/jekai        →  평소대로 동작
/user/jekai/proj   →  평소대로 동작
/users             →  이름만 비슷한 건 영향 없음
```

적용되는 곳 — 목록을 읽게 만드는 통로를 모두 막습니다:

| 곳 | 동작 |
| --- | --- |
| 위젯 입력창 자동완성 | 그 폴더의 목록을 **아예 요청하지 않음** (하위는 정상) |
| 유효성 검사 | "없는 경로"로 판정 |
| 다이얼로그 시작 폴더 | 그 자리에서 열지 않고 안전한 곳으로 대체 |
| 다이얼로그 파일 이름 칸 자동완성 | 같은 모델로 갈아 끼워 차단 |
| 다이얼로그 파일 목록 | 차단 경로를 **더블클릭/Enter 로 열 수 없음** |
| "Look in" 드롭다운 | 차단 경로 항목을 **고를 수 없음** |
| 파일 이름 칸 + Enter / 열기 버튼 | 차단 경로를 **확정할 수 없음** (`..` 처럼 상대 경로로 올라가는 것도 포함) |
| (마지막 방어) | 그래도 들어가지면 직전 폴더로 되돌림 |

다이얼로그 쪽은 `guard_dialog(dialog)` 가 한 번에 걸어 주며, `FilePathEdit` 은
자동으로 호출합니다. 직접 만든 `QFileDialog` 에도 걸 수 있습니다:

```python
from file_dialog_widget import guard_dialog
guard_dialog(dialog)          # 차단 경로가 없으면 아무 일도 하지 않음
```

실측: 하위 3개가 있는 차단 경로의 자동완성 목록 개수 0, 하위 경로는 정상 개수.

### 쓰는 법

```python
from file_dialog_widget import FilePathEdit, safety

# uid/gid 조회가 막혀 멈추게 만드는 LDAP 처럼, 경로만 봐서는 모르는 의존 서비스를 등록
safety.configure(
    timeout=1.0,
    ttl=30.0,
    probes=[("ldap.corp", 389)],          # 경로만 봐서는 모르는 의존 서비스
    guarded_roots=["/user", "/mnt/nfs"],  # 그 자리 자체는 열지 않을 경로
)

edit = FilePathEdit(mode="open_file", path_timeout=1.0)   # 안전 확인 켜기
```

`path_timeout` 은 **기본으로 켜져 있습니다**(`safety.DEFAULT_TIMEOUT` = 1.0초).
**유효성 검사**와 **다이얼로그 시작 폴더 결정**에 적용되어, 죽은 마운트를 가리키면
"없는 경로"로 판정하고 다이얼로그는 그 폴더 대신 안전한 위치에서 엽니다.

**로컬 경로는 평소와 똑같이** `os.path` 로 곧바로 확인하므로 부담이 없습니다
(마운트 표를 보고 원격일 때만 프로브·스레드를 씁니다). 실측으로 로컬 경로 300회
검사에 0.09초, 새로 만들어진 스레드 0개입니다.

```python
edit = FilePathEdit(mode="open_file")                     # 켜짐 (기본, 1.0초)
edit = FilePathEdit(mode="open_file", path_timeout=3.0)   # 더 넉넉하게
edit = FilePathEdit(mode="open_file", path_timeout=None)  # 끄기
```

| 함수 | 설명 |
| --- | --- |
| `safety.configure(timeout, ttl, probes, guarded_roots)` | 제한 시간 · 캐시 · 프로브 대상 · 차단 경로 |
| `safety.is_guarded(path)` / `guarded_roots()` | 그 자리 자체를 막았는지 / 막은 목록 |
| `safety.is_reachable(path, timeout)` | 만져도 멈추지 않을지 판정 |
| `safety.safe_isdir/safe_isfile/safe_exists(path)` | 멈추지 않는 `os.path` 대체 |
| `safety.is_remote(path)` / `mount_for(path)` | 원격 여부 / 마운트 정보 |
| `safety.probe_host(host, port, timeout)` | 소켓만으로 서버 확인 |
| `safety.pending_checks()` | 아직 안 돌아온 확인 스레드 수 (진단용) |
| `safety.clear_cache()` | 마운트를 고친 뒤 판정 캐시 비우기 |

### 한계

**죽은 네트워크 경로**(`path_timeout`) 방어는 이 위젯이 파일시스템을 만지는 지점
(유효성 검사 · 시작 폴더)에만 걸립니다. 다이얼로그가 열린 뒤 사용자가 죽은 마운트로
직접 이동하면 Qt 가 자기 모델로 읽으므로 막을 수 없습니다. 그런 경로는 사이드바·
즐겨찾기에 넣지 않는 편이 안전합니다.

**차단 경로**(`guarded_roots`) 는 위 표의 통로를 모두 막습니다. Qt 가 C++ 에서 연결해 둔
`activated` · `accept()` 는 파이썬에서 끊을 수 없어, **그 신호가 나기 전 단계인 입력
이벤트를 삼키는** 방식을 씁니다. 그래서 프로그램이 직접 `dialog.setDirectory("/user")`
같이 호출하는 경우는 막지 않습니다(그때는 마지막 방어가 되돌리지만, **되돌리는 시점엔
이미 한 번 읽은 뒤**입니다). 그런 경로는 사이드바·즐겨찾기에 넣지 않는 편이
안전합니다. 또 `/proc/self/mountinfo` 를 쓰므로 마운트 판별은 **리눅스 전용**입니다
(다른 OS 에서는 원격 판별이 안 되어 3단계 타임아웃만 동작합니다).

## 유효성 판정 규칙

| 상황 | `open_file` / `open_files` | `save_file` | `directory` |
| --- | --- | --- | --- |
| 비어 있음 | `required` 면 무효 | `required` 면 무효 | `required` 면 무효 |
| 존재하는 파일 | 유효 | 유효 | 무효 (폴더가 아님) |
| 존재하는 폴더 | 무효 (파일이 아님) | 무효 | 유효 |
| 없는 경로 | 무효 | 상위 폴더가 있으면 유효 | 무효 |

`must_exist=False` 로 두면 "없는 경로"도 상위 폴더만 있으면 유효로 봅니다.
`validate=False` 면 유효성 표시 자체를 끕니다(항상 유효로 취급).

`~` 로 시작하는 경로는 판정 시 홈 디렉터리로 확장해서 검사하지만,
`path()` 는 사용자가 입력한 원본 문자열을 그대로 돌려줍니다.

## 위젯 없이 쓰기

내부 헬퍼는 위젯과 독립적이라 그대로 가져다 쓸 수 있습니다.

```python
from file_dialog_widget import build_filter, exec_file_dialog, validate_paths

name_filter = build_filter([("이미지", ["png", "jpg"])])   # "이미지 (*.png *.jpg);;모든 파일 (*)"

paths, chosen = exec_file_dialog(
    parent=self, mode="open_files", directory="/home/user", name_filter=name_filter
)

ok, reason = validate_paths(paths, mode="open_files")
```

`exec_file_dialog()` 는 모드에 따라 다른 `QFileDialog` 정적 메서드를 호출하고,
바인딩마다 다른 반환 형태를 항상 `(경로 리스트, 선택된 필터)` 로 정규화합니다.

사이드바를 손보려면 `places=` 에 `Places` 를 넘깁니다. 이때만 정적 메서드 대신
인스턴스 다이얼로그를 띄우고(네이티브 창으로는 사이드바를 못 바꾸므로) 링크 추적 ·
우클릭 메뉴 · 차단 경로 방어까지 함께 겁니다.

```python
from file_dialog_widget import Places, exec_file_dialog

paths, chosen = exec_file_dialog(
    mode="open_file",
    places=Places(favorites=store, recent=recent, sidebar_urls=["~", "/mnt/data"]),
)
```

테스트에서는 이 함수만 monkeypatch 하면 실제 다이얼로그 없이 위젯을 검증할 수 있습니다.

## 코드 구조

의존이 위에서 아래로만 흐르도록 층을 나눠 두었습니다(순환 없음).

```
util        경로 정규화 · QUrl 변환 · 자잘한 Qt 차이 흡수
constants   선택 모드, 기본 캡션, 항목 경로 역할
safety      경로 안전 판정 (Qt 없이 도는 순수 로직)
filters     Qt 필터 문자열 조립
validators  경로 유효성 판정
history     최근 경로 (QSettings)
icons       별표 · 시계 아이콘, 분류 아이콘 제공자
favorites   즐겨찾기 저장소 (심볼릭 링크 폴더)
recent      최근 파일 저장소 (favorites 를 물려받음)
places      사이드바에 얹는 것들의 묶음 (즐겨찾기 · 최근 · 직접 지정)
menus       우클릭 메뉴 (즐겨찾기 추가 · 분류 삭제 · 사이드바 정리)
hooks       다이얼로그에 거는 것들 (링크 추적 · 차단 경로 방어)
dialog      QFileDialog 얇은 래퍼 (모드별 호출 · 옵션 · 시작 폴더)
path_edit   FilePathEdit
form        FilePathForm
```

`safety` 는 Qt 를 쓰지 않아 단독으로 테스트할 수 있고, Qt 쪽 연동은 `hooks` 가 맡습니다.
저장소(`favorites` · `recent`)는 다이얼로그를 몰라도 되도록 `util` 만 봅니다.

## 테스트

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

## 라이선스

MIT
