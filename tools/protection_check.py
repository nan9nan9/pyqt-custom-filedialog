"""보호가 정말 잠겨 있는지 확인한다 — 핵심 판정에 일부러 결함을 심어 본다.

    python tools/protection_check.py            전부
    python tools/protection_check.py icons.py   그 파일 것만

이 라이브러리가 지키는 것들(automount 를 건드리지 않기 · 남의 기록을 지우지
않기 · 죽은 마운트에서 멈추지 않기 …)은 규칙이 여러 통로에 걸쳐 있어서,
테스트가 "통과"해도 무엇을 지키는지 알기 어렵다. **소스에 결함을 하나 심고
테스트가 잡는지** 보면 확실하다. 아래 표의 한 줄이 곧 "예전에 실제로 났던
버그 하나 + 그것을 잡는 테스트" 다.

각 줄은 ``(무엇을 심나, 파일, 원래 코드, 심을 코드, 잡아야 할 테스트)`` 다.
원래 코드가 **그대로** 있어야 심을 수 있으므로, 리팩터링으로 그 줄이 바뀌면
여기서 ``원문 없음`` 으로 알려 준다 — 조용히 넘어가지 않는다. 그때는 새 원문에
맞춰 이 표를 고치면 된다(보호가 사라진 것인지 옮겨진 것인지 함께 확인할 것).
"""

import atexit
import pathlib
import signal
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "custom_file_dialog"

