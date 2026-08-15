# custom-file-dialog

`QFileDialog` 를 확장한 파일 다이얼로그입니다. 즐겨찾기 · 최근 파일 · 사이드바
구성 · 용도별 시작 위치 기억 · 죽은 네트워크 경로 방어를 더했고, `qtpy` 를 사용하여
**PyQt5 / PyQt6 / PySide2 / PySide6** 모두에서 동작합니다 — 전체 테스트를
네 바인딩에서 각각 돌려 확인합니다. 바인딩마다 없는 API(예: PySide2 의
`Q_ARG`, Qt6 의 QFileDialog 내부 슬롯)는 설치 시점에 감지해 같은 동작을
하는 다른 경로로 자동 전환합니다.

**쓰는 방법은 세 가지**입니다. 필요한 쪽만 골라 쓰면 되고, 섞어 써도 됩니다.

### 1. `CustomFileDialog` — `QFileDialog` 를 쓰던 그대로

`QFileDialog` 를 물려받은 클래스입니다. **생성자에 설정을 넣고 `exec()` 로 띄운 뒤
결과를 받는** 방식이 `QFileDialog` 와 같습니다.

```python
from custom_file_dialog import CustomFileDialog

dlg = CustomFileDialog(
    self,
    mode="open_file",
    caption="입력 파일 선택",
    filters=[("CSV", ["csv"]), ("엑셀", ["xlsx", "xls"])],
)
if dlg.exec():
    print(dlg.selectedFiles())      # ["/home/user/data.csv"]
    print(dlg.selectedPath())       # "/home/user/data.csv"  (1개짜리 편의 메서드)
```

`QFileDialog` 와 나란히 두면 이렇습니다.

```python
# Qt 기본
dlg = QFileDialog(self, "입력 파일 선택")
dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
dlg.setNameFilters(["CSV (*.csv)", "엑셀 (*.xlsx *.xls)"])
if dlg.exec():
    files = dlg.selectedFiles()

# 이 라이브러리 — 설정을 생성자에서 한 번에
dlg = CustomFileDialog(self, mode="open_file",
                       filters=[("CSV", ["csv"]), ("엑셀", ["xlsx", "xls"])])
if dlg.exec():
    files = dlg.selectedFiles()
```

`QFileDialog` 를 물려받았으므로 `setDirectory()` · `selectNameFilter()` ·
`currentChanged` 처럼 **원래 쓰던 것은 그대로 씁니다.** 이 라이브러리가 더하는
것은 생성자 인자로 켭니다.

```python
dlg = CustomFileDialog(
    self,
    mode="open_files",
    filters=[("이미지", ["png", "jpg"])],
    favorites=store,            # 즐겨찾기 분류를 사이드바에
    recent=recent,              # 최근 파일 항목
    settings_key="입력이미지",   # 이 이름으로 마지막에 쓰던 폴더에서 열기
    default_suffix="png",       # 저장 모드에서 확장자 자동 부착
)
if dlg.exec():
    paths = dlg.selectedFiles()   # 즐겨찾기 링크는 원본 경로로 복원되어 나옵니다
```

`mode` 하나로 네 종류를 다 부릅니다 — `"open_file"` · `"open_files"` ·
`"save_file"` · `"directory"`.

