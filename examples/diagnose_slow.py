"""다이얼로그가 왜 느린지 **그 환경에서** 찍어 본다.

    python examples/diagnose_slow.py                      # 임시 폴더로 재 본다
    python examples/diagnose_slow.py --work ~/작업       # **실제** 폴더로 재 본다
    python examples/diagnose_slow.py --safety            # 안전장치를 켠 상태로

``--work`` 를 주지 않으면 임시 폴더(``/tmp/cfd-진단-…``)를 만들어 쓰고 끝나면
지운다. 그것은 파일 5개짜리 **가짜 폴더**라, 느린 자리를 찾는 데는 실제로 쓰는
폴더를 주는 편이 낫다. ``--work`` 로 준 폴더는 **건드리지도 지우지도 않는다.**

느린 자리는 둘로 나뉘고, 이 스크립트도 그렇게 나눠 잰다.

1. **여는 순간** — 창이 처음 뜰 때까지. 파이썬·Qt 를 올리고, 저장된 설정과
   표준 위치를 읽고, 아이콘 테마를 뒤지고, 시작 폴더를 나열한다. 이 값들은
   Qt 가 프로세스 안에 캐시하므로 **새 프로세스에서 한 번** 재야 한다.
   단계마다 무엇을 읽는지와, 맨 ``QFileDialog`` 대비 이 라이브러리가 더 쓰는
   시간을 함께 찍는다 — 대개는 여는 비용의 대부분이 Qt 자신이다.
2. **오갈 때** — 이미 열린 창에서 사이드바 항목을 누를 때. 데모와 같은 구성으로
   하나씩 눌러 가며 잰다. 창은 실제로 뜨며(오프스크린이 아님) 조작은 프로그램이
   대신 한다.

Qt 가 사이드바 목록을 저장하는 파일이 비정상적으로 커졌는지도 함께 본다 —
그 파일이 커지면 **이 라이브러리를 쓰지 않는 앱의** 파일 대화상자까지 느려지거나
죽는다.

클릭 한 번의 시간을 **우리 코드**와 **Qt** 로 갈라 잰다.

    우리 코드   이 패키지 안에서 쓴 시간(판정 · 훅 · 아이콘 제공자)
    Qt         나머지 — 그 폴더로 옮기고 **나열**하는 데 Qt 가 쓴 시간

Qt 쪽이 대부분이면 그 폴더를 읽는 것 자체가 느린 것이고(네트워크 지연 ·
파일 수), 우리 코드 쪽이 크면 어느 함수인지까지 찍어 준다.

잴 때 쓰는 임시 저장소와 작업 폴더는 **끝나면 지운다** — 홈이 네트워크인
환경에서 쓰는 도구라 홈에 흔적을 남기지 않는다.
"""

import cProfile
import os
import pstats
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qtpy.QtCore import Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QListView

from custom_file_dialog import CustomFileDialog, FavoritesStore, RecentStore, safety
from custom_file_dialog import mounts, reach


def _mount_report():
    """마운트 표에서 이 환경의 성격을 요약한다."""
    table = mounts.iter_mounts(refresh=True)
    remote = [m for m in table if m[1] in mounts.REMOTE_FSTYPES]
    auto = [m for m in table if m[1] in mounts.AUTOMOUNT_FSTYPES]
    print("마운트 표: 전체 %d개 · 원격 %d개 · automount %d개"
          % (len(table), len(remote), len(auto)))
    options = _mount_options()
    for point, fstype, source in (remote + auto)[:12]:
        print("    %-36s %-9s %s" % (point, fstype, source))
        flags = options.get(os.path.normpath(point), "")
        caching = [f for f in flags.split(",")
                   if f.startswith(("ac", "noac", "actimeo", "lookupcache", "vers", "proto"))]
        if caching:
            print("        옵션: %s" % ",".join(caching))
        if "noac" in flags.split(",") or "actimeo=0" in flags:
            print("        ** 속성 캐시가 꺼져 있다(noac/actimeo=0). 파일 정보를"
                  " 물을 때마다 서버까지 간다 — 목록이 느린 가장 흔한 원인이다. **")
    if len(remote) + len(auto) > 12:
        print("    … 외 %d개" % (len(remote) + len(auto) - 12))
    return table


