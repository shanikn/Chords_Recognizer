# Evaluation

Scores chordchart against reference annotations (spec §7), per beat tracker:

    uv run python -m evaluate.run_eval                      # madmom and Beat This! + DBN
    uv run python -m evaluate.run_eval --trackers madmom    # one tracker
    uv run python -m evaluate.run_eval --songs yesterday --no-save
    uv run python -m evaluate.run_eval --align-only         # just the time offsets

- **Songs:** 10 Beatles album tracks (`songs.toml`) with the Isophonics chord and beat
  annotations. The annotation archive is downloaded once into `evaluate/data/` and
  checked against its SHA-256 (`data.py`); nothing from it is committed. Audio: the
  official 2009 stereo remasters on The Beatles' YouTube channel, through chordchart's
  download cache.
- **Alignment** (`align.py`): the offset between the recording and the annotations,
  from the audio and the annotations only: chroma against the annotated chords (coarse,
  +-3 s), then the annotated beats against the onsets (fine, +-0.25 s). A song is
  skipped if its audio doesn't cover the annotated music.
- **Runs:** the full pipeline for each tracker (`pipeline.analyze(beats_fn=...)`, no
  analysis cache). madmom is chordchart's default; Beat This! + DBN comes from
  `experiments/beat_this` and needs its download step first (see `trackers.py`).
- **Scores** (`score.py`): chords with `mir_eval.chord` (majmin, root, mirex,
  segmentation) at each stage: `raw` (the recognizer; the same for every tracker),
  `beat_sync`, `chart`; beats with `mir_eval.beat` (F-measure, CMLt, AMLt; first 5 s
  trimmed), downbeat F-measure, meter. Means are weighted by duration.
- **Results:** `results/<date>-<git sha>-<trackers>.json`, committed.

Different from the spec's plan: the annotations are third-party (Isophonics), so they're
downloaded rather than committed under `songs/<slug>/`, and one `songs.toml` lists the
songs.
