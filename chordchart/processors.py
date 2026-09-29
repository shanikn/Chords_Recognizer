"""The models, built once and reused: Beat This! (beats) and madmom's chord and key CNNs.

Building a processor loads its model files from disk. With `num_threads > 1`, madmom
also starts a `multiprocessing.Pool` inside it: the DBN decodes the 3/4 and 4/4
hypotheses in parallel (and madmom's own downbeat RNN, if used, runs its ensemble of
networks in parallel). madmom never closes those pools, so whoever builds a Processors
must `close()` it:

    with Processors(num_threads=4) as procs:     # command line: one analysis
        analyze(song, processors=procs)

`chordchart serve` builds one Processors at startup, reuses it for every analysis, and
closes it on shutdown. Reuse is safe because every processor we use works offline and
keeps no state between calls (the RNN layers reset at the start of each call).
"""

from __future__ import annotations

import multiprocessing.pool
from collections.abc import Iterator, Sequence

from chordchart.beats import FPS


class Processors:
    def __init__(self, beats_per_bar: Sequence[int] = (3, 4), num_threads: int = 1) -> None:
        from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
        from madmom.features.key import CNNKeyRecognitionProcessor

        from chordchart.beat_this import BeatThisModel, make_dbn

        self.beats_per_bar = tuple(beats_per_bar)
        self.num_threads = num_threads
        self.beat_this = BeatThisModel(threads=num_threads)
        self.beat_this_dbn = make_dbn(self.beats_per_bar, threads=num_threads)
        self._madmom_beats: tuple | None = None  # madmom's RNN + DBN, built on first use
        self.chord_features = CNNChordFeatureProcessor()
        self.chord_crf = CRFChordRecognitionProcessor()
        self.key = CNNKeyRecognitionProcessor()
        self._closed = False

    @property
    def downbeat_rnn(self):
        return self._madmom_beat_processors()[0]

    @property
    def downbeat_dbn(self):
        return self._madmom_beat_processors()[1]

    def _madmom_beat_processors(self) -> tuple:
        """madmom's own beat tracker (track_beats_madmom), loaded only if it's used."""
        if self._madmom_beats is None:
            from madmom.features.downbeats import (
                DBNDownBeatTrackingProcessor,
                RNNDownBeatProcessor,
            )

            self._madmom_beats = (
                RNNDownBeatProcessor(num_threads=self.num_threads),
                DBNDownBeatTrackingProcessor(
                    beats_per_bar=list(self.beats_per_bar), fps=FPS, num_threads=self.num_threads
                ),
            )
        return self._madmom_beats

    def pools(self) -> list[multiprocessing.pool.Pool]:
        """The worker pools madmom started inside our processors."""
        return list(_find_pools(vars(self).values()))

    def close(self, force: bool = False) -> None:
        """Shut the worker pools down. `force` stops them at once instead of letting
        running work finish (used when the desktop app quits mid-analysis)."""
        if self._closed:
            return
        self._closed = True
        for pool in self.pools():
            if force:
                pool.terminate()
            else:
                pool.close()
            pool.join()

    def __enter__(self) -> Processors:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _find_pools(objects) -> Iterator[multiprocessing.pool.Pool]:
    """Walk madmom's processor tree. Sequential/ParallelProcessor keep children in
    `.processors`; a processor with a pool has `.map = pool.map`."""
    from madmom.processors import Processor

    seen: set[int] = set()
    stack = list(objects)
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, list | tuple):
            stack.extend(obj)
        elif isinstance(obj, Processor):
            pool = getattr(getattr(obj, "map", None), "__self__", None)
            if isinstance(pool, multiprocessing.pool.Pool):
                yield pool
            stack.extend(vars(obj).values())
