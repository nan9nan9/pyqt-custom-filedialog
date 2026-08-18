"""파일 다이얼로그 — 이 패키지의 알맹이.

:class:`CustomFileDialog` 가 **유일한 구현**이다. ``QFileDialog`` 를 물려받아,
사이드바 구성 · 즐겨찾기 · 링크 추적 · 차단 경로 방어 · 용도별 시작 위치를
생성자 인자로 켠다. 실제로 거는 일은 각 모듈이 하고
(:mod:`~custom_file_dialog.hooks` 가 한 번에 걸어 준다) 여기서는 조립만 한다.

:func:`exec_file_dialog` 은 그 클래스를 한 줄로 쓰는 겉면이다. 꾸밀 것이 없고
``native`` 가 참일 때만 ``QFileDialog`` 정적 메서드를 써서 OS 네이티브 창으로
연다. 그 경로의 반환값 정규화는 :func:`_run_dialog` 이 맡는다.

    exec_file_dialog(...)
        ├─ 꾸밀 것이 있으면  -> CustomFileDialog(...).exec()
        └─ 없고 native 면    -> _run_dialog(...)  = QFileDialog 정적 메서드

:func:`resolve_start_dir` 은 "어디서 열까"를 정하는 정책 하나만 담는다.
테스트에서는 :func:`exec_file_dialog` 이나 :func:`_run_dialog` 을 monkeypatch
하면 실제 창 없이 위쪽 동작을 검증할 수 있다.
"""

import os
import time

from qtpy.QtWidgets import QFileDialog, QFileSystemModel

from . import history, safety
from .debuglog import enable_debug
from .debuglog import is_enabled as debug_enabled
from .debuglog import log, step
from .constants import DEFAULT_CAPTIONS, SelectMode, normalize_mode
from .filters import build_filter, ensure_suffix, suffix_of
from .places import PlacesOptions
from .qt_compat import enum_value, exec_dialog, make_options, option_value
from .recent import DEFAULT_RECENT_MAX
from .sidebar import fit_sidebar
from .util import to_urls
from .validators import isdir_check


