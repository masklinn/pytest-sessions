"""Session management"""

import pathlib
import shutil
from typing import Callable

import pytest


def test_multiple_sessions(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_x(): pass")

    pytester.runpytest()
    pytester.runpytest()

    sessions = list(
        pytester.path.joinpath('.pytest_cache', 'd', 'sessions').iterdir()
    )
    assert len(sessions) == 2


def test_truncate(pytester: pytest.Pytester) -> None:
    pytester.makefile(".ini", pytest="[pytest]\nsessions_limit = 10")

    pytester.makepyfile("def test_x(): pass")

    for _ in range(11):
        pytester.runpytest()

    sessions = list(
        pytester.path.joinpath('.pytest_cache', 'd', 'sessions').iterdir()
    )
    assert len(sessions) == 10


def test_save_session(pytester: pytest.Pytester) -> None:
    """A sessions copied away from the default"""
    pytester.makefile(".ini", pytest="[pytest]\nsessions_limit = 10")
    pytester.makepyfile("def test_x(): pass")

    pytester.runpytest()
    sessionsdir = pytester.path.joinpath('.pytest_cache', 'd', 'sessions')
    basefile = next(sessionsdir.iterdir())
    assert basefile.stat().st_nlink == 1
    save = sessionsdir.joinpath("saved")
    save.hardlink_to(basefile)
    assert basefile.stat().st_nlink == 2

    for _ in range(20):
        pytester.runpytest()

    assert not basefile.is_file()
    assert save.is_file()
    assert save.stat().st_nlink == 1
    assert sum(1 for _ in sessionsdir.iterdir()) == 11


def _rename(s: pathlib.Path, _tmp: pathlib.Path) -> str:
    new = s.parent / 'saved'
    new.hardlink_to(s)
    return new.name


def _tmpify(s: pathlib.Path, tmp: pathlib.Path) -> pathlib.Path:
    external = tmp / "external.sqlite"
    shutil.copy(s, external)  # tmp might be on a different partition
    return external


@pytest.mark.parametrize('sessionifier', [
    pytest.param(lambda s, _: s.name, id='previous'),
    pytest.param(_rename, id='saved'),
    pytest.param(_tmpify, id='foreign'),
])  # fmt: skip
def test_reference(
    pytester: pytest.Pytester,
    tmp_path: pathlib.Path,
    sessionifier: Callable[[pathlib.Path, pathlib.Path], str | pathlib.Path],
) -> None:
    # generate test failure
    pytester.makepyfile("""
        def test_1(): assert True
        def test_2(): assert False
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, failed=1)

    sessions = sorted(
        pytester.path.joinpath('.pytest_cache/d/sessions').glob('session-*')
    )
    reference = sessionifier(sessions[-1], tmp_path)

    # fix test
    pytester.makepyfile("""
        def test_1(): assert True
        def test_2(): assert True
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)

    # rerun against reference, should rerun the one originally failed test
    result = pytester.runpytest("--lf", f"--reference={reference}")
    result.assert_outcomes(passed=1, failed=0)
    result.stdout.fnmatch_lines(["collected 1 item", "*1 passed in*"])
