"""Sessions is organised around multiple sub-plugins to handle its
capabilities:

- `SessionPlugin` serves to save and update sessions, it uses the
  reference in order to maintain old states when a followup session
  only uses partial collection (e.g. `pytest` then `pytest foo.py`
  should not lose  failures outside of `foo.py`)
- `OrderPlugin` reorders selected cases based on their state in the
  previous session

  WARNING: should emit some sort of warning if the rerun order is not
  a subset of the rerun filter: if only reruning previously failed
  test it doesn't make sense to prioritise new tests (aka --lf + --nf
  makes no sense)

- `RerunPlugin` handles the filtering of tests based on their previous
  session
- `FilterPlugin` handles a specific optimisation and could be part of
  `Rerun` but seemed simpler separate: when rerunning only non-`new`
  tests, we can ignore any file which did not contain any test with
  the selected outcomes, as they can't contain tests we wish to rerun.

  The base case is lastfailed, which doesn't need to collect inside
  files (or even directories) which didn't have a failed test during
  the reference session, so those can be skipped directly. But the
  same can be done for e.g. stepwise (we only want the n failed tests
  and all the pending tests from the previous session).


Sessions assigns a number of *outcomes* to tests it has collected (or
revived), most of those are pytest states:

- passed
- failed
- error
- xfailed
- xpassed
- skipped
- ?warnings?

However it also has two oddball states `new` and `pending`.

`new` is the outcome which is assigned after collection, if the nodeid
was not found in the reference session. Because of the `FilterPlugin`,
it thus should only happen in non-rerun sessions *or* sessions which
specifically selected the `new` outcome. `pending` is what `new` tests
transition to at the end of the session, if they did not get executed.

A major complication for collecting test results is that each
*invocation context* can present a subset of outcomes:

- `collect` can fail, in which case we only get the file information
  not the full nodeid
- `setup` can pass, error, or skip, in the latter two cases it will
  ?skip teardown? (TODO: check, also what if it triggers a warning)
- `call` can pass, error, skip, fail, xpass, or xfail, following which
  teardown will run
- `teardown` can pass or error (TODO: or warn?) whether the test
  passed or failed

However per `pytest_runtest_protocol`, `pytest_runtest_logfinish` is
called after running a test, which could be used to aggregate the test
info collected during `pytest_runtest_logreport`?

A given sessions database transitions through several phases marked by
updating the `user_version`:

- phase 1 is set after creating the schema
- phase 2
- phase 3
- phase 4
- phase 5 is set at the end of the session, at this point the session
  can be the implied reference of a new session

"""

import datetime
import itertools
import json
import os
import pathlib
import typing
from collections.abc import Container, Generator, Iterable, Iterator