def _mount_options():
    """``마운트지점 -> 옵션문자열``. mountinfo 의 옵션 칸을 그대로 읽는다."""
    found = {}
    try:
        with open(mounts.MOUNTINFO, encoding="utf-8", errors="surrogateescape") as handle:
            lines = handle.readlines()
    except OSError:
        return found
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        point = os.path.normpath(fields[4])
        found[point] = fields[5] + "," + fields[separator + 3]
    return found


def _listing_cost(directory):
    """``ls`` 와 Qt 가 왜 다른지 — 같은 폴더로 직접 재 본다.

    ``ls`` 는 폴더만 읽고(getdents 한 번), Qt 는 크기·종류·시각을 채우려고
    **항목마다 stat** 한다. 네트워크에서는 그 stat 하나하나가 서버까지 가는
    왕복이라, 폴더 읽기가 아무리 빨라도 목록은 느리다.
    """
    started = time.perf_counter()
    try:
        names = os.listdir(directory)
    except OSError as error:
        print("    폴더를 읽지 못했다: %s" % error)
        return
    read = time.perf_counter() - started

    started = time.perf_counter()
    for name in names:
        try:
            os.lstat(os.path.join(directory, name))
        except OSError:
            pass
    stat_all = time.perf_counter() - started

    print("    항목 %d개 · 폴더만 읽기(ls 가 하는 일) %.0f ms"
          " · 항목마다 stat(Qt 가 하는 일) %.0f ms"
          % (len(names), read * 1000, stat_all * 1000))
    if names and stat_all > 4 * max(read, 0.0005):
        print("        -> stat 이 %.0f배 비싸다. 목록이 느린 것은 폴더 크기가"
              " 아니라 **항목마다 서버에 묻는 비용** 이다." % (stat_all / max(read, 1e-6)))


# 다이얼로그를 처음 여는 동안 지나가는 단계들. **새 프로세스에서** 순서대로
# 재야 한다 — Qt 는 아이콘 테마 · 종류 정보 · 설정을 프로세스 안에 캐시해서,
# 이미 돌던 프로세스에서 재면 두 번째부터는 전부 0 ms 로 나온다.
_STARTUP_PROBE = """
import os, sys, time
sys.path.insert(0, %r)
T = time.perf_counter()
def mark(label):
    global T
    now = time.perf_counter()
    print("%%s\t%%.6f" %% (label, now - T), flush=True)
    T = now

from qtpy.QtCore import QMimeDatabase, QSettings, QStandardPaths, QUrl
from qtpy.QtWidgets import QApplication, QFileDialog
mark("파이썬·Qt 모듈 import")

app = QApplication([])
mark("QApplication (플랫폼·테마 플러그인 · 폰트)")

settings = QSettings(QSettings.Scope.UserScope, "QtProject")
settings.value("FileDialog/shortcuts")
mark("Qt 설정 읽기 (저장된 사이드바)")

for name in ("HomeLocation", "DesktopLocation", "DocumentsLocation"):
    scope = getattr(QStandardPaths, "StandardLocation", QStandardPaths)
    QStandardPaths.writableLocation(getattr(scope, name))
mark("표준 위치 (XDG user-dirs)")

QMimeDatabase().mimeTypeForFile("x.txt", QMimeDatabase.MatchMode.MatchExtension)
mark("종류 판별 첫 조회 (shared-mime-info)")

from qtpy.QtWidgets import QFileIconProvider
from qtpy.QtCore import QFileInfo
provider = QFileIconProvider()
provider.icon(QFileInfo(os.path.expanduser("~")))
mark("아이콘 테마 첫 조회 (~/.icons · ~/.local/share/icons)")

plain = QFileDialog(None)
plain.setOption(QFileDialog.Option.DontUseNativeDialog, True)
plain.setDirectory(%r)
mark("맨 QFileDialog 생성")

plain.show()
app.processEvents()
mark("맨 QFileDialog show()")
plain.done(0)

from custom_file_dialog import CustomFileDialog, FavoritesStore, RecentStore
favorites = FavoritesStore(base_dir=os.path.join(%r, "favorites"))
recent = RecentStore(base_dir=os.path.join(%r, "recent"), max_items=10)
mark("우리 저장소 열기 (즐겨찾기 · 최근 파일)")

ours = CustomFileDialog(None, mode="open_file", directory=%r,
                        favorites=favorites, recent=recent)
mark("CustomFileDialog 생성 (사이드바 · 훅 · 안전 판정)")

ours.show()
app.processEvents()
mark("CustomFileDialog show()")
ours.done(0)
"""


