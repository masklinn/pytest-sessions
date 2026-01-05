# https://raw.githubusercontent.com/pytest-dev/pytest/2dc5b56e7934bfb8bed30fde4b8f972133b23691/testing/test_cacheprovider.py
import os

from pytest import Pytester


class TestNewFirst:
    def test_newfirst_usecase(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            **{
                "test_1/test_1.py": """
                def test_1(): assert 1
            """,
                "test_2/test_2.py": """
                def test_1(): assert 1
            """,
            }
        )

        p1 = pytester.path.joinpath("test_1/test_1.py")
        os.utime(p1, ns=(p1.stat().st_atime_ns, int(1e9)))

        result = pytester.runpytest("-v")
        result.stdout.fnmatch_lines(
            [
                "*test_1/test_1.py::test_1 PASSED*",
                "*test_2/test_2.py::test_1 PASSED*",
            ]
        )

        result = pytester.runpytest("-v", "--nf")
        result.stdout.fnmatch_lines(
            [
                "*test_2/test_2.py::test_1 PASSED*",
                "*test_1/test_1.py::test_1 PASSED*",
            ]
        )

        p1.write_text(
            "def test_1(): assert 1\ndef test_2(): assert 1\n", encoding="utf-8"
        )
        os.utime(p1, ns=(p1.stat().st_atime_ns, int(1e9)))

        result = pytester.runpytest("--nf", "--collect-only", "-q")
        result.stdout.fnmatch_lines(
            [
                "test_1/test_1.py::test_2",
                "test_2/test_2.py::test_1",
                "test_1/test_1.py::test_1",
            ]
        )

        # Newest first with (plugin) pytest_collection_modifyitems hook.
        pytester.makepyfile(
            myplugin="""
            def pytest_collection_modifyitems(items):
                items[:] = sorted(items, key=lambda item: item.nodeid)
                print("new_items:", [x.nodeid for x in items])
            """
        )
        pytester.syspathinsert()
        result = pytester.runpytest(
            "--nf", "-p", "myplugin", "--collect-only", "-q"
        )
        result.stdout.fnmatch_lines(
            [
                "new_items: *test_1.py*test_1.py*test_2.py*",
                "test_1/test_1.py::test_2",
                "test_2/test_2.py::test_1",
                "test_1/test_1.py::test_1",
            ]
        )

    def test_newfirst_parametrize(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            **{
                "test_1/test_1.py": """
                import pytest
                @pytest.mark.parametrize('num', [1, 2])
                def test_1(num): assert num
            """,
                "test_2/test_2.py": """
                import pytest
                @pytest.mark.parametrize('num', [1, 2])
                def test_1(num): assert num
            """,
            }
        )

        p1 = pytester.path.joinpath("test_1/test_1.py")
        os.utime(p1, ns=(p1.stat().st_atime_ns, int(1e9)))

        result = pytester.runpytest("-v")
        result.stdout.fnmatch_lines(
            [
                "*test_1/test_1.py::test_1[1*",
                "*test_1/test_1.py::test_1[2*",
                "*test_2/test_2.py::test_1[1*",
                "*test_2/test_2.py::test_1[2*",
            ]
        )

        result = pytester.runpytest("-v", "--nf")
        result.stdout.fnmatch_lines(
            [
                "*test_2/test_2.py::test_1[1*",
                "*test_2/test_2.py::test_1[2*",
                "*test_1/test_1.py::test_1[1*",
                "*test_1/test_1.py::test_1[2*",
            ]
        )

        p1.write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('num', [1, 2, 3])\n"
            "def test_1(num): assert num\n",
            encoding="utf-8",
        )  # fmt: skip
        os.utime(p1, ns=(p1.stat().st_atime_ns, int(1e9)))

        # Running only a subset does not forget about existing ones.
        result = pytester.runpytest("-v", "--nf", "test_2/test_2.py")
        result.stdout.fnmatch_lines(
            ["*test_2/test_2.py::test_1[1*", "*test_2/test_2.py::test_1[2*"]
        )

        result = pytester.runpytest("-v", "--nf")
        result.stdout.fnmatch_lines(
            [
                "*test_1/test_1.py::test_1[3*",
                "*test_2/test_2.py::test_1[1*",
                "*test_2/test_2.py::test_1[2*",
                "*test_1/test_1.py::test_1[1*",
                "*test_1/test_1.py::test_1[2*",
            ]
        )
