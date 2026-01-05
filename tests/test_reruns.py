"""expanded cacheprovider features"""

import pytest


def test_new(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_a="def test_foo(): pass",
        test_b="def test_foo(): pass",
    )
    pytester.runpytest().assert_outcomes(passed=2)
    pytester.makepyfile(test_c="def test_foo(): pass")
    pytester.runpytest("--rerun", "new").assert_outcomes(passed=1, deselected=2)
    pytester.runpytest("--rerun", "new").assert_outcomes(passed=3)


def test_mixed(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
def test_a():
    pass

def test_b():
    pass

def test_c():
    assert 0

def test_d():
    assert 0
"""
    )
    pytester.runpytest().assert_outcomes(passed=2, failed=2)

    pytester.makepyfile(test_x="def test_foo(): pass")
    pytester.runpytest('--rerun', 'new,failed').assert_outcomes(
        passed=1, failed=2, deselected=2
    )
    pytester.runpytest('--rerun', 'new,failed').assert_outcomes(
        failed=2, deselected=3
    )


def test_order(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
def test_a():
    pass

def test_b():
    pass

def test_c():
    assert 0

def test_d():
    assert 0
"""
    )
    pytester.runpytest().assert_outcomes(passed=2, failed=2)

    result = pytester.runpytest('-v')
    result.assert_outcomes(passed=2, failed=2)
    result.stdout.fnmatch_lines([
        "test_order.py::test_a PASSED*",
        "test_order.py::test_b PASSED*",
        "test_order.py::test_c FAILED*",
        "test_order.py::test_d FAILED*",
    ])  # fmt: skip

    result = pytester.runpytest('-v', '--rerun-order', "failed")
    result.assert_outcomes(passed=2, failed=2)
    result.stdout.fnmatch_lines([
        "test_order.py::test_c FAILED*",
        "test_order.py::test_d FAILED*",
        "test_order.py::test_a PASSED*",
        "test_order.py::test_b PASSED*",
    ])  # fmt: skip

    pytester.makepyfile(test_foo="def test_x(): pass")
    result = pytester.runpytest('-v', '--rerun-order', "new,failed")
    result.assert_outcomes(passed=3, failed=2)
    result.stdout.fnmatch_lines([
        "test_foo.py::test_x PASSED*",
        "test_order.py::test_c FAILED*",
        "test_order.py::test_d FAILED*",
        "test_order.py::test_a PASSED*",
        "test_order.py::test_b PASSED*",
    ])  # fmt: skip


def test_stepwisex(pytester: pytest.Pytester) -> None:
    params = ('-x', '--rerun', 'failed,pending')
    pytester.makepyfile(
        """
def test_a(): assert 0
def test_b(): assert 0
def test_c(): assert 0
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(failed=1)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): assert 0
def test_c(): assert 0
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1, failed=1)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): pass
def test_c(): assert 0
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1, failed=1)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): pass
def test_c(): pass
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1, failed=1)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): pass
def test_c(): pass
def test_d(): pass
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1)


def test_stepwise_skip(pytester: pytest.Pytester) -> None:
    params = ('--maxfail', '2', '--rerun', 'failed,pending')
    pytester.makepyfile(
        """
def test_a(): assert 0
def test_b(): assert 0
def test_c(): assert 0
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(failed=2)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): assert 0
def test_c(): assert 0
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1, failed=2)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): pass
def test_c(): pass
def test_d(): assert 0
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=2, failed=1)

    pytester.makepyfile(
        """
def test_a(): pass
def test_b(): pass
def test_c(): pass
def test_d(): pass
"""
    )
    pytester.runpytest(*params).assert_outcomes(passed=1)