def _startup_breakdown(work, storage):
    """다이얼로그가 **처음 뜰 때** 어디에 시간이 가는지 단계별로.

    같은 프로세스에서 재면 두 번째부터 전부 0 ms 가 나오므로 새로 띄워 잰다.
    """
    import subprocess

    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    script = _STARTUP_PROBE % (src, work, storage, storage, work)
    print("\n여는 데 드는 시간을 단계로 나눠 본다 (새 프로세스에서 1회):")
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print("    10분 안에 안 끝났다 — 어딘가 멈춰 있다(죽은 마운트가 유력).")
        return
    if done.returncode != 0:
        print("    재지 못했다 (종료 %d):" % done.returncode)
        print("    " + (done.stderr.strip().splitlines() or ["출력 없음"])[-1])
        return

    steps = []
    for line in done.stdout.splitlines():
        if "\t" in line:
            label, seconds = line.rsplit("\t", 1)
            steps.append((label, float(seconds) * 1000))
    if not steps:
        return

    total = sum(ms for _label, ms in steps)
    print("    %-46s %9s  %s" % ("단계", "걸린 시간", "몫"))
    for label, ms in steps:
        share = ms / total * 100 if total else 0
        bar = "#" * int(round(share / 4))
        print("    %-46s %7.0f ms  %4.1f%% %s" % (label, ms, share, bar))
    print("    %-46s %7.0f ms" % ("합계", total))

    table = dict(steps)
    bare = table.get("맨 QFileDialog 생성", 0) + table.get("맨 QFileDialog show()", 0)
    ours = (table.get("CustomFileDialog 생성 (사이드바 · 훅 · 안전 판정)", 0)
            + table.get("CustomFileDialog show()", 0)
            + table.get("우리 저장소 열기 (즐겨찾기 · 최근 파일)", 0))
    print("\n    맨 QFileDialog %.0f ms  vs  이 라이브러리 %.0f ms  (차이 %+.0f ms)"
          % (bare, ours, ours - bare))
    if bare > ours:
        print("    -> 여는 비용의 대부분은 **Qt 자신**이다. 이 라이브러리를 빼도 그만큼 든다.")

    _explain_startup(table)


# 단계 이름 -> (이 단계가 무엇을 읽는가, 느릴 때 할 것)
_STARTUP_HINTS = {
    "Qt 설정 읽기 (저장된 사이드바)": (
        "~/.config/QtProject.conf",
        "그 파일이 커져 있는지 보라. Qt5 와 Qt6 은 비-ASCII 경로의 인코딩을 "
        "다르게 읽어, 두 판을 번갈아 쓰면 저장된 경로가 왕복마다 배로 늘어난다.",
    ),
    "표준 위치 (XDG user-dirs)": (
        "~/.config/user-dirs.dirs",
        "홈이 네트워크면 이 한 번이 그대로 왕복이다.",
    ),
    "아이콘 테마 첫 조회 (~/.icons · ~/.local/share/icons)": (
        "~/.icons · ~/.local/share/icons · XDG_DATA_DIRS",
        "홈이 네트워크면 테마 폴더를 뒤지는 것이 전부 왕복이다. "
        "favorites_icon=False 로 제공자를 빼면 Qt 기본 동작이 된다.",
    ),
    "종류 판별 첫 조회 (shared-mime-info)": (
        "/usr/share/mime",
        "보통 로컬이라 빠르다. 여기가 느리면 mime 데이터베이스가 원격에 있다.",
    ),
    "QApplication (플랫폼·테마 플러그인 · 폰트)": (
        "Qt 플러그인 · fontconfig 캐시",
        "QT_QPA_PLATFORMTHEME=gtk3 면 GTK 초기화까지 여기 든다. "
        "fontconfig 캐시가 네트워크 홈에 있으면 특히 느리다.",
    ),
    "우리 저장소 열기 (즐겨찾기 · 최근 파일)": (
        "저장소 폴더",
        "저장소가 네트워크 홈에 있다. base_dir 로 로컬 디스크를 주면 사라진다.",
    ),
    "파이썬·Qt 모듈 import": (
        "파이썬 패키지 · Qt 공유 라이브러리",
        "파이썬과 Qt 를 처음 올리는 값이라 어느 앱에서나 든다. 앱이 이미 Qt 를 "
        "쓰고 있으면 이 값은 다이얼로그를 열 때 다시 들지 않는다.",
    ),
    "맨 QFileDialog 생성": (
        "시작 폴더 · Qt 자신의 위젯 트리",
        "이 라이브러리가 없어도 드는 값이다. 여기가 크면 원인은 우리가 아니라 "
        "시작 폴더를 읽는 비용이다 — 아래 'ls vs Qt' 줄을 보라.",
    ),
    "맨 QFileDialog show()": (
        "시작 폴더 나열 · 첫 그리기",
        "이 라이브러리가 없어도 드는 값이다.",
    ),
    "CustomFileDialog 생성 (사이드바 · 훅 · 안전 판정)": (
        "사이드바 경로들 · 마운트 표",
        "여기만 크면 우리 몫이다. 사이드바 항목 수를 줄이거나 path_timeout 을 "
        "낮춰 보라.",
    ),
    "CustomFileDialog show()": (
        "사이드바 폭 맞춤 · 첫 그리기",
        "사이드바 항목이 많을수록 는다.",
    ),
}