# (설명, 파일, 원래 코드, 심을 결함, 잡아야 할 테스트)
CASES = [   (   'history 자르는 기준',
        'history.py',
        'del items[max(self.max_items, stored) :]',
        'del items[max(self.max_items, len(items) - 1) :]',
        'keeps_other_widgets_entries'),
    (   'reach UNKNOWN 묶음키',
        'reach.py',
        '            func, path, timeout=timeout, pending_key=_unknown_key(path)\n',
        '            func, path, timeout=timeout\n',
        'unknown_paths_do_not_pile_up'),
    (   'reach UNKNOWN 프로브',
        'reach.py',
        '            os.path.lexists, path, timeout=timeout, pending_key=_unknown_key(path)',
        '            os.stat, path, timeout=timeout, pending_key=_unknown_key(path)',
        'missing_path_is_reachable'),
    (   'dialog ~ 펴기(후보)',
        'dialog.py',
        '    current = os.path.expanduser((current_paths or [""])[0] or "")',
        '    current = (current_paths or [""])[0] or ""',
        'expands_tilde'),
    (   'dialog ~ 펴기(반환)',
        'dialog.py',
        '            return os.path.expanduser(candidate)',
        '            return candidate',
        'expands_tilde'),
    (   'places recent_max',
        'places.py',
        '        recent_max = changes.pop("recent_max", None)\n'
        '        if recent_max is None:\n'
        '            recent_max = self.recent_max',
        '        recent_max = changes.pop("recent_max", self.recent_max)',
        'recent_max_on_explicit_none'),
    (   'menus places 방어',
        'menus.py',
        'if self._dialog is None or self._places is None:',
        'if self._dialog is None:',
        'reject_missing_places'),
    (   'guard 막은 경로 안내',
        'guard.py',
        '        return self._first_blocked(entries) or entries[0][1]',
        '        return entries[0][1]',
        'names_the_path_that_blocked'),
    (   'guard 모드별 따옴표',
        'guard.py',
        '                _typed_path(self._dialog, t) for t in _typed_parts(self._dialog, '
        'text)',
        '                _typed_path(self._dialog, t) for t in _split_typed(text)',
        'quotes_in_names'),
    (   'mounts bytes 해석',
        'mounts.py',
        '    if isinstance(path, bytes):\n        path = os.fsdecode(path)\n',
        '',
        'guarded_root_accepts_bytes'),
    (   'drops 폴더모드 확인',
        'drops.py',
        '    if not isfile(path):\n        return None\n',
        '',
        'invents_a_parent'),
    (   'drops 파일모드 확인',
        'drops.py',
        '    if not isfile(path):',
        '    if False:',
        'unverifiable_folder_in_file_mode'),
    (   'places 소유 저장소 보호',
        'places.py',
        '            and self.recent_is_ours\n',
        '',
        'app_owned_store'),
    (   'menus 새폴더 활성',
        'menus.py',
        '            if name == "qt_new_folder_action":',
        '            if False:',
        'read_only_dialog'),
    (   'favorites 이름 길이',
        'favorites.py',
        '        return _safe_name(_shorten(str(name), _LINK_NAME_BYTES))',
        '        return _safe_name(str(name))',
        'leak_os_errors'),
    (   'favorites 널바이트',
        'favorites.py',
        '    if "\\0" in text:\n        raise ValueError("분류 이름에 널 바이트를 쓸 수 없습니다.")\n',
        '',
        'leak_os_errors'),
    (   'favorites 오류 문구',
        'favorites.py',
        '            if not safety.is_reachable(target):',
        '            if False:',
        'unverifiable_target'),
    (   'reach 묶음키 범위',
        'reach.py',
        '    return os.path.dirname(os.path.abspath(path)) or os.sep',
        '    return os.path.splitdrive(os.path.abspath(path))[0] or os.sep',
        'dead_share_does_not_lock'),
    (   'reach 스레드 상한',
        'reach.py',
        '        if len(_pending) >= MAX_PENDING_CHECKS:\n'
        '            # 묶음 키가 못 잡은 폭주의 마지막 방어. 키를 아무리 잘 잡아도\n'
        '            # "어디까지가 한 마운트인지" 모르는 경우가 있어, 스레드 수 자체에\n'
        '            # 상한을 둔다(멈춘 스레드는 죽일 수 없으므로 안 만드는 수밖에 없다).\n'
        '            return False, None\n',
        '',
        'hard_ceiling'),
    (   'favorites 분류 길이 한도',
        'favorites.py',
        '_MAX_NAME_BYTES = 255',
        '_MAX_NAME_BYTES = 200',
        'existing_long_category'),
    (   'guard 조각별 구분자',
        'guard.py',
        '            if part.strip().endswith(("/", os.sep)) and not '
        'candidate.endswith(os.sep):',
        '            if (self._edit.text() or "").strip().endswith(("/", os.sep)) and not '
        'candidate.endswith(os.sep):',
        'trailing_separator_counts'),
    (   'places 링크 풀기',
        'places.py',
        '            self.recent.record_all(self.resolve_all(paths))',
        '            self.recent.record_all(paths)',
        'record_recent_resolves'),
    (   '예산 공유(프로브)',
        'reach.py',
        '            share = min(_remaining(deadline), probe_budget(wait))',
        '            share = wait',
        'never_spends_more or probe_budget_follows'),
    (   '예산 공유(stat)',
        'reach.py',
        '    left = _remaining(deadline)\n    if left <= 0:\n        return False\n',
        '    left = wait\n',
        'never_spends_more'),
    (   '제공자 한 번만 설치',
        'dialog.py',
        '                    self._icon_provider = provider  # setIconProvider 는 소유하지 않는다\n'
        '                    self.setIconProvider(provider)',
        '                    self._icon_provider = provider\n'
        '                    self.setIconProvider(provider)\n'
        '                    self.directoryEntered.connect(\n'
        '                        lambda _p: self.setIconProvider(provider))',
        'icon_provider_is_never_swapped'),
    (   '아는 답 넘기기',
        'icons.py',
        '                    if store.is_category_dir(path, is_dir=is_dir):',
        '                    if store.is_category_dir(path):',
        'does_not_restat'),
    (   '넘겨받은 답 쓰기',
        'favorites.py',
        '        return os.path.isdir(absolute) if is_dir is None else bool(is_dir)',
        '        return os.path.isdir(absolute)',
        'does_not_restat'),
    (   '종류별 캐시',
        'icons.py',
        '        if key is not None:\n            _plain_icons[key] = answer',
        '        pass',
        'asks_qt_once_per_kind'),
    (   '링크 표시 유지',
        'icons.py',
        '        return (info.isSymLink(), _mime_name(CategoryIconProvider._suffix(info)))',
        '        return (False, _mime_name(CategoryIconProvider._suffix(info)))',
        'symlinks_distinct'),
    (   '확장자를 종류 무관하게',
        'icons.py',
        '        return name[dot + 1:] if dot > 0 else ""',
        '        return name[dot + 1:].lower() if dot > 0 else ""',
        'splits_whatever_qt_splits'),
    (   '뿌리를 폴더와 구분',
        'icons.py',
        '        if info.isRoot():\n            return ("root", info.absoluteFilePath())',
        '        if False:\n            return ("root", info.absoluteFilePath())',
        'matches_qt_for_every_kind'),
    (   '특수 폴더 가르기',
        'icons.py',
        '    return normal if normal in _special_dirs else ""',
        '    return ""',
        'splits_whatever_qt_splits'),
    (   '폴더 링크 화살표',
        'icons.py',
        '            return ("dir", info.isSymLink(),\n                    _special_dir(info.absoluteFilePath()))',
        '            return ("dir", _special_dir(info.absoluteFilePath()))',
        'keeps_symlinks_distinct'),
    (   '특수 파일을 파일과 구분',
        'icons.py',
        '        if info.exists() and not info.isFile():\n            return ("special",)',
        '        if False:\n            return ("special",)',
        'matches_qt_for_every_kind'),
    (   '종류 이름으로 접기',
        'icons.py',
        '        return (info.isSymLink(), _mime_name(CategoryIconProvider._suffix(info)))',
        '        return (info.isSymLink(), CategoryIconProvider._suffix(info))',
        'splits_whatever_qt_splits'),
    (   '확장자만 보고 종류 정하기',
        'icons.py',
        '            "x." + suffix if suffix else "x", match).name()',
        '            "x." + suffix if suffix else "x").name()',
        'ignores_a_real_file_of_the_same_fake_name'),
    (   '프로브 예산 비례',
        'reach.py',
        '    return min(wait, max(PROBE_TIMEOUT, wait * PROBE_SHARE))',
        '    return min(wait, PROBE_TIMEOUT)',
        'probe_budget_follows'),
    (   '점파일도 확장자를 본다',
        'icons.py',
        '        dot = name.find(".", 1)',
        '        dot = name.find(".")',
        'splits_whatever_qt_splits'),
    (   '폴더 모드에서 파일 숨기기',
        'dialog.py',
        '            if mode == SelectMode.DIRECTORY and show_dirs_only:\n                self.setOption(option_value("ShowDirsOnly"), True)',
        '            pass',
        'show_dirs_only_actually_hides_files'),
    (   '전역 설정에 우리 항목 안 남기기',
        'dialog.py',
        '        self.setSidebarUrls(self._places.without_our_places(self.sidebarUrls()))',
        '        pass',
        'leaves_no_trace_of_our_places'),
    (   '감출 때도 빼기',
        'dialog.py',
        '        self._strip_our_places()\n        super().hideEvent(event)',
        '        super().hideEvent(event)',
        'never_reach_the_saved_settings_file'),
    (   '앱 종료 때도 빼기',
        'dialog.py',
        '            application.aboutToQuit.connect(self._strip_our_places)',
        '            pass',
        'never_reach_the_saved_settings_file'),
    (   '다시 열면 사이드바 복원',
        'dialog.py',
        '                self._apply_sidebar_urls()\n                self._places_stripped = False',
        '                pass',
        'leaves_no_trace_of_our_places'),
    (   '우리 것만 골라 빼기',
        'places.py',
        '                path == base or path.startswith(base + os.sep) for base in bases',
        '                True for base in bases',
        'leaves_no_trace_of_our_places'),
    (   '꺼졌을 때 재지 않기',
        'debuglog.py',
        '    if not is_enabled():\n        yield\n        return',
        '    if False:\n        yield\n        return',
        'costs_nothing_when_off'),
    (   '남의 핸들러 존중',
        'debuglog.py',
        '    if _handler is None and not logger.handlers:',
        '    if _handler is None:',
        'does_not_add_a_handler_when_the_app'),
    (   '들어갈 때도 남기기',
        'debuglog.py',
        '    logger.debug("%s> %s", indent, label)',
        '    pass',
        'entering_a_step_is_logged'),
    (   '훑은 결과 넘기기',
        'dialog.py',
        '                install_hooks(self, self._places, current, scanned)',
        '                install_hooks(self, self._places, current)',
        'stores_are_scanned_once_per_open'),
    (   '첫 show 에서 다시 안 훑기',
        'dialog.py',
        '            if self._places_stripped:',
        '            if True:',
        'stores_are_scanned_once_per_open'),
    (   '이동도 재기',
        'dialog.py',
        '        started = self._nav_started.pop(os.path.normpath(path), None)',
        '        started = None',
        'logs_each_navigation_with_icon_counts'),
    (   '프로그램 이동도 시작점',
        'dialog.py',
        '            self._mark_navigation(path)\n        super().setDirectory(directory)',
        '        super().setDirectory(directory)',
        'logs_each_navigation_with_icon_counts'),
    (   '아이콘 캐시 적중 세기',
        'icons.py',
        '        self._missed += 1',
        '        pass',
        'icon_counts_show_the_cache_working'),
    (   '제공자마다 따로 세기',
        'icons.py',
        '        self._asked = 0             # 우리에게 물어 온 횟수',
        '        self._asked = _shared_asked  # 제공자끼리 공유(섞인다)',
        'icon_counts_show_the_cache_working'),
    (   '끝 못 본 이동 상한',
        'dialog.py',
        '        if len(self._nav_started) >= _MAX_PENDING_NAVIGATIONS:',
        '        if False:',
        'unfinished_navigations_do_not_pile_up'),
    (   '끄면 정말 끄기',
        'debuglog.py',
        '        logger.setLevel(logging.WARNING)',
        '        logger.setLevel(logging.NOTSET)',
        'enable_debug_false_really_turns_it_off'),
    (   '찍을 곳 바꾸기',
        'debuglog.py',
        '    if _handler is not None and _handler.stream is not _stream_of(stream):\n        _drop_handler()',
        '    pass',
        'enable_debug_can_move_where_it_writes'),
    (   '아이콘 색 열쇠',
        'icons.py',
        '    return QColor(color).name(QColor.NameFormat.HexArgb)',
        '    return str(color)',
        'drawn_icons_are_shared'),
    (   '하드링크 폴백',
        'favorites.py',
        '                os.link(target, link_path)\n                return\n',
        '                pass\n',
        'hardlink_fallback'),
    (   '공유 최근목록 보호',
        'recent.py',
        '        limit = max(self.max_items, keep)',
        '        limit = self.max_items',
        'shared_recent_store_is_not_trimmed'),
    (   '아이콘 한 벌만 그리기',
        'icons.py',
        '    icon = store.get(key)\n    if icon is None:\n        icon = store[key] = make()\n    return icon',
        '    return make()',
        'drawn_icons_are_shared'),
    (   '사이드바 차단 설치',
        'guard.py',
        '_watch(sidebar, _SidebarBlocker(sidebar, parent=dialog), installed)',
        '_watch(sidebar, _ItemBlocker(sidebar, (_RELEASE,), parent=dialog), installed)',
        'sidebar_click_cannot'),
    (   '사이드바 방향키',
        'guard.py',
        '            step = self._STEPS.get(event.key())',
        '            step = None',
        'sidebar_keyboard_move'),
    (   'bounce 미루기',
        'guard.py',
        '        QTimer.singleShot(0, lambda: _set_directory(dialog, last["dir"]))',
        '        _set_directory(dialog, last["dir"])',
        'bounces_back'),
    (   '시작위치 이름칸',
        'dialog.py',
        '        if safety.may_enter(directory):\n'
        '            self.selectFile(os.path.basename(directory))',
        '        self.selectFile(os.path.basename(directory))',
        'blocked_start_dir_does_not_fill'),
    (   '사이드바 목록 반환',
        'path_edit.py',
        '        return list(self._places().sidebar_urls(self._current_dir_now()) or [])',
        '        urls = self._places().sidebar_urls(self._current_dir_now())\n'
        '        return list(urls) if urls is not None else None',
        'effective_sidebar_urls_is_always')]


