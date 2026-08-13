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

from qtpy.QtWidgets import QFileDialog

from . import history, safety
from .constants import DEFAULT_CAPTIONS, SelectMode, normalize_mode
from .filters import build_filter, ensure_suffix, suffix_of
from .places import Places
from .qt_compat import enum_value, exec_dialog, make_options
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
    name_filter=None,
    extra_options=None,
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
        name_filter: ``filters`` 의 예전 이름. 둘 다 주면 ``filters`` 가 이긴다.
        extra_options: ``options`` 의 예전 이름.

        나머지 인자는 :class:`CustomFileDialog` 와 같다.

    Returns:
        ``(paths, selected_filter)`` 튜플. 취소하면 ``([], selected_filter)``.
    """
    filters = filters if filters is not None else name_filter
    options = options if options is not None else extra_options
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


# Qt 기본 아이콘 제공자 — 프로세스에 하나만 두고 모든 다이얼로그가 공유한다.
#
# ``setIconProvider`` 는 **소유권을 가져가지 않는다.** 다이얼로그마다 새로 만들어
# 그 다이얼로그에 매달아 두면, 창을 닫을 때 파이썬 쪽 객체가 먼저 수거되면서
# C++ 모델이 이미 사라진 제공자를 가리켜 세그폴트가 난다(PyQt6 에서 재현).
# 하나를 오래 살려 두면 그 순서 문제가 사라진다.
_PLAIN_ICONS = None


def _plain_icon_provider():
    global _PLAIN_ICONS
    if _PLAIN_ICONS is None:
        from qtpy.QtWidgets import QFileIconProvider

        _PLAIN_ICONS = QFileIconProvider()
    return _PLAIN_ICONS


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
    ):
        mode = normalize_mode(mode)
        super().__init__(parent, caption or DEFAULT_CAPTIONS.get(mode, "선택"))

        self._mode = mode
        self._default_suffix = default_suffix
        self._settings_key = settings_key
        self._sidebar_width = sidebar_width
        self._sidebar_fitted = False
        self._path_timeout = None if path_timeout is None else float(path_timeout)
        self._places = places if places is not None else Places.from_options(
            favorites=favorites,
            recent=recent,
            recent_max=recent_max,
            sidebar_urls=sidebar_urls,
            fixed_urls=fixed_sidebar_urls,
            icon=favorites_icon,
        )

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

        name_filter = build_filter(filters, add_all_files=add_all_files_filter)
        if name_filter and mode != SelectMode.DIRECTORY:
            self.setNameFilters([f for f in name_filter.split(";;") if f])
            if selected_filter:
                self.selectNameFilter(selected_filter)
        if default_suffix:
            self.setDefaultSuffix(default_suffix)

        if not directory and settings_key:
            directory = resolve_start_dir(
                [], last_dir=history.last_dir(settings_key), mode=mode,
                timeout=self._path_timeout,
            )
        if directory:
            self._start_at(directory)

        # 아이콘 제공자는 사이드바보다 먼저 걸어야 사이드바 항목에도 반영된다
        # (QUrlModel 이 등록 시점의 DecorationRole 을 복사해 가기 때문).
        provider = self._places.icon_provider()
        if provider is not None:
            self.setIconProvider(provider)

        # 시작 폴더는 위에서 정해졌으므로 그대로 "현재 위치" 항목이 된다
        current = self.directory().absolutePath()
        urls = self._places.sidebar_urls(current)
        if urls is not None:
            self.setSidebarUrls(to_urls(urls))

        # 사이드바 표시 · 링크 추적 · 우클릭 메뉴 · 차단 경로 방어를 한 번에
        from .hooks import install_hooks

        install_hooks(self, self._places, current)

        # 사이드바에 아이콘이 복사된 뒤부터는 저장소 폴더에서만 제공자를 쓴다
        if provider is not None:
            self._watch_icon_provider(provider, current)

        # show() 로 띄워도 기억이 남도록 exec() 가 아니라 신호에 건다
        self.accepted.connect(self._on_accepted)

    def _watch_icon_provider(self, provider, current):
        """분류 아이콘 제공자를 **저장소 폴더를 볼 때만** 걸어 둔다.

        아이콘 제공자는 목록의 **항목마다** 파이썬으로 불린다. 항목이 수천 개인
        폴더(홈 등)에서는 그것만으로 목록이 채워질 때 GUI 가 100ms 단위로 멈추고,
        그 폴더를 떠난 뒤에도 이미 시작된 나열이 끝나며 멈춤이 이어진다. 분류
        아이콘이 필요한 자리는 저장소 안뿐이라 그 밖에서는 기본 제공자로 되돌린다
        (분류가 아닌 항목에는 어차피 기본 아이콘을 주므로 보이는 것은 같다).
        """
        plain = _plain_icon_provider()
        self._icon_providers = (provider, plain)

        def apply_for(path):
            wanted = provider if self._places.is_inside(path) else plain
            if self.iconProvider() is not wanted:
                self.setIconProvider(wanted)

        self.directoryEntered.connect(apply_for)
        apply_for(current)

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
        super().showEvent(event)
        if not self._sidebar_fitted:
            # 스플리터가 아직 자리 잡기 전(fit 이 None)이면 다음 show 때 다시 시도
            self._sidebar_fitted = fit_sidebar(self, self._sidebar_width) is not None

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
        if safety.may_enter(directory) and isdir(directory):
            self.setDirectory(directory)
            return
        parent_dir = os.path.dirname(directory)
        if parent_dir and safety.may_enter(parent_dir):
            self.setDirectory(parent_dir)
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

    if current_paths:
        current = current_paths[0]
        if current:
            if mode == SelectMode.DIRECTORY:
                # 폴더 모드는 그 폴더 자체에서 시작하는 편이 자연스럽다.
                if isdir(current):
                    return current
                parent = os.path.dirname(current)
                if isdir(parent):
                    return parent
            else:
                if isdir(current):
                    return current
                parent = os.path.dirname(current)
                # 파일 이름까지 넘기면 다이얼로그가 그 이름을 미리 채워 준다.
                if isdir(parent):
                    return current if mode == SelectMode.SAVE_FILE else parent
            # 여기까지 왔으면 그 폴더는 **없거나 만질 수 없다.** 둘 다 시작
            # 위치로는 부적합하므로 다음 후보로 넘어간다 — 예전에는 "없지만
            # 로컬이라 도달 가능"한 경로를 그대로 돌려주어, 입력창에 오타가
            # 남아 있으면 start_dir 을 무시하고 없는 폴더에서 열렸다.

    for candidate in (start_dir, last_dir):
        if candidate and isdir(candidate):
            return candidate

    return os.getcwd()
