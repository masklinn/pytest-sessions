# `pytest-sessions: a supercharged cacheprovider`

`pytest-sessions` provides a generalised rerunning policy, extending
what cacheprovider or stepwise provide.

`--reruns` takes a comma-separated list of outcomes (`pending`,
`skipped`, `xfailed`, `xpassed`, `warnings`, `error`, `failed`,
`passed`) and re-runs just those tests from a reference session.
`--reruns-order` takes the same but uses those to sort tests before
running them.

For instance

    pytest --reruns=failed,error

is equivalent to

    pytest --last-failed

For convenience, and to improve compatibility between the various
rerun control commands, `pytest-sessions` actually reimplement it as
that alias, as well as:

- `--ff` (`--rerun-order=failed,error`)
- `--nf` (`--rerun-order=new`)
- `--sw` (`-x --rerun=failed,error,pending,new`)
- `--sw-skip` (`--maxfail=2 --rerun=failed,error,pending,new`)

Because all those commands have the same unified backing store, it
also means they can be switched between and will behave in a way that
is consistent (for some values of consistency) e.g. `-x --lf` will
rerun failed tests one by one until all are fixed.

## Reference Session

`pytest-sessions` stores a history of multiple
sessions[^sessions_limit], by default the reference session is the
latest finalised session but it's possible to change that reference
point via the `--reference` parameter, which takes either:

- the name of a session file in the cache directory, by default
  session files are of the form
  `session-{timestamp:YYYYMMDDHHmmssSSSSSS}`, but session files can be
  copied and renamed[^naming]
- the absolute path to a session file e.g. obtained from CI or a third
  party

[^sessions_limit]: 100 by default, after which older sessions are
    pruned
[^naming]: renamed session files are not subject to pruning and are
    conserved long-term

## Re-reporting

Because pytest-sessions stores the full output of a run, it is able to
re-report the run, possibly with different options than the original
e.g. `-r` can be specified differently than the original to get more
details without having to run the test suite again, which is quite
useful for very long test suites. In theory, this should also be
compatible with plugins based around standard reporting hooks.

This mode is triggered by the `--show-session` option, and can take an
arbitrary reference session (though as usual it uses the latest
session by default).

## Divergences

### `stepwise`

The original stepwise plugins clears the cache / resets the run on new
tests. sessions runs the new tests in addition to any pending or
failed tests instead.

### Internal State

In order to reimplement the relevant features, sessions blocks
`lfplugin`, `nfplugin`, and `stepwiseplugin`. Although `cacheprovider`
is not touched (and indeed is leveraged) any direct interaction with
the `cacheprovider` in order to try and access the underlying data of
the three plugins will fail.
