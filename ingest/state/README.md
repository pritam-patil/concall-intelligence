# ingest/state — committed run state

`seen_seq_ids.json` is the **seq_id seen-ledger** for the nightly check-for-new
job (`ingest/src/ingest/check_new.py`, run by
`.github/workflows/nightly-ingest.yml`). It records which NSE filings have
already been ingested so each night only processes deltas:

```json
{ "seen": ["seq:106702876", "seq:106699212", ...] }
```

A filing's key is `seq:<seq_id>` (NSE's own announcement filing id). A filing
is ingested only if its key is not already in this list; after ingest, the new
keys are merged in (sorted, for stable diffs) and the workflow **commits this
file back** to `main`. This is the same commit-state pattern as nse-assist's
`data/notify_state.json` alert scheduler.

## Why this is committed (and not gitignored)

nse-assist's bare-runner lessons: a `fresh actions/checkout` runner has only
what's committed. An artifact the code reads but that was gitignored as
"regenerable" is simply **absent** on the runner, and the code silently falls
back — nse-assist hit that three times, the third caught only in real CI logs.
So: **commit exactly the artifacts a fresh checkout needs**, and this ledger is
one of them. It is intentionally outside every `.gitignore` rule.

The ledger path is resolved via `check_new.seen_path()`, which honours a
`SEEN_LEDGER_PATH` override. CI sets it to an absolute repo path so the file the
code writes is exactly the one the commit step commits (an editable-vs-installed
package can otherwise move `__file__`); tests point it at a temp dir. When
simulating a bare runner, isolate **every** such path — a real CI run is still
the only fully faithful test.
