"""DEBUG 모드 — 단계별 소요 시간을 logging 으로 남긴다."""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from custom_file_dialog import CustomFileDialog, enable_debug
from custom_file_dialog import debuglog


@pytest.fixture(autouse=True)
def _off():
    """테스트마다 꺼진 상태에서 시작하고, 끝나면 되돌린다."""
    enable_debug(False)
    yield
    enable_debug(False)


def _records(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == debuglog.LOGGER_NAME]


def test_off_by_default(qapp, tmp_path, caplog):
    """기본은 꺼져 있다 — 라이브러리를 그냥 쓰면 로그가 없다."""
    with caplog.at_level(logging.DEBUG, logger=debuglog.LOGGER_NAME):
        # caplog 이 로거 수준을 올리므로, 우리 스스로 켜지 않았음을 본다
        assert debuglog._handler is None
    dialog = CustomFileDialog(None, mode="open_file", directory=str(tmp_path))
    dialog.done(0)


def test_step_costs_nothing_when_off(monkeypatch):
    """꺼져 있으면 **시각도 읽지 않는다.**

    이 패키지는 죽은 네트워크 경로를 다루는 것이 일이라, 재는 비용 자체가
    GUI 지연이 된다. 그래서 꺼졌을 때는 perf_counter 조차 부르면 안 된다.
    """
    calls = []
    monkeypatch.setattr(debuglog.time, "perf_counter",
                        lambda: calls.append(1) or 0.0)
    assert not debuglog.is_enabled()
    with debuglog.step("아무것도 안 해야 한다"):
        pass
    assert calls == []


def test_logs_each_step_of_opening(qapp, tmp_path, caplog):
    """열 때 지나가는 단계마다 이름과 걸린 시간이 남는다."""
    enable_debug()
    with caplog.at_level(logging.DEBUG, logger=debuglog.LOGGER_NAME):
        dialog = CustomFileDialog(None, mode="open_file", directory=str(tmp_path))
        dialog.done(0)
    messages = _records(caplog)
    assert any("다이얼로그 생성" in m and "ms" in m for m in messages)
    for label in ("시작 위치 정하기", "사이드바 목록 만들기", "훅 설치"):
        assert any(label in m for m in messages), label


def test_debug_argument_turns_it_on(qapp, tmp_path, caplog):
    """``debug=True`` 한 줄로도 켜진다 (enable_debug 와 같은 것)."""
    with caplog.at_level(logging.DEBUG, logger=debuglog.LOGGER_NAME):
        dialog = CustomFileDialog(
            None, mode="open_file", directory=str(tmp_path), debug=True
        )
        dialog.done(0)
        # caplog.at_level 은 블록을 나가며 로거 수준을 되돌린다 — 안에서 본다
        assert debuglog.is_enabled()
    assert any("다이얼로그 생성" in m for m in _records(caplog))


def test_entering_a_step_is_logged_before_it_finishes(caplog):
    """들어간 것도 남긴다 — **멈추면 끝 줄이 영영 안 나오기** 때문이다.

    죽은 마운트에서 멈췄을 때 어디서 멈췄는지는 이 줄로만 알 수 있다.
    """
    enable_debug()
    with caplog.at_level(logging.DEBUG, logger=debuglog.LOGGER_NAME):
        try:
            with debuglog.step("멈추는 자리"):
                assert any("> 멈추는 자리" in m for m in _records(caplog))
                raise OSError("죽은 마운트")
        except OSError:
            pass
    messages = _records(caplog)
    # 예외로 빠져나가도 그때까지 걸린 시간이 남는다
    assert any("멈추는 자리" in m and "중간에 실패" in m for m in messages)


def test_does_not_add_a_handler_when_the_app_already_configured_logging():
    """앱이 이미 핸들러를 달아 두었으면 우리 것을 붙이지 않는다(중복 출력 방지)."""
    logger = logging.getLogger(debuglog.LOGGER_NAME)
    theirs = logging.NullHandler()
    logger.addHandler(theirs)
    try:
        enable_debug()
        assert debuglog._handler is None
        assert logger.handlers == [theirs]
    finally:
        logger.removeHandler(theirs)


