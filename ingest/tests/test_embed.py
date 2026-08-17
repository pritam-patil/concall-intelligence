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

import time
import uuid

import pytest

from ingest.embed import (
    BATCH_BACKOFF_BASE_SECONDS,
    MAX_BATCH_RETRIES,
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
