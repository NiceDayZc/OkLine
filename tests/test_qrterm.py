"""Offline tests for :mod:`okline.qrterm` (terminal QR-code rendering).

These exercise the pure rendering helpers used by the QR-login flow:

* :func:`qr_matrix`     — the boolean module grid (with quiet-zone border)
* :func:`qr_to_ascii`   — Unicode "half"/"full" block art
* :func:`print_qr`      — writes the art to a stream

The actual QR encoding is provided by the third-party ``qrcode`` package, so
the whole module is skipped if it is not installed.  No network / Node.js.
"""

from __future__ import annotations

import io
import math

import pytest

# QR rendering depends on the optional 'qrcode' package; skip cleanly without it.
pytest.importorskip("qrcode")

from okline.qrterm import print_qr, qr_matrix, qr_to_ascii

# Arbitrary payload that is long enough to need a non-trivial QR version.
DATA = "https://line.me/R/ti/p/okline-test-payload"

# Unicode glyphs the renderer uses, for readable assertions.
FULL_BLOCK = "█"
UPPER_HALF = "▀"
LOWER_HALF = "▄"


# ---------------------------------------------------------------------------
# qr_matrix
# ---------------------------------------------------------------------------
def test_qr_matrix_is_square_list_of_bools():
    """The matrix is a square grid of plain ``bool`` values."""
    matrix = qr_matrix(DATA)

    assert isinstance(matrix, list)
    assert len(matrix) > 0
    # square: every row has the same length as the number of rows
    n = len(matrix)
    assert all(isinstance(row, list) and len(row) == n for row in matrix)
    # cells are real bools, not ints/None
    assert all(isinstance(cell, bool) for row in matrix for cell in row)


def test_qr_matrix_includes_quiet_zone_border():
    """The ``border`` quiet zone is present and is made of light (False) cells.

    The outermost ``border`` ring of modules must be light (``False``) so the
    finder pattern is surrounded by clear space, and a larger border yields a
    correspondingly larger matrix.
    """
    border = 4
    matrix = qr_matrix(DATA, border=border)
    n = len(matrix)

    # The entire top/bottom `border` rows are light.
    for r in list(range(border)) + list(range(n - border, n)):
        assert not any(matrix[r]), f"row {r} in quiet zone should be all light"
    # The entire left/right `border` columns are light.
    for row in matrix:
        assert not any(row[:border])
        assert not any(row[n - border :])


def test_qr_matrix_border_changes_size():
    """A wider border produces a strictly larger (still square) matrix."""
    small = qr_matrix(DATA, border=1)
    big = qr_matrix(DATA, border=6)

    assert len(small) < len(big)
    # The size difference is exactly 2 * (extra border) per side.
    assert len(big) - len(small) == 2 * (6 - 1)


@pytest.mark.parametrize("ec", ["L", "M", "Q", "H"])
def test_qr_matrix_accepts_each_error_correction_level(ec):
    """All four error-correction levels render a valid square matrix."""
    matrix = qr_matrix(DATA, error_correction=ec)
    assert len(matrix) == len(matrix[0])


def test_qr_matrix_unknown_error_correction_falls_back():
    """An unrecognised EC level falls back to the default instead of raising."""
    fallback = qr_matrix(DATA, error_correction="ZZZ")
    default = qr_matrix(DATA, error_correction="M")
    # Same payload + same effective EC level => identical geometry.
    assert len(fallback) == len(default)


# ---------------------------------------------------------------------------
# qr_to_ascii — line counts and widths
# ---------------------------------------------------------------------------
def test_half_style_line_count_is_ceil_half_rows():
    """'half' packs two module-rows per text line: lines == ceil(rows / 2)."""
    matrix = qr_matrix(DATA)
    rows = len(matrix)

    lines = qr_to_ascii(DATA, style="half").split("\n")
    assert len(lines) == math.ceil(rows / 2)
    # Each half-block line is exactly one glyph wide per module column.
    assert all(len(line) == rows for line in lines)