위험한 경로 방어(`guarded_roots` 등)는 생성자가 아니라 **앱 시작 시 한 번**
정합니다 — [앱 시작할 때 한 번](#앱-시작할-때-한-번--전역-설정) 참고.

### 2. `exec_file_dialog()` — 한 줄로 끝내기

띄우고 결과만 받으면 될 때. 안에서 `CustomFileDialog` 를 쓰므로 동작은 같습니다.

```python
from custom_file_dialog import exec_file_dialog

paths, chosen = exec_file_dialog(self, "open_file", filters=[("CSV", ["csv"])])
if paths:
    print(paths[0])
```

반환은 **언제나** `(경로 리스트, 선택된 필터)` 이고, 취소하면 `([], 필터)` 입니다.
바인딩(PyQt5/6 · PySide2/6)과 모드마다 다른 Qt 의 반환 형태를 여기서 흡수합니다.

| | `QFileDialog` 정적 메서드 | `exec_file_dialog()` |
| --- | --- | --- |
| 함수 | 모드마다 다름 (`getOpenFileName` …) | 하나 (`mode=` 로 구분) |
| 반환 | 바인딩·모드마다 다름 (문자열 / 리스트 / 튜플) | 언제나 `(경로 리스트, 선택된 필터)` |
| 취소했을 때 | `""` 또는 `[]` | 언제나 `([], 필터)` |
| 필터 | Qt 문자열만 | 파이썬 리스트·dict·문자열 다 됨 |
| 저장 확장자 | 플랫폼마다 붙기도 안 붙기도 | 항상 붙여 줌 |

자세한 내용은 [다이얼로그로 쓰기](#다이얼로그로-쓰기)를 보세요.

### 3. 화면에 붙이는 경로 입력 줄 — `FilePathEdit`

설정 화면처럼 **경로가 폼에 남아 있어야 할 때** 쓰는 위젯입니다.
입력창 + 찾아보기 버튼 한 줄이고, `[...]` 를 누르면 위와 같은 다이얼로그가 뜹니다.

```
┌─────────────────────────────────────────────┐
│ 입력 파일: [/home/user/data.csv      ] [...] │
│ 출력 폴더: [/home/user/out           ] [...] │
└─────────────────────────────────────────────┘
        → [...] 클릭 시 QFileDialog 팝업
```

```python
edit = FilePathEdit(mode="open_file", label="입력 파일:", filters=[("CSV", ["csv"])])
layout.addWidget(edit)
print(edit.path())
```

유효성 표시(빨간 테두리) · 드래그&드롭 · 경로 자동완성 · 최근 경로 드롭다운처럼
**입력창이 있어야 성립하는 기능**은 이쪽에만 있습니다.

| 이럴 때 | 쓸 것 |
| --- | --- |
| `QFileDialog` 쓰던 코드를 옮겨 옴 | `CustomFileDialog` |
| 띄우기 전후로 다이얼로그를 더 만져야 함 (신호 연결 등) | `CustomFileDialog` |
| 띄우고 결과만 받으면 끝 | `exec_file_dialog()` |
| 설정 화면에 경로가 계속 보여야 함 | `FilePathEdit` |
| 사용자가 경로를 직접 타이핑하거나 끌어다 놓음 | `FilePathEdit` |
| 여러 경로를 폼으로 묶어 한 번에 꺼냄 | `FilePathForm` |

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
  현재 작업 디렉터리* 순으로 위치를 정합니다. **용도마다 따로 기억**할 수 있어
  (`settings_key`), 입력 CSV 자리와 결과 저장 자리가 각자 마지막에
  쓰던 폴더에서 열립니다.
- **저장 확장자 자동 부착** — 저장 모드에서 확장자를 빼고 입력하면
  `default_suffix` 또는 선택된 필터의 확장자를 붙여 줍니다.
- **사이드바 커스터마이즈** — 다이얼로그 왼쪽 목록을 **홈 · 현재 위치 · 최근 파일 ·
  북마크**로 구성하거나, 원하는 폴더 목록으로 통째로 교체할 수 있습니다.
  홈은 **집 아이콘(🏠)** 으로, 다이얼로그가 열리는 자리는 폴더 이름 대신
  **"현재 위치"** 로 표시되어 한눈에 구분됩니다.
- **우클릭 메뉴** — 파일 목록에서 우클릭해 **즐겨찾기에 추가** · **경로 복사**,
  사이드바에서 우클릭해 분류 삭제 / 최근 목록 비우기 / 항목 제거.
  분류 안에서는 "삭제" 대신 **`'설계'에서 제거`** 처럼 무엇에서 빠지는지가
  이름에 적혀 나옵니다(원본 파일은 그대로).
- **최근 파일** — 최근에 고른 파일을 사이드바 항목 하나로 자동으로 모읍니다 (옵션).
- **즐겨찾기** — 흩어져 있는 **파일·폴더**를 분류별로 모아 사이드바에 **별표(★)** 로
  띄우고, 클릭 한 번으로 그 목록에서 바로 고릅니다 (`FavoritesStore`).
  고른 항목의 **원본 경로**가 "Look in" 에 뜨고, **상위 폴더(↑)** 도 그 원본
  기준으로 올라가 링크 창고에 갇히지 않습니다.
- **죽은 네트워크 경로 방어** — NFS 서버가 응답하지 않아도 GUI 가 멈추지 않도록
  마운트 판별 + 소켓 프로브 + 타임아웃을 조합합니다. **기본으로 켜져 있고**,
  로컬 경로에는 부담이 없습니다 (`path_timeout`).
- **나열하면 안 되는 자리 차단** — `/user` 처럼 아래에 마운트가 잔뜩 달린 경로를
  등록해 두면 그 자리는 열지 않고 하위 경로만 쓰게 합니다 (`guarded_roots`).
  경로를 미리 다 알기 어렵다면 `min_depth=2` 로 **얕은 자리는 자동완성이 아예
  나열하지 않게** 할 수 있습니다. 파일 이름 칸에 `/user/my` 를 **한 글자씩 치는
  것만으로** 일어나던 글자당 마운트 시도(자동 stat)도 함께 막고, autofs 마운트
  지점은 설정 없이도 자동으로 알아봅니다.
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

## 앱 시작할 때 한 번 — 전역 설정

다이얼로그마다 주는 것이 아니라 **앱 전체에 한 번** 정하는 것들이 셋 있습니다.
셋 다 선택 사항이고, `QApplication` 을 만든 직후에 부르면 됩니다.

```python
from custom_file_dialog import configure_settings, configure_storage, safety

app = QApplication([])
app.setOrganizationName("회사이름")
app.setApplicationName("앱이름")

# 1) 최근 경로·시작 위치를 저장할 QSettings 위치
#    (setOrganizationName/setApplicationName 을 했다면 생략해도 됩니다)
configure_settings("회사이름", "앱이름")

# 2) 즐겨찾기·최근 파일이 들어갈 뿌리 폴더
#    (생략하면 ~/.config/custom_file_dialog 아래에 자동 생성)
configure_storage("~/.config/myapp")

# 3) 위험한 경로 방어 — 나열만으로 시스템이 주저앉는 자리
safety.configure(
    guarded_roots=["/user", "/mnt/nfs", "/net"],  # 그 자리 자체는 열지 않음
    min_depth=2,                                  # 얕은 자리는 자동완성이 나열 안 함
    timeout=1.0,                                  # 죽은 마운트 판별 제한 시간
)
```

그 뒤로는 다이얼로그를 평소대로 만들면 위 설정이 **자동으로 적용**됩니다.

```python
dlg = CustomFileDialog(self, mode="open_files", favorites=True, settings_key="입력이미지")
if dlg.exec():                       # /user 는 이미 막혀 있습니다
    paths = dlg.selectedFiles()
```

> **순서가 중요합니다.** `safety.configure()` 는 다이얼로그를 **만들기 전에**
> 불러야 합니다. 자동완성 모델을 갈아 끼우는 일이 생성 시점에 일어나기 때문에,
> 이미 만들어 둔 다이얼로그는 나중에 `configure()` 를 불러도 보호되지 않습니다.
> (그 뒤에 새로 만드는 다이얼로그는 정상적으로 보호됩니다.)

**왜 생성자 인자가 아닌가** — `guarded_roots` 는 *이 다이얼로그*가 아니라
**이 컴퓨터**의 성질입니다. `/user` 는 어느 다이얼로그가 열든 위험합니다.
생성자 인자로 두면 한 자리만 보호하고 다른 자리를 빠뜨리기 쉬워서 전역으로
뒀습니다. 자세한 내용은
[죽은 네트워크 경로에서 멈추지 않기](#죽은-네트워크-경로에서-멈추지-않기-nfs-등)를 보세요.

## 위젯으로 쓰기 — `FilePathEdit`

```python
from qtpy.QtWidgets import QApplication, QVBoxLayout, QWidget
from custom_file_dialog import FilePathEdit

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

버튼을 누르면 **모든 기능이 켜진 `CustomFileDialog` 하나**가 뜹니다. 기능마다
상자를 따로 두지 않고, 실제로 앱에 붙였을 때와 같은 모습으로 보여 줍니다.

```
[파일 열기]  [여러 개 열기]  [저장하기]  [폴더 선택]     ← 모드마다 settings_key 가 다름

켜져 있는 기능 — 꺼서 차이를 볼 수 있습니다
  ★ 즐겨찾기 · 🕘 최근 파일 · 🏠 홈 아이콘과 "현재 위치" · 📌 settings_key · 🛡 안전
```

다이얼로그에서 확인할 것:

- **사이드바** — 🏠 홈 아이콘, "현재 위치" 이름, 🕘 최근 파일, ★ 즐겨찾기 분류
- **우클릭** — 파일 목록에서 "즐겨찾기에 추가 ▸", 분류 안에서는 "'설계'에서 제거"
- **링크 추적** — 분류에서 항목을 고르면 Look in 에 원본 경로가 뜨고,
  **상위 폴더(↑)** 도 링크 창고가 아니라 원본이 있는 폴더로 갑니다
- **시작 위치** — 모드마다 마지막에 쓰던 폴더에서 열립니다
- **안전** — `/user` 를 흉내낸 폴더는 자동완성이 나열하지 않고 들어가지도 못합니다

데모용 임시 폴더 트리가 창을 열 때 만들어지고 닫을 때 지워지므로, 실제 파일은
건드리지 않습니다.

## 선택 모드

| mode | 여는 다이얼로그 | 값 | 존재해야 유효? |
| --- | --- | --- | --- |
| `"open_file"` | `getOpenFileName` | 경로 1개 | O |
| `"open_files"` | `getOpenFileNames` | 경로 여러 개 (`paths()`) | O |
| `"save_file"` | `getSaveFileName` | 경로 1개 | X (상위 폴더만 있으면 됨) |
| `"directory"` | `getExistingDirectory` | 폴더 1개 | O |

```python
from custom_file_dialog import SelectMode

FilePathEdit(mode=SelectMode.DIRECTORY)   # 문자열 "directory" 와 동일
```

`open_files` 모드는 여러 경로를 한 줄에 `"; "` 로 이어서 표시하고,
`paths()` 로 리스트를 꺼냅니다. 경로에 `;` 가 들어갈 수 있는 환경이라면
`separator` 인자로 다른 구분자를 지정하세요.

## 필터 지정

`filters` 는 아래 어떤 형태로 줘도 됩니다. **`FilePathEdit` 과
`exec_file_dialog()` 가 똑같이 받습니다.**

```python
filters=[("이미지", ["png", "jpg"])]        # (설명, 확장자 목록)  ← 권장
filters=[("이미지", "*.png *.jpg")]         # (설명, 패턴 문자열)
filters={"이미지": ["png"], "문서": ["txt"]} # dict (선언 순서 유지)
filters=["*.png", "*.txt"]                  # 패턴만
filters="이미지 (*.png);;모든 파일 (*)"      # 이미 Qt 필터 문자열이면 그대로
```

확장자는 `"png"` / `".png"` / `"*.png"` 아무 형태나 됩니다. 확장자가 아닌
**접미사/접두사 패턴**도 됩니다 — `*` 를 직접 쓰면 그대로 보존됩니다.

```python
filters=[("라이브러리", ["*lib"]),      # foolib, toplib ...   (접미사)
         ("코너", ["*_corner"]),        # ss_corner ...        (접미사)
         ("설정", ["cfg_*"])]           # cfg_top ...          (접두사)
```

주의: 별 없이 `"lib"` 이라고 쓰면 **확장자** `*.lib` 로 해석됩니다. 접미사
`*lib` 를 원하면 `*` 를 붙여 주세요. 저장 모드의 확장자 자동 부착은
**확장자 패턴(`*.ext`)일 때만** 동작합니다 — `*_corner` 필터에서 "ss" 를
저장해도 `ss._corner` 처럼 엉뚱한 이름을 만들지 않습니다.

`"모든 파일 (*)"` 을 끝에 붙일지는 `add_all_files_filter` 로 정합니다.
**기본값이 서로 다릅니다** — `FilePathEdit` 은 `True`(붙임), `exec_file_dialog()` 는
`False`(넘긴 그대로)입니다. 다이얼로그 쪽은 `QFileDialog` 를 직접 부르던 코드를
옮겨 올 때 필터가 늘어나지 않는 편이 덜 놀랍기 때문입니다.

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
| `.effective_sidebar_marks()` | 그중 표시가 바뀌는 항목 (홈=집 아이콘, 현재 위치=이름) |
| `.set_favorites(store)` / `.favorites()` | 즐겨찾기 저장소 지정 |
| `.set_recent_files(s)` / `.recent_files()` / `.recent_items()` | 최근 파일 저장소 / 목록 |
| `.set_favorites_icon(icon)` | 즐겨찾기 분류 아이콘 (`True`=별표 / `QIcon` / `False`) |
| `.set_completer(bool)` / `.completer_enabled()` | 경로 자동완성 켜고 끄기 (큰 폴더에서 유용) |
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
- `native` : OS 네이티브 다이얼로그 사용 (기본 `True`).
  **안전장치(`safety.configure`)를 켜 두었거나 시스템에 autofs 마운트가 있으면
  이 설정과 무관하게 Qt 자체 창으로 열립니다** — OS 가 그리는 창에는 자동완성
  차단도 확정 차단도 걸 수 없기 때문입니다
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
from custom_file_dialog import FilePathForm

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

**생성자 인자만으로 다 됩니다.** 사이드바를 위해 따로 위젯을 만들 필요는 없습니다.
가장 짧게는 이렇습니다.

```python
dlg = CustomFileDialog(self, mode="open_file", favorites=True, recent=True)
if dlg.exec():
    paths = dlg.selectedFiles()
```

`True` 를 주면 **기본 위치**(`~/.config/custom_file_dialog` — `configure_storage()`
로 옮길 수 있음)에 저장소를 알아서 만들어 씁니다.
등록해 둔 즐겨찾기·최근 파일은 **디스크에 남아 있으므로, 띄울 때마다 새로
만들어도 내용은 그대로입니다.** 목록을 코드에서 관리할 필요가 없습니다.

```python
# 세 번을 새로 띄워도 사이드바는 그대로
for _ in range(3):
    CustomFileDialog(self, favorites=True)   # -> [홈, 현재 위치, 설계, 보고서]
```

저장소를 직접 다뤄야 할 때(등록·삭제·위치 지정)만 인스턴스를 만들어 넘깁니다.

```python
dlg = CustomFileDialog(
    self,
    mode="open_file",
    favorites=store,                        # 즐겨찾기 분류 -> 사이드바에 ★
    recent=recent,                          # 최근 파일     -> 🕘
    sidebar_urls=["~", "/mnt/data"],        # 기준 목록 통째로 교체
    fixed_sidebar_urls=["~", "/mnt/data"],  # 우클릭 "제거" 잠금
    favorites_icon=False,                   # 아이콘 끄기 (Qt 기본 폴더 아이콘)
)
```

| 인자 | 하는 일 | 생략하면 |
| --- | --- | --- |
| `favorites` | `True` = 기본 위치에 자동 생성 / `FavoritesStore` = 그걸 사용 | 안 올림 |
| `recent` | `True` = 자동 생성 / `RecentStore` = 그걸 사용 | 안 올림 |
| `recent_max` | `recent=True` 로 만들 때 기억할 개수 | 20 |
| `sidebar_urls` | 기준 목록을 통째로 지정 | 홈 + 현재 위치 |
| `fixed_sidebar_urls` | 우클릭 "사이드바에서 제거" 를 막을 위치 | 홈만 보호 |
| `favorites_icon` | 분류·홈 아이콘 (`True` / `QIcon` / `False`) | `True` (별표·시계·집) |
| `sidebar_width` | 사이드바 폭(px). `0` 이면 내용에 안 맞춤(최소 115) | 항목에 맞춰 자동 |
| `places` | 위 여섯 개 대신 `Places` 를 통째로 | — |

저장소는 **디스크 폴더를 가리키는 손잡이**일 뿐이라 만드는 비용도 거의 없습니다
(`FavoritesStore()` 200회에 4ms). 앱 어딘가에 들고 다닐 필요 없이 필요할 때마다
만들면 됩니다. 저장 위치는 "저장 위치 정하기" 를 참고하세요.

`QFileDialog` 를 물려받았으므로 띄우기 전에 **원래 API 로 더 만져도** 됩니다.

```python
dlg = CustomFileDialog(self, mode="open_file", favorites=store)
dlg.setSidebarUrls(dlg.sidebarUrls() + [QUrl.fromLocalFile("/mnt/data")])
dlg.exec()
```

`exec_file_dialog()` 로 쓸 때는 `places=` 에 `Places` 를 넘깁니다.

```python
exec_file_dialog(
    mode="open_file",
    places=Places(favorites=store, recent=recent, sidebar_urls=["~", "/mnt/data"]),
)
```

`FilePathEdit` 도 대부분 같은 이름의 인자를 받습니다 (`recent` 대신
`recent_files`). 다만 `places` 와 `sidebar_width` 는 **다이얼로그 전용**입니다 —
위젯은 띄울 때마다 다이얼로그를 새로 만들며 구성을 스스로 조립합니다.
위젯에만 있는 것은 **실행 중에 바꾸는 setter** (`set_sidebar_urls()` 등)뿐인데,
다이얼로그는 띄울 때마다 새로 만드니 생성자로 충분합니다.

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
  하나만 남습니다(이때는 홈으로 표시됩니다).
- `sidebar_urls` 로 기준 목록을 직접 주면 **그 목록을 그대로 존중**합니다
  (홈·현재 위치를 끼워 넣지 않습니다). 최근 파일·북마크는 그 뒤에 붙습니다.

최종 결과는 다이얼로그를 만든 뒤 `sidebarUrls()` 로 확인할 수 있습니다.

```python
dlg = CustomFileDialog(self, mode="open_file", favorites=store, recent=recent)
[u.toLocalFile() for u in dlg.sidebarUrls()]
# [홈, 현재 위치, 최근 파일, 북마크/분류A, 북마크/분류B]
```

위젯 쪽은 띄우기 전에도 미리 볼 수 있습니다 — `edit.effective_sidebar_urls()`.

### 사이드바 폭

Qt 는 사이드바를 **내용과 무관한 고정 폭**으로 엽니다(측정값 79~115px).
Qt 기본 항목("Computer", 홈)에는 맞지만, 여기서 얹는 "현재 위치" · "최근 파일" ·
분류 이름은 잘려서 `현…` 처럼 보입니다. 그래서 처음 열릴 때 **항목이 잘리지
않을 만큼만** 넓힙니다.

```python
CustomFileDialog(self, favorites=store)                     # 자동 (기본)
CustomFileDialog(self, favorites=store, sidebar_width=220)  # 직접 지정
CustomFileDialog(self, favorites=store, sidebar_width=0)    # 내용에 맞추지 않음
```

설정이 하나도 없는 새 프로필에서 실제로 잰 값입니다.

| | 사이드바 폭 |
| --- | --- |
| Qt 날것 (`sidebar_width=0` 이 하던 일) | 79 — 너무 좁음 |
| `sidebar_width=0` (지금) | **115** — 최소 폭 |
| 자동 (항목 5개) | **143** — 내용에 맞춤 |
| `sidebar_width=220` | 220 |

- **115px 아래로는 내려가지 않습니다** (`MIN_SIDEBAR_WIDTH`).
- 이미 충분히 넓으면 **그대로 둡니다**(좁히지 않습니다).
- 아무리 넓게 줘도 파일 목록 자리는 남깁니다.
- 사용자가 경계를 끌어 조절하면 그 뒤로는 건드리지 않습니다.

### 홈과 현재 위치 표시

두 자리는 폴더 이름만으로는 알아보기 어려워서 표시를 손봅니다.

```
🏠 myaccount        ← 홈: 폴더 아이콘 대신 집 아이콘 (이름은 그대로)
📁 현재 위치     ← 다이얼로그가 열린 폴더: 폴더 이름("workspace") 대신 이 이름
🕘 최근 파일
★ 설계
```

**표시만** 바꾸는 것이라 클릭했을 때 열리는 경로는 그대로입니다. 마우스를 올리면
나오는 툴팁에는 늘 전체 경로가 나옵니다. 두 가지 예외가 있습니다.

- 홈에서 다이얼로그를 열면 두 항목이 하나로 합쳐지므로 **홈 쪽만** 남습니다
  ("현재 위치"라고 부르지 않습니다).
- `sidebar_urls` 로 기준 목록을 직접 주면 "현재 위치" 항목을 붙이지 않으므로
  이름도 바꾸지 않습니다. 홈 아이콘은 그대로 씌웁니다.

`favorites_icon=False` 를 주면 집 아이콘도 끄고 Qt 기본 폴더 아이콘을 씁니다.
이름 바꾸기는 아이콘 설정과 무관하게 동작합니다. 이름 문자열은 `CURRENT_LABEL`
로 노출되어 있습니다.

### 기준 목록 직접 지정

기본 구성 대신 원하는 폴더 목록을 쓸 수 있습니다.

```python
dlg = CustomFileDialog(
    self,
    mode="open_file",
    sidebar_urls=["~", "~/프로젝트", "/mnt/data"],   # 통째로 교체
)

CustomFileDialog(self, sidebar_urls=[])      # 사이드바 비우기
CustomFileDialog(self, sidebar_urls=None)    # 커스터마이즈 끄기 (Qt 저장값 그대로)
```

기존 항목을 남기고 뒤에 덧붙이려면:

```python
from custom_file_dialog import current_sidebar_urls

CustomFileDialog(self, sidebar_urls=current_sidebar_urls() + ["/mnt/data"])
```

위젯은 실행 중에도 바꿀 수 있습니다 — `edit.set_sidebar_urls([...])`.

경로 문자열과 `QUrl` 을 섞어 줄 수 있고, `~` 는 홈 디렉터리로 펼쳐집니다.
`current_sidebar_urls()` 는 Qt가 사이드바를 저장하는 설정 키를 **읽기만** 하므로
부작용이 없습니다(저장된 값이 없으면 Qt 기본값인 Computer + 홈을 돌려줍니다).
반환값의 `QUrl("file:")` 항목이 사이드바의 **Computer** 입니다.

> **꼭 알아 둘 두 가지**
>
> 1. **네이티브 다이얼로그에서는 불가능합니다.** OS가 그리는 창이라 Qt가 사이드바를
>    바꿀 수 없습니다. 그래서 `CustomFileDialog` 는 **항상 Qt 자체 다이얼로그**로
>    뜨고, `exec_file_dialog()` 도 `places=` 를 주면 `native` 설정과 무관하게
>    Qt 자체 다이얼로그로 전환됩니다.
> 2. **Qt가 사이드바를 영구 저장합니다.** 리눅스 기준 `~/.config/QtProject.conf` 의
>    `[FileDialog] shortcuts` 에 기록되어, 한 번 지정하면 프로그램을 다시 켜도,
>    나아가 같은 설정을 공유하는 **다른 Qt 앱에서도** 그 항목이 보입니다.
>    `sidebar_urls=None` 로 되돌려도 이미 저장된 항목은 남습니다.
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

직접 준 항목의 **표시 이름은 폴더 이름 그대로**입니다 (`/mnt/data` → `data`).
Qt 모델에는 이름을 넣어 둘 자리가 없어서 — `QUrlModel` 이 파일시스템 변화를
통지받을 때마다 이름과 아이콘을 경로에서 다시 읽어 덮어씁니다 — "현재 위치"처럼
이름을 바꾸려면 **그리는 단계**에서 갈아 끼워야 합니다. `mark_sidebar()` 가
`{경로: (이름, 아이콘)}` 표를 받아 그 일을 하는 델리게이트를 걸어 줍니다.

```python
from custom_file_dialog import mark_sidebar
```

기본으로 표시가 바뀌는 것은 홈 · "현재 위치" · 즐겨찾기 분류(★) · 최근 파일(🕘)
입니다(`Places.sidebar_marks()` 참고).
델리게이트를 쓰고 싶지 않다면 원하는 이름의 심볼릭 링크를 만들어 그 경로를
넣는 방법도 그대로 쓸 수 있습니다.

## 즐겨찾기 — 파일·폴더를 분류별로 모아 두기

사이드바는 **디렉터리만** 받습니다(파일 URL 을 넣으면 Qt 가 조용히 버립니다).
그래서 `FavoritesStore` 는 분류마다 실제 폴더를 만들고 그 안에 대상들의
**심볼릭 링크**를 모읍니다. 분류가 사이드바에 뜨고, 클릭하면 오른쪽 목록에
등록해 둔 파일·폴더가 함께 나옵니다.

```python
from custom_file_dialog import CustomFileDialog, FavoritesStore

store = FavoritesStore()                        # 앱 데이터 폴더 아래에 생성
store.add("설계", "/proj/a/설계도.csv")           # 파일
store.add("설계", "/proj/b/산출물")               # 폴더도 가능
store.add("보고서", "/proj/b/보고서.md")

dlg = CustomFileDialog(self, mode="open_file", favorites=store)
if dlg.exec():         # 사이드바에 "설계", "보고서" 가 보인다
    dlg.selectedPath() # -> /proj/a/설계도.csv  (링크가 아니라 원본 경로)
```

```
┌─────────────┬─────────────────────┐
│ 📁 myaccount    │ 📁 산출물            │
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

분류 폴더가 만들어질 위치는 네 단계로 정해집니다.

```python
from custom_file_dialog import (
    FavoritesStore, RecentStore,
    configure_favorites, configure_storage, default_base_dir,
)

# 1) 저장소 하나만 다른 곳에 두기 — 생성자 인자가 가장 우선
store = FavoritesStore(base_dir="/srv/공용/즐겨찾기")

# 2) 즐겨찾기 폴더만 앱 전체 기본으로 — 시작할 때 한 번
configure_favorites("~/문서/내앱-즐겨찾기")
store = FavoritesStore()          # -> ~/문서/내앱-즐겨찾기

# 3) 즐겨찾기·최근 파일이 함께 들어갈 **뿌리**를 지정
configure_storage("~/.config/myapp")
FavoritesStore()                  # -> ~/.config/myapp/favorites
RecentStore()                     # -> ~/.config/myapp/recent

# 4) 아무것도 안 하면 ~/.config 아래 (XDG_CONFIG_HOME 준수)
default_base_dir()                # -> ~/.config/custom_file_dialog/favorites
```

| 함수 | 설명 |
| --- | --- |
| `configure_storage(path)` | 즐겨찾기·최근 파일의 **뿌리 폴더** 지정 (`<뿌리>/favorites` · `<뿌리>/recent`). `None` 이면 기본(`~/.config/custom_file_dialog`)으로 복귀 |
| `configure_favorites(path)` | **즐겨찾기 폴더만** 직접 지정 (뿌리보다 우선). `None` 이면 지정 해제 |
| `configured_base_dir()` | 위에서 지정한 즐겨찾기 위치 (지정 안 했으면 `None`) |
| `default_storage_dir()` / `default_base_dir()` | 지금 실제로 쓸 뿌리 / 즐겨찾기 위치 |
| `store.base_dir` | 그 저장소가 쓰는 위치 |

- `~` 표기를 쓸 수 있고, 없는 폴더는 만들어 줍니다(`create=False` 로 끌 수 있음).
- 위치를 바꾸면 **그 위치의 즐겨찾기만** 보입니다. 기존 것을 옮기려면 폴더째
  복사하세요(링크 안의 대상 경로는 그대로 유효합니다).
- 실행 중에 바꾸려면 새 저장소를 만들어 갈아 끼우면 됩니다:
  `edit.set_favorites(FavoritesStore(base_dir=새경로))`.

### 별표 아이콘

분류에는 기본적으로 별표(★) 아이콘이 붙습니다. 외부 이미지 없이 `QPainter` 로
그리므로 별도 리소스 파일이 필요 없습니다.

```python
# 기본 = 별표
CustomFileDialog(self, mode="open_file", favorites=store)

# 다른 아이콘으로
CustomFileDialog(self, mode="open_file", favorites=store,
                 favorites_icon=QIcon("/path/to/icon.png"))

# 끄기 (Qt 기본 폴더 아이콘)
CustomFileDialog(self, mode="open_file", favorites=store, favorites_icon=False)
```

위젯은 실행 중에도 바꿀 수 있습니다 — `edit.set_favorites_icon(True)`.

색이나 크기를 바꾸려면 `star_icon()` 을 직접 부르면 됩니다:

```python
from custom_file_dialog import star_icon

CustomFileDialog(self, favorites=store,
                 favorites_icon=star_icon(color="#1565c0", sizes=(16, 24, 32)))

# 별 크기 조절: inset 은 반지름에서 빼는 픽셀 수라 지름은 그 두 배만큼 작아진다
star_icon(inset=0)    # 픽스맵을 꽉 채움 (기본보다 2px 큼)
star_icon(inset=2)    # 기본보다 2px 더 작게
```

내부적으로는 `CategoryIconProvider` 가 `QFileDialog.setIconProvider()` 로 걸려,
**분류 폴더에만** 별표를 씌우고 나머지 경로는 Qt 기본 아이콘을 그대로 씁니다.
이 역시 네이티브 다이얼로그에서는 불가능하므로 Qt 자체 다이얼로그로 열립니다.

#### 네트워크 홈에서의 아이콘 (알아 두세요)

Qt 기본 아이콘은 **종류마다 한 번만** 조회하고 재사용합니다. Qt 는 아이콘을
고르려고 파일 종류를 알아내고(확장자가 없으면 내용까지 들여다봅니다) 아이콘
테마 폴더를 뒤지는데, 그 목록에 `~/.icons` 와 `~/.local/share/icons` 가
들어 있습니다. **홈이 네트워크에 있으면 항목 하나하나가 서버 왕복**이 됩니다.
게다가 Qt 는 필터로 걸러져 화면에 안 보이는 항목까지 전부 훑습니다.

실측(NFS 홈, 항목 274개 · 화면에는 7개만 표시):

| | 조회 횟수 | 걸린 시간 |
|---|---|---|
| 종류별 캐시 없음 | 274회 | 1,332 ms |
| 종류별 캐시 (현재) | 10회 | 0.6 ms |

열쇠는 `(심볼릭 링크인가, 종류 이름)` 입니다. 종류 이름은 `text/plain` 처럼
**Qt 가 아이콘을 고를 때 쓰는 기준 그대로**이고, 파일 내용이 아니라 확장자만
보고 정합니다(`QMimeDatabase` · `MatchExtension`) — 내용을 읽으면 그것이 다시
서버 왕복이기 때문입니다.

확장자를 **그대로** 열쇠로 쓰면 안 됩니다. 날짜나 버전이 박힌 이름
(`로그.2024.01.txt`)은 파일마다 다른 확장자 사슬이 되어 열쇠가 파일 수만큼
늘고, 그러면 캐시가 하는 일이 없어집니다 — 실측으로 그런 파일 2,000개짜리
폴더에서 조회 2,000회 · 열쇠 2,000개가 났고 **같은 폴더를 다시 열어도 또
2,000회**였습니다. 종류로 접으면 **조회 1회 · 열쇠 1개**가 됩니다.

**폴더는 특수 폴더인지로 가릅니다.** Qt6 은 홈과 바탕화면에 XDG 전용 아이콘을
주므로(실측: Qt6 바인딩 + gtk3 에서만, 그리고 그 둘뿐 — Qt5 는 전부 같습니다)
폴더를 한 열쇠에 묶으면 평범한 폴더가 **바탕화면 모양**으로 오염됩니다. 그렇다고
이름을 열쇠에 넣으면 폴더 수만큼 열쇠가 나서 캐시가 죽습니다 — 네트워크 홈은
폴더가 대부분이라 하필 가장 비싼 자리에서 그렇게 됩니다. 그래서 `QStandardPaths`
가 아는 특수 폴더만 제 경로로 가르고 나머지는 한 칸에 모읍니다(열쇠 증가 상한
10개, 추가 조회 0회).

맞바꾼 것은 하나입니다 — **같은 종류인 파일들이 내용과 무관하게 같은 아이콘**을
받습니다(확장자가 없는 파일과 점파일 `.bashrc` 는 한 종류로 묶입니다).

Qt 자신도 이 조회가 네트워크 드라이브에서 비싸다고 보고 끄는 옵션
(`DontUseCustomDirectoryIcons`)을 두고 있습니다. 파일 종류별 아이콘이 그대로
필요하고 홈이 로컬이라면 `favorites_icon=False` 로 이 제공자를 아예 빼면
Qt 기본 동작이 됩니다.

#### 그래도 느리면

어디서 시간이 가는지 그 환경에서 직접 재는 스크립트가 있습니다.

```bash
python examples/diagnose_slow.py            # 데모와 같은 구성으로 재 본다
python examples/diagnose_slow.py --safety   # 안전장치를 켠 상태로
```

클릭 한 번을 **우리 코드**와 **Qt** 로 갈라 재고, 우리 코드 쪽이 크면 어느
함수인지까지 찍습니다. 홈·저장소가 어느 마운트에 얹혀 있는지, 원격 마운트의
`noac`/`actimeo=0`(속성 캐시를 끄는 옵션 — 목록이 느린 흔한 원인)도 함께
알려 줍니다. `examples/demo.py` 는 사이드바를 누를 때마다 같은 내용을 아래
로그에 남깁니다.

같은 방식으로 그린 아이콘이 두 개 더 있습니다 — 최근 파일의 `clock_icon()` 과
홈의 `home_icon()`. 셋 다 `color` · `sizes` · `inset` 을 같은 뜻으로 받습니다.

```python
from custom_file_dialog import clock_icon, home_icon, star_icon

home_icon(color="#1565c0")          # 홈 아이콘 색만 바꾸기
```

홈 아이콘은 분류 아이콘과 달리 아이콘 제공자가 아니라 사이드바 델리게이트
(`mark_sidebar()`)로 씌웁니다. 사이드바에서만 집 모양이 되고, 오른쪽 파일 목록에
나오는 홈 폴더는 Qt 기본 폴더 아이콘 그대로입니다.

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

### "상위 폴더"는 원본 기준으로 올라갑니다

항목을 고른 다음 툴바의 **상위 폴더(↑, `Alt+Up`)** 를 누르면, "Look in" 이 가리키던
**원본 경로 기준**으로 올라갑니다.

```
<favorites>/설계/설계도.csv        (원본 /proj/a/설계도.csv)
  ↑ 누르면  →  /proj/a             ← 원본이 있는 폴더
  (기본 Qt 동작이었다면  <favorites> — 분류 폴더만 늘어선 링크 창고)
```

Qt 는 "지금 보고 있는 폴더의 부모"로 올라가므로, 그대로 두면 링크를 모아 둔 저장소
안쪽으로 들어가 버립니다. 거기는 열어 봐야 쓸 것이 없어 사실상 막다른 길입니다.
폴더 링크도 같은 규칙으로 **원본이 있는 폴더**로 갑니다.

- 아무것도 고르지 않았거나 링크가 아닌 항목을 골랐다면 Qt 기본 동작 그대로입니다.
- 분류 폴더 **밖**에서는 손대지 않습니다.
- 원본이 죽은 마운트에 있으면 옮기지 않고 제자리에 둡니다(GUI 를 멈추지 않으려고).

Qt 가 C++ 에서 연결해 둔 `_q_navigateToParent` 는 파이썬에서 끊을 수 없어, 버튼이
눌린 순간 목적지를 정해 두었다가 **Qt 가 옮긴 직후에 바로잡는** 방식입니다.
잘못 들르는 저장소는 늘 로컬이라 오가는 비용이 없습니다.

직접 걸려면:

```python
from custom_file_dialog import Places, install_hooks

places = Places(favorites=favorites, recent=recent)
install_hooks(dialog, places)   # 사이드바 표시 + 링크 추적 + 우클릭 메뉴 + 차단 경로 방어
```

`FilePathEdit` 은 이것을 자동으로 겁니다.

### 우클릭 메뉴

**파일 목록**에서 파일이나 폴더를 우클릭하면 맨 위에 `즐겨찾기에 추가 ▸` 가 붙습니다.
기존 분류를 고르거나 새 분류를 만들 수 있고, 구분선 아래에 `경로 복사`
(클립보드로), 다시 그 아래에는 **Qt 기본 항목이 그대로** 따라붙습니다
(이름 변경 · 삭제 · 숨김 파일 · 새 폴더).

```
설계도.csv 우클릭
 ├─ 즐겨찾기에 추가 ▸
 │    ├ 설계          ← 이미 등록돼 있으면 비활성
 │    ├ ─────
 │    └ 새 분류...     ← 이름을 물어보고 만든다
 ├─ ─────
 ├─ 경로 복사          ← 클립보드로. 분류 안의 링크는 원본 경로를 복사
 ├─ ─────
 ├─ Rename / Delete
 └─ Show hidden files / New Folder
```

새 분류를 만들면 사이드바에도 바로 나타납니다. 분류 폴더 **안의 링크**에는 이
메뉴가 뜨지 않습니다(이미 등록된 것이므로).

**분류 폴더 안에서는 "삭제" 대신 "…에서 제거"** 가 나옵니다.

```
<즐겨찾기>/설계 폴더에서 설계도.csv 우클릭
 ├─ '설계'에서 제거          ← Qt 기본 "Delete" 를 대신합니다
 ├─ ─────
 ├─ 경로 복사               ← 링크가 아니라 원본 경로를 복사합니다
 ├─ ─────
 ├─ Rename                  ← 목록에 보일 이름만 바꿉니다
 └─ Show hidden files / New Folder

<최근 파일> 폴더에서 우클릭
 └─ '최근 파일'에서 제거
```

거기 보이는 것은 원본을 가리키는 심볼릭 링크라, Qt 의 "Delete" 도 실제로는 링크만
지웁니다. 하지만 이름만 보면 **원본 파일이 지워진다고 읽히기 쉬워서**, 무엇에서
빠지는지를 메뉴 이름에 적고 "Delete" 는 뺐습니다(둘을 함께 두면 어느 쪽이 원본을
지우는지 더 헷갈립니다).

- **원본 파일은 건드리지 않습니다.** 목록에서만 빠집니다.
- 다시 등록하면 그만이라 **확인 대화상자를 띄우지 않습니다.** 분류를 통째로
  지우는 사이드바 메뉴는 예전처럼 확인합니다.
- 같은 파일을 다른 이름으로 두 번 등록해 두었어도 **우클릭한 그 항목만** 빠집니다.
- 분류 폴더 **바로 아래** 항목에만 붙습니다. 폴더 링크를 따라 들어간 안쪽은 이미
  원본이라 Qt 기본 메뉴 그대로입니다(거기서의 "Delete" 는 진짜 삭제입니다).
- 즐겨찾기 없이 **최근 파일만** 써도 이 메뉴는 나옵니다.

```python
menus.entryRemoved.connect(on_entry_removed)   # (분류, 뺀 항목의 원본 경로)
```

**사이드바**에서 우클릭하면 항목 종류에 맞는 메뉴가 나옵니다. Qt 기본 메뉴("Remove")를
대신하되, 일반 항목에는 같은 제거 기능을 그대로 제공합니다.

```
★설계        우클릭 → "'설계' 즐겨찾기에서 삭제"   (분류 폴더째 제거)
🕘최근 파일    우클릭 → "'최근 파일' 목록 비우기"   (항목은 남기고 안만 비움)
끌어다 놓은 폴더 우클릭 → "사이드바에서 제거"        (목록에서만 빠짐)
myaccount (홈)   우클릭 → "사이드바에서 제거" (비활성)  (기본 보호 위치)
```

**보호 위치** — 실수로 빼면 곤란한 항목은 `fixed_sidebar_urls` 로 잠급니다.
기본값(`None`)은 **사용자 홈만** 보호합니다.

```python
CustomFileDialog(self, mode="open_file")                                # 홈 보호(기본)
CustomFileDialog(self, mode="open_file", fixed_sidebar_urls=["~", "/srv/공용"])
CustomFileDialog(self, mode="open_file", fixed_sidebar_urls=[])         # 보호 없음
```

| 값 | 동작 |
| --- | --- |
| `None` (기본) | 사용자 홈만 제거 불가 (`Places().fixed_urls()`) |
| `[경로, …]` | 나열한 위치만 제거 불가 (홈을 지키려면 함께 넣습니다) |
| `[]` | 아무것도 보호하지 않음 (홈도 뺄 수 있음) |

어느 쪽이든 **원본 파일·폴더는 건드리지 않습니다**. 직접 제어하려면:

```python
from custom_file_dialog import FavoritesMenus, Places

places = Places(favorites=favorites, recent=recent, fixed_urls=None)  # None = 홈만 보호
menus = FavoritesMenus(dialog, places, confirm=True, add_menu=True)
menus.install()                       # 네이티브 다이얼로그면 False 를 반환
menus.favoriteAdded.connect(on_added)         # (분류, 경로)
menus.entryRemoved.connect(on_entry_removed)  # (분류, 뺀 항목의 원본 경로)
menus.categoryRemoved.connect(on_removed)
menus.recentCleared.connect(on_cleared)
menus.sidebarEntryRemoved.connect(on_unpinned)   # 일반 항목을 사이드바에서 뺐을 때
menus.add_to_favorites("/proj/a/x.csv", "설계")   # 코드에서 직접 등록
menus.remove_entry(store, "설계", link_path)      # 코드에서 직접 제거
```

`add_menu=False` 를 주면 파일 목록 메뉴는 건드리지 않고 사이드바 메뉴만 겁니다.

### 경로 복원

즐겨찾기에서 고르면 다이얼로그는 **링크 경로**를 돌려줍니다.
`CustomFileDialog.selectedFiles()` 와 `FilePathEdit.path()` 는 이를 **자동으로**
원본으로 되돌립니다. 직접 만든 `QFileDialog` 의 결과라면 `places.resolve_all()` 을
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
from custom_file_dialog import CustomFileDialog, RecentStore

# 1) 가장 간단하게 — 기본 위치에 저장소를 자동으로 만든다
CustomFileDialog(self, mode="open_file", recent=True, recent_max=20)

# 2) 저장소를 직접 만들어 여러 자리가 같은 목록을 공유
recent = RecentStore(max_items=20)
CustomFileDialog(self, mode="open_file", recent=recent)
CustomFileDialog(self, mode="save_file", recent=recent)

recent.items()               # 최신순 원본 경로 목록
recent.clear()               # 목록 비우기
```

```
┌────────────┬─────────────────────────────┐
│ myaccount      │ 📄 나.csv                    │   ← 홈
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
| `default_recent_dir()` | 기본 저장 위치 — 저장소 뿌리 아래의 `recent` (예전 규칙으로 쌓아 둔 폴더가 있으면 그것을 계속 씁니다) |

순서는 **심볼릭 링크 자신의 수정 시각**(만든 시각)으로 판단합니다. 별도 상태 파일이
필요 없고, 다시 고르면 링크를 지웠다 새로 만들어 맨 앞으로 올립니다.

> 참고: 목록의 **순서는 `items()` 기준**입니다. 다이얼로그 오른쪽 파일 목록은 Qt 가
> 자기 정렬 기준(기본은 이름순)으로 보여 주므로 최신순으로 나오지 않습니다.
> 최신순으로 보려면 다이얼로그에서 "Date Modified" 열로 정렬하세요.

## 최근 경로 기억하기

```python
from custom_file_dialog import FilePathEdit, configure_settings

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

### 용도마다 다른 시작 위치 — `settings_key`

`settings_key` 는 **이 자리를 구분하는 이름표**입니다. 미리 등록하거나 발급받는
값이 아니라 아무 문자열이나 정하면 되고, **같은 이름 = 같은 기억**입니다.
입력 CSV 를 고르는 자리와 결과를 저장하는 자리에 서로 다른 이름을 주면, 각자
마지막에 쓰던 폴더에서 열립니다.

```python
FilePathEdit(mode="open_file", settings_key="입력csv",  filters=[("CSV", ["csv"])])
FilePathEdit(mode="save_file", settings_key="결과저장", filters=[("JSON", ["json"])])
FilePathEdit(mode="open_file", settings_key="이미지",   filters=[("이미지", ["png"])])
```

```
입력csv  →  마지막에 CSV 를 꺼낸 폴더에서 열림
결과저장 →  마지막에 결과를 저장한 폴더에서 열림
이미지   →  마지막에 이미지를 고른 폴더에서 열림
```

**다이얼로그도 같은 이름**을 씁니다. 화면에 위젯을 두지 않고 메뉴 항목처럼
그때그때 띄우는 경우입니다.

```python
CustomFileDialog(self, mode="open_file", settings_key="입력csv")
exec_file_dialog(mode="open_file", settings_key="입력csv")
# 열 때  : "입력csv" 로 마지막에 쓰던 폴더에서 시작
# 닫을 때: 고른 파일이 있는 폴더를 그 이름으로 다시 기억
```

셋 다 **같은 저장소**를 쓰므로, 위젯에서 고른 폴더를 다이얼로그가 이어받고
그 반대도 됩니다.

이름에는 아무 문자열이나 쓸 수 있습니다 — 한글·공백·특수문자 모두 Qt 가 알아서
인코딩해 저장합니다. 다만 슬래시(`/`)는 `QSettings` 에서 **그룹 구분자**로
해석되므로(동작에는 문제없지만 설정 파일이 지저분해집니다) 피하는 편이 좋습니다.

```python
settings_key="입력csv"                  # 용도 이름
settings_key="MainWindow.inputEdit"     # 창.위젯 이름 — 겹칠 일이 없습니다
settings_key=f"내보내기.{export_type}"   # 동적으로 만들어도 됩니다
```

직접 읽고 쓰려면:

```python
from custom_file_dialog import last_dir, remember_dir

last_dir("입력csv")                      # -> "/data/csv" (기억이 없으면 None)
remember_dir("입력csv", "/data/csv/a.csv")   # 파일을 주면 그 파일이 있는 폴더를 기억
```

| | 동작 |
| --- | --- |
| 기억이 없을 때 | *현재 값 → `start_dir` → 현재 작업 디렉터리* 순으로 평소대로 결정 |
| 기억해 둔 폴더가 사라졌을 때 | 안전한 곳으로 대체 (그 폴더에서 열지 않음) |
| 기억해 둔 폴더가 죽은 마운트일 때 | `path_timeout` 으로 걸러 내고 대체 |
| `directory` 를 함께 줬을 때 | `directory` 가 우선. 기억은 그래도 갱신됨 |
| `settings_key` 를 안 줬을 때 | 아무것도 기억하지 않음 |

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
판정은 **마운트 단위로 캐시**해 죽은 서버를 매번 두드리지 않고, 멈춘 확인
스레드가 있는 마운트는 **돌아올 때까지 다시 두드리지 않고 즉시 실패로**
판정합니다 — 키 입력마다 확인해도 멈춘 스레드는 **마운트당 한 개**를 넘지
않습니다. autofs 위 경로는 3 단계로도 가지 않습니다(만지는 것 자체가 마운트
시도라 스레드 없이 바로 실패 판정).

### 나열하면 안 되는 자리 — `guarded_roots`

`/user` 처럼 **아래에 마운트가 잔뜩 달린 자리**는 목록을 읽는 것만으로 전부
마운트되면서 시스템이 주저앉습니다. 입력창에 `/user` 만 쳐도 자동완성이 바로 그
일을 합니다. 그런 경로를 등록해 두면 **그 자리 자체는 열지 않고**, 한 단계라도
아래인 경로만 쓰게 됩니다.

```python
safety.configure(guarded_roots=["/user", "/mnt/nfs", "/net"])
```

데모에서 임시 폴더로 `/user` 상황을 재현해 두었습니다. "🛡 안전" 체크박스를
껐다 켜며 자동완성과 이동이 막히는지 확인해 보세요.

```
/user                  →  접근 안 함 (자동완성도 목록을 읽지 않음)
/user/myaccount        →  평소대로 동작 (목록 · 이동 · 유효성)
/user/myaccount/proj   →  평소대로 동작
/users                 →  이름만 비슷한 건 영향 없음
```

한 가지 예외는 **파일 이름 칸에서 Enter 로 확정하는 것**입니다. 차단 경로
바로 아래(`/user/myaccount`)는 그 확인 자체가 "`/user` 안에서 그 이름을
찾아라" = 마운트 시도라, 끝에 `/` 를 붙여 **열겠다고 밝혀야** 합니다.

```
/user/myaccount     Enter →  막힘 + 안내 팝업
/user/myaccount/    Enter →  열림 (명시적 폴더 표기)
/user/myaccount/a.csv Enter →  열림 (부모가 이미 붙은 자리)
```

적용되는 곳 — 목록을 읽게 만드는 통로를 모두 막습니다:

| 곳 | 동작 |
| --- | --- |
| 위젯 입력창 자동완성 | 그 폴더의 목록을 **아예 요청하지 않음** (하위는 정상) |
| 유효성 검사 | 차단 경로 자체는 "없는 경로"로 판정, 그 **바로 아래의 미완성 경로**(`/user/my`)는 stat 없이 판정 보류 |
| 다이얼로그 시작 폴더 | 그 자리에서 열지 않고 안전한 곳으로 대체 |
| 다이얼로그 파일 이름 칸 자동완성 | 같은 모델로 갈아 끼워 차단 |
| 다이얼로그 파일 이름 칸 **키 입력마다의 자동 확인** | Qt 가 글자마다 하던 입력 경로 `access()`/`stat()` 을 위험한 자리에서 **하지 않음** — `/user/my` 를 치는 순간의 마운트 시도가 사라짐 |
| 다이얼로그 파일 목록 | 차단 경로를 **더블클릭/Enter 로 열 수 없음** |
| "Look in" 드롭다운 | 차단 경로 항목을 **고를 수 없음** |
| 사이드바 | 차단 경로 항목을 **클릭·방향키로 고를 수 없음** (Qt 는 선택이 바뀌는 순간 이동하므로 누르는 단계에서 막습니다) |
| 파일 이름 칸 + Enter / 열기 버튼 | 차단 경로는 물론 **그 바로 아래도 `/` 없이는 확정 불가** (`..` 처럼 상대 경로로 올라가는 것도 포함) |
| (마지막 방어) | 그래도 들어가지면 직전 폴더로 되돌림 |

다이얼로그 쪽은 `guard_dialog(dialog)` 가 한 번에 걸어 주며, `FilePathEdit` 은
자동으로 호출합니다. 직접 만든 `QFileDialog` 에도 걸 수 있습니다:

```python
from custom_file_dialog import guard_dialog
guard_dialog(dialog)          # 차단 경로가 없으면 아무 일도 하지 않음
```

실측: 하위 3개가 있는 차단 경로의 자동완성 목록 개수 0, 하위 경로는 정상 개수.

### 자동완성 최소 깊이 — `min_depth`

`guarded_roots` 는 위험한 자리를 **이름으로 지목**합니다. 그런데 `/user` 아래에
무엇이 달려 있는지 미리 다 알기 어렵거나, 지목을 빠뜨리면 그대로 멈춥니다.
`min_depth` 는 그 그물입니다 — **얕은 자리는 아예 나열하지 않습니다.**

```python
safety.configure(min_depth=2)      # 2단계 아래부터만 자동완성
```

깊이는 루트에서부터 셉니다 (`/` = 0, `/user` = 1, `/user/myaccount` = 2).

```
/ho          →  완성 안 됨   (/ 를 나열해야 하므로. 0단계)
/user/my      →  완성 안 됨   (/user 를 나열해야 하므로. 1단계)  ← 멈추던 지점
/user/myaccount/ →  평소대로 완성 (/user/myaccount 는 2단계)
```

깊이가 min_depth 보다 **작거나 같은** 경로는 **어떤 접근도 하지 않습니다** —
자동완성 나열, 키 입력마다의 자동 stat(Qt 는 글자를 칠 때마다 입력 경로를
`access()`/`stat()` 으로 만져 봅니다), 그리고 **Enter/열기 버튼으로 확정하는
것**까지 전부입니다. automount 아래에서는 확정의 stat 한 번도 마운트 시도라,
`/user/my` 에서 Enter 를 쳐도 시스템이 멈추기 때문입니다.

**끝의 `/` 는 "이 폴더를 열겠다"는 명시적 표기**로 보고 min_depth 깊이부터
허용합니다. `min_depth=2` 기준:

```
/user/my           Enter →  막힘 + 안내 팝업 (예전엔 여기서 멈췄다)
/user/myaccount       Enter →  막힘 + 안내 팝업 ("2단계 이상 + 끝에 / 를 붙이세요")
/user/myaccount/      Enter →  열림 (명시적 폴더 표기 — 의도한 마운트는 이 한 번으로)
/user/myaccount/a.csv Enter →  확정됨 (깊이 3)
/user/            Enter →  막힘 (/ 를 붙여도 min_depth 미만은 불가)
```

**autofs 마운트 지점 자체**는 깊이와 무관하게, `/` 를 붙여도 열리지 않습니다.
그 자리를 여는 것은 "아래 이름을 전부 마운트해 보라"는 뜻이라, 하나만 붙이는
하위(`/user/myaccount/`)와 위험이 다르기 때문입니다.

**얕은 자리로 들어가는 것도 막습니다** — 들어가는 순간 그 자리가 통째로
나열되어 automount 라면 전부 마운트되기 때문입니다. 더블클릭 · "Look in"
드롭다운 · **상위 폴더(↑)** 가 모두 같은 규칙을 따르고, 그래도 들어가지면
직전 폴더로 되돌립니다.

```
/user/myaccount 에서 ↑ 를 누르면 →  /user (깊이 1) 이므로 막힘
/user/myaccount/proj 에서 ↑     →  /user/myaccount (깊이 2) 이므로 정상
```

대가는 `/ho` → `/home` 처럼 **얕은 자리의 완성·자동 확인·Enter 확정·이동이
함께 없어지는 것**입니다. **사이드바에 등록해 둔 자리도 막힙니다** — 그 클릭이
바로 그 폴더를 통째로 읽는 통로이기 때문입니다(사이드바 클릭 한 번으로 `/user`
가 열리고 형제 계정이 전부 stat 되던 것을 실측으로 확인했습니다). 그러니
`min_depth` 를 홈보다 깊게 잡으면 홈 아이콘도 눌리지 않습니다.

> **autofs 는 자동으로 압니다.** `/proc/self/mountinfo` 에 autofs 로 잡히는
> 마운트 위 경로는 `guarded_roots`/`min_depth` 를 설정하지 않아도 나열·자동
> stat·`safe_*` 확인이 전부 막힙니다(`safety.may_stat` · `safety.on_automount`).
> 만지는 것 자체가 마운트 시도라, 스레드+타임아웃으로 두드리는 것도 하지 않고
> **디스크 접근 없이 즉시** 판정합니다.

### 자동완성 아예 끄기 — `allow_listing` / `completer=False`

깊이로는 가릴 수 없는 자리도 있습니다. **파일이 수만 개인 폴더**는 깊이가 깊어도
완성 후보를 만들려고 그 폴더를 통째로 읽습니다. 그럴 땐 나열 자체를 끕니다.

```python
safety.configure(allow_listing=False)      # 앱 전체 — 어떤 폴더도 읽지 않음
```

```python
edit = FilePathEdit(mode="open_file", completer=False)   # 이 위젯만
edit.set_completer(False)                                # 실행 중에도 전환
```

| | `safety.configure(allow_listing=False)` | `FilePathEdit(completer=False)` |
| --- | --- | --- |
| 범위 | 앱 전체 | 그 위젯 하나 |
| 다이얼로그 파일 이름 칸 | **함께 막힘** | 영향 없음 |
| 실행 중 전환 | `configure()` 다시 호출 | `set_completer(bool)` |
| 모델·감시 스레드 | 남아 있음 (읽지만 않음) | 버림 |

둘 다 **나열만** 막습니다. 경로를 직접 치거나 붙여 넣는 것, 유효성 표시,
다이얼로그 동작은 그대로입니다.

> `canFetchMore()` 자체는 값싼 호출입니다(측정값 0.1ms). `fetchMore()` 도 워커
> 스레드에 넘기기만 합니다(2.4ms). 실제로 멈추는 곳은 **그 워커 스레드가 항목마다
> `stat()` 을 하는 지점**입니다 — automount 가 달려 있으면 항목 수만큼 마운트가
> 붙고, 죽은 NFS 위라면 D 상태로 들어가 돌아오지 않습니다. 그래서 "읽기 시작조차
> 하지 않게" 막는 것이 유일하게 확실한 방법입니다.
>
> 참고로 **로컬 디스크**라면 파일 개수 자체는 생각보다 견딥니다. 40,000개짜리
> 폴더를 측정했을 때 전부 읽는 데 0.4초, GUI 가 한 번에 멈춘 최대 시간 96ms,
> 완성 후보 계산 13ms 였습니다. 멈춤이 심하다면 개수보다 **마운트 쪽**을 먼저
> 의심해 보세요.

### 세 가지 중 무엇을 쓸까

성격이 다르니 함께 쓰는 편이 좋습니다.

| | `guarded_roots` | `min_depth` | `allow_listing=False` |
| --- | --- | --- | --- |
| 지정 방식 | 위험한 경로를 이름으로 나열 | 깊이 하나로 일괄 | 스위치 하나 |
| 막는 것 | 나열 + 자동 stat + 확정 + 이동 (그 자리 전용) | 나열 + 자동 stat + **Enter 확정 + 이동** (깊이 기준) | 자동완성 나열만 |
| 모르는 위험 경로 | 못 막음 (autofs 는 자동 인지) | 얕으면 막힘 | **전부 막힘** |
| 부작용 | 없음 (그 자리만) | 얕은 자리의 완성·자동 확인이 사라짐 | 자동완성이 통째로 사라짐 |

```python
safety.configure(
    guarded_roots=["/user"],   # 아는 자리는 이동까지 확실히 막고
    min_depth=2,               # 모르는 얕은 자리는 나열부터 안 하게
)
```

자동완성이 있어야 쓸 만한 앱이라면 위 조합으로 시작하고, 그래도 멈춘다면
`allow_listing=False` 로 내립니다.

### 쓰는 법

```python
from custom_file_dialog import FilePathEdit, safety

safety.configure(
    timeout=1.0,
    ttl=30.0,
    guarded_roots=["/user", "/mnt/nfs"],  # 그 자리 자체는 열지 않을 경로
)

edit = FilePathEdit(mode="open_file", path_timeout=1.0)   # 안전 확인 켜기
```

**`QApplication` 을 만든 직후, 다이얼로그를 만들기 전에** 부르세요. 자동완성
모델을 갈아 끼우는 일이 다이얼로그 생성 시점에 일어나므로, 이미 만들어 둔
다이얼로그는 나중에 `configure()` 를 불러도 보호되지 않습니다.

```python
# 이렇게
safety.configure(guarded_roots=["/user"])
dlg = CustomFileDialog(self, mode="open_file")     # 보호됨

# 이러면 안 됩니다
dlg = CustomFileDialog(self, mode="open_file")     # 이 다이얼로그는 보호 안 됨
safety.configure(guarded_roots=["/user"])          # 다음에 만드는 것부터 적용
```

`is_guarded()` 같은 **판정 자체는 전역이라 항상 최신**입니다. 늦게 부르면
"이미 만든 다이얼로그에 장치가 안 걸린" 것이지, 설정이 무시되는 것은 아닙니다.

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

확인 한 번은 **서버 소켓 프로브 → 실제 `stat`** 두 단계인데, 둘이 `path_timeout`
하나를 나눠 씁니다(합이 그 시간을 넘지 않습니다). 프로브 몫은 그중 1/4 이고
최소 0.25초입니다 — 살아 있는 서버의 TCP 연결은 LAN 에서 수 ms 라, 여기에 예산을
다 주면 응답을 삼키는 마운트 하나가 창 뜨는 시간을 통째로 먹기 때문입니다.
연결이 느린 서버(WAN·VPN)라면 `path_timeout` 을 늘리면 프로브 몫도 함께 늘어납니다.

| `path_timeout` | 프로브 몫 | 남는 `stat` 몫 |
| --- | --- | --- |
| 0.1초 | 0.10초 | 0초 |
| 1.0초 (기본) | 0.25초 | 0.75초 |
| 3.0초 | 0.75초 | 2.25초 |

| 함수 | 설명 |
| --- | --- |
| `safety.configure(timeout, ttl, guarded_roots, min_depth, allow_listing)` | 제한 시간 · 캐시 · 차단 경로 · 자동완성 최소 깊이 · 나열 허용 |

> `safety` 는 세 층(`mounts` → `policy` → `reach`)을 묶은 겉면입니다. 앱은
> `safety` 만 쓰면 되고, 층을 나눈 기준은 **파일시스템을 언제 만지는가**입니다
> — `mounts`(마운트 표)와 `policy`(정책 판정)는 문자열만 보므로 절대 멈추지
> 않고, 실제로 만지는 것은 `reach` 뿐입니다.

| `safety.is_guarded(path)` / `guarded_roots()` | 그 자리 자체를 막았는지 / 막은 목록 |
| `safety.is_too_shallow(path)` / `min_depth()` | 자동완성이 나열하지 않을 만큼 얕은지 / 설정값 |
| `safety.may_list(path)` / `listing_allowed()` | 위 셋 + automount 를 한 번에 판정 / 나열 스위치 상태 |
| `safety.may_stat(path)` | 입력 중인 경로를 **자동으로 stat** 해도 되는지 (부모가 차단 경로 · 깊이 ≤ min_depth · autofs 위면 False) |
| `safety.may_enter(path)` | 그 자리를 **열어(들어가) 되는지** (차단 경로 · 깊이 < min_depth · autofs 위면 False) |
| `safety.may_open(path)` | **확정**(Enter·열기)해도 되는지 (차단 경로 · 깊이 ≤ min_depth 면 False — 끝에 `/` 를 붙인 폴더 표기는 min_depth 깊이부터, 더 깊은 경로는 그대로 허용) |
| `safety.on_automount(path)` / `has_automounts()` | autofs 마운트 위인지 / 시스템에 있는지 |
| `safety.path_depth(path)` | 루트에서부터 센 깊이 (`/user` = 1) |
| `safety.reset()` | 모든 설정을 기본값으로 |
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

> **자주 멈추는 마운트가 있다면 `guarded_roots` 에 넣으세요.** 방화벽 등으로
> 응답이 없는 자리는 `path_timeout` 만으로는 **들어가는 것**을 못 막습니다 —
> 평범한 NFS 마운트는 `may_enter` 가 참이라 사용자가 더블클릭으로 들어갈 수
> 있고, 그때부터는 Qt 가 자기 스레드로 읽으므로 이 라이브러리의 상한 밖입니다.
> `guarded_roots` 에 넣으면 그 자리로 들어가는 통로가 전부 막힙니다.
>
> 우리가 만드는 확인 스레드 쪽은 안전합니다(실측): 막힌 마운트 하나를 2,000회
> 확인해도 멈춘 스레드는 **1개**(같은 마운트는 다시 두드리지 않습니다), 마운트가
> 20개 막혀도 **8개**에서 멈춥니다(`reach.MAX_PENDING_CHECKS`). 그 8개가 쓰는
> 메모리는 0.16MB 이고, 붙들고 있는 것에 **Qt 객체가 없어** 나중에 되살아나도
> 세그폴트 위험이 없습니다.
>
> GUI 스레드가 한 번에 멈추는 시간은 **`path_timeout` 이내**입니다(그 이상은
> 아닙니다). 어디서 막히느냐로 갈립니다 — 서버가 응답을 삼켜 **연결부터**
> 안 되면 프로브 몫(기본 0.25초)에서 끝나지만, **포트는 열려 있고 `stat` 만**
> 멈추면(서버 재부팅 직후·nfsd 먹통) 남은 예산까지 다 씁니다. 실측으로
> `path_timeout=1.0` 이면 1.0초, `3.0` 이면 3.0초입니다.

**Qt5 는 UTF-8 로케일이 필요합니다.** `LANG=C` / `LC_ALL=C` 로 띄우면 PyQt5·
PySide2 가 한글 파일 이름을 **아예 보지 못합니다**(`QDir.entryList()` 가 빈
목록, `QFile.exists()` 가 False). 파이썬 층은 멀쩡해서 유효성 검사는 "있다"고
하는데 다이얼로그에는 안 보이는 어긋남이 생깁니다. Qt6(PyQt6·PySide6)는
무관합니다. 실측으로 `LANG=C` 에서 전체 테스트 275건 중 28건이 이 이유로
깨집니다 — 앱 실행 환경의 로케일을 UTF-8 로 맞춰 주세요.

**차단 경로**(`guarded_roots`) 는 위 표의 통로를 모두 막습니다. Qt 가 C++ 에서 연결해 둔
`activated` · `accept()` 는 파이썬에서 끊을 수 없어, **그 신호가 나기 전 단계인 입력
이벤트를 삼키는** 방식을 씁니다. 그래서 프로그램이 직접 `dialog.setDirectory("/user")`
같이 호출하는 경우는 막지 않습니다(그때는 마지막 방어가 되돌리지만, **되돌리는 시점엔
이미 한 번 읽은 뒤**입니다). 그런 경로는 사이드바·즐겨찾기에 넣지 않는 편이
안전합니다. 또 `/proc/self/mountinfo` 를 쓰므로 마운트 판별은 **리눅스 전용**입니다
(다른 OS 에서는 원격 판별이 안 되어 3단계 타임아웃만 동작합니다).

**나열 끄기**(`allow_listing=False`) 는 이름 그대로 **자동완성만** 막습니다 —
사용자가 눌러 들어가는 것까지 막는 뜻이 아니라, 이동·확정 차단에는 관여하지
않습니다. 반면 **최소 깊이**(`min_depth`) 는 자동완성뿐 아니라 **이동과 확정까지**
막습니다(위 표의 통로 전부). 깊이는 문자열만 보고 세므로 심볼릭 링크가 가리키는
실제 위치가 아니라 **입력한 경로 기준**입니다 (`/link/a` 는 2단계).

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

## 다이얼로그로 쓰기

### `CustomFileDialog`

`QFileDialog` 를 물려받은 클래스입니다. 생성자에 설정을 넣고 `exec()` 로 띄웁니다.

```python
from custom_file_dialog import CustomFileDialog

dlg = CustomFileDialog(
    self,                                  # parent
    mode="open_files",
    caption="이미지 고르기",                # 생략하면 모드별 한국어 기본 제목
    directory="/home/user",
    filters=[("이미지", ["png", "jpg"])],
)
if dlg.exec():
    for path in dlg.selectedFiles():
        print(path)
```

| 생성자 인자 | 설명 |
| --- | --- |
| `parent` | 부모 위젯 (모달 기준). 보통 `self` |
| `mode` | `"open_file"` · `"open_files"` · `"save_file"` · `"directory"` |
| `caption` / `directory` | 창 제목 / 처음 열릴 폴더 (파일 경로를 주면 그 파일이 미리 선택됨) |
| `filters` / `selected_filter` | 파일 필터 ("필터 지정" 참고) / 처음 선택될 항목 |
| `add_all_files_filter` | 필터 끝에 "모든 파일 (*)" 추가 (기본 `False`) |
| `default_suffix` | 저장 모드에서 확장자 자동 부착 (없으면 필터에서 유추) |
| `show_dirs_only` | 폴더 모드에서 파일을 숨길지 (기본 `True`) |
| `options` | 추가 `QFileDialog.Option` |
| `favorites` / `recent` | `True` (기본 위치에 자동 생성) 또는 `FavoritesStore` / `RecentStore` |
| `recent_max` | `recent=True` 로 만들 때 기억할 개수 (기본 20) |
| `sidebar_width` | 사이드바 폭(px). 기본은 항목에 맞춰 자동, 최소 115 |
| `sidebar_urls` / `fixed_sidebar_urls` | 사이드바 기준 목록 / 제거를 막을 위치 |
| `favorites_icon` | 분류·홈 아이콘 (`True` / `QIcon` / `False`) |
| `places` | 위 다섯 개 대신 `Places` 를 통째로 |
| `settings_key` | 자리별 시작 위치 기억 ("용도마다 다른 시작 위치" 참고) |
| `path_timeout` | 죽은 네트워크 경로 방어 제한 시간(초) |

| 메서드 | 설명 |
| --- | --- |
| `.exec()` / `.exec_()` | `QFileDialog` 그대로. 확인하면 참 |
| `.selectedFiles()` | 고른 경로들. **즐겨찾기 링크는 원본으로 복원**되고 모드에 맞게 개수가 맞춰집니다 |
| `.selectedPath()` | 경로 하나 (없으면 `None`) |
| `.selectedNameFilter()` | `QFileDialog` 그대로 |
| `.mode()` / `.places()` | 이 다이얼로그의 선택 모드 / 사이드바 묶음 |

`QFileDialog` 의 나머지 API(`setDirectory()` · `selectFile()` · `currentChanged` ·
`directoryEntered` …)도 전부 그대로 씁니다.

> **항상 Qt 자체 다이얼로그로 뜹니다.** 여기서 더하는 것(사이드바 · 아이콘 · 링크
> 추적 · 우클릭 메뉴 · 차단 경로 방어)은 모두 Qt 위젯을 직접 건드려야 하는데,
> 네이티브 창은 OS 가 그려서 손댈 수 없습니다. 꾸밀 것이 없고 네이티브 창이
> 필요하면 `exec_file_dialog(native=True)` 를 쓰세요.

### `exec_file_dialog()`

띄우고 결과만 받으면 될 때. 안에서 `CustomFileDialog` 를 쓰므로 동작은 같습니다.

```python
from custom_file_dialog import exec_file_dialog

paths, chosen = exec_file_dialog(
    parent=self, mode="open_files", caption="이미지 고르기",
    directory="/home/user", filters=[("이미지", ["png", "jpg"])],
)
```

```python
paths, _ = exec_file_dialog(self, "open_file")    # 파일 1개  -> ["/a/b.csv"] 또는 []
paths, _ = exec_file_dialog(self, "open_files")   # 여러 개    -> ["/a/1.csv", ...]
paths, _ = exec_file_dialog(self, "save_file")    # 저장 이름  -> ["/a/새파일.csv"]
paths, _ = exec_file_dialog(self, "directory")    # 폴더 1개   -> ["/a"]
```

생성자 인자는 `CustomFileDialog` 와 거의 같고, 사이드바를 손보려면 `places=` 에
`Places` 를 넘깁니다. `native=True` 로 두면 정적 메서드를 써서 OS 네이티브 창이
뜹니다(대신 꾸미기는 적용되지 않습니다). 단 **안전장치가 켜져 있으면
`native=True` 라도 Qt 자체 창으로 전환**됩니다 — 네이티브 창에는 보호를 걸 수
없어, 그대로 두면 `guarded_roots`·`min_depth` 를 켜고도 automount 사고가
그대로 납니다.

```python
from custom_file_dialog import Places, exec_file_dialog

paths, chosen = exec_file_dialog(
    mode="open_file",
    places=Places(favorites=store, recent=recent, sidebar_urls=["~", "/mnt/data"]),
    settings_key="입력csv",   # 이 이름의 마지막 폴더에서 열고, 고른 뒤 다시 기억
)
```

즐겨찾기 링크는 `CustomFileDialog` 를 쓰는 경로(= `places=` 를 준 경우)에서
자동으로 원본으로 복원됩니다. 직접 `QFileDialog` 를 쓴 결과라면
`places.resolve_all(paths)` 를 한 번 통과시키세요.

### 그 밖의 헬퍼

내부 헬퍼도 위젯과 독립적이라 그대로 가져다 쓸 수 있습니다.

```python
from custom_file_dialog import build_filter, validate_paths

build_filter([("이미지", ["png", "jpg"])])   # "이미지 (*.png *.jpg)"
ok, reason = validate_paths(paths, mode="open_files")
```

테스트에서는 `exec_file_dialog` 만 monkeypatch 하면 실제 다이얼로그 없이 위젯을
검증할 수 있습니다.

## 코드 구조

의존이 **위에서 아래로만** 흐르도록 6층으로 나눠 두었습니다(순환 없음 — 아래
표는 실제 import 를 위상 정렬해 뽑은 것입니다).

```
0  constants   선택 모드 · 기본 캡션 · 항목 경로 역할
   qt_compat   바인딩(PyQt5/6·PySide2/6)마다 다른 enum·exec 접근 흡수
   safety      경로 안전 판정 (Qt 를 쓰지 않는 순수 로직)
   util        경로 정규화 · QUrl 변환

1  favorites   즐겨찾기 저장소 (심볼릭 링크 폴더)
   filters     Qt 필터 문자열 조립
   guard       나열하면 안 되는 자리 방어 (모델 교체 · 이벤트 삼키기)
   history     최근 경로 · 용도별 마지막 폴더 (QSettings)
   icons       별표 · 시계 · 집 아이콘, 분류 아이콘 제공자
   links       즐겨찾기 링크를 원본처럼 (Look in · 진입 · 상위 폴더)
   sidebar     사이드바 표시(홈 아이콘 · "현재 위치")와 폭
   validators  경로 유효성 판정

2  menus       우클릭 메뉴 (즐겨찾기 추가 · 항목 제거 · 분류 삭제)
   recent      최근 파일 저장소 (favorites 를 물려받음)

3  hooks       위 장치들을 한 번에 걸어 주는 설치기
   places      사이드바에 얹는 것들의 묶음(Places) + 그 설정 보관(PlacesOptions)
   drops       끌어다 놓은 것을 모드에 맞는 경로로 거르는 규칙

4  dialog      CustomFileDialog · exec_file_dialog · resolve_start_dir
   path_edit   FilePathEdit

5  form        FilePathForm
```

몇 가지 원칙:

- **`CustomFileDialog` 가 유일한 구현입니다.** `exec_file_dialog()` 은 그 클래스를
  한 줄로 쓰는 겉면이고, 꾸밀 것이 없고 `native` 일 때만 `QFileDialog` 정적
  메서드로 빠집니다. 위젯도 같은 함수를 씁니다.
- **`safety` 는 Qt 를 쓰지 않습니다.** 단독으로 테스트할 수 있고, Qt 쪽 연동은
  `guard` 가 맡습니다.
- **저장소(`favorites` · `recent`)는 다이얼로그를 모릅니다.** `util` 만 봅니다.
- **`hooks` 는 설치 순서만 압니다.** 실제 구현은 `sidebar` · `links` · `guard` ·
  `menus` 가 각각 들고 있어, 하나를 고칠 때 나머지를 안 봐도 됩니다.

## 테스트

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

**네 바인딩에서 모두 돌려야 합니다.** 바인딩마다 없는 API 가 달라(PySide2 의
`Q_ARG`, Qt6 의 QFileDialog 내부 슬롯) 한 곳에서만 통과하는 코드가 나옵니다.

```bash
QT_API=pyside2 python -m pytest -q      # PySide2 는 Python ≤3.10
QT_API=pyside6 python -m pytest -q
QT_API=pyqt6   python -m pytest -q
```

### 보호가 정말 잠겨 있는지 확인하기

이 라이브러리가 지키는 것들(automount 를 건드리지 않기 · 남의 기록을 지우지
않기 · 죽은 마운트에서 멈추지 않기 …)은 규칙이 여러 통로에 걸쳐 있어서,
테스트가 "통과"해도 무엇을 지키는지 알기 어렵습니다. **핵심 판정에 일부러
결함을 심어** 테스트가 잡는지 보면 확실합니다.

```bash
python tools/protection_check.py             # 34곳 전부
python tools/protection_check.py guard.py    # 그 파일 것만
```

표의 한 줄이 곧 **"예전에 실제로 났던 버그 하나 + 그것을 잡는 테스트"** 입니다.
결함을 심을 자리를 소스 원문으로 적어 두므로, 리팩터링으로 그 줄이 바뀌면
`원문 없음` 으로 알려 줍니다 — 조용히 넘어가지 않습니다. 그때는 보호가
사라진 것인지 옮겨진 것인지 확인하고 표를 고치면 됩니다.

지키는 것들을 갈래로 묶으면 이렇습니다.

| 갈래 | 예 |
| --- | --- |
| automount 를 건드리지 않기 | `risky_place` 가 autofs 를 무시 · 명시 표기(`/`)가 automount 지점도 허용 · 사이드바 차단이 빠짐 |
| 죽은 마운트에서 안 멈추기 | 확인 예산이 두 배로 늘어남 · 멈춘 스레드 묶음 키가 너무 넓거나 좁음 · 상한 없음 |
| 남의 데이터를 안 건드리기 | 최근 목록 자르는 기준 · 앱이 넘긴 저장소의 개수 덮어쓰기 · 링크 풀지 않고 기록 |
| 사용자가 고른 것만 넘기기 | 드롭이 확인 못 한 경로를 부모로 바꿔치기 · 차단된 폴더 이름이 파일 칸에 채워짐 |
| 네트워크에서 느려지지 않기 | 아이콘 제공자를 폴더마다 다시 걸기 · 같은 stat 두 번 · 종류별 캐시 없이 항목마다 조회 |

테스트도 소스와 같은 기준으로 나뉘어 있습니다. 공용 부트스트랩(QSettings 를
임시 폴더로 돌리는 것 포함)은 `conftest.py`, 파일을 넘나드는 도우미는
`helpers.py` 에 있습니다.

```
tests/
  conftest.py                 부트스트랩 · 공용 픽스처 (qapp · 저장소 · 죽은 NFS 흉내)
  helpers.py                  공용 도우미 (_make_tree · _spin · _menu_dialog …)
  test_filters_validators.py  필터 조립 · 경로 유효성
  test_safety.py              죽은 마운트 방어 · 차단 경로 · 자동완성 제한
  test_dialog.py              exec_file_dialog · CustomFileDialog · settings_key
  test_sidebar.py             사이드바 항목 · 순서 · 표시 · 폭 · 아이콘
  test_stores.py              즐겨찾기 · 최근 파일 저장소
  test_links.py               심볼릭 링크 추적
  test_menus.py               우클릭 메뉴
  test_widget.py              FilePathEdit · FilePathForm
```

### 해결됨 — 파일 대화상자가 여는 순간 죽던 문제

증상은 이랬습니다. 스위트가 드물게 SIGSEGV 로 죽고, 네이티브 스택은
`findChild` 가 자식 트리를 훑다 `QObject::objectName()` 에서 터집니다.
`MALLOC_PERTURB_` 를 걸면 재현율이 올라가고, 데스크톱 테마·바인딩과는 무관
합니다. 오래 "우리 가드가 객체를 잘못 잡고 있다"고 봤지만 **원인은 딴 데
있었습니다.**

**근본 원인**: Qt 는 사이드바 목록(`shortcuts`)을 다이얼로그가 사라질 때
사용자 전역 설정(`~/.config/QtProject.conf`)에 저장하고 여는 순간 되읽습니다.
그 파일은 그 사용자의 **모든 Qt 앱**이 함께 씁니다. 그런데 **Qt5 와 Qt6 은 그
값의 비-ASCII 인코딩을 다르게 읽습니다.** 이 라이브러리의 분류 폴더 이름은
반드시 비-ASCII 라(`최근 파일` · `즐겨찾기`) 그 경로가 한 번 저장되면, 두 판을
번갈아 도는 환경에서 **왕복마다 배로** 늘어납니다.

| 왕복 | 저장된 경로 길이 |
|---|---|
| 씨앗 | 25자 |
| 1회 | 33자 |
| 2회 | 45자 |
| 3회 | 69자 |

이 저장소의 개발 환경에서 그 파일이 **805MB** 가 됐고, 그 상태에서는 이
라이브러리를 한 줄도 쓰지 않은 **맨 `QFileDialog` 조차** 여는 순간 죽었습니다.

```python
from PyQt5.QtWidgets import QApplication, QFileDialog
app = QApplication([])
QFileDialog(None).show()          # -> SIGSEGV (100%)
```

설정 저장 위치만 임시 폴더로 돌리면 같은 코드가 멀쩡히 돕니다. 그것이
증거였습니다.

**고친 것**은 둘입니다.

- `CustomFileDialog` 는 닫힐 때 **우리가 얹은 항목만** 사이드바에서 빼고
  닫습니다. Qt 가 전역 설정에 저장하는 목록에 우리 이름이 아예 들어가지
  않습니다. 사용자가 직접 끌어다 놓은 항목이나 앱이 준 항목은 그대로 두고,
  다시 열면 우리 항목도 그대로 돌아옵니다.
- 테스트가 띄우는 자식 프로세스도 `QApplication` 보다 먼저 설정 위치를 임시
  폴더로 돌립니다. 그 프로세스만 이 격리가 빠져 있어서 **사용자의 진짜 설정을
  오염시킨 장본인**이었습니다.

이미 커진 설정 파일이 있다면 그 값만 지우면 됩니다(다른 Qt 앱 설정은
`QtProject.conf` 안 다른 절에 있으므로 파일째 지우지 마세요).

```bash
python - <<'EOF'
from PyQt5.QtCore import QSettings
s = QSettings(QSettings.Scope.UserScope, "QtProject")
print(s.fileName())
s.remove("FileDialog/shortcuts")
EOF
```

## 라이선스

MIT
