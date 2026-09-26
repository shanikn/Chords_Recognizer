import pytest

from chordchart.symbols import harte_to_symbol


@pytest.mark.parametrize(
    ("harte", "symbol"),
    [
        ("C:maj", "C"),
        ("A:min", "Am"),
        ("F#:min", "F#m"),
        ("Bb:maj", "Bb"),
        ("C", "C"),  # Harte shorthand: a bare root means major
        ("N", "N"),
        ("X", "N"),  # "unknown" is shown as no chord
    ],
)
def test_harte_to_symbol(harte, symbol):
    assert harte_to_symbol(harte) == symbol


def test_unsupported_quality_raises():
    with pytest.raises(ValueError, match="C:7"):
        harte_to_symbol("C:7")
