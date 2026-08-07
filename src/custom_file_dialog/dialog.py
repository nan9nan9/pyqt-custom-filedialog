"""QFileDialog 호출을 모드별로 감싼 얇은 래퍼.

바인딩(PyQt5/PyQt6/PySide2/PySide6)마다 미묘하게 다른 반환값/enum 접근을
여기서 흡수해서, 위젯 쪽은 항상 ``(경로 리스트, 선택된 필터)`` 만 받게 한다.
테스트에서는 이 모듈의 :func:`exec_file_dialog` 만 monkeypatch 하면 실제
다이얼로그를 띄우지 않고 위젯 동작을 검증할 수 있다.
"""

import os

from qtpy.QtWidgets import QFileDialog

from . import history, safety
from .constants import DEFAULT_CAPTIONS, SelectMode
from .util import to_urls
from .filters import ensure_suffix, suffix_of


def _enum(enum_name, value_name):
    """스코프 enum 값을 바인딩에 무관하게 얻는다.

    Qt6 은 ``QFileDialog.FileMode.ExistingFile`` 만 허용하고 Qt5 는 둘 다
    허용한다. 스코프가 있으면 그쪽을 먼저 본다.
    """
    scope = getattr(QFileDialog, enum_name, QFileDialog)
    return getattr(scope, value_name)


def _option(name):
    """QFileDialog.Option 값을 바인딩에 무관하게 얻는다."""
    return _enum("Option", name)


def _no_options():
    """아무 옵션도 켜지지 않은 빈 옵션 값."""
    option_type = getattr(QFileDialog, "Option", None)
    if option_type is not None:
        return option_type(0)
    return QFileDialog.Options()


def make_options(native=True, show_dirs_only=False, extra=None):
    """QFileDialog 에 넘길 options 값을 조립한다.

    Args:
        native: False 면 Qt 자체 다이얼로그를 강제한다(DontUseNativeDialog).
            오프스크린/테스트 환경이나 일관된 UI 가 필요할 때 쓴다.
        show_dirs_only: 디렉터리 선택 시 파일을 숨긴다.
        extra: 추가로 OR 할 QFileDialog.Option 값.
    """
    options = _no_options()
    if not native:
        options |= _option("DontUseNativeDialog")
    if show_dirs_only:
        options |= _option("ShowDirsOnly")
    if extra is not None:
        options |= extra
    return options


def exec_file_dialog(
    parent=None,
    mode=SelectMode.OPEN_FILE,
    caption=None,
    directory=None,
    name_filter=None,
    selected_filter=None,
    native=True,
    default_suffix=None,
    show_dirs_only=True,
    extra_options=None,
    places=None,
    remember=None,
    path_timeout=safety.DEFAULT_TIMEOUT,
):
    """모드에 맞는 QFileDialog 를 띄우고 결과를 반환한다.

    Args:
        parent: 부모 위젯(모달 기준). 보통 ``self.window()``.
        mode: :class:`~custom_file_dialog.constants.SelectMode` 값.
        caption: 다이얼로그 제목. None 이면 모드별 기본 제목.
        directory: 처음 열릴 디렉터리(또는 파일 경로).
        name_filter: Qt 필터 문자열 (``"이미지 (*.png);;모든 파일 (*)"``).
        selected_filter: 처음 선택되어 있을 필터 항목.
        native: OS 네이티브 다이얼로그 사용 여부.
        default_suffix: 저장 모드에서 확장자가 없을 때 붙일 확장자.
            None 이면 선택된 필터에서 유추한다.
        show_dirs_only: 디렉터리 모드에서 파일을 숨길지 여부.
        extra_options: 추가 QFileDialog.Option.
        places: :class:`~custom_file_dialog.places.Places` — 사이드바에 얹을
            것들(즐겨찾기 · 최근 파일 · 직접 지정 위치 · 아이콘 · 보호 위치).
            주면 **네이티브 다이얼로그를 쓸 수 없어** 자동으로 Qt 자체
            다이얼로그로 전환된다(네이티브 창은 OS 가 그리므로 Qt 가 사이드바를
            바꿀 수 없다).
        remember: **용도 이름.** 주면 그 용도로 마지막에 쓰던 폴더에서 열고,
            고르고 나면 그 폴더를 다시 기억한다. 자리마다 다른 이름을 주면
            (``"입력csv"`` · ``"결과저장"``) 각자 따로 기억한다.

            ``directory`` 를 함께 주면 그쪽이 우선이다(기억은 그래도 갱신된다).
            ``FilePathEdit(settings_key=...)`` 와 **같은 저장소**를 쓰므로 같은
            이름을 주면 위젯과 다이얼로그가 기억을 주고받는다.
        path_timeout: 기억해 둔 폴더가 죽은 마운트를 가리킬 때 멈추지 않도록
            하는 제한 시간(초). ``None`` 이면 확인하지 않는다. ``remember`` 를
            쓸 때만 의미가 있다.

    Returns:
        ``(paths, selected_filter)`` 튜플. 취소하면 ``([], selected_filter)``.
    """
    if caption is None:
        caption = DEFAULT_CAPTIONS.get(mode, "선택")
    if remember and not directory:
        # 기억해 둔 폴더가 사라졌거나 죽은 마운트면 안전한 곳으로 대체된다
        directory = resolve_start_dir(
            [], last_dir=history.last_dir(remember), mode=mode, timeout=path_timeout
        )
    paths, chosen = _run_dialog(
        parent, mode, caption, directory or "", name_filter or "",
        selected_filter or "", native, default_suffix, show_dirs_only,
        extra_options, places,
    )
    if remember and paths:
        history.remember_dir(remember, paths[0])
    return paths, chosen