def exec_file_dialog(
    parent=None,
    mode=SelectMode.OPEN_FILE,
    caption=None,
    directory=None,
    filters=None,
    selected_filter=None,
    native=True,
    default_suffix=None,
    add_all_files_filter=False,
    show_dirs_only=True,
    options=None,
    places=None,
    favorites=None,
    recent=None,
    recent_max=DEFAULT_RECENT_MAX,
    sidebar_urls=None,
    fixed_sidebar_urls=None,
    favorites_icon=True,
    sidebar_width=None,
    settings_key=None,
    path_timeout=safety.DEFAULT_TIMEOUT,
    debug=None,
):
    """다이얼로그를 띄우고 ``(경로 리스트, 선택된 필터)`` 를 돌려준다.

    :class:`CustomFileDialog` 를 한 줄로 쓰는 방법이다. 생성자 인자를 그대로
    받고, 꾸밀 것이 있으면 그 클래스로 띄운다. 꾸밀 것이 없고 ``native`` 가
    참이면 ``QFileDialog`` 정적 메서드를 써서 **OS 네이티브 창**으로 연다.

        paths, chosen = exec_file_dialog(self, "open_file", filters=[("CSV", ["csv"])])
        if paths:
            ...

    바인딩(PyQt5/6 · PySide2/6)과 모드마다 다른 Qt 의 반환 형태를 여기서
    흡수하므로, 반환은 **언제나** ``(리스트, 문자열)`` 이고 취소하면
    ``([], 필터)`` 다.

    Args:
        native: OS 네이티브 다이얼로그 사용 여부. ``False`` 면 정적 메서드에
            ``DontUseNativeDialog`` 를 켜서 Qt 자체 창으로 연다. 꾸밀 것을 하나라도
            주면 (``places`` · ``favorites`` · ``recent`` · ``sidebar_urls`` …)
            이 설정과 무관하게 :class:`CustomFileDialog` 로 전환된다.
        debug: ``True`` 면 단계별 소요 시간을 ``logging`` 으로 남긴다
            (:func:`~custom_file_dialog.debuglog.enable_debug` 와 같다).
            ``None`` 이면 지금 설정을 그대로 둔다.
        나머지 인자는 :class:`CustomFileDialog` 와 같다.

    Returns:
        ``(paths, selected_filter)`` 튜플. 취소하면 ``([], selected_filter)``.
    """
    if debug is not None:
        enable_debug(debug)
    selected_filter = selected_filter or ""

    # 사이드바·아이콘·링크 추적은 Qt 위젯을 직접 건드려야 해서 네이티브 창으로는
    # 불가능하다. 하나라도 주면 인스턴스 다이얼로그로 전환한다.
    # (저장소 인자는 False 도 "안 씀"이므로 진리값으로, 사이드바 인자는 [] 가
    #  "비우기"라는 뜻이 있으므로 None 여부로 가린다.)
    decorated = (
        bool(places)
        or bool(favorites)
        or bool(recent)
        or sidebar_urls is not None
        or fixed_sidebar_urls is not None
        or sidebar_width is not None
    )

    # 안전장치가 켜져 있으면 네이티브 창을 쓸 수 없다. OS 가 그리는 창에는
    # 자동완성도 확정도 가로챌 수 없어, guarded_roots/min_depth 를 켜 두고도
    # automount 사고가 그대로 난다. 꾸밀 것이 없어도 인스턴스로 열어 가드를 건다.
    if not decorated and safety.protection_active():
        decorated = True

    if decorated:
        dialog = CustomFileDialog(
            parent,
            mode=mode,
            caption=caption,
            directory=directory,
            filters=filters,
            selected_filter=selected_filter,
            default_suffix=default_suffix,
            add_all_files_filter=add_all_files_filter,
            show_dirs_only=show_dirs_only,
            options=options,
            places=places,
                favorites=favorites,
                recent=recent,
                recent_max=recent_max,
                sidebar_urls=sidebar_urls,
            fixed_sidebar_urls=fixed_sidebar_urls,
            favorites_icon=favorites_icon,
            sidebar_width=sidebar_width,
            settings_key=settings_key,
            path_timeout=path_timeout,
        )
        try:
            if not exec_dialog(dialog):
                return [], selected_filter
            # selectedFiles() 가 링크 복원 · 개수 맞춤 · 확장자 부착까지 끝내 준다
            return dialog.selectedFiles(), (
                dialog.selectedNameFilter() or selected_filter
            )
        finally:
            # parent 를 주면 다이얼로그가 그 자식으로 남아, 부를 때마다 창과
            # 파일시스템 감시 모델이 쌓인다. 결과를 읽은 뒤 정리를 예약한다
            # (deleteLater 는 이벤트 루프로 돌아간 뒤에 지우므로 반환값은 안전).
            dialog.deleteLater()

    # ---- 네이티브(정적 메서드) 경로 — 꾸밀 것이 없을 때만 ----
    if caption is None:
        caption = DEFAULT_CAPTIONS.get(mode, "선택")
    if settings_key and not directory:
        # 기억해 둔 폴더가 사라졌거나 죽은 마운트면 안전한 곳으로 대체된다
        directory = resolve_start_dir(
            [], last_dir=history.last_dir(settings_key), mode=mode, timeout=path_timeout
        )
    # 위젯과 같은 형태를 받아 준다: 문자열 · [(설명, 확장자들)] · dict …
    name_filter = build_filter(filters, add_all_files=add_all_files_filter)

    paths, chosen = _run_dialog(
        parent, mode, caption, directory or "", name_filter or "",
        selected_filter, native, default_suffix, show_dirs_only, options,
    )
    if settings_key and paths:
        history.remember_dir(settings_key, paths[0])
    return paths, chosen


