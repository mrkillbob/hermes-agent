# Inspect existing Kanban cards without starting Hermes

Use the source checkout's supported Python (3.11–3.14) and installed dependencies:

```sh
python -m hermes_cli.kanban_inspect --database /absolute/path/kanban.db --board default t_12345678 t_abcdef01
```

This separate diagnostic entry point does not call the Hermes bootstrap, normal
Kanban initializing connection, migrations, schema repair or task disposition.
It needs no running Gateway or storage-owner process. The explicit database path
is reported with a caller-supplied board label; this is inspection evidence, not
proof that the path is the canonical board or permission to change a card.
Output contains full private card history: keep it in an approved private location.

The connection uses SQLite `mode=ro`, `query_only`, normal locking and one read
transaction pinned before the card reads. Committed WAL frames are included;
uncommitted changes and commits after the snapshot starts are excluded. All
selected cards share that snapshot. Full revision inputs are preserved, including
links and subscriptions. Missing schema or cards fails without partial output.
At most twenty distinct cards and eight MiB of serialized row data are admitted;
overflow refuses rather than truncating history. SQLite row/value size is capped
at eight MiB. Lock waits and SQL progress use five-second limits, which cannot
interrupt a stalled OS call or arbitrary Python work.

Read-only means no writes to database records or schema. SQLite may create or
update WAL/SHM bookkeeping files where OS permissions allow. SQLite documents
that read-only WAL access requires readable existing sidecars, permission to
create them, or a genuinely immutable database. The linked SQLite build matters:
an incompatible system Python may fail even where the application's bundled
Python succeeds. Errors report the SQLite version and native error name/code
when available. Use the supported application interpreter; do not change access
settings to hide a failure. This command does not automatically switch readers.

There is deliberately no `immutable=1`, `nolock` or raw-copy fallback. PID absence,
a successful earlier read, and a missing WAL file do not establish immutability.
SQLite's backup API also needs a valid source connection and is unnecessary for
this bounded read transaction; copying the main file alone can omit WAL commits.
If normal access fails, retain the exact error and diagnose path, permissions,
sidecars and reader build without initializing or repairing the source.

References: [SQLite read-only WAL](https://sqlite.org/wal.html#read_only_databases),
[URI options and immutable contract](https://sqlite.org/uri.html),
[online backup consistency](https://sqlite.org/backup.html).
