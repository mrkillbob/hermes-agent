# Exact orphan-run maintenance

This source-only command closes explicitly reviewed orphan run records on already terminal cards. It never restarts workers, changes cards, clears their pointers, signals PIDs, enables automation or supplies CI evidence. Installation and live maintenance each need their own approval. An uninstalled candidate command is not an installed API.

Run from the reviewed source with its qualified Python environment. Each selection entry is an exact `{"board":"default","run_id":123,"task_id":"t_example"}` pair; a named board resolves to `<explicit-home>/kanban/boards/<board>/kanban.db`, and default resolves to `<explicit-home>/kanban.db`. Ambient board/database pins cannot redirect these paths. Worker/delegated contexts refuse cross-board maintenance. Paths, device/inode and schema are bound into the manifest; aliases and replacement refuse.

## Preview and review

```
python -m hermes_cli.kanban_orphan_recovery dry-run \
  --home /EXPLICIT/APPROVED/HERMES/HOME \
  --selection /OWNED/LOCAL/RUN/selection.json > /OWNED/LOCAL/RUN/plan.json
```

Preview performs SQLite reads without migrations or repair. The manifest records full raw task/run preimages, existing closed successor history and task events. Existing closed successor runs remain untouched; any new or changed successor/history after review refuses execution. Every selected card must be done/archived with no current run, claim, lease or worker PID. A well-formed retained task birth marker is historical provenance when its PID is NULL, as in normal completion/archive; preserve and bind it unchanged. Any other run that is open or retains a worker PID refuses, because even a closed run can have a lingering worker. Nonexpired/unknown leases, malformed worker identities and selected live/unknown workers also refuse.

Claims from an unreviewed host refuse. `--claim-host HOST` declares a reviewed historical alias of the execution host and must be part of the approved scope; it is not permission to reconcile another machine's workers. Before including any historical claim alias, establish that it belongs to the execution host and review whether the board was imported from another machine. Numeric PID absence on this machine cannot prove another host's worker exited. Capture fresh liveness at execution, not only at preview. Birth fingerprints use the canonical start-time drift tolerance; malformed epochs or missing identity witnesses remain unknown. No PID is signaled, including a positively identified reused PID.

The approval binds the JSON-content digest (not the whitespace-dependent file hash):

```
python -c 'import json; from pathlib import Path; from hermes_cli.kanban_orphan_recovery import digest; print(digest(json.loads(Path("/OWNED/LOCAL/RUN/plan.json").read_text(encoding="utf-8-sig"))))'
```

Review the complete selection, preimages, historical successors, host aliases, source/patch identity and digest. Keep runtime/autostarts stopped and both feedback jobs disabled; coordinate outside runtime owner's QuickEntry interval. Use a fresh private unsynced receipt directory outside the board home; never place backup outputs in Documents, Desktop or a sync root.

## Approved apply and guarded undo

Only after explicit live clearance of this reviewed operation:

```
python -m hermes_cli.kanban_orphan_recovery apply \
  --plan /OWNED/LOCAL/RUN/plan.json --plan-sha256 REVIEWED_CONTENT_DIGEST \
  --run-dir /OWNED/LOCAL/RUN/fresh-recovery

python -m hermes_cli.kanban_orphan_recovery undo \
  --journal /OWNED/LOCAL/RUN/fresh-recovery/journal.json \
  --plan-sha256 REVIEWED_CONTENT_DIGEST
```

Before any SQL mutation, apply creates exclusive SQLite-online backups of only the selected boards, verifies integrity/preimages and retains their hashes plus a durable full-preimage journal. Online backup retries have a 30-second deadline. On POSIX, the fresh receipt directory entry, backup files and journal are fsynced before SQL mutations. On Windows, file fsync and atomic journal replacement are used; Python does not provide the POSIX directory-fsync guarantee. Committed WAL content is included; a main-file copy is not used. Both normal write transactions are acquired and all rows revalidated before the first mutation. Atomicity is per database. Each database commit is journaled; if the second board fails, the first remains explicitly applied and may be resumed or guardedly undone. Never restore a whole live board backup over later foreign writes.

Only `task_runs.status`, `outcome`, `ended_at` change to administrative `reclaimed`/`reclaimed` plus the reconciliation timestamp. This timestamp does not invent a historical process exit or claim execution succeeded. Original PID/birth, claims, lease, start/heartbeat, summary/error and raw metadata are retained. Cards, assignees, WIP/base, comments and other runs stay unchanged. Recovery and undo append distinct task-event audits with operation ID, approved plan digest and exact pre/postimage digests.

A commit can succeed before filesystem journaling fails, or before a post-commit invariant raises. Retry inspects exact durable run postimages and in-transaction audits; it does not infer rollback from an exception. Same-operation apply/undo is idempotent and adds no duplicate audits or timestamps. Undo requires exact postimage plus unchanged task/history, restores only the three modified run fields, and retains both audits. Any foreign change to protected pre/postimages refuses; unrelated foreign rows are preserved. An undone operation cannot be reapplied without a fresh preview/review.

Resume and undo require exactly one retained backup per selected board, the expected local artifact paths, matching hashes, SQLite integrity, schema and full pending preimages. Missing/incomplete backups or initial journal creation failure cause refusal before SQL changes; preserve that allocation and use a separately reviewed fresh run. Never delete evidence as a retry workaround. Unexpected triggers on touched tables refuse maintenance. No schema creation/migration or automatic corruption repair occurs.


## Maintenance approval scope

Prepare a fresh read-only manifest from the approved source. Review exact board/task/run pairs, full raw task/run/history preimages, existing closed successors, historical claim-host evidence and the content digest. Approval must name the source commit/tree, explicit home and physical databases, the exact selection and fresh manifest digest, owned unsynced backup/journal allocation, coordinated stopped maintenance interval, and whether same-operation guarded undo is authorized.

Keep operational inventories, raw live manifests and host-specific evidence in the private maintenance receipt directory. A published source command or passing disposable test suite does not approve source adoption or live execution. Gateway-state retirement, service activation, other acceptance intervals and PR review/enrollment/CI readiness remain separate operations.