import pytest
import sqlite3
from _pytest._code.code import TracebackStyle
from _pytest.reports import CollectErrorRepr


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "sessions_limit",
        help="number of sessions to keep in the history",
        type="int",
        default=100,
    )
    parser.addoption(
        "--reference", help="reference session (default: last completed)"
    )
    parser.addoption(
        "--rerun",
        help="comma-separated list of test categories to rerun"
        "(pending, deselected, skipped, xfailed, xpassed, warnings, error, failed)",
    )
    parser.addoption(
        "--rerun-order",
        help="comma-separated list of test categories order "
        "(same as rerun, plus 'new', note that tests which were never even collected in"
        " the reference session will show up as new)",
    )
    parser.addoption(
        "--show-session",
        action="store_true",
        help="re-report the reference session",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    dbdir = config.cache.mkdir('sessions')
    if ref := config.getoption('reference'):
        refpath = dbdir / ref
    else:
        for refpath in sorted(dbdir.glob('session-*'), reverse=True):
            with sqlite3.connect(refpath) as refcn:
                if refcn.execute('PRAGMA user_version').fetchone() == (5,):
                    break
        else:
            refpath = ':memory:'

    if config.getoption("--show-session"):
        config.pluginmanager.register(
            ShowSessionPlugin(config, os.fspath(refpath)), "showsessionplugin"
        )
        return

    config.pluginmanager.set_blocked('lfplugin')
    config.pluginmanager.set_blocked('nfplugin')
    config.pluginmanager.set_blocked('stepwiseplugin')
    session = SessionPlugin(config)
    config.pluginmanager.register(session, 'sessionplugin')

    all_if_none = True
    rerun_order = config.getoption('--rerun-order')
    if rerun := config.getoption('--rerun'):
        pass
    elif config.getoption('--lf'):
        all_if_none = config.getoption('--lfnf') == 'all'
        rerun = 'failed,error'
    elif config.getoption('--sw-skip'):
        rerun = "failed,error,pending,new"
        config.option.maxfail = 2
    elif config.getoption('--sw-reset'):
        rerun = None
        config.option.maxfail = 1
    elif config.getoption('--sw'):
        rerun = "failed,error,pending,new"
        config.option.maxfail = 1

    if rerun_order := config.getoption('--rerun-order'):
        pass
    elif config.getoption('--ff'):
        rerun_order = "failed,error"
    elif config.getoption('--nf'):
        rerun_order = "new"

    config.pluginmanager.register(
        rp := RerunPlugin(
            session,
            os.fspath(refpath),
            rerun,
            all_if_none,
        ),
        'session-rerunplugin',
    )
    if rerun_order:
        if reorder := {
            o: i
            for i, o in enumerate(
                filter(None, map(str.strip, rerun_order.split(',')))
            )
        }:
            config.pluginmanager.register(
                ReorderPlugin(
                    rp.cn,
                    reorder,
                ),
                'session-reorderplugin',
            )


def init_db(cn: sqlite3.Connection, schema: str = 'main') -> None:
    cn.executescript(f"""
CREATE TABLE {schema}.items (
    nodeid text not null,
    outcome text not null CHECK (
        outcome in (
            'pending',
            'skipped',
            'xfailed',
            'xpassed',
            'warnings',
            'error',
            'failed',
            'passed'
        )
    ),

    collect text,
    setup text,
    call text,
    teardown text,

    -- filename could be extracted from nodeid but there is no way to
    -- reconstruct lineno (?) and testname is dotted, and missing entirely
    -- for non-python test files, so easier to straight store the location
    filename text,
    lineno integer,
    testname text
) STRICT;

CREATE UNIQUE INDEX {schema}.items_nodeid_idx ON items (nodeid);
CREATE INDEX {schema}.items_outcome_idx ON items (outcome);

PRAGMA {schema}.user_version = 1;
""")


class SessionPlugin:
    def __init__(self, config: pytest.Config) -> None:
        assert config.cache is not None, "cacheprovider must be enabled"
        self.config: pytest.Config = config
        self.session: pytest.Session | None = None
        self.dbdir = config.cache.mkdir("sessions")
        self.session_name = self.dbdir / datetime.datetime.now().strftime(
            'session-%Y%m%d%H%M%S%f'
        )
        self.cn = sqlite3.connect(
            self.session_name, timeout=0.0, isolation_level=None
        )
        init_db(self.cn)

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.session = session

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        config = self.config
        if config.getoption("cacheshow") or hasattr(config, "workerinput"):
            return

        if config.getoption("collectonly"):
            return

        self.cn.execute("ANALYZE")
        self.cn.execute("PRAGMA user_version = 5;")
        limit = session.config.getini("sessions_limit")
        # TODO: in case of concurrent sessions, should not take
        #       pending sessions (user_version < 5) in account
        sessionfiles = sorted(self.dbdir.glob("session-*"))
        for dbfile in sessionfiles[:-limit]:
            dbfile.unlink(missing_ok=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self,
        session: pytest.Session,
        config: pytest.Config,
        items: list[pytest.Item],
    ) -> None:
        self.cn.executemany(
            "INSERT INTO items (nodeid, outcome) VALUES (?, 'pending')",
            [[it.nodeid] for it in items],
        )

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            outcome = "failed"
        elif report.skipped:
            outcome = "skipped"
        else:
            return
        self.cn.execute(
            """
            INSERT INTO items (nodeid, outcome, collect)
            VALUES (?, ?, ?)
            ON CONFLICT
            DO UPDATE SET outcome = excluded.outcome, collect = excluded.collect
            """,
            [
                report.nodeid,
                outcome,
                json.dumps(report._to_json()),
            ],
        )

    def pytest_warning_recorded(
        self,
        # warning_message: warnings.WarningMessage,
        when: typing.Literal[
            'config', 'collect', 'runtest'
        ],  # TODO: warning during setup or teardown?
        nodeid: str,
        # location: tuple[str, str, str] | None,
    ) -> None:
        if not nodeid or when != 'runtest':
            return

        self.cn.execute(
            "UPDATE items SET outcome = 'warnings' WHERE nodeid = ?",
            [nodeid],
        )

    def pytest_runtest_logstart(
        self, nodeid: str, location: tuple[str, int | None, str]
    ) -> None:
        self.cn.execute(
            "UPDATE items SET filename = ?, lineno = ?, testname = ? WHERE nodeid = ?",
            [*location, nodeid],
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # TODO: review this, it's called for each phase (setup, call,
        #       teardown) with the outcome *for that phase*
        # TODO: also some combinations might be odd?
        outcome: str = report.outcome
        if hasattr(report, 'wasxfail'):
            if report.skipped:
                outcome = 'xfailed'
            else:
                outcome = f'x{outcome}'
        elif report.skipped:
            outcome = 'skipped'
        elif (
            report.when in ("collect", "setup", "teardown")
            and outcome == "failed"
        ):
            outcome = "error"

        self.cn.execute(
            "UPDATE items SET outcome = ? WHERE nodeid = ? AND outcome in ('pending', 'passed')",
            [outcome, report.nodeid],
        )
        if (when := report.when) in ('setup', 'call', 'teardown'):
            self.cn.execute(
                f"UPDATE items SET {when} = ? WHERE nodeid = ?",
                [json.dumps(report._to_json()), report.nodeid],
            )


class RerunPlugin:
    rerun: set[str] | None

    def __init__(
        self,
        session: SessionPlugin,
        reference: str,
        rerun: str | None,
        all_if_none: bool,
    ) -> None:
        self.skipped_files = 0
        self.config: pytest.Config = session.config
        self.cn = sqlite3.connect(
            session.session_name, timeout=0.0, isolation_level=None
        )
        self.cn.execute("ATTACH ? AS reference", [reference])
        if reference == ':memory:':
            init_db(self.cn, schema='reference')

        if rerun:
            self.rerun = {tag for r in rerun.split(',') if (tag := r.strip())}
            if self.rerun and 'new' not in self.rerun:
                try:
                    self.config.pluginmanager.register(
                        SkipCollection(self.config, self.cn, self.rerun),
                        'sessionskipcollectionplugin',
                    )
                except Empty:
                    if all_if_none:
                        self.rerun = None
        else:
            self.rerun = None
        self.all_if_none = all_if_none

    def pytest_collection_modifyitems(
        self,
        config: pytest.Config,
        items: list[pytest.Item],
    ) -> None:
        self.cn.execute("""
        INSERT INTO main.items
            SELECT *
            FROM reference.items
            WHERE true
        ON CONFLICT DO NOTHING
        """)
        if not self.rerun:
            return

        prev = dict(
            self.cn.execute("""
        SELECT main.items.nodeid, coalesce(reference.items.outcome, 'new')
        FROM main.items
        LEFT JOIN reference.items ON (main.items.nodeid = reference.items.nodeid)
        """)
        )
        kept = []
        deselected = []
        for item in items:
            if prev[item.nodeid] in self.rerun:
                kept.append(item)
            else:
                deselected.append(item)
        if kept or not self.all_if_none:
            config.hook.pytest_deselected(items=deselected)
            items[:] = kept

            # carry forwards the previous runstate of existing
            # collected but deselected tests, otherwise it gets
            # forgotten and the next session sees them as new again
            if updates := [
                (outcome, item.nodeid)
                for item in deselected
                if (outcome := prev[item.nodeid]) != 'new'
            ]:  # fmt: skip
                self.cn.executemany(
                    "UPDATE main.items SET outcome = ? WHERE nodeid = ?",
                    updates,
                )

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.cn.execute("""
        UPDATE main.items
        SET outcome = reference.items.outcome
        FROM reference.items
        WHERE main.items.nodeid = reference.items.nodeid
          AND main.items.outcome = 'pending'
          AND reference.items.outcome != 'passed'
        """)


class ShowSessionPlugin:
    def __init__(self, config: pytest.Config, reference: str) -> None:
        self.config = config
        self.cn = sqlite3.connect(reference, timeout=0.0)
        # mapping interface is a lot more convenient than tuples here
        self.cn.row_factory = sqlite3.Row

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection(
        self, session: pytest.Session
    ) -> typing.Literal[True]:
        for row in self.cn.execute(
            "SELECT * FROM items WHERE collect IS NOT NULL"
        ):
            assert row['outcome'] in ('failed', 'skipped'), \
                "pytest_collectreport only stores collection skipping or failure"  # fmt: skip

            report = pytest.CollectReport._from_json(json.loads(row['collect']))
            if isinstance(report.longrepr, list):
                # apparently `CollectReport._from_json` does not deserialize tuple longrepr correctly
                report.longrepr = tuple(report.longrepr)
            elif report.failed and isinstance(report.longrepr, str):
                # pytest 9 requires rewrapping the repr or we get a
                # different short summary between normal and replay,
                # even though the string version is more useful (it
                # has the error message)
                report.longrepr = CollectErrorRepr(report.longrepr)

            self.config.hook.pytest_collectreport(report=report)

        [count] = self.cn.execute(
            "SELECT count(*) FROM items WHERE outcome NOT IN ('pending', 'new') AND nodeid LIKE '%::%'"
        ).fetchone()
        session.testscollected = count

        if count:
            return True

        # Create mock items to trick pytest into thinking it collected tests
        # This ensures session.testscollected is set correctly and runtestloop is called
        class MockItem(pytest.Item):
            def runtest(self) -> None:
                pass

            def repr_failure(
                self,
                excinfo: pytest.ExceptionInfo[BaseException],
                style: TracebackStyle | None = None,
            ) -> str:
                return ""

            def reportinfo(self) -> tuple[pathlib.Path, int, str]:
                return self.path, 0, ""

        class MockFile(pytest.File):
            def collect(self) -> list[typing.Any]:
                return []

        mfile = MockFile.from_parent(
            parent=session, path=pathlib.Path("session_replay.py")
        )

        session.items = [
            MockItem.from_parent(parent=mfile, name=name)
            for name in map("mock_item_{}".format, range(count))
        ]  # fmt: skip

        return True

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session: pytest.Session) -> bool | None:
        # duplicate this from pytest since we're completely replacing the runloop
        # TODO: continue_on_collection_errors?
        if session.testsfailed:
            raise session.Interrupted(
                f"{session.testsfailed} error{'s' if session.testsfailed != 1 else ''} during collection"
            )

        self._replay_reports()
        return True

    def _replay_reports(self) -> None:
        logstart = self.config.hook.pytest_runtest_logstart
        logreport = self.config.hook.pytest_runtest_logreport
        logfinish = self.config.hook.pytest_runtest_logfinish
        for row in self.cn.execute(
            "SELECT * FROM items WHERE nodeid LIKE '%::%'"
        ):
            location = row['filename'], row['lineno'], row['testname']
            logstart(nodeid=row['nodeid'], location=location)

            for phase in ('setup', 'call', 'teardown'):
                if (r := row[phase]) is not None:
                    report = pytest.TestReport._from_json(json.loads(r))
                    # apparently `BaseReport._from_json` does not handle longrepr correctly
                    if isinstance(report.longrepr, list):
                        report.longrepr = tuple(report.longrepr)
                    logreport(report=report)

            logfinish(nodeid=row['nodeid'], location=location)


class Empty(Exception):
    pass


class SkipCollection:
    """Rerun collection optimisation, mostly lifted from
    ``LFPluginCollWrapper``: skips collecting paths where the
    reference session didn't trigger any ``outcomes``.

    However this is strongly caveated by the requirements of lfnf: if
    all the ``outcomes`` got remved, renamed, etc... causing them to
    not be part of the collection we need to run a full collection.

    This means during collection, we must prioritise last-failed node,
    and only start ignoring nodes after we've found at least one
    failure.
    """

    def __init__(
        self, config: pytest.Config, cn: sqlite3.Connection, outcomes: set[str]
    ) -> None:
        assert outcomes
        assert 'new' not in outcomes
        self.config = config
        self.rootdir = config.rootpath
        self.skipped_paths = 0
        self.found_failure = False

        outcome_patterns = ', '.join('?' * len(outcomes))
        nodeids = [
            nodeid
            for [nodeid] in cn.execute(
                f"SELECT nodeid FROM reference.items WHERE outcome in ({outcome_patterns})",
                [*outcomes],
            )
        ]
        paths = {nodeid.split('::')[0] for nodeid in nodeids}
        if not any(config.rootpath.joinpath(path).is_file() for path in paths):
            raise Empty()

        self.trie = IdTrie(config.rootpath, nodeids)
        if not self.trie:
            raise Empty()

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(
        self,
        collector: pytest.Collector,
    ) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
        report = yield
        if isinstance(collector, (pytest.Session, pytest.Directory)):
            report.result.sort(
                key=lambda node: node.path in self.trie,
                reverse=True,
            )
        elif isinstance(collector, pytest.File) and collector.path in self.trie:
            nodes = report.result
            if not self.found_failure:
                if not any(x.nodeid in self.trie for x in nodes):
                    return report

                self.config.pluginmanager.register(
                    SkipCollected(self),
                    "sessionplugin-skipcollected",
                )
                self.found_failure = True

            session = collector.session
            nodes[:] = [
                node
                for node in nodes
                if node.nodeid in self.trie
                or session.isinitpath(node.path)
                or isinstance(node, pytest.Collector)
            ]

        return report


class SkipCollected:
    def __init__(self, parent: SkipCollection) -> None:
        self.parent = parent
        self.trie = parent.trie

    def pytest_make_collect_report(
        self,
        collector: pytest.Collector,
    ) -> pytest.CollectReport | None:
        if isinstance(collector, pytest.File):
            if collector.path not in self.trie:
                self.parent.skipped_paths += 1

                return pytest.CollectReport(
                    collector.nodeid, "passed", longrepr=None, result=[]
                )
        return None


Trie = dict[str, 'Trie']


class IdTrie(Container[pathlib.Path | str]):
    def __init__(self, rootpath: pathlib.Path, nodeids: Iterable[str]) -> None:
        self.rootpath = rootpath
        self.d: Trie = {}
        for nodeid in nodeids:
            d = self.d
            for k in self.id_to_path(nodeid):
                d = d.setdefault(k, {})

    def __bool__(self) -> bool:
        return bool(self.d)

    @staticmethod
    def id_to_path(nodeid: str) -> Iterator[str]:
        path, *symbols = nodeid.split('::')
        return itertools.chain(pathlib.Path(path).parts, symbols)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, pathlib.Path):
            item = os.fspath(item.relative_to(self.rootpath))
        assert isinstance(item, str)
        d: Trie | None = self.d
        for segment in self.id_to_path(item):
            if d is None:
                return False
            d = d.get(segment)
        return d is not None


class ReorderPlugin:
    def __init__(self, cn: sqlite3.Connection, reorder: dict[str, int]) -> None:
        self.cn = cn
        self.reorder = reorder

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(
        self, items: list[pytest.Item]
    ) -> Iterator[None]:
        res = yield

        prev = dict(
            self.cn.execute("""
        SELECT coalesce(main.items.nodeid, reference.items.nodeid),
               coalesce(reference.items.outcome, 'new')
        FROM main.items FULL OUTER JOIN reference.items
          ON (main.items.nodeid = reference.items.nodeid)
        """)
        )
        items.sort(key=lambda it: (
            self.reorder.get(prev[it.nodeid], 99),
            -it.path.stat().st_mtime,
        ))  # fmt: skip
        return res