def run_case(label, filename, good, bad, selector):
    """결함을 심고 테스트를 돌린 뒤 원래대로 되돌린다."""
    path = SRC / filename
    original = path.read_text(encoding="utf-8")
    if good not in original:
        return "원문 없음"
    _patched[path] = original           # 시그널로 죽어도 되돌릴 수 있게 남긴다
    path.write_text(original.replace(good, bad, 1), encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "-x", "-k", selector],
            capture_output=True, text=True, timeout=900,
        )
    finally:
        _restore_all()
    return _verdict(done.returncode)


# pytest 종료 코드 (docs: "Exit codes")
_PASSED, _FAILED, _INTERRUPTED, _INTERNAL, _USAGE, _NO_TESTS = range(6)


def _verdict(code):
    """종료 코드를 판정으로. **"고른 테스트가 없다"를 성공으로 세면 안 된다.**

    테스트 이름이 바뀌면 pytest 는 아무것도 못 고르고 5 로 끝나는데, 그것을
    "0 이 아니니 잡았다"로 세면 **결함을 심지 않아도 통과**한다. 실제로 이
    저장소에서 테스트 이름이 한 번 바뀐 적이 있어(``test_probe_share_is_capped``
    -> ``test_probe_budget_follows_the_timeout``) 그대로였다면 도구가 거짓말을
    했을 자리다.
    """
    if code == _FAILED:
        return "잡음"
    if code == _PASSED:
        return "놓침"
    if code == _NO_TESTS:
        return "테스트 없음"
    return "확인 불가(%d)" % code


