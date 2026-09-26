"""Guards for the pinned madmom commit (spec §2.1).

madmom's bundled models are pickled with an old NumPy dtype signature. NumPy 2.4+
deprecates it and NumPy 3 is expected to reject it (upstream PR #559). Loading every
processor we use with warnings turned into errors makes that failure show up here
first, instead of as a confusing error deep inside the pipeline.
"""

import warnings


def test_madmom_imports():
    import madmom

    assert madmom.__version__.startswith("0.17")


def test_madmom_models_load_without_warnings():
    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        CNNChordFeatureProcessor()
        CRFChordRecognitionProcessor()
        RNNDownBeatProcessor()
        DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
        CNNKeyRecognitionProcessor()