def test_full_style_has_one_line_per_row_and_double_width():
    """'full' uses one text line per module-row and two glyphs per module."""
    matrix = qr_matrix(DATA)
    rows = len(matrix)

    lines = qr_to_ascii(DATA, style="full").split("\n")
    assert len(lines) == rows
    # Two characters ("██" or "  ") per module => twice as wide as 'full' style.
    assert all(len(line) == 2 * rows for line in lines)


def test_full_is_twice_as_wide_as_half():
    """The 'full' style renders each module twice as wide as 'half'."""
    half = qr_to_ascii(DATA, style="half").split("\n")[0]
    full = qr_to_ascii(DATA, style="full").split("\n")[0]
    assert len(full) == 2 * len(half)


def test_half_style_only_uses_expected_glyphs():
    """Half-block output is built solely from the four expected glyphs."""
    text = qr_to_ascii(DATA, style="half")
    allowed = {FULL_BLOCK, UPPER_HALF, LOWER_HALF, " ", "\n"}
    assert set(text) <= allowed


def test_full_style_only_uses_block_or_space():
    """Full-block output is built solely from full blocks and spaces."""
    text = qr_to_ascii(DATA, style="full")
    assert set(text) <= {FULL_BLOCK, " ", "\n"}


# ---------------------------------------------------------------------------
# qr_to_ascii — invert polarity
# ---------------------------------------------------------------------------
def test_invert_flips_blocks_and_spaces_full_style():
    """With invert, every block becomes a space and vice-versa (full style).

    The default polarity draws *light* modules as bright blocks; ``invert``
    swaps that, so the two renderings are exact character-by-character
    complements (blocks <-> spaces).
    """
    normal = qr_to_ascii(DATA, style="full", invert=False)
    flipped = qr_to_ascii(DATA, style="full", invert=True)

    assert normal != flipped
    assert len(normal) == len(flipped)
    for a, b in zip(normal, flipped):
        if a == "\n":
            assert b == "\n"
        elif a == FULL_BLOCK:
            assert b == " "
        else:
            assert a == " " and b == FULL_BLOCK


def test_invert_changes_half_style_output():
    """Invert also changes the (compact) half-block rendering."""
    normal = qr_to_ascii(DATA, style="half", invert=False)
    flipped = qr_to_ascii(DATA, style="half", invert=True)
    assert normal != flipped
    # Same geometry, just different polarity.
    assert len(normal.split("\n")) == len(flipped.split("\n"))


def test_default_polarity_draws_quiet_zone_as_blocks():
    """Non-inverted output draws the light quiet zone as bright blocks.

    The top-left corner sits in the (light) border, which the default polarity
    renders as a block; the first full-style line is therefore all blocks.
    """
    first_line = qr_to_ascii(DATA, style="full").split("\n")[0]
    assert set(first_line) == {FULL_BLOCK}

    # Inverting turns that same quiet-zone line into all spaces.
    inv_first = qr_to_ascii(DATA, style="full", invert=True).split("\n")[0]
    assert set(inv_first) == {" "}


# ---------------------------------------------------------------------------
# print_qr
# ---------------------------------------------------------------------------
def test_print_qr_writes_to_provided_stream():
    """``print_qr`` writes the rendered art (plus newline) to ``out``."""
    buf = io.StringIO()
    print_qr(DATA, out=buf)

    output = buf.getvalue()
    assert output  # something was written
    # The body matches qr_to_ascii exactly; print() adds a trailing newline.
    expected = qr_to_ascii(DATA, style="half")
    assert output == expected + "\n"


def test_print_qr_respects_style_and_invert():
    """``print_qr`` forwards style/invert through to the renderer."""
    buf = io.StringIO()
    print_qr(DATA, style="full", invert=True, out=buf)

    expected = qr_to_ascii(DATA, style="full", invert=True)
    assert buf.getvalue() == expected + "\n"


def test_print_qr_handles_stream_without_reconfigure():
    """A plain ``StringIO`` (no ``reconfigure``) is handled gracefully."""
    buf = io.StringIO()
    assert not hasattr(buf, "reconfigure")
    # Should not raise even though reconfigure() is unavailable.
    print_qr(DATA, out=buf)
    assert buf.getvalue().endswith("\n")