# 이 시간을 넘긴 단계만 설명한다. 네트워크 홈에서는 왕복 한 번이 5~10 ms 라,
# 50 ms 는 "몇 번 왕복했다"는 뜻이 된다.
THRESHOLD_MS = 50


def _explain_startup(steps):
    """오래 걸린 단계마다 무엇을 읽는지와 할 일을 붙인다."""
    slow = [(label, ms) for label, ms in steps.items() if ms >= THRESHOLD_MS]
    if not slow:
        print("    %d ms 를 넘는 단계가 없다 — 여는 것 자체는 문제가 아니다."
              % THRESHOLD_MS)
        return
    print("\n    %d ms 를 넘은 단계마다 무엇을 읽는지:" % THRESHOLD_MS)
    for label, ms in sorted(slow, key=lambda item: -item[1]):
        reads, advice = _STARTUP_HINTS.get(label, ("(모름)", ""))
        print("      · %s — %.0f ms" % (label, ms))
        print("          읽는 곳: %s" % reads)
        if advice:
            print("          %s" % advice)


def _settings_file_report():
    """Qt 가 사이드바를 저장하는 파일이 비정상적으로 크지 않은지."""
    from qtpy.QtCore import QSettings

    path = QSettings(QSettings.Scope.UserScope, "QtProject").fileName()
    try:
        size = os.path.getsize(path)
    except OSError:
        print("\nQt 설정 파일: 아직 없다 (%s)" % path)
        return
    print("\nQt 설정 파일: %s — %s" % (path, _bytes(size)))
    if size > 1 << 20:
        print("    ** 비정상이다.** 파일 대화상자 상태만 담는 파일이 1MB 를 넘을")
        print("       이유가 없다. 이 상태에서는 **이 라이브러리를 쓰지 않는 앱의**")
        print("       파일 대화상자까지 여는 순간 느려지거나 죽는다(실측: 805MB 에서")
        print("       맨 QFileDialog.show() 가 100% SIGSEGV). 저장된 목록만 지운다:")
        print("           python -c \"from qtpy.QtCore import QSettings;"
              " QSettings(QSettings.Scope.UserScope, 'QtProject')"
              ".remove('FileDialog/shortcuts')\"")
        print("       (그 파일의 다른 값은 그대로 남는다.)")


