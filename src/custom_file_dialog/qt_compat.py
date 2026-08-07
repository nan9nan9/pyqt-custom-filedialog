"""바인딩(PyQt5/PyQt6/PySide2/PySide6)마다 다른 Qt 접근 방식을 흡수한다.

Qt5 는 ``QFileDialog.ExistingFile`` 도 되지만 Qt6 은 ``QFileDialog.FileMode``
스코프를 거쳐야 하고, ``QDialog.exec_`` 와 ``exec`` 도 바인딩마다 있고 없다.
그런 차이를 여기 한곳에 모아 두어 나머지 모듈은 신경 쓰지 않게 한다.

Qt 위젯만 알면 되고 이 패키지의 다른 모듈은 참조하지 않는다(가장 아래 층).
"""

from qtpy.QtWidgets import QFileDialog


def scoped_attr(owner, scope_name, value_name):
    """Qt6 에서 새로 생긴 enum 스코프를 건너뛰고 값을 얻는다.

    Qt6 은 ``QStyle.StateFlag.State_Enabled`` 만 허용하고 Qt5 는 스코프 없이도
    허용한다. 스코프가 있으면 그쪽을, 없으면 소유 클래스에서 바로 찾는다.

        scoped_attr(QStyle, "StateFlag", "State_Enabled")
        scoped_attr(QStyle, "StandardPixmap", "SP_DirOpenIcon")
    """
    scope = getattr(owner, scope_name, owner)
    return getattr(scope, value_name)


def enum_value(enum_name, value_name):
    """``QFileDialog`` 의 스코프 enum 값을 바인딩에 무관하게 얻는다.

        enum_value("FileMode", "ExistingFile")
        enum_value("AcceptMode", "AcceptSave")
    """
    return scoped_attr(QFileDialog, enum_name, value_name)


def option_value(name):
    """``QFileDialog.Option`` 값을 바인딩에 무관하게 얻는다."""
    return enum_value("Option", name)


def no_options():
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
    options = no_options()
    if not native:
        options |= option_value("DontUseNativeDialog")
    if show_dirs_only:
        options |= option_value("ShowDirsOnly")
    if extra is not None:
        options |= extra
    return options


def exec_dialog(dialog):
    """``QDialog`` 를 띄운다 — Qt5 는 ``exec_``, Qt6 은 ``exec``.

    Returns:
        ``QDialog.DialogCode`` 값(확인이면 참).
    """
    run = getattr(dialog, "exec_", None) or dialog.exec
    return run()
