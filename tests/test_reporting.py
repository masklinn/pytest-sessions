import functools
import re

import pytest

CLEAN_COLLECTED_LINE = functools.partial(
    re.compile(r"^collected .*\n", flags=re.MULTILINE).sub,
    "",
)
CLEAN_FILE_LINE = functools.partial(
    re.compile(r"^([\w\./-]+\s+\S+)\s+\[\d+%\]$", flags=re.MULTILINE).sub,
    r"\1",
)
CLEAN_TIME = functools.partial(
    re.compile(r"(in) \d+\.\d+s (=+)$").sub,
    r"\1 X.XXs \2",
)


def clean_report(out: str) -> str:
    return CLEAN_TIME(CLEAN_FILE_LINE(CLEAN_COLLECTED_LINE(out)))


def test_show_session_bypass_execution(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        def test_side_effect():
            import pathlib
            pathlib.Path("touched").touch()
    """)
    pytester.runpytest()
    assert pytester.path.joinpath("touched").exists()

    pytester.path.joinpath("touched").unlink()

    # Re-report should NOT run the test
    pytester.runpytest("--show-session")
    assert not pytester.path.joinpath("touched").exists()


def test_show_session_simple(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        def test_pass(): assert True
        def test_fail(): assert False
    """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, failed=1)

    # Re-report
    result = pytester.runpytest("--show-session", "-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_pass PASSED*", "*test_fail FAILED*"])


def test_show_session_output(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import sys
        def test_out():
            print("hello stdout")
            print("hello stderr", file=sys.stderr)
            assert True
    """)
    original = pytester.runpytest('-rP')

    # ensure the latest session does not use rP output, so we need to
    # have stored the correct underlying data even though pytest
    # didn't print it
    pytester.runpytest()

    reconstructed = pytester.runpytest("--show-session", "-rP")

    assert clean_report(str(reconstructed.stdout)) == clean_report(
        str(original.stdout)
    )


def test_show_session_skip_xfail(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("""
        import pytest
        def test_skip(): pytest.skip("skipping this")
        @pytest.mark.xfail(reason="xfailing this")
        def test_xfail(): assert False
        @pytest.mark.xfail(reason="xpassing this")
        def test_xpass(): assert True
    """)
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1, xfailed=1, xpassed=1)

    result = pytester.runpytest("--show-session", "-ra")
    result.assert_outcomes(skipped=1, xfailed=1, xpassed=1)
    result.stdout.fnmatch_lines([
        "*SKIPPED*skipping this",
        "*XFAIL*test_xfail*xfailing this*",
        "*XPASS*test_xpass*",
    ])  # fmt: skip


def test_show_session_collection_error(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_error="import non_existent_module")
    original = pytester.runpytest()
    original.assert_outcomes(errors=1)
    # Collection error usually reported as error in main summary?
    # Or "errors=1" in test summary.
    # Actually pytest reports "collected 0 items" and "Interrupted: 1 error during collection"
    reconstructed = pytester.runpytest("--show-session")
    assert clean_report(str(reconstructed.stdout)) == clean_report(
        str(original.stdout)
    )


def test_all_outcomes(pytester: pytest.Pytester) -> None:
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
    original = pytester.runpytest()
    reconstructed = pytester.runpytest('--show-session')
    assert clean_report(str(reconstructed.stdout)) == clean_report(
        str(original.stdout)
    )
    assert str(reconstructed.stderr) == str(original.stderr)