# 결함을 심어 둔 파일 -> 원본. 시그널로 죽을 때 되돌리는 데 쓴다.
_patched = {}


def _restore_all():
    while _patched:
        path, original = _patched.popitem()
        path.write_text(original, encoding="utf-8")


def _on_signal(signum, _frame):
    """SIGTERM·SIGHUP 등으로 죽어도 **소스를 되돌리고** 나간다.

    ``finally`` 는 예외만 받는다. 터미널을 닫거나(SIGHUP) SSH 가 끊기거나
    ``timeout``/``pkill`` 을 만나면 결함이 심긴 채 남는데, 하필 남는 것이
    문법도 멀쩡하고 테스트 하나만 잡는 조용한 결함이라 그대로 커밋될 수 있다.
    (``SIGKILL`` 은 잡을 수 없다 — 그때는 ``git status`` 로 확인할 것.)
    """
    _restore_all()
    print("\n신호 %d 로 중단 — 심어 둔 결함을 되돌렸다." % signum)
    sys.exit(130)


def main(argv):
    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        handler = getattr(signal, name, None)
        if handler is not None:
            signal.signal(handler, _on_signal)
    atexit.register(_restore_all)

    keep = argv[1] if len(argv) > 1 else ""
    cases = [c for c in CASES if keep in c[0] or keep in c[1]]
    if not cases:
        print("고른 것이 없다: %r  (라벨이나 파일 이름의 일부를 준다 — 예: icons.py)"
              % keep)
        return 2

    bad = []
    for label, filename, good, seed, selector in cases:
        verdict = run_case(label, filename, good, seed, selector)
        print("%-13s %-26s %-14s %s" % (verdict, label, filename, selector))
        if verdict != "잡음":
            bad.append("%s(%s)" % (label, verdict))

    print("\n%d/%d 잡음" % (len(cases) - len(bad), len(cases)))
    if bad:
        print("확인이 안 된 것: %s" % ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
