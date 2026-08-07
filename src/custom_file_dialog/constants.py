"""선택 모드, 기본 캡션 등 공용 상수 정의."""

from qtpy.QtCore import Qt

# 사이드바 항목(QUrlModel)과 파일 목록 항목(QFileSystemModel) 모두
# 이 역할에 경로(QUrl 또는 문자열)를 담는다. Qt5/Qt6 동일.
PATH_ROLE = Qt.ItemDataRole.UserRole + 1


class SelectMode:
    """`FilePathEdit` 가 열 `QFileDialog` 의 종류.

    문자열 상수로 두어 ``FilePathEdit(mode="directory")`` 처럼 문자열을
    그대로 넘겨도 되고, ``SelectMode.DIRECTORY`` 로 넘겨도 되게 한다.
    """

    OPEN_FILE = "open_file"     # 기존 파일 1개 선택
    OPEN_FILES = "open_files"   # 기존 파일 여러 개 선택
    SAVE_FILE = "save_file"     # 저장할 파일 이름 지정 (없어도 됨)
    DIRECTORY = "directory"     # 디렉터리 선택

    ALL = (OPEN_FILE, OPEN_FILES, SAVE_FILE, DIRECTORY)


# 모드별 기본 다이얼로그 제목
DEFAULT_CAPTIONS = {
    SelectMode.OPEN_FILE: "파일 선택",
    SelectMode.OPEN_FILES: "파일 선택 (여러 개)",
    SelectMode.SAVE_FILE: "다른 이름으로 저장",
    SelectMode.DIRECTORY: "폴더 선택",
}

# 모드별 입력창 기본 placeholder
DEFAULT_PLACEHOLDERS = {
    SelectMode.OPEN_FILE: "파일 경로를 입력하거나 끌어다 놓으세요",
    SelectMode.OPEN_FILES: "파일 경로들 (구분자로 구분)",
    SelectMode.SAVE_FILE: "저장할 파일 경로",
    SelectMode.DIRECTORY: "폴더 경로를 입력하거나 끌어다 놓으세요",
}

# 모드별 "경로가 존재해야 하는가" 기본값.
# save_file 은 아직 없는 파일을 지정하는 것이 정상이므로 False.
DEFAULT_MUST_EXIST = {
    SelectMode.OPEN_FILE: True,
    SelectMode.OPEN_FILES: True,
    SelectMode.SAVE_FILE: False,
    SelectMode.DIRECTORY: True,
}

# 찾아보기 버튼 기본 텍스트
DEFAULT_BUTTON_TEXT = "..."

# open_files 모드에서 여러 경로를 한 줄에 표시할 때 쓰는 구분자
DEFAULT_SEPARATOR = "; "

# history 기능을 켰을 때 기억할 최근 경로 개수
DEFAULT_HISTORY_SIZE = 10

# 유효하지 않은 경로일 때 입력창 테두리 색
INVALID_BORDER_COLOR = "#e53935"

# 모든 파일 필터 (필터 목록 끝에 자동으로 붙는 항목)
ALL_FILES_FILTER = ("모든 파일", ["*"])


def normalize_mode(mode):
    """모드 문자열을 검증해서 반환한다. 잘못된 값이면 ValueError."""
    if mode not in SelectMode.ALL:
        raise ValueError(
            "mode 는 %s 중 하나여야 합니다 (받은 값: %r)"
            % (", ".join(repr(m) for m in SelectMode.ALL), mode)
        )
    return mode


def is_multi_mode(mode):
    """여러 경로를 선택하는 모드인지 여부."""
    return mode == SelectMode.OPEN_FILES
