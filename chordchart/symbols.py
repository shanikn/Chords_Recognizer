"""Chord symbols: Harte syntax <-> the short form shown on a chart.

Models and annotation files (.lab) write chords in Harte syntax, `root:quality`, e.g.
"C:maj", "A:min", "G:7" (Harte et al., ISMIR 2005). It's unambiguous and it's what
mir_eval scores, so the whole pipeline uses it internally. A chart shows the short
form musicians read: "C", "Am", "G7".

Milestones 1-6 only produce major/minor chords (madmom's vocabulary). Milestone 7
extends this module to 7ths and sus chords.
"""

_QUALITY_SUFFIX = {"maj": "", "min": "m"}


def harte_to_symbol(harte: str) -> str:
    """Convert Harte to display form: "A:min" -> "Am".

    "N" (no chord) and "X" (unknown) both display as "N".
    """
    if harte in ("N", "X"):
        return "N"
    root, _, quality = harte.partition(":")
    quality = quality or "maj"
    if quality not in _QUALITY_SUFFIX:
        raise ValueError(f"unsupported chord quality in {harte!r}")
    return root + _QUALITY_SUFFIX[quality]