def _run_dialog(
    parent, mode, caption, directory, name_filter, selected_filter,
    native, default_suffix, show_dirs_only, extra_options, places,
):
    """모드별로 알맞은 QFileDialog 호출을 골라 실행한다."""
    if places:
        # 사이드바/아이콘을 건드리려면 인스턴스를 직접 만들어야 한다
        # (정적 메서드로는 불가).
        return _exec_instance_dialog(
            parent, mode, caption, directory, name_filter, selected_filter,
            default_suffix, show_dirs_only, extra_options, places,
        )

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


# 모드별 (AcceptMode, FileMode) 설정값
_INSTANCE_MODES = {
    SelectMode.OPEN_FILE: ("AcceptOpen", "ExistingFile"),
    SelectMode.OPEN_FILES: ("AcceptOpen", "ExistingFiles"),
    SelectMode.SAVE_FILE: ("AcceptSave", "AnyFile"),
    SelectMode.DIRECTORY: ("AcceptOpen", "Directory"),
}


def _exec_instance_dialog(
    parent, mode, caption, directory, name_filter, selected_filter,
    default_suffix, show_dirs_only, extra_options, places,
):
    """QFileDialog 인스턴스를 직접 만들어 띄운다(사이드바/아이콘 커스터마이즈용).

    정적 메서드(getOpenFileName 등)로는 사이드바나 아이콘을 건드릴 수 없어서,
    이 경로만 인스턴스를 만든다. 네이티브 창은 OS 가 그려서 setSidebarUrls() /
    setIconProvider() 가 무시되므로 항상 Qt 자체 다이얼로그
    (DontUseNativeDialog)로 연다.
    """
    dialog = QFileDialog(parent, caption)
    dialog.setOptions(
        make_options(
            native=False,
            show_dirs_only=(mode == SelectMode.DIRECTORY and show_dirs_only),
            extra=extra_options,
        )
    )

    accept_mode, file_mode = _INSTANCE_MODES[mode]
    dialog.setAcceptMode(_enum("AcceptMode", accept_mode))
    dialog.setFileMode(_enum("FileMode", file_mode))

    if name_filter and mode != SelectMode.DIRECTORY:
        dialog.setNameFilters([f for f in name_filter.split(";;") if f])
        if selected_filter:
            dialog.selectNameFilter(selected_filter)
    if default_suffix:
        dialog.setDefaultSuffix(default_suffix)

    # 시작 위치. 파일 경로가 오면 폴더로 열고 파일 이름은 미리 채워 준다.
    if directory:
        if os.path.isdir(directory):
            dialog.setDirectory(directory)
        else:
            parent_dir = os.path.dirname(directory)
            if parent_dir:
                dialog.setDirectory(parent_dir)
            dialog.selectFile(os.path.basename(directory))

    # 아이콘 제공자는 사이드바보다 먼저 걸어야 사이드바 항목에도 반영된다
    # (QUrlModel 이 등록 시점의 DecorationRole 을 복사해 가기 때문).
    provider = places.icon_provider()
    if provider is not None:
        dialog.setIconProvider(provider)

    # 시작 폴더는 위에서 정해졌으므로 그대로 "현재 위치" 항목이 된다
    current = dialog.directory().absolutePath()
    urls = places.sidebar_urls(current)
    if urls is not None:
        dialog.setSidebarUrls(to_urls(urls))

    # 다이얼로그에 거는 것들(사이드바 표시 · 링크 추적 · 메뉴 · 차단)은
    # hooks 가 한 번에 처리한다. 사이드바를 채울 때 쓴 "현재 위치"를 그대로
    # 넘겨 이름을 갈아 끼울 항목이 어긋나지 않게 한다.
    from .hooks import install_hooks

    install_hooks(dialog, places, current)

    accepted = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
    if not accepted:
        return [], selected_filter

    paths = [p for p in dialog.selectedFiles() if p]
    chosen = dialog.selectedNameFilter() or selected_filter
    if mode == SelectMode.SAVE_FILE and paths:
        paths = [ensure_suffix(paths[0], default_suffix or suffix_of(chosen))]
    elif mode != SelectMode.OPEN_FILES:
        paths = paths[:1]
    return paths, chosen


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
    isdir = (
        os.path.isdir
        if timeout is None
        else (lambda p: safety.safe_isdir(p, timeout))
    )

    if current_paths:
        current = current_paths[0]
        if current:
            if mode == SelectMode.DIRECTORY:
                # 폴더 모드는 그 폴더 자체에서 시작하는 편이 자연스럽다.
                return current if isdir(current) else os.path.dirname(current)
            if isdir(current):
                return current
            parent = os.path.dirname(current)
            # 파일 이름까지 넘기면 다이얼로그가 그 이름을 미리 채워 준다.
            if isdir(parent):
                return current if mode == SelectMode.SAVE_FILE else parent
            if parent and (timeout is None or safety.is_reachable(parent, timeout)):
                return parent

    for candidate in (start_dir, last_dir):
        if candidate and isdir(candidate):
            return candidate

    return os.getcwd()
