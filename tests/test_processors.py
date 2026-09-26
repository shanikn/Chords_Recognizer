"""Processors built once and reused: same results, no locked files, pools shut down."""

import tempfile

import pytest

from chordchart import pipeline
from chordchart.pipeline import analyze
from chordchart.processors import Processors


@pytest.mark.slow
def test_reuse_gives_identical_results_and_leaves_no_locked_files(click_track, monkeypatch):
    # The pipeline normally ignores temp-dir cleanup errors; make them fail the test, so
    # a WAV still held open by a reused processor (madmom memory-maps files) shows up.
    real = tempfile.TemporaryDirectory

    def strict_tempdir(**kwargs):
        return real(**{**kwargs, "ignore_cleanup_errors": False})

    monkeypatch.setattr(pipeline.tempfile, "TemporaryDirectory", strict_tempdir)

    with Processors() as procs:
        first = analyze(click_track, processors=procs)
        second = analyze(click_track, processors=procs)
    fresh = analyze(click_track)  # builds and closes its own set

    for song in (second, fresh):
        assert song.bars == first.bars
        assert song.key == first.key
        assert song.debug["raw"] == first.debug["raw"]
    assert "models" not in first.timings  # reused: nothing loaded during the analysis
    assert "models" in fresh.timings


@pytest.mark.slow
def test_close_shuts_the_worker_pools_down():
    procs = Processors(num_threads=2)
    pools = procs.pools()
    assert len(pools) == 2  # downbeat RNN ensemble + 3/4-vs-4/4 DBN
    workers = [w for pool in pools for w in pool._pool]
    assert all(w.is_alive() for w in workers)

    procs.close()

    assert all(not w.is_alive() for w in workers)
    procs.close()  # a second close is harmless