def _run_dialog(
    parent, mode, caption, directory, name_filter, selected_filter,
    native, default_suffix, show_dirs_only, extra_options,
):
    """모드별로 알맞은 ``QFileDialog`` **정적 메서드**를 골라 실행한다.

    바인딩과 모드마다 다른 반환 형태를 ``(리스트, 필터)`` 로 맞춘다. 테스트에서
    이 함수만 monkeypatch 하면 실제 창 없이 위쪽 동작을 검증할 수 있다.
    """
    if mode == SelectMode.DIRECTORY:
        options = make_options(native, show_dirs_only=show_dirs_only, extra=extra_options)
        path = QFileDialog.getExistingDirectory(parent, caption, directory, options)
        return ([path] if path else []), selected_filter

    options = make_options(native, extra=extra_options)

    if mode == SelectMode.OPEN_FILES:
        paths, chosen = QFileDialog.getOpenFileNames(
            parent, caption, directory, name_filter, selected_filter, options
        )
        return [p for p in paths if p], (chosen or selected_filter)

    if mode == SelectMode.SAVE_FILE:
        path, chosen = QFileDialog.getSaveFileName(
            parent, caption, directory, name_filter, selected_filter, options
        )
        chosen = chosen or selected_filter
        if path:
            # 네이티브 다이얼로그가 확장자를 붙여 주지 않는 플랫폼을 위한 보정.
            path = ensure_suffix(path, default_suffix or suffix_of(chosen))
        return ([path] if path else []), chosen

    path, chosen = QFileDialog.getOpenFileName(
        parent, caption, directory, name_filter, selected_filter, options
    )
    return ([path] if path else []), (chosen or selected_filter)


# 끝을 못 본 이동을 몇 개까지 들고 있을지 (DEBUG 계측용)
_MAX_PENDING_NAVIGATIONS = 8


# 모드별 (AcceptMode, FileMode) 설정값
_INSTANCE_MODES = {
    SelectMode.OPEN_FILE: ("AcceptOpen", "ExistingFile"),
    SelectMode.OPEN_FILES: ("AcceptOpen", "ExistingFiles"),
    SelectMode.SAVE_FILE: ("AcceptSave", "AnyFile"),
    SelectMode.DIRECTORY: ("AcceptOpen", "Directory"),
}