def _bytes(size):
    """사람이 읽는 크기. ``ls -l`` · ``du -b`` 와 맞추려고 1000 단위를 쓴다."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return "%d B" % size if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1000.0


def _where(path, table):
    """그 경로가 어느 마운트에 얹혀 있는지 한 줄로."""
    mount = mounts.mount_for(path)
    if mount is None:
        return "마운트 모름"
    kind = "원격" if mount[1] in mounts.REMOTE_FSTYPES else (
        "automount" if mount[1] in mounts.AUTOMOUNT_FSTYPES else "로컬")
    return "%s (%s, %s)" % (kind, mount[1], mount[0])


def _some_files(directory, count):
    """그 폴더에 실제로 있는 파일 몇 개(없으면 빈 목록).

    폴더 전체를 정렬하지 않는다 — 네트워크 폴더에서 항목이 많으면 그 자체가
    재려는 비용만큼 든다. 앞에서부터 필요한 개수만 집는다.
    """
    found = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    found.append(entry.path)
                    if len(found) >= count:
                        break
    except OSError:
        return []
    return found


def _option(name, default=None):
    """``--work /경로`` 처럼 값을 받는 인자를 읽는다 (없으면 default)."""
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return default


def _timed(func):
    started = time.perf_counter()
    return func(), time.perf_counter() - started


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    table = _mount_report()

    home = os.path.expanduser("~")
    storage = os.path.join(home, ".config", "custom-file-dialog-진단")

    # 작업 폴더. 안 주면 임시 폴더를 만들어 쓰고 끝나면 지운다 — 다만 그것은
    # **가짜 폴더**라, 느린 것이 폴더 탓인지 아닌지는 말해 주지 못한다.
    # 실제로 느린 자리를 재려면 그 폴더를 직접 줘라: --work /경로
    given = _option("--work")
    if given:
        work = os.path.abspath(os.path.expanduser(given))
        if not os.path.isdir(work):
            print("--work 로 준 폴더가 없다: %s" % work)
            return
        made_work = False
    else:
        work = tempfile.mkdtemp(prefix="cfd-진단-")
        made_work = True
        for index in range(5):
            with open(os.path.join(work, "파일%02d.csv" % index), "w") as handle:
                handle.write("x")

    print("\n홈        %-60s %s" % (home, _where(home, table)))
    print("저장소    %-60s %s" % (storage, _where(storage, table)))
    print("작업폴더  %-60s %s" % (work, _where(work, table)))
    if made_work:
        print("          ^ 이 스크립트가 방금 만든 **임시 폴더**다(파일 5개, 끝나면")
        print("            지운다). 사이드바의 '현재 위치'가 이것이다. 여기가 느리게")
        print("            나오면 폴더 탓이 아니라 **첫 클릭에 몰리는 1회성 비용**일")
        print("            수 있다 — 아래 '두 번째 바퀴' 값과 견줘 보라.")
        print("            진짜 작업 폴더를 재려면:  --work /실제/경로")

    favorites = FavoritesStore(base_dir=os.path.join(storage, "favorites"))
    recent = RecentStore(base_dir=os.path.join(storage, "recent"), max_items=10)
    # 즐겨찾기·최근 파일에 얹을 것은 **그 폴더에 실제로 있는 파일**에서 고른다.
    # (--work 로 남의 폴더를 받으면 이름을 알 수 없다. 빈 폴더면 그냥 건너뛴다 —
    #  분류 폴더는 비어 있어도 사이드바에 나오므로 재는 데 지장이 없다.)
    seeds = _some_files(work, 2)
    if seeds:
        favorites.add("설계", seeds[0])
        recent.record(seeds[-1])
    else:
        print("\n작업 폴더에 파일이 없어 즐겨찾기·최근 파일은 비운 채로 잰다.")

    if "--safety" in sys.argv:
        # 데모의 "안전장치" 체크박스와 같은 설정
        safety.configure(guarded_roots=[os.path.join(work, "user")], min_depth=2)
    print("\n안전 설정: %s" % safety.settings())

    _settings_file_report()
    _startup_breakdown(work, storage)

    dialog, spent = _timed(lambda: CustomFileDialog(
        None, mode="open_file", directory=work, favorites=favorites, recent=recent
    ))
    dialog.show()
    for _ in range(40):
        app.processEvents()
    print("다이얼로그 생성 %.0f ms (멈춘 확인 스레드 %d개)"
          % (spent * 1000, reach.pending_checks()))

    sidebar = dialog.findChild(QListView, "sidebar")
    if sidebar is None:
        print("사이드바를 못 찾았다 — 네이티브 창인 것 같다.")
        return

    rows = sidebar.model().rowCount()
    names = [sidebar.model().index(r, 0).data() for r in range(rows)]
    print("\n사이드바 %d개: %s" % (rows, names))

    print("\n사이드바가 가리키는 폴더를 직접 재 본다 (ls vs Qt 의 차이):")
    from custom_file_dialog.constants import PATH_ROLE
    from custom_file_dialog.util import url_path
    for row in range(rows):
        target = url_path(sidebar.model().index(row, 0).data(PATH_ROLE))
        if target and os.path.isdir(target):
            print("  %s  (%s)" % (names[row], target))
            print("      얹힌 곳: %s" % _where(target, table))
            _listing_cost(target)

    print("\n%-18s %10s %12s %12s   %s"
          % ("항목", "전체", "우리 코드", "Qt", "간 곳"))
    worst = []
    for round_index in range(2):         # 두 바퀴 — 첫 바퀴는 캐시가 비어 있다
        if round_index:
            print("  -- 두 번째 바퀴 (캐시가 채워진 뒤) --")
        for row in range(rows):
            index = sidebar.model().index(row, 0)
            point = sidebar.visualRect(index).center()

            profile = cProfile.Profile()
            started = time.perf_counter()
            profile.enable()
            QTest.mousePress(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseRelease(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=point)
            for _ in range(60):
                app.processEvents()
            profile.disable()
            total = time.perf_counter() - started

            ours, rows_by_time = _our_share(profile)
            worst.extend(rows_by_time)
            print("%-18s %8.0f ms %10.1f ms %10.0f ms   %s"
                  % (names[row][:18], total * 1000, ours * 1000,
                     (total - ours) * 1000, dialog.directory().absolutePath()))

    print("\n멈춘 확인 스레드: %d개" % reach.pending_checks())

    worst.sort(reverse=True)
    if worst and worst[0][0] > 0.001:
        print("\n우리 코드에서 오래 걸린 함수:")
        for spent, where in worst[:8]:
            print("    %7.1f ms  %s" % (spent * 1000, where))

    print("\n읽는 법:")
    print("  · **여는 것 자체가 느리다면** 위의 '단계로 나눠 본다' 표를 보라.")
    print("    거기서 가장 큰 줄이 원인이고, 그 줄마다 무엇을 읽는지 적어 두었다.")
    print("    아래 표(사이드바 클릭)는 이미 열린 창을 **오갈 때**의 비용이라 다르다.")
    print("  · 'Qt' 가 대부분이면 -> 위의 'ls vs Qt' 줄을 보라. Qt 는 크기·종류·")
    print("    시각을 채우려고 **항목마다 stat** 한다(실측: 항목당 10회 이상,")
    print("    오갈 때마다 다시). ls 는 폴더만 읽으므로 빠른 것이 정상이고,")
    print("    공정한 비교는 `ls -lU` 다. 네트워크에서는 이 stat 이 전부 왕복이다.")
    print("  · '우리 코드' 가 크면 -> 위에 찍힌 함수가 범인이다. 그 줄을 알려 달라.")
    print("  · 어떤 경로를 몇 번 만지는지까지 보려면:")
    print("      strace -f -e trace=%file -o /tmp/cfd.trace \\")
    print("          %s %s" % (sys.executable, os.path.abspath(__file__)))
    print("      awk -F'\"' '{print $2}' /tmp/cfd.trace | sort | uniq -c | sort -rn | head -20")

    dialog.done(0)
    shutil.rmtree(storage, ignore_errors=True)      # 홈에 흔적을 남기지 않는다
    if made_work:                                   # --work 로 받은 폴더는 남의 것이다
        shutil.rmtree(work, ignore_errors=True)


def _our_share(profile):
    """프로파일에서 **이 패키지 코드**가 쓴 시간만 뽑는다."""
    stats = pstats.Stats(profile)
    total = 0.0
    rows = []
    for (filename, lineno, name), entry in stats.stats.items():
        if "custom_file_dialog" not in filename:
            continue
        tottime = entry[2]
        total += tottime
        rows.append((tottime, "%s:%d %s" % (os.path.basename(filename), lineno, name)))
    return total, rows


if __name__ == "__main__":
    main()
