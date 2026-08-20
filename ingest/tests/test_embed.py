"""Unit tests for the parts of ingest.embed that don't need a live database
or a real embeddings API: chunk_uuid determinism, and the batch-level
backoff/retry logic (embed_batch_with_backoff). The DB upsert + resumability
logic (already_embedded_uuids, run()) was verified for real instead — see
ingest/README.md's "Embedding" section for what that covered and how (a
local PostgREST instance, real Cloudflare and Gemini API calls) — mocking a
Supabase client convincingly enough to test that meaningfully would mostly
test the mock, not the code.
"""

from __future__ import annotations

import math
import time
import uuid
from types import SimpleNamespace

import pytest

from ingest.embed import (
    BATCH_BACKOFF_BASE_SECONDS,
    MAX_BATCH_RETRIES,
    UUID_IN_BATCH,
    already_embedded_uuids,
    chunk_uuid,
    embed_batch_with_backoff,
    read_chunks_jsonl,
)

# --- chunk_uuid ---------------------------------------------------------------


def test_chunk_uuid_is_deterministic():
    a = chunk_uuid("abc123")
    b = chunk_uuid("abc123")
    assert a == b


def test_chunk_uuid_is_a_valid_uuid():
    result = chunk_uuid("some-sha256-hex-string")
    parsed = uuid.UUID(result)  # raises ValueError if malformed
    assert str(parsed) == result


def test_chunk_uuid_differs_for_different_input():
    assert chunk_uuid("aaa") != chunk_uuid("bbb")


def test_chunk_uuid_is_stable_across_process_runs():
    # A fixed expected value, not just "matches itself" -- catches a future
    # change to the namespace constant, which would silently remap every
    # existing chunk to a different row without erroring anywhere.
    assert chunk_uuid("abc123") == str(
        uuid.uuid5(uuid.UUID("6a6e2b0e-6b7b-4f34-9f2e-9d6b6f5c8e3d"), "abc123")
    )


# --- read_chunks_jsonl --------------------------------------------------------


def test_read_chunks_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "chunks.jsonl"
    path.write_text('{"id": "a"}\n\n{"id": "b"}\n   \n{"id": "c"}\n')
    rows = read_chunks_jsonl(path)
    assert [r["id"] for r in rows] == ["a", "b", "c"]


# --- embed_batch_with_backoff --------------------------------------------------


class _FlakyProvider:
    """Fails `fail_times` times, then succeeds. Records sleep-free call
    timestamps aren't needed here -- MAX_BATCH_RETRIES and the backoff
    formula are checked directly against `time.sleep` calls instead of
    real wall-clock time, so this test doesn't take 5+15+... seconds."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"simulated failure #{self.calls}")
        return [[0.0] * 3 for _ in texts]


class _AlwaysFailsProvider:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise RuntimeError(f"simulated failure #{self.calls}")


def test_succeeds_immediately_without_sleeping(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    provider = _FlakyProvider(fail_times=0)
    result = embed_batch_with_backoff(provider, ["a", "b"])

    assert provider.calls == 1
    assert sleeps == []
    assert result == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


def test_retries_and_recovers_within_max_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    provider = _FlakyProvider(fail_times=MAX_BATCH_RETRIES - 1)
    result = embed_batch_with_backoff(provider, ["a"])

    assert provider.calls == MAX_BATCH_RETRIES
    assert len(sleeps) == MAX_BATCH_RETRIES - 1
    assert result is not None


def test_backoff_delays_grow_exponentially(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    provider = _FlakyProvider(fail_times=MAX_BATCH_RETRIES - 1)
    embed_batch_with_backoff(provider, ["a"])

    expected = [BATCH_BACKOFF_BASE_SECONDS * (2**i) for i in range(MAX_BATCH_RETRIES - 1)]
    assert sleeps == expected


def test_gives_up_after_max_retries_and_raises_the_last_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    provider = _AlwaysFailsProvider()
    with pytest.raises(RuntimeError, match=f"simulated failure #{MAX_BATCH_RETRIES}"):
        embed_batch_with_backoff(provider, ["a"])

    assert provider.calls == MAX_BATCH_RETRIES


def test_does_not_sleep_before_the_first_attempt(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    provider = _AlwaysFailsProvider()
    with pytest.raises(RuntimeError):
        embed_batch_with_backoff(provider, ["a"])

    # MAX_BATCH_RETRIES attempts, MAX_BATCH_RETRIES - 1 sleeps between them
    # -- never a sleep before the very first try.
    assert len(sleeps) == MAX_BATCH_RETRIES - 1


# --- already_embedded_uuids batching (regression) -----------------------------
#
# This does NOT test the DB/resumability *semantics* — those are still
# verified for real (see the module docstring). It pins ONE structural
# property: the id list is split into batches of <= UUID_IN_BATCH and results
# are unioned across them. Sending every id in a single `in.(...)` filter built
# a ~25KB request URL that the hosted Supabase gateway (Kong) rejected with a
# bare 400 — a prod-only failure a bare local PostgREST never surfaced. These
# assertions fail if that batching regresses back to one over-long request.


class _FakeQuery:
    """Enough of the postgrest query-builder chain for already_embedded_uuids:
    records each in_() batch on the shared list, and answers execute() as if
    `embedded_ids` are the rows already embedded."""

    def __init__(self, in_batches, embedded_ids):
        self._in_batches = in_batches
        self._embedded_ids = embedded_ids
        self._batch: list[str] = []

    def select(self, *args, **kwargs):
        return self

    def in_(self, _column, values):
        values = list(values)
        self._in_batches.append(values)
        self._batch = values
        return self

    def eq(self, *args, **kwargs):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *args, **kwargs):
        return self

    def execute(self):
        rows = [{"id": i} for i in self._batch if i in self._embedded_ids]
        return SimpleNamespace(data=rows)


class _FakeClient:
    def __init__(self, embedded_ids=()):
        self.in_batches: list[list[str]] = []
        self._embedded_ids = set(embedded_ids)

    def table(self, _name):
        return _FakeQuery(self.in_batches, self._embedded_ids)


def test_already_embedded_batches_ids_within_url_safe_limit():
    n = UUID_IN_BATCH * 2 + 1  # forces >= 3 batches, the last one partial
    ids = [f"u{i}" for i in range(n)]
    client = _FakeClient()

    already_embedded_uuids(client, ids, "cloudflare_bge")

    # No single request exceeds the batch limit...
    assert client.in_batches, "expected at least one batched query"
    assert all(len(batch) <= UUID_IN_BATCH for batch in client.in_batches)
    # ...and the batches together cover exactly the input, in order — nothing
    # dropped, nothing duplicated.
    assert [i for batch in client.in_batches for i in batch] == ids
    assert len(client.in_batches) == math.ceil(n / UUID_IN_BATCH)


def test_already_embedded_unions_results_across_batches():
    n = UUID_IN_BATCH * 2 + 1
    ids = [f"u{i}" for i in range(n)]
    # One hit in the first batch, one in the last — both must come back, which
    # only holds if results are accumulated across batches, not overwritten.
    embedded = {ids[0], ids[-1]}
    client = _FakeClient(embedded_ids=embedded)

    result = already_embedded_uuids(client, ids, "cloudflare_bge")

    assert result == embedded


def test_already_embedded_empty_input_makes_no_query():
    client = _FakeClient()
    result = already_embedded_uuids(client, [], "cloudflare_bge")
    assert result == set()
    assert client.in_batches == []
