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


def test_render_text_golden_with_section(sample_song):
    sample_song.section_start = 65.0
    sample_song.section_end = 130.0
    assert render_text(sample_song) == EXPECTED.replace(
        "Time: 4/4\n", "Time: 4/4\nSection: 1:05-2:10\n"
    )


def test_section_without_end_shows_where_analysis_stopped(sample_song):
    sample_song.section_start = 65.0  # duration is 10 s
    assert render_text(sample_song).splitlines()[2] == "Section: 1:05-1:15"


def test_render_text_shows_warnings(sample_song):
    sample_song.warnings.append("2 beats per bar detected")
    lines = render_text(sample_song).splitlines()
    assert lines[2] == "Note: 2 beats per bar detected"


def test_render_text_is_ascii_apart_from_the_title(sample_song):
    render_text(sample_song).encode("ascii")


def test_section_shows_the_request_when_widened_to_bars(sample_song):
    sample_song.section_start, sample_song.section_end = 64.2, 131.0
    sample_song.requested_start, sample_song.requested_end = 65.0, 130.0
    line = render_text(sample_song).splitlines()[2]
    assert line == "Section: 1:04.2-2:11 (requested 1:05-2:10)"


def test_section_hides_the_request_when_it_matches(sample_song):
    sample_song.section_start, sample_song.section_end = 64.0, 128.0
    sample_song.requested_start, sample_song.requested_end = 64.0, 128.0
    assert render_text(sample_song).splitlines()[2] == "Section: 1:04-2:08"


def test_section_request_without_end(sample_song):
    sample_song.section_start = 64.2  # duration is 10 s
    sample_song.requested_start = 65.0
    line = render_text(sample_song).splitlines()[2]
    assert line == "Section: 1:04.2-1:14.2 (requested from 1:05)"
