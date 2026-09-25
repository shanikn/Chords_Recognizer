from chordchart.render.text import render_text

EXPECTED = (
    "Test Song\n"
    "Key: C major   Tempo: 120 BPM   Time: 4/4\n"
    "\n"
    "| C        | C G      | Am       | F        |\n"
    "| G        |\n"
)


def test_render_text_golden(sample_song):
    assert render_text(sample_song) == EXPECTED


def test_render_text_shows_warnings(sample_song):
    sample_song.warnings.append("2 beats per bar detected")
    lines = render_text(sample_song).splitlines()
    assert lines[2] == "Note: 2 beats per bar detected"


def test_render_text_is_ascii_apart_from_the_title(sample_song):
    render_text(sample_song).encode("ascii")
