"""Basic recording features"""

import json
import sqlite3

import pytest


def test_plugins_disabled(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_plugins_disabled(pytestconfig):
            assert not pytestconfig.pluginmanager.has_plugin("lfplugin")
            assert not pytestconfig.pluginmanager.has_plugin("nfplugin")
            assert not pytestconfig.pluginmanager.has_plugin("stepwiseplugin")
    """
    )

    pytester.runpytest()

    f = next(pytester.path.joinpath('.pytest_cache', 'd', 'sessions').iterdir())
    db = sqlite3.connect(f)
    assert db.execute("PRAGMA user_version").fetchone() == (5,)
    items = db.execute("SELECT nodeid, outcome FROM items").fetchall()
    # fmt:off
    assert items == [(
        "test_plugins_disabled.py::test_plugins_disabled",
        "passed",
    )]
    # fmt:on


def test_outcomes(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """\
import pytest

@pytest.mark.skip()
def test_skip1():
    pass

def test_skip2():
    raise pytest.skip()

def test_fail():
    assert 0

@pytest.fixture
def dep(): 1/0

def test_error(dep):
    pass

@pytest.mark.xfail()
def test_xfail():
    1 / 0

@pytest.mark.xfail()
def test_xpass():
    pass

def test_deselected():
    pass
"""
    )
    pytester.runpytest("-k", "not deselected")
    f = next(pytester.path.joinpath('.pytest_cache', 'd', 'sessions').iterdir())
    db = sqlite3.connect(f)
    items = db.execute("SELECT outcome FROM items order by nodeid").fetchall()

    assert items == [
        ('pending',),
        ('error',),
        ('failed',),
        ('skipped',),
        ('skipped',),
        ('xfailed',),
        ('xpassed',),
    ]


def test_cap(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import logging
        import sys
        import warnings

        def test_stdout():
            print("stdout")

        def test_stderr():
            print("stderr", file=sys.stderr)

        def test_log():
            logging.info("log!")

        def test_warning():
            warnings.warn("warning!")
    """
    )

    pytester.runpytest('--log-level=NOTSET')

    f = next(pytester.path.joinpath('.pytest_cache', 'd', 'sessions').iterdir())
    db = sqlite3.connect(f)
    assert db.execute("PRAGMA user_version").fetchone() == (5,)
    items = []
    for outcome, call in db.execute(
        "SELECT outcome, call FROM items order by nodeid"
    ):
        sections = dict(json.loads(call)['sections'])
        items.append((
            outcome,
            sections.get('Captured log call', ''),
            sections.get('Captured stdout call', ''),
            sections.get('Captured stderr call', ''),
        ))  # fmt: skip
    assert items == [
        ('passed', 'INFO     root:test_cap.py:12 log!', '', ''),
        ('passed', '', '', 'stderr\n'),
        ('passed', '', 'stdout\n', ''),
        ('warnings', '', '', ''),
    ]
