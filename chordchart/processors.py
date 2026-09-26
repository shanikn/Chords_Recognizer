"""madmom's processors, built once and reused.

Building a processor loads its model files from disk. With `num_threads > 1`, madmom
also starts a `multiprocessing.Pool` inside it: the downbeat RNN runs its ensemble of
networks in parallel, and the DBN decodes the 3/4 and 4/4 hypotheses in parallel.
madmom never closes those pools, so whoever builds a Processors must `close()` it:

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
        from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
        from madmom.features.key import CNNKeyRecognitionProcessor

        self.beats_per_bar = tuple(beats_per_bar)
        self.num_threads = num_threads
        self.downbeat_rnn = RNNDownBeatProcessor(num_threads=num_threads)
        self.downbeat_dbn = DBNDownBeatTrackingProcessor(
            beats_per_bar=list(self.beats_per_bar), fps=FPS, num_threads=num_threads
        )
        self.chord_features = CNNChordFeatureProcessor()
        self.chord_crf = CRFChordRecognitionProcessor()
        self.key = CNNKeyRecognitionProcessor()
        self._closed = False

    def pools(self) -> list[multiprocessing.pool.Pool]:
        """The worker pools madmom started inside our processors."""
        return list(_find_pools(vars(self).values()))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pool in self.pools():
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
