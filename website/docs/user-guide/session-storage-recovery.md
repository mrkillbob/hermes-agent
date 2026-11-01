---
title: "Session Storage Recovery"
description: "What to do when Hermes says another process holds an old copy of the session database's write-ahead log, and what the files beside state.db are"
---

# Session storage recovery

Hermes keeps every conversation in one SQLite file per profile, `state.db`, with two
sidecar files SQLite manages itself: `state.db-wal` (the write-ahead log) and `state.db-shm`.
Several Hermes processes can share that file safely — the gateway, the Desktop app, the
dashboard, cron, and CLI commands all write through SQLite's own locking.

One thing is not safe: **rewriting the store while another process is writing to it**.
When that happens, the processes still holding the *old* copy of the log stop writing on
purpose and every turn answers with a message like:

> another Hermes process still holds an old copy of the session database's write-ahead log,
> so Hermes stopped writing to keep the file safe …

This refusal prevents further writes through a mismatched database generation. It does
not prove that a recovery copy exists or that every conversation can be recovered.

## Preserve the recovery source first

1. **Keep the processes holding the affected profile open.** A deleted database or WAL
   can remain accessible only through an existing process handle. Closing the last holder
   can discard that copy. Do not restart the gateway, quit the Desktop app, or stop the
   remaining holders until recovery evidence has been verified.

2. **Identify holders with `hermes doctor`** (add `-p <profile>` for a named profile).
   Use the inspection command without `--fix`. Keep its output and the affected profile's
   recovery logs. Process-inventory failure does not establish that no holder exists.

3. **Have an operator verify a durable recovery copy.** Hermes attempts to capture a
   retired generation, but capture can fail. A `state.db.retired-wal-*` directory is forensic
   evidence, not by itself a complete, restorable backup. Preserve it and its manifest; an
   operator must establish that the captured database and log belong together and cover the
   data being recovered. If verification fails, retain the holders and seek recovery help.
   Do not use a restart or `doctor --fix` as a substitute for this verification.

After that verification, the operator can coordinate a graceful stop, inspect the recovery
source, and choose a supported restore/restart procedure. Start one process first and verify
both recovered conversations and new writes before resuming other writers. A cleared refusal
alone is not proof that earlier data was recovered.

## Do not

- **Do not run `hermes doctor --fix` while the processes are running.** Doctor refuses the
  checkpoint while it can see a process holding the retired log, but on a host where it cannot
  inspect processes the fix path is exactly the second writer that caused the problem.
- **Do not delete `state.db-wal` or `state.db-shm`.** The log holds committed conversations
  that are not yet in `state.db`. Deleting it is the one action that turns a refusal into
  real data loss.
- **Do not copy `state.db` alone.** The three files are one image. Use a snapshot
  (`hermes backup`) or `hermes sessions recover`, never `cp state.db somewhere/`.
- **Do not ask the agent to fix it.** The agent's own session is in the same store; it will
  hit the same refusal.

## Maintenance commands refuse while someone is writing

`hermes sessions optimize`, `hermes sessions optimize-storage` and `hermes sessions prune`
rewrite the store (VACUUM, a full-text index rebuild, bulk deletes). Running one of them under
a live gateway is how a fleet of agents ends up in the refusal above, so they now check first
and refuse while another process holds the database:

```text
Refusing `hermes sessions optimize-storage`: another process is using ~/.hermes/state.db.
  PID 41230 (hermes gateway run): state.db, state.db-shm, state.db-wal
  PID 41355 (hermes serve --profile work): state.db-wal
Rewriting the database under a live writer is how every agent ends up refusing turns with the
retired state.db-wal error. Nothing is lost.
Stop them first (`hermes gateway stop`, quit the Desktop app, pause cron), then re-run.
Override with --force if you accept the risk.
```

`--dry-run` previews are never blocked. `--force` runs anyway — use it only when you know
the listed processes are idle (a reader you started yourself, for example). The same check
runs when you type `sessions optimize` in the Desktop console. `optimize-storage` keeps watching
while it runs: whenever another process holds the database (under `--force`, or one that opens it
mid-run) it pauses between chunks so that process's writes get through, which makes it much slower.

`hermes sessions prune` is the exception while the gateway is running: the gateway owns
`state.db`, so prune shows its preview from a read-only view and the gateway itself deletes
the selected sessions (skipping any with a turn still running). It refuses as above only when
no gateway is running and some other process holds the database.

## Files you may find beside `state.db`

| File or directory | What it is | What to do |
|---|---|---|
| `state.db-wal`, `state.db-shm` | SQLite's live write-ahead log and its shared-memory index. A large `-wal` is normal while the gateway or Desktop is running. | Leave them alone. They shrink on their own at the next checkpoint. |
| `state.db.retired-wal-<timestamp>-<pid>/` | A capture Hermes made of the log copy a process was still holding when it refused to write, plus a `manifest.json` describing it. Forensic evidence, not a backup you restore blindly. | Keep it. If conversations from just before the incident are missing after recovery, attach the directory to a bug report; a maintainer can tell from `manifest.json` whether those frames belong on top of the current file. |
| `state.db.pre-update-emergency-<timestamp>.bak` | A snapshot the Desktop updater takes before it touches the store. | Keep it until you have used the updated app for a while. Restore only with every Hermes process stopped: `hermes sessions recover --source <file> --inspect-only` first. |
| `state.db.corrupt.<timestamp>.bak`, `*.malformed-backup` | Copies of a file Hermes found damaged before it repaired or quarantined it. | Do not restore them over `state.db` — they are the same damage. Keep for a report; safe to delete once you are back to normal. |
| `state-snapshots/` | Quick snapshots `hermes update` and `hermes backup` take. | Restore with every Hermes process stopped; see [`hermes backup`](../reference/cli-commands.md#hermes-backup). |

## After a verified recovery copy and coordinated stop

Once the operator has verified the recovery copy and coordinated the stop, inspect the
selected source before restoring it. For an existing main database file:

```bash
hermes sessions recover --source ~/.hermes/state.db --inspect-only
```

`--inspect-only` never modifies the file. If it reports the store as recoverable, follow the
command it prints, or restore the newest snapshot from `state-snapshots/`. The mechanics
behind all of this are in the developer guide:
[State DB recovery](../developer-guide/state-db-recovery.md) and
[Session storage](../developer-guide/session-storage.md).