def test_enable_debug_false_removes_our_handler():
    """끄면 우리가 붙인 핸들러를 뗀다 — 남기면 남의 로그까지 새어 나온다."""
    enable_debug()
    assert debuglog._handler is not None
    assert enable_debug(False) is False
    assert debuglog._handler is None
    assert not debuglog.is_enabled()


def test_labels_line_up_regardless_of_hangul():
    """이름 길이·한글 여부와 무관하게 시간 칸이 **같은 자리**에서 시작한다.

    글자 수로 세면 한글이 든 줄만 오른쪽으로 밀린다(한글은 두 칸을 차지한다).
    """
    # 실제 줄은 `들여쓰기 + _dotted(...)` 로 찍히므로 들여쓰기까지 세어 본다
    widths = {
        indent + debuglog._width(debuglog._dotted(label, indent))
        for label, indent in (
            ("abc", 0), ("사이드바", 0), ("훅 설치(가드 · 메뉴)", 0),
            ("abc", 2), ("사이드바", 2),        # 들여쓴 줄도 같은 자리에 맞는다
            ("저장소 훑기: recent", 4),
        )
    }
    assert len(widths) == 1, widths


def test_logs_each_navigation_with_icon_counts(qapp, tmp_path, caplog):
    """폴더를 옮길 때마다 걸린 시간과 **아이콘 조회 수**가 남는다.

    사이드바를 누를 때가 이 구간이다 — 여는 것과는 따로 재야 한다. 아이콘은
    Qt 가 목록의 항목마다(화면에 안 보이는 것까지) 묻고, 답을 만들려면 아이콘
    테마 폴더(``~/.icons``)를 뒤진다. 홈이 네트워크면 그 하나하나가 서버
    왕복이라(실측 항목당 4.9ms · 274개에 1.3초) 캐시를 두었는데, 그 캐시가 그
    환경에서 실제로 듣는지는 "물음 대 조회" 두 숫자로만 알 수 있다.
    """
    from custom_file_dialog import FavoritesStore

    work = tmp_path / "작업"
    work.mkdir()
    for index in range(5):
        (work / ("자료%d.csv" % index)).write_text("x")

    # 아이콘 계수는 **우리 아이콘 제공자가 걸렸을 때만** 나온다. 즐겨찾기·최근
    # 파일이 없으면 Qt 기본 제공자가 쓰이고, 그건 우리가 셀 수 없다.
    favorites = FavoritesStore(base_dir=str(tmp_path / "fav"))
    favorites.add_category("설계")
    dialog = CustomFileDialog(
        None, mode="open_file", directory=str(tmp_path), favorites=favorites
    )
    enable_debug()
    with caplog.at_level(logging.DEBUG, logger=debuglog.LOGGER_NAME):
        dialog.setDirectory(str(work))
        for _ in range(60):
            qapp.processEvents()
    dialog.done(0)

    messages = _records(caplog)
    assert any("폴더 이동" in m and str(work) in m for m in messages), messages
    assert any("폴더 이동 끝" in m and "ms" in m for m in messages), messages
    assert any("아이콘: 물음" in m and "Qt 조회" in m for m in messages), messages


def test_icon_counts_show_the_cache_working(qapp, tmp_path):
    """같은 종류가 되풀이되면 **물음은 늘어도 조회는 안 는다.**

    이 관계가 깨지면 캐시가 죽은 것이고, 네트워크 홈에서 그 값이 그대로 지연이
    된다. 숫자로 못 박아 둔다.
    """
    from qtpy.QtCore import QFileInfo

    from custom_file_dialog import icons as icons_module
    from custom_file_dialog.favorites import FavoritesStore
    from custom_file_dialog.icons import CategoryIconProvider

    for index in range(20):
        (tmp_path / ("자료%02d.csv" % index)).write_text("x")

    icons_module._plain_icons.clear()
    icons_module.take_icon_stats()
    provider = CategoryIconProvider(FavoritesStore(base_dir=str(tmp_path / "fav")))
    for index in range(20):
        provider.icon(QFileInfo(str(tmp_path / ("자료%02d.csv" % index))))

    asked, missed, _spent = icons_module.take_icon_stats()
    assert asked == 20
    assert missed == 1, "종류가 하나뿐인데 Qt 에 %d번 물었다" % missed
    # 가져간 뒤에는 0 부터 다시 센다
    assert icons_module.take_icon_stats() == (0, 0, 0.0)