class CustomFileDialog(QFileDialog):
    """설정을 넣어 만들고 ``exec()`` 로 띄우는 다이얼로그 — QFileDialog 그대로.

        dlg = CustomFileDialog(
            self,
            mode="open_file",
            caption="입력 파일 선택",
            filters=[("CSV", ["csv"]), ("엑셀", ["xlsx", "xls"])],
            favorites=store,
            settings_key="입력csv",
        )
        if dlg.exec():
            paths = dlg.selectedFiles()      # 원본 경로로 복원되어 나온다

    ``QFileDialog`` 를 물려받았으므로 ``setDirectory()`` · ``selectNameFilter()`` ·
    ``currentChanged`` 처럼 원래 쓰던 것을 그대로 쓸 수 있다. 이 라이브러리가
    더하는 것(사이드바 구성 · 즐겨찾기 · 최근 파일 · 링크 추적 · 차단 경로 방어 ·
    용도별 시작 위치)은 생성자 인자로 켠다.

    한 줄로 끝내고 싶으면 :func:`exec_file_dialog` 를 쓴다. 안에서 이 클래스를
    쓰므로 동작은 같다.

    **항상 Qt 자체 다이얼로그로 뜬다.** 여기서 더하는 것은 모두 Qt 위젯을 직접
    건드려야 하는데 네이티브 창은 OS 가 그려서 손댈 수 없다. 꾸밀 것이 없고
    네이티브 창이 필요하면 ``exec_file_dialog(native=True)`` 를 쓴다.

    Args:
        parent: 부모 위젯(모달 기준).
        mode: :class:`~custom_file_dialog.constants.SelectMode` 값.
        caption: 창 제목. None 이면 모드별 기본 제목.
        directory: 처음 열릴 폴더(또는 파일 경로 — 그 파일이 미리 선택된다).
            **여기에 줘야 한다.** 만든 뒤에 ``setDirectory()`` 를 부르면 사이드바의
            "현재 위치" 항목은 생성 시점의 폴더를 가리킨 채로 남는다(사이드바는
            생성할 때 한 번 채워지기 때문).
        filters: 파일 필터. ``FilePathEdit(filters=...)`` 와 같은 형태를 모두
            받는다 (:func:`~custom_file_dialog.filters.build_filter` 참고).
        selected_filter: 처음 선택되어 있을 필터 항목.
        default_suffix: 저장 모드에서 확장자가 없을 때 붙일 확장자.
        add_all_files_filter: 필터 끝에 "모든 파일 (*)" 을 붙일지.
        show_dirs_only: 폴더 모드에서 파일을 숨길지.
        options: 추가 ``QFileDialog.Option``.
        places: :class:`~custom_file_dialog.places.Places` 를 직접 줄 때. 주면
            아래 favorites/recent/… 인자는 무시한다.
        favorites: :class:`~custom_file_dialog.favorites.FavoritesStore`, 또는
            ``True`` 면 **기본 위치**(앱 데이터 폴더)에 하나 만들어 쓴다.
            저장소는 디스크 폴더를 가리키는 손잡이라 띄울 때마다 새로 만들어도
            등록해 둔 것은 그대로 남는다.
        recent: :class:`~custom_file_dialog.recent.RecentStore`, 또는 ``True``.
        recent_max: ``recent=True`` 로 만들 때 기억할 개수.
        sidebar_urls: 사이드바 기준 목록 (None 이면 홈 + 현재 위치).
        fixed_sidebar_urls: 사이드바에서 제거를 막을 위치 (None 이면 홈만).
        favorites_icon: 분류·홈 아이콘 (True / QIcon / False).
        settings_key: 이 자리를 구분할 이름. 주면 그 이름으로 마지막에 쓰던
            폴더에서 열고, 고르고 나면 그 폴더를 다시 기억한다.
            ``FilePathEdit(settings_key=...)`` 와 같은 저장소를 쓴다
            (:func:`~custom_file_dialog.history.last_dir` 참고).
        path_timeout: 죽은 네트워크 경로에서 멈추지 않도록 하는 제한 시간(초).
        sidebar_width: 사이드바 폭(px). ``None``(기본)이면 **항목이 잘리지 않을
            만큼** 자동으로 넓힌다(이미 넓으면 그대로 둔다). ``0`` 이면 내용에는
            맞추지 않는다. 둘 다 :data:`MIN_SIDEBAR_WIDTH` 아래로는 내려가지
            않는다. 숫자를 주면 그 폭을 그대로 쓴다.
        debug: ``True`` 면 여는 동안 **단계마다 걸린 시간**을 ``logging`` 으로
            남긴다 — 어디서 시간이 가는지(또는 어디서 멈췄는지) 보려고 쓴다.
            ``None``(기본)이면 지금 설정을 그대로 둔다. 같은 것을
            :func:`~custom_file_dialog.debuglog.enable_debug` 로도, 환경 변수
            ``CFD_DEBUG=1`` 로도 켤 수 있다.
    """

    def __init__(
        self,
        parent=None,
        mode=SelectMode.OPEN_FILE,
        caption=None,
        directory=None,
        filters=None,
        selected_filter=None,
        default_suffix=None,
        add_all_files_filter=False,
        show_dirs_only=True,
        options=None,
        places=None,
        favorites=None,
        recent=None,
        recent_max=DEFAULT_RECENT_MAX,
        sidebar_urls=None,
        fixed_sidebar_urls=None,
        favorites_icon=True,
        settings_key=None,
        path_timeout=safety.DEFAULT_TIMEOUT,
        sidebar_width=None,
        debug=None,
    ):
        if debug is not None:
            enable_debug(debug)
        mode = normalize_mode(mode)
        super().__init__(parent, caption or DEFAULT_CAPTIONS.get(mode, "선택"))

        with step("다이얼로그 생성", mode=mode):
            self._mode = mode
            self._default_suffix = default_suffix
            self._settings_key = settings_key
            self._sidebar_width = sidebar_width
            self._sidebar_fitted = False
            self._places_stripped = False   # done() 이 우리 항목을 빼 갔는가
            # 생성자가 시작 폴더를 잡을 때 이미 setDirectory 가 불리므로,
            # 신호를 잇기(_watch_navigation) 전에 자리를 만들어 둔다.
            self._nav_started = {}
            self._icon_provider = None      # 아래에서 걸릴 수도, 안 걸릴 수도
            self._path_timeout = None if path_timeout is None else float(path_timeout)
            # 위젯과 같은 규칙으로 조립한다(True = 기본 위치에 자동 생성 등).
            # 다이얼로그는 뜬 뒤 설정이 바뀌지 않으므로 한 번 만들고 만다.
            with step("저장소·사이드바 설정 조립"):
                self._places = places if places is not None else PlacesOptions(
                favorites=favorites,
                recent=recent,
                recent_max=recent_max,
                sidebar_urls=sidebar_urls,
                    fixed_urls=fixed_sidebar_urls,
                    icon=favorites_icon,
                ).places()

            # 네이티브 창으로는 아래 것들을 하나도 걸 수 없다
            self.setOptions(
                make_options(
                    native=False,
                    show_dirs_only=(mode == SelectMode.DIRECTORY and show_dirs_only),
                    extra=options,
                )
            )
            accept_mode, file_mode = _INSTANCE_MODES[mode]
            self.setAcceptMode(enum_value("AcceptMode", accept_mode))
            self.setFileMode(enum_value("FileMode", file_mode))

            # **setFileMode 뒤에 ShowDirsOnly 를 다시 건다.** Qt5 의 setFileMode 는
            # 그 비트를 `mode == DirectoryOnly` 로 덮어써서, 옵션을 먼저 걸면 폴더
            # 모드인데 파일이 그대로 나왔다(PyQt5·PySide2, 아무 신호 없이).
            #
            # 순서를 통째로 뒤집는 방법도 되지만 그러면 "파일 형식" 칸이
            # `Directories` 대신 `All Files (*)` 가 된다 — 네이티브 헬퍼가 있는
            # 테마(GNOME 의 gtk3)에서 위젯이 setOptions 시점에 만들어지면서 이름
            # 필터가 나중에 덮어쓰기 때문이다. 4개 바인딩 × 2테마에서 재 보니
            # 이 방식만 둘 다 지킨다.
            if mode == SelectMode.DIRECTORY and show_dirs_only:
                self.setOption(option_value("ShowDirsOnly"), True)

            name_filter = build_filter(filters, add_all_files=add_all_files_filter)
            if name_filter and mode != SelectMode.DIRECTORY:
                self.setNameFilters([f for f in name_filter.split(";;") if f])
                if selected_filter:
                    self.selectNameFilter(selected_filter)
            if default_suffix:
                self.setDefaultSuffix(default_suffix)

            with step("시작 위치 정하기"):
                if not directory and settings_key:
                    directory = resolve_start_dir(
                        [], last_dir=history.last_dir(settings_key), mode=mode,
                        timeout=self._path_timeout,
                    )
                if directory:
                    self._start_at(directory)
                log("시작 폴더 = %s", self.directory().absolutePath())

            # 아이콘 제공자는 사이드바보다 먼저 걸어야 사이드바 항목에도 반영된다
            # (QUrlModel 이 등록 시점의 DecorationRole 을 복사해 가기 때문).
            #
            # **여기서 한 번만 건다.** 폴더를 옮길 때마다 갈아 끼우면 안 된다 —
            # setIconProvider 는 모델이 그때까지 기억한 **모든 노드**를 다시 훑으며
            # 노드마다 QFileInfo 를 만든다(= stat). 실측: 폴더 40개를 둘러본 뒤 교체
            # 한 번에 icon() 1,751회 · 18.5ms(로컬 ext4). 네트워크 홈에서는 그 stat 이
            # 전부 서버 왕복이라, 저장소를 드나들 때마다 그 값을 물었다(즐겨찾기·
            # 최근 파일을 눌렀을 때 가장 느렸던 이유다 — 사이드바를 16번 오가며
            # icon() 5,160회 -> 1,184회).
            with step("아이콘 제공자 준비"):
                provider = self._places.icon_provider()
                if provider is not None:
                    self._icon_provider = provider  # setIconProvider 는 소유하지 않는다
                    self.setIconProvider(provider)

            # 시작 폴더는 위에서 정해졌으므로 그대로 "현재 위치" 항목이 된다
            current = self.directory().absolutePath()
            with step("사이드바 목록 만들기"):
                # 저장소는 **여기서 한 번만** 훑고, 그 결과를 아래 훅에도 넘긴다.
                scanned = self._places.scan_categories()
                self._apply_sidebar_urls(scanned)

            # 사이드바 표시 · 링크 추적 · 우클릭 메뉴 · 차단 경로 방어를 한 번에
            from .hooks import install_hooks

            with step("훅 설치(가드 · 메뉴 · 사이드바 표시)"):
                install_hooks(self, self._places, current, scanned)
        self._watch_navigation()

        # show() 로 띄워도 기억이 남도록 exec() 가 아니라 신호에 건다
        self.accepted.connect(self._on_accepted)

    # ------------------------------------------------------------- 조회
    def mode(self):
        """이 다이얼로그의 선택 모드."""
        return self._mode

    def places(self):
        """사이드바에 얹은 것들의 묶음 (:class:`Places`)."""
        return self._places

    def selectedFiles(self):        # noqa: N802 (Qt 시그니처)
        """고른 경로들. **즐겨찾기 링크는 원본 경로로 되돌려서** 돌려준다.

        모드에 맞게 개수도 맞춘다(여러 개 모드가 아니면 1개). 저장 모드에서는
        확장자가 없으면 ``default_suffix`` 나 선택된 필터의 확장자를 붙인다.
        """
        paths = [p for p in super().selectedFiles() if p]
        if self._mode == SelectMode.SAVE_FILE and paths:
            suffix = self._default_suffix or suffix_of(self.selectedNameFilter())
            paths = [ensure_suffix(paths[0], suffix)]
        elif self._mode != SelectMode.OPEN_FILES:
            paths = paths[:1]
        return self._places.resolve_all(paths)

    def selectedPath(self):         # noqa: N802 (Qt 시그니처에 맞춘 이름)
        """고른 경로 하나 (없으면 None). 여러 개 모드에서는 첫 번째."""
        paths = self.selectedFiles()
        return paths[0] if paths else None

    def showEvent(self, event):     # noqa: N802 (Qt 시그니처)
        """처음 보일 때 사이드바 폭을 항목에 맞춘다.

        스플리터 크기는 **보이기 전에는 정해지지 않으므로**(생성 직후엔
        ``[0, 0]``) 생성자가 아니라 여기서 맞춘다. 한 번만 하므로, 사용자가
        경계를 끌어 조절한 뒤 창을 다시 열어도 그 폭이 유지된다.
        """
        with step("다이얼로그 show()"):
            if self._places_stripped:
                # done() 이 빼 갔을 때만 다시 얹는다. 처음 보일 때는 생성자가
                # 이미 얹어 두었으므로 여기서 또 만들면 저장소를 헛되이 한 번
                # 더 훑는다(네트워크 홈에서는 그것이 그대로 지연이다).
                # 다시 만드는 것이 맞다 — 닫혀 있는 동안 우클릭 메뉴로 즐겨찾기가
                # 바뀌었을 수 있다.
                self._apply_sidebar_urls()
                self._places_stripped = False
            super().showEvent(event)
            if not self._sidebar_fitted:
                # 스플리터가 아직 자리 잡기 전이면 다음 show 때 다시 시도
                with step("사이드바 폭 맞추기"):
                    self._sidebar_fitted = (
                        fit_sidebar(self, self._sidebar_width) is not None
                    )

    def done(self, result):         # noqa: N802 (Qt 시그니처)
        """닫히기 전에 **우리 사이드바 항목을 빼 둔다.**

        Qt 는 사이드바 목록을 다이얼로그가 사라질 때 사용자 전역 설정에 저장
        한다 — 그 사용자의 **모든 Qt 앱**이 함께 쓰는 파일이다. 우리 분류 폴더는
        이름이 반드시 비-ASCII 라 거기 남으면 Qt5/Qt6 왕복마다 경로가 배로
        늘어나고, 끝에는 그 사용자의 파일 대화상자가 전부 죽는다. 자세한 이유는
        :meth:`~custom_file_dialog.places.Places.without_our_places` 에 적었다.

        다시 :meth:`showEvent` 가 불리면 그대로 되돌려 놓으므로, 이 다이얼로그를
        닫았다 다시 여는 쓰임에도 사이드바는 그대로다.
        """
        self.setSidebarUrls(self._places.without_our_places(self.sidebarUrls()))
        self._places_stripped = True
        super().done(result)

    def _watch_navigation(self):
        """폴더 한 번 옮기는 데 얼마나 걸리는지 DEBUG 로 남긴다.

        사이드바를 누르거나 폴더를 두 번 눌렀을 때가 이 구간이다 — 여는 것과는
        따로 재야 한다. 시작은 ``directoryEntered``(그 폴더로 옮기기로 했다),
        끝은 모델의 ``directoryLoaded``(그 폴더를 다 읽었다)로 잡는다. 그 사이가
        **나열에 든 시간**이고, 네트워크 폴더에서 눈에 보이는 지연이 그것이다.

        신호만 이어 두고, 꺼져 있으면 슬롯이 곧바로 돌아온다. 이동 한 번에 두 번
        불릴 뿐이라 평소 부담이 없다.
        """
        try:
            self.directoryEntered.connect(self._debug_entered)
            # 모델이 **하나가 아닐 수 있다** — 안전장치를 켜면 자동완성용
            # GuardedFileSystemModel 이 하나 더 붙는다. findChild 로 하나만 집으면
            # 어느 것이 잡히는지는 자식 목록 순서 운이므로 전부에 잇는다. 같은
            # 이동에 두 번 불려도 시작 기록을 꺼내 쓰는 쪽이 한 번뿐이라 괜찮다.
            for model in self.findChildren(QFileSystemModel):
                model.directoryLoaded.connect(self._debug_loaded)
        except (AttributeError, TypeError):
            pass                    # 신호가 없는 바인딩이면 계측만 포기한다

    def setDirectory(self, directory):      # noqa: N802 (Qt 시그니처)
        """폴더를 옮긴다. 계측이 켜져 있으면 그 시작 시각을 잡아 둔다.

        ``setDirectory`` 는 ``directoryEntered`` 를 **내지 않는다**(그 신호는
        사용자가 옮겼을 때만 나온다). 그래서 프로그램이 옮긴 것까지 재려면
        여기서도 시작을 찍어야 한다 — 앱이 처음 여는 자리가 대개 이쪽이다.
        """
        if debug_enabled():
            path = directory.absolutePath() if hasattr(directory, "absolutePath") \
                else str(directory)
            self._mark_navigation(path)
        super().setDirectory(directory)

    def _mark_navigation(self, path):
        """이동 시작 — 그 폴더의 나열이 끝나면 :meth:`_debug_loaded` 가 받는다."""
        if self._icon_provider is not None:
            self._icon_provider.take_stats()    # 이동 전의 계수는 흘려보낸다
        # **끝을 못 보는 이동이 있다.** 없는 폴더로 옮기거나 마운트가 멈추면
        # directoryLoaded 가 영영 안 온다 — 하필 이 계측을 켜 두는 환경이 그런
        # 곳이다. 그대로 두면 오래 도는 앱에서 계속 쌓인다(실측: 없는 폴더로
        # 500번 옮기니 500개). 최근 것 몇 개만 남긴다.
        if len(self._nav_started) >= _MAX_PENDING_NAVIGATIONS:
            oldest = min(self._nav_started, key=self._nav_started.get)
            del self._nav_started[oldest]
        self._nav_started[os.path.normpath(path)] = time.perf_counter()
        log("> 폴더 이동: %s", path)

    def _debug_entered(self, path):
        if not debug_enabled():
            return
        self._mark_navigation(path)

    def _debug_loaded(self, path):
        if not debug_enabled():
            return
        started = self._nav_started.pop(os.path.normpath(path), None)
        if started is None:
            return                  # 우리가 시작을 못 본 나열(미리 읽기 등)
        log("폴더 이동 끝: %s = %.1f ms", path, (time.perf_counter() - started) * 1000)
        if self._icon_provider is not None:
            self._icon_provider.log_stats("이 폴더를 나열하며")

    def _apply_sidebar_urls(self, scanned=None):
        """우리 사이드바 목록을 얹는다(얹을 게 없으면 그대로 둔다).

        ``scanned`` 를 주면 저장소를 다시 훑지 않는다
        (:meth:`~custom_file_dialog.places.Places.scan_categories`).
        """
        urls = self._places.sidebar_urls(self.directory().absolutePath(), scanned)
        if urls is not None:
            self.setSidebarUrls(to_urls(urls))

    # ------------------------------------------------------------- 내부
    def _start_at(self, directory):
        """시작 위치를 잡는다. 파일 경로면 그 폴더를 열고 이름을 미리 채운다.

        어느 쪽이든 **열어도 되는 자리인지**(:func:`~custom_file_dialog.safety.may_enter`)
        먼저 본다. 여는 순간 그 자리가 통째로 나열되기 때문이다. ``setDirectory``
        는 ``directoryEntered`` 를 내지 않아 마지막 방어(bounce)도 걸리지 않으므로,
        여기서 걸러야 한다. ``path_timeout=None`` 으로 시간 확인을 꺼도 이 판정은
        문자열만 보므로 그대로 동작한다.
        """
        isdir = isdir_check(self._path_timeout)
        # ``~`` 는 여기서 편다. Qt 는 풀어 주지 않아, 그대로 넘기면
        # ``setDirectory("~/문서")`` 가 cwd 기준 상대 경로로 해석된다.
        directory = os.path.expanduser(directory or "")
        if safety.may_enter(directory) and isdir(directory):
            self.setDirectory(directory)
            return
        parent_dir = os.path.dirname(directory)
        # 폴백에도 **실제 확인**이 필요하다. may_enter 는 문자열과 마운트 표만
        # 보므로 죽은 NFS 도 통과시킨다 — 기억해 둔 파일 경로로 열 때 그 부모를
        # 그대로 setDirectory 하면 GUI 가 D 상태로 멈춘다.
        if parent_dir and safety.may_enter(parent_dir) and isdir(parent_dir):
            self.setDirectory(parent_dir)
        # 이름을 채우는 것은 **파일 경로를 받았을 때**의 배려다. 우리가 열기를
        # 거부한 자리(may_enter=False)면 그것은 폴더 이름이라, 채워 두면 파일
        # 이름 칸에 "user" 가 들어가고 selectedFiles() 가 사용자가 고른 적도
        # 없는 <cwd>/user 를 돌려준다.
        if safety.may_enter(directory):
            self.selectFile(os.path.basename(directory))

    def _on_accepted(self):
        paths = self.selectedFiles()
        if not paths:
            return
        # 사이드바에 "최근 파일"을 얹었다면 고른 것을 거기 쌓는다. 위젯을 거치지
        # 않고 이 클래스로 바로 띄워도 목록이 자라야 한다. (recent 를 안 쓰면
        # record_recent 는 아무 일도 하지 않고, 폴더·저장소 안의 링크는 저장소가
        # 스스로 거른다. FilePathEdit 도 한 번 더 기록하지만 재기록은 지웠다
        # 다시 만드는 동작이라 결과가 같다.)
        self._places.record_recent(paths)
        if self._settings_key:
            history.remember_dir(self._settings_key, paths[0])


