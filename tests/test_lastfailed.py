# https://raw.githubusercontent.com/pytest-dev/pytest/2dc5b56e7934bfb8bed30fde4b8f972133b23691/testing/test_cacheprovider.py
import os
import sqlite3
import shutil
from pathlib import Path
from typing import Any, Sequence

import pytest
from pytest import ExitCode, MonkeyPatch, Pytester


class TestLastFailed:
    def test_lastfailed_usecase(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.dont_write_bytecode", True)
        p = pytester.makepyfile(
            """
            def test_1(): assert 0
            def test_2(): assert 0
            def test_3(): assert 1
            """
        )
        result = pytester.runpytest(str(p))
        result.stdout.fnmatch_lines(["*2 failed*"])
        p = pytester.makepyfile(
            """
            def test_1(): assert 1
            def test_2(): assert 1
            def test_3(): assert 0
            """
        )
        result = pytester.runpytest(str(p), "--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 3 items / 1 deselected / 2 selected",
                "run-last-failure: rerun previous 2 failures",
                "*= 2 passed, 1 deselected in *",
            ]
        )
        result = pytester.runpytest(str(p), "--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 3 items",
                "run-last-failure: no previously failed tests, not deselecting items.",
                "*1 failed*2 passed*",
            ]
        )
        pytester.path.joinpath(".pytest_cache", ".git").mkdir(parents=True)
        result = pytester.runpytest(str(p), "--lf", "--cache-clear")
        result.stdout.fnmatch_lines(["*1 failed*2 passed*"])
        assert pytester.path.joinpath(".pytest_cache", "README.md").is_file()
        assert pytester.path.joinpath(".pytest_cache", ".git").is_dir()

        # Run this again to make sure clear-cache is robust
        if os.path.isdir(".pytest_cache"):
            shutil.rmtree(".pytest_cache")
        result = pytester.runpytest("--lf", "--cache-clear")
        result.stdout.fnmatch_lines(["*1 failed*2 passed*"])

    def test_failedfirst_order(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_a="def test_always_passes(): pass",
            test_b="def test_always_fails(): assert 0",
        )
        result = pytester.runpytest()
        # Test order will be collection order; alphabetical
        result.stdout.fnmatch_lines(["test_a.py*", "test_b.py*"])
        result = pytester.runpytest("--ff")
        # Test order will be failing tests first
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 1 failure first",
                "test_b.py*",
                "test_a.py*",
            ]
        )

    def test_lastfailed_failedfirst_order(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_a="def test_always_passes(): assert 1",
            test_b="def test_always_fails(): assert 0",
        )
        result = pytester.runpytest()
        # Test order will be collection order; alphabetical
        result.stdout.fnmatch_lines(["test_a.py*", "test_b.py*"])
        result = pytester.runpytest("--lf", "--ff")
        # Test order will be failing tests first
        result.stdout.fnmatch_lines(["test_b.py*"])
        result.stdout.no_fnmatch_line("*test_a.py*")

    def test_lastfailed_difference_invocations(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.dont_write_bytecode", True)
        pytester.makepyfile(
            test_a="""
                def test_a1(): assert 0
                def test_a2(): assert 1
            """,
            test_b="def test_b1(): assert 0",
        )
        p = pytester.path.joinpath("test_a.py")
        p2 = pytester.path.joinpath("test_b.py")

        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*2 failed*"])
        result = pytester.runpytest("--lf", p2)
        result.stdout.fnmatch_lines(["*1 failed*"])

        pytester.makepyfile(test_b="def test_b1(): assert 1")
        result = pytester.runpytest("--lf", p2)
        result.stdout.fnmatch_lines(["*1 passed*"])
        result = pytester.runpytest("--lf", p)
        result.stdout.fnmatch_lines(
            [
                "collected 2 items / 1 deselected / 1 selected",
                "run-last-failure: rerun previous 1 failure",
                "*= 1 failed, 1 deselected in *",
            ]
        )

    def test_lastfailed_usecase_splice(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.dont_write_bytecode", True)
        pytester.makepyfile(
            "def test_1(): assert 0", test_something="def test_2(): assert 0"
        )
        p2 = pytester.path.joinpath("test_something.py")
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*2 failed*"])
        result = pytester.runpytest("--lf", p2)
        result.stdout.fnmatch_lines(["*1 failed*"])
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(["*2 failed*"])

    def test_lastfailed_xpass(self, pytester: Pytester) -> None:
        pytester.inline_runsource(
            """
            import pytest
            @pytest.mark.xfail
            def test_hello():
                assert 1
        """
        )
        config = pytester.parseconfigure()
        assert config.cache is not None
        lastfailed = config.cache.get("cache/lastfailed", -1)
        assert lastfailed == -1

    def test_non_serializable_parametrize(self, pytester: Pytester) -> None:
        """Test that failed parametrized tests with unmarshable parameters
        don't break pytest-cache.
        """
        pytester.makepyfile(
            r"""
            import pytest

            @pytest.mark.parametrize('val', [
                b'\xac\x10\x02G',
            ])
            def test_fail(val):
                assert False
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*1 failed in*"])

    @pytest.mark.parametrize("parent", ("directory", "package"))
    def test_terminal_report_lastfailed(self, pytester: Pytester, parent: str) -> None:
        if parent == "package":
            pytester.makepyfile(
                __init__="",
            )

        test_a = pytester.makepyfile(
            test_a="""
            def test_a1(): pass
            def test_a2(): pass
        """
        )
        test_b = pytester.makepyfile(
            test_b="""
            def test_b1(): assert 0
            def test_b2(): assert 0
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 4 items", "*2 failed, 2 passed in*"])

        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 2 failures (skipped 1 file)",
                "*2 failed in*",
            ]
        )

        result = pytester.runpytest(test_a, "--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: 2 known failures not in selected tests",
                "*2 passed in*",
            ]
        )

        result = pytester.runpytest(test_b, "--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 2 failures",
                "*2 failed in*",
            ]
        )

        result = pytester.runpytest("test_b.py::test_b1", "--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure",
                "*1 failed in*",
            ]
        )

    def test_terminal_report_failedfirst(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_a="""
            def test_a1(): assert 0
            def test_a2(): pass
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "*1 failed, 1 passed in*"])

        result = pytester.runpytest("--ff")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 1 failure first",
                "*1 failed, 1 passed in*",
            ]
        )

    def test_lastfailed_collectfailure(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        pytester.makepyfile(
            test_maybe="""
            import os
            env = os.environ
            if '1' == env['FAILIMPORT']:
                raise ImportError('fail')
            def test_hello():
                assert '0' == env['FAILTEST']
        """
        )

        def rlf(fail_import: int, fail_run: int) -> Any:
            monkeypatch.setenv("FAILIMPORT", str(fail_import))
            monkeypatch.setenv("FAILTEST", str(fail_run))

            pytester.runpytest("-q")
            config = pytester.parseconfigure()
            assert config.cache is not None
            lastfailed = config.cache.get("cache/lastfailed", -1)
            return lastfailed

        lastfailed = rlf(fail_import=0, fail_run=0)
        assert lastfailed == -1

        lastfailed = rlf(fail_import=1, fail_run=0)
        assert list(lastfailed) == ["test_maybe.py"]

        lastfailed = rlf(fail_import=0, fail_run=1)
        assert list(lastfailed) == ["test_maybe.py::test_hello"]

    def test_lastfailed_failure_subset(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        pytester.makepyfile(
            test_maybe="""
            import os
            env = os.environ
            if '1' == env['FAILIMPORT']:
                raise ImportError('fail')
            def test_hello():
                assert '0' == env['FAILTEST']
        """
        )

        pytester.makepyfile(
            test_maybe2="""
            import os
            env = os.environ
            if '1' == env['FAILIMPORT']:
                raise ImportError('fail')

            def test_hello():
                assert '0' == env['FAILTEST']

            def test_pass():
                pass
        """
        )

        def rlf(
            fail_import: int, fail_run: int, args: Sequence[str] = ()
        ) -> tuple[Any, Any]:
            monkeypatch.setenv("FAILIMPORT", str(fail_import))
            monkeypatch.setenv("FAILTEST", str(fail_run))

            result = pytester.runpytest("-q", "--lf", *args)
            config = pytester.parseconfigure()
            assert config.cache is not None
            lastfailed = config.cache.get("cache/lastfailed", -1)
            return result, lastfailed

        result, lastfailed = rlf(fail_import=0, fail_run=0)
        assert lastfailed == -1
        result.stdout.fnmatch_lines(["*3 passed*"])

        result, lastfailed = rlf(fail_import=1, fail_run=0)
        assert sorted(list(lastfailed)) == ["test_maybe.py", "test_maybe2.py"]

        result, lastfailed = rlf(fail_import=0, fail_run=0, args=("test_maybe2.py",))
        assert list(lastfailed) == ["test_maybe.py"]

        # edge case of test selection - even if we remember failures
        # from other tests we still need to run all tests if no test
        # matches the failures
        result, lastfailed = rlf(fail_import=0, fail_run=0, args=("test_maybe2.py",))
        assert list(lastfailed) == ["test_maybe.py"]
        result.stdout.fnmatch_lines(["*2 passed*"])

    def test_lastfailed_creates_cache_when_needed(self, pytester: Pytester) -> None:
        # Issue #1342
        pytester.makepyfile(test_empty="")
        pytester.runpytest("-q", "--lf")
        assert not os.path.exists(".pytest_cache/v/cache/lastfailed")

        pytester.makepyfile(test_successful="def test_success():\n    assert True")
        pytester.runpytest("-q", "--lf")
        assert not os.path.exists(".pytest_cache/v/cache/lastfailed")

        pytester.makepyfile(test_errored="def test_error():\n    assert False")
        pytester.runpytest("-q", "--lf")
        assert os.path.exists(".pytest_cache/v/cache/lastfailed")

    def test_xfail_not_considered_failure(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            @pytest.mark.xfail
            def test(): assert 0
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*1 xfailed*"])
        assert self.get_cached_last_failed(pytester) == []

    def test_xfail_strict_considered_failure(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            @pytest.mark.xfail(strict=True)
            def test(): pass
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*1 failed*"])
        assert self.get_cached_last_failed(pytester) == [
            "test_xfail_strict_considered_failure.py::test"
        ]

    @pytest.mark.parametrize("mark", ["mark.xfail", "mark.skip"])
    def test_failed_changed_to_xfail_or_skip(
        self, pytester: Pytester, mark: str
    ) -> None:
        pytester.makepyfile(
            """
            import pytest
            def test(): assert 0
        """
        )
        result = pytester.runpytest()
        assert self.get_cached_last_failed(pytester) == [
            "test_failed_changed_to_xfail_or_skip.py::test"
        ]
        assert result.ret == 1

        pytester.makepyfile(
            f"""
            import pytest
            @pytest.{mark}
            def test(): assert 0
        """
        )
        result = pytester.runpytest()
        assert result.ret == 0
        assert self.get_cached_last_failed(pytester) == []
        assert result.ret == 0

    @pytest.mark.parametrize("quiet", [True, False])
    @pytest.mark.parametrize("opt", ["--ff", "--lf"])
    def test_lf_and_ff_prints_no_needless_message(
        self, quiet: bool, opt: str, pytester: Pytester
    ) -> None:
        # Issue 3853
        pytester.makepyfile("def test(): assert 0")
        args = [opt]
        if quiet:
            args.append("-q")
        result = pytester.runpytest(*args)
        result.stdout.no_fnmatch_line("*run all*")

        result = pytester.runpytest(*args)
        if quiet:
            result.stdout.no_fnmatch_line("*run all*")
        else:
            assert "rerun previous" in result.stdout.str()

    def get_cached_last_failed(self, pytester: Pytester) -> list[str]:
        config = pytester.parseconfigure()
        assert config.cache is not None
        return sorted(config.cache.get("cache/lastfailed", {}))

    def test_cache_cumulative(self, pytester: Pytester) -> None:
        """Test workflow where user fixes errors gradually file by file using --lf."""
        # 1. initial run
        test_bar = pytester.makepyfile(
            test_bar="""
            def test_bar_1(): pass
            def test_bar_2(): assert 0
        """
        )
        test_foo = pytester.makepyfile(
            test_foo="""
            def test_foo_3(): pass
            def test_foo_4(): assert 0
        """
        )
        pytester.runpytest()
        assert self.get_cached_last_failed(pytester) == [
            "test_bar.py::test_bar_2",
            "test_foo.py::test_foo_4",
        ]

        # 2. fix test_bar_2, run only test_bar.py
        pytester.makepyfile(
            test_bar="""
            def test_bar_1(): pass
            def test_bar_2(): pass
        """
        )
        result = pytester.runpytest(test_bar)
        result.stdout.fnmatch_lines(["*2 passed*"])
        # ensure cache does not forget that test_foo_4 failed once before
        assert self.get_cached_last_failed(pytester) == ["test_foo.py::test_foo_4"]

        result = pytester.runpytest("--last-failed")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure (skipped 1 file)",
                "*= 1 failed in *",
            ]
        )
        assert self.get_cached_last_failed(pytester) == ["test_foo.py::test_foo_4"]

        # 3. fix test_foo_4, run only test_foo.py
        test_foo = pytester.makepyfile(
            test_foo="""
            def test_foo_3(): pass
            def test_foo_4(): pass
        """
        )
        result = pytester.runpytest(test_foo, "--last-failed")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items / 1 deselected / 1 selected",
                "run-last-failure: rerun previous 1 failure",
                "*= 1 passed, 1 deselected in *",
            ]
        )
        assert self.get_cached_last_failed(pytester) == []

        result = pytester.runpytest("--last-failed")
        result.stdout.fnmatch_lines(["*4 passed*"])
        assert self.get_cached_last_failed(pytester) == []

    def test_lastfailed_no_failures_behavior_all_passed(
        self, pytester: Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_1(): pass
            def test_2(): pass
        """
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["*2 passed*"])
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(["*2 passed*"])
        result = pytester.runpytest("--lf", "--lfnf", "all")
        result.stdout.fnmatch_lines(["*2 passed*"])

        # Ensure the list passed to pytest_deselected is a copy,
        # and not a reference which is cleared right after.
        pytester.makeconftest(
            """
            deselected = []

            def pytest_deselected(items):
                global deselected
                deselected = items

            def pytest_sessionfinish():
                print("\\ndeselected={}".format(len(deselected)))
        """
        )

        result = pytester.runpytest("--lf", "--lfnf", "none")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items / 2 deselected / 0 selected",
                "run-last-failure: no previously failed tests, deselecting all items.",
                "deselected=2",
                "* 2 deselected in *",
            ]
        )
        assert result.ret == ExitCode.NO_TESTS_COLLECTED

    def test_lastfailed_no_failures_behavior_empty_cache(
        self, pytester: Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_1(): pass
            def test_2(): assert 0
        """
        )
        result = pytester.runpytest("--lf", "--cache-clear")
        result.stdout.fnmatch_lines(["*1 failed*1 passed*"])
        result = pytester.runpytest("--lf", "--cache-clear", "--lfnf", "all")
        result.stdout.fnmatch_lines(["*1 failed*1 passed*"])
        result = pytester.runpytest("--lf", "--cache-clear", "--lfnf", "none")
        result.stdout.fnmatch_lines(["*2 desel*"])

    def test_lastfailed_skip_collection(self, pytester: Pytester) -> None:
        """
        Test --lf behavior regarding skipping collection of files that are not marked as
        failed in the cache (#5172).
        """
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """
                import pytest

                @pytest.mark.parametrize('i', range(3))
                def test_1(i): pass
            """,
                "pkg2/test_2.py": """
                import pytest

                @pytest.mark.parametrize('i', range(5))
                def test_1(i):
                    assert i not in (1, 3)
            """,
            }
        )
        # first run: collects 8 items (test_1: 3, test_2: 5)
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 8 items", "*2 failed*6 passed*"])
        # second run: collects only 5 items from test_2, because all tests from test_1 have passed
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 2 failures (skipped 1 file)",
                "*= 2 failed in *",
            ]
        )

        # add another file and check if message is correct when skipping more than 1 file
        pytester.makepyfile(
            **{
                "pkg1/test_3.py": """
                def test_3(): pass
            """
            }
        )
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: rerun previous 2 failures (skipped 2 files)",
                "*= 2 failed in *",
            ]
        )

    def test_lastfailed_skip_collection_with_nesting(self, pytester: Pytester) -> None:
        """Check that file skipping works even when the file with failures is
        nested at a different level of the collection tree."""
        pytester.makepyfile(
            **{
                "test_1.py": """
                    def test_1(): pass
                """,
                "pkg/__init__.py": "",
                "pkg/test_2.py": """
                    def test_2(): assert False
                """,
            }
        )
        # first run
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "*1 failed*1 passed*"])
        # second run - test_1.py is skipped.
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure (skipped 1 file)",
                "*= 1 failed in *",
            ]
        )

    def test_lastfailed_with_known_failures_not_being_selected(
        self, pytester: Pytester
    ) -> None:
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """def test_1(): assert 0""",
                "pkg1/test_2.py": """def test_2(): pass""",
            }
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "* 1 failed, 1 passed in *"])

        Path("pkg1/test_1.py").unlink()
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: 1 known failures not in selected tests",
                "* 1 passed in *",
            ]
        )

        # Recreate file with known failure.
        pytester.makepyfile(**{"pkg1/test_1.py": """def test_1(): assert 0"""})
        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure (skipped 1 file)",
                "* 1 failed in *",
            ]
        )

        # Remove/rename test: collects the file again.
        pytester.makepyfile(**{"pkg1/test_1.py": """def test_renamed(): assert 0"""})
        result = pytester.runpytest("--lf", "-rf")
        result.stdout.fnmatch_lines(
            [
                "collected 2 items",
                "run-last-failure: 1 known failures not in selected tests",
                "pkg1/test_1.py F *",
                "pkg1/test_2.py . *",
                "FAILED pkg1/test_1.py::test_renamed - assert 0",
                "* 1 failed, 1 passed in *",
            ]
        )

        result = pytester.runpytest("--lf", "--co")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure (skipped 1 file)",
                "",
                "<Dir *>",
                "  <Dir pkg1>",
                "    <Module test_1.py>",
                "      <Function test_renamed>",
            ]
        )

    def test_lastfailed_args_with_deselected(self, pytester: Pytester) -> None:
        """Test regression with --lf running into NoMatch error.

        This was caused by it not collecting (non-failed) nodes given as
        arguments.
        """
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """
                    def test_pass(): pass
                    def test_fail(): assert 0
                """,
            }
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "* 1 failed, 1 passed in *"])
        assert result.ret == 1

        result = pytester.runpytest("pkg1/test_1.py::test_pass", "--lf", "--co")
        assert result.ret == 0
        result.stdout.fnmatch_lines(
            [
                "*collected 1 item",
                "run-last-failure: 1 known failures not in selected tests",
                "",
                "<Dir *>",
                "  <Dir pkg1>",
                "    <Module test_1.py>",
                "      <Function test_pass>",
            ],
            consecutive=True,
        )

        result = pytester.runpytest(
            "pkg1/test_1.py::test_pass", "pkg1/test_1.py::test_fail", "--lf", "--co"
        )
        assert result.ret == 0
        result.stdout.fnmatch_lines(
            [
                "collected 2 items / 1 deselected / 1 selected",
                "run-last-failure: rerun previous 1 failure",
                "",
                "<Dir *>",
                "  <Dir pkg1>",
                "    <Module test_1.py>",
                "      <Function test_fail>",
                "*= 1/2 tests collected (1 deselected) in *",
            ],
        )

    def test_lastfailed_with_class_items(self, pytester: Pytester) -> None:
        """Test regression with --lf deselecting whole classes."""
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """
                    class TestFoo:
                        def test_pass(self): pass
                        def test_fail(self): assert 0

                    def test_other(): assert 0
                """,
            }
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 3 items", "* 2 failed, 1 passed in *"])
        assert result.ret == 1

        result = pytester.runpytest("--lf", "--co")
        assert result.ret == 0
        result.stdout.fnmatch_lines(
            [
                "collected 3 items / 1 deselected / 2 selected",
                "run-last-failure: rerun previous 2 failures",
                "",
                "<Dir *>",
                "  <Dir pkg1>",
                "    <Module test_1.py>",
                "      <Class TestFoo>",
                "        <Function test_fail>",
                "      <Function test_other>",
                "",
                "*= 2/3 tests collected (1 deselected) in *",
            ],
            consecutive=True,
        )

    def test_lastfailed_with_all_filtered(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """
                    def test_fail(): assert 0
                    def test_pass(): pass
                """,
            }
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "* 1 failed, 1 passed in *"])
        assert result.ret == 1

        # Remove known failure.
        pytester.makepyfile(
            **{
                "pkg1/test_1.py": """
                    def test_pass(): pass
                """,
            }
        )
        result = pytester.runpytest("--lf", "--co")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: 1 known failures not in selected tests",
                "",
                "<Dir *>",
                "  <Dir pkg1>",
                "    <Module test_1.py>",
                "      <Function test_pass>",
                "",
                "*= 1 test collected in*",
            ],
            consecutive=True,
        )
        assert result.ret == 0

    def test_packages(self, pytester: Pytester) -> None:
        """Regression test for #7758.

        The particular issue here was that Package nodes were included in the
        filtering, being themselves Modules for the __init__.py, even if they
        had failed Modules in them.

        The tests includes a test in an __init__.py file just to make sure the
        fix doesn't somehow regress that, it is not critical for the issue.
        """
        pytester.makepyfile(
            **{
                "__init__.py": "",
                "a/__init__.py": "def test_a_init(): assert False",
                "a/test_one.py": "def test_1(): assert False",
                "b/__init__.py": "",
                "b/test_two.py": "def test_2(): assert False",
            },
        )
        pytester.makeini(
            """
            [pytest]
            python_files = *.py
            """
        )
        result = pytester.runpytest()
        result.assert_outcomes(failed=3)
        result = pytester.runpytest("--lf")
        result.assert_outcomes(failed=3)

    def test_non_python_file_skipped(
        self,
        pytester: Pytester,
        dummy_yaml_custom_test: None,
    ) -> None:
        pytester.makepyfile(
            **{
                "test_bad.py": """def test_bad(): assert False""",
            },
        )
        result = pytester.runpytest()
        result.stdout.fnmatch_lines(["collected 2 items", "* 1 failed, 1 passed in *"])

        result = pytester.runpytest("--lf")
        result.stdout.fnmatch_lines(
            [
                "collected 1 item",
                "run-last-failure: rerun previous 1 failure (skipped 1 file)",
                "* 1 failed in *",
            ]
        )


