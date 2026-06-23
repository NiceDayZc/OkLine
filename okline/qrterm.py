"""Render a QR code as scannable ASCII / Unicode art in the terminal.

Used by the QR-login flow so you can scan the login code straight from your
console window — no image file needed.

Polarity: on a normal dark-background terminal the *light* modules (and the
quiet-zone border) are drawn as bright blocks while *dark* modules are left as
the terminal background, producing a standard "dark squares on light" QR that
phone cameras read reliably.  Pass ``invert=True`` for a light-background
terminal.
"""

from __future__ import annotations


def qr_matrix(data: str, *, border: int = 2, error_correction: str = "M") -> list[list[bool]]:
    """Return the QR module matrix (``True`` = dark module) including border."""
    try:
        import qrcode
        import qrcode.constants as C
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "QR rendering needs the 'qrcode' package: pip install qrcode"
        ) from exc
    ec = {
        "L": C.ERROR_CORRECT_L,
        "M": C.ERROR_CORRECT_M,
        "Q": C.ERROR_CORRECT_Q,
        "H": C.ERROR_CORRECT_H,
    }.get(error_correction.upper(), C.ERROR_CORRECT_M)
    qr = qrcode.QRCode(border=border, error_correction=ec)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def qr_to_ascii(
    data: str, *, border: int = 2, invert: bool = False, style: str = "half"
) -> str:
    """Return a string that draws ``data`` as a QR code.

    ``style``:
      * ``"half"``  — Unicode half-blocks (compact: 2 rows per text line).
      * ``"full"``  — two spaces / two full-blocks per module (widest, most
        compatible with terminals that mishandle half-blocks).
    """
    matrix = qr_matrix(data, border=border)

    def bright(dark: bool) -> bool:
        # a "bright" cell is rendered as a block; dark modules stay as bg
        return dark if invert else (not dark)

    if style == "full":
        lines = []
        for row in matrix:
            lines.append("".join("██" if bright(cell) else "  " for cell in row))
        return "\n".join(lines)

    # half-block style: pair up rows
    HALF = {
        (True, True): "█",  # full block
        (True, False): "▀",  # upper half
        (False, True): "▄",  # lower half
        (False, False): " ",  # space
    }
    rows = matrix
    lines = []
    for i in range(0, len(rows), 2):
        top = rows[i]
        bot = rows[i + 1] if i + 1 < len(rows) else [False] * len(top)
        line = "".join(HALF[(bright(t), bright(b))] for t, b in zip(top, bot))
        lines.append(line)
    return "\n".join(lines)


def print_qr(
    data: str, *, border: int = 2, invert: bool = False, style: str = "half", out=None
) -> None:
    """Print ``data`` as a QR code to ``out`` (default stdout).

    Reconfigures stdout to UTF-8 on Windows so the block glyphs render instead
    of raising ``UnicodeEncodeError``; falls back to the ``"full"`` ASCII-ish
    style if that is not possible.
    """
    import sys

    from ._util import reconfigure_stdout_utf8

    stream = out or sys.stdout
    reconfigure_stdout_utf8(stream)  # make sure the block chars can be written
    text = qr_to_ascii(data, border=border, invert=invert, style=style)
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:  # pragma: no cover - last-resort fallback
        ascii_text = qr_to_ascii(data, border=border, invert=invert, style="full").replace(
            "█", "#"
        )
        print(ascii_text, file=stream, flush=True)