def resolve_start_dir(
    current_paths,
    start_dir=None,
    last_dir=None,
    mode=SelectMode.OPEN_FILE,
    timeout=None,
):
    """다이얼로그를 열 때 초기 위치로 쓸 경로를 결정한다.

    우선순위는 다음과 같다.

    1. 현재 입력되어 있는 경로 (그 파일이 있는 폴더에서 시작)
    2. 생성자에 지정한 ``start_dir``
    3. 직전에 선택했던 폴더 (history 사용 시)
    4. 현재 작업 디렉터리

    ``timeout`` 을 주면 죽은 네트워크 경로를 건너뛴다. 다이얼로그가 응답 없는
    마운트에서 열려 통째로 멈추는 것을 막는다
    (:mod:`~custom_file_dialog.safety` 참고).
    """
    isdir = isdir_check(timeout)

    # 판정(isdir_check)만 ``~`` 를 펴고 **결과를 안 펴면** 호출자가 그대로 Qt 에
    # 넘겨 cwd 에서 열린다 — Qt 는 ``~`` 를 풀지 않는다. 후보로 인정한 순간
    # 편 형태로 바꿔서 다음 단계에 넘긴다.
    current = os.path.expanduser((current_paths or [""])[0] or "")
    if current:
        if isdir(current):
            return current
        parent = os.path.dirname(current)
        if isdir(parent):
            # 저장 모드는 파일 이름까지 넘긴다 — 다이얼로그가 미리 채워 준다.
            return current if mode == SelectMode.SAVE_FILE else parent
        # 여기까지 왔으면 그 폴더는 **없거나 만질 수 없다.** 둘 다 시작 위치로는
        # 부적합하므로 다음 후보로 넘어간다 — 예전에는 "없지만 로컬이라 도달
        # 가능"한 경로를 그대로 돌려주어, 입력창에 오타가 남아 있으면 start_dir
        # 을 무시하고 없는 폴더에서 열렸다.

    for candidate in (start_dir, last_dir):
        if candidate and isdir(candidate):
            return os.path.expanduser(candidate)

    return os.getcwd()
