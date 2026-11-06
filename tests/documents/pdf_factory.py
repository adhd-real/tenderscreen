"""Build small synthetic PDFs for tests: text pages, scanned pages, blank pages.

Everything is generated here; no real documents are used. Scanned pages are
Pillow images of text placed full-page, so they have no text layer.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

DEVANAGARI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/fonts-deva-extra/gargi.ttf",
)
# A4 at 300 dots per inch, the resolution scans are made and OCR'd at.
SCAN_SIZE = (2480, 3508)

ENGLISH_NOTICE = (
    "NOTICE INVITING TENDER for supply of road construction material.",
    "Earnest money deposit Rs. 25,000/- only, payable by demand draft.",
    "Last date of submission: 15-Oct-2026 05:00 PM at the divisional office.",
)
AWARD_SCAN = ("AWARD OF CONTRACT", "Contract value Rs. 12,34,567", "Number of bids received 4")
HINDI_NOTICE = ("भारत सरकार", "निविदा सूचना", "अंतिम तिथि 15 अक्टूबर 2026")


def find_devanagari_font() -> str | None:
    """Path of an installed Devanagari TrueType font, or None."""
    return next((p for p in DEVANAGARI_FONT_CANDIDATES if Path(p).is_file()), None)


@dataclass(frozen=True)
class TextPage:
    """A page with a real text layer, drawn with reportlab."""

    lines: Sequence[str] = ENGLISH_NOTICE
    font_path: str | None = None  # a TrueType font to embed (needed for Devanagari)
    font_size: float = 12


@dataclass(frozen=True)
class ScannedPage:
    """A full-page image of text: no text layer, like a scan."""

    lines: Sequence[str] = AWARD_SCAN
    font_path: str | None = None  # None: Pillow's built-in font (Latin only)
    overlay: str | None = None  # small real text drawn on top, like a signature stamp


@dataclass(frozen=True)
class BlankPage:
    """A page with nothing on it."""

    marker: str = field(default="blank", repr=False)


PageSpec = TextPage | ScannedPage | BlankPage


def scan_image(lines: Sequence[str], font_path: str | None = None) -> Image.Image:
    """A white A4 'scan' at 300 dpi with ``lines`` of large black text."""
    image = Image.new("L", SCAN_SIZE, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 72) if font_path else ImageFont.load_default(size=64)
    for row, line in enumerate(lines):
        draw.text((150, 300 + row * 140), line, fill=0, font=font)
    return image


def _font_name(font_path: str) -> str:
    name = Path(font_path).stem
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, font_path))
    return name


def build_pdf_bytes(pages: Sequence[PageSpec], *, password: str | None = None) -> bytes:
    """PDF bytes with one page per spec (A4)."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, encrypt=password)
    width, height = A4
    for page in pages:
        if isinstance(page, TextPage):
            font = _font_name(page.font_path) if page.font_path else "Helvetica"
            canvas.setFont(font, page.font_size)
            for row, line in enumerate(page.lines):
                canvas.drawString(72, height - 100 - row * 24, line)
        elif isinstance(page, ScannedPage):
            image = scan_image(page.lines, page.font_path)
            canvas.drawImage(ImageReader(image), 0, 0, width=width, height=height)
            if page.overlay:
                canvas.setFont("Helvetica", 6)
                canvas.drawString(width - 260, 30, page.overlay)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def build_pdf(path: Path, pages: Sequence[PageSpec], *, password: str | None = None) -> Path:
    """Write a synthetic PDF to ``path`` and return the path."""
    path.write_bytes(build_pdf_bytes(pages, password=password))
    return path


SIGNATURE = "Digitally signed by Test Officer Date: 2026.10.15 17:30:45 IST"
OFFSET = 1000.0


def build_offset_mediabox_scan(path: Path) -> Path:
    """A scan with a signature overlay on a page whose media box starts at (1000, 1000)."""
    width, height = A4
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    canvas.translate(OFFSET, OFFSET)
    canvas.drawImage(ImageReader(scan_image(AWARD_SCAN)), 0, 0, width=width, height=height)
    canvas.setFont("Helvetica", 6)
    canvas.drawString(width - 260, 30, SIGNATURE)
    canvas.showPage()
    canvas.save()
    document = pdfium.PdfDocument(buffer.getvalue())
    try:
        document[0].set_mediabox(OFFSET, OFFSET, OFFSET + width, OFFSET + height)
        document.save(path)
    finally:
        document.close()
    return path


def build_form_wrapped_scan(path: Path) -> Path:
    """A scan inside a Form XObject with offset coordinates, as merge and stamp tools make."""
    width, height = A4
    canvas = Canvas(str(path), pagesize=A4)
    canvas.beginForm("scan", OFFSET, OFFSET, OFFSET + width, OFFSET + height)
    canvas.drawImage(
        ImageReader(scan_image(AWARD_SCAN)), OFFSET, OFFSET, width=width, height=height
    )
    canvas.endForm()
    canvas.saveState()
    canvas.translate(-OFFSET, -OFFSET)
    canvas.doForm("scan")
    canvas.restoreState()
    canvas.setFont("Helvetica", 6)
    canvas.drawString(width - 260, 30, SIGNATURE)
    canvas.showPage()
    canvas.save()
    return path


def build_nested_form_scan(path: Path) -> Path:
    """A scan two Form XObjects deep: the inner form is scaled, the outer translated.

    The image sits at (10000, 10000) in the inner form; the outer form draws the
    inner one at half size, shifted by -5000, so on the page it covers the
    bottom-left quarter (25% of the page). Composing the two form matrices in
    the wrong order would move it off the page (0%).
    """
    width, height = A4
    far = 10_000.0
    canvas = Canvas(str(path), pagesize=A4)
    canvas.beginForm("inner", far, far, far + width, far + height)
    canvas.drawImage(ImageReader(scan_image(AWARD_SCAN)), far, far, width=width, height=height)
    canvas.endForm()
    canvas.beginForm("outer", 0, 0, width, height)
    canvas.translate(-far / 2, -far / 2)
    canvas.scale(0.5, 0.5)
    canvas.doForm("inner")
    canvas.endForm()
    canvas.doForm("outer")
    canvas.showPage()
    canvas.save()
    return path


def build_form_bomb(path: Path, depth: int, fanout: int = 10) -> Path:
    """A tiny PDF whose page draws nested Form XObjects ``fanout ** depth`` times.

    A few kilobytes that cost PDFium exponential time and memory: the kind of
    hostile input the sandbox must contain.
    """
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    content = b"BT /F1 12 Tf 72 720 Td (Nested forms) Tj ET\n" + b"q /X0 Do Q\n" * fanout
    objects[3] = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font "
        b"<< /F1 5 0 R >> /XObject << /X0 10 0 R >> >> /Contents 4 0 R >>"
    )
    objects[4] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    for level in range(depth):
        number = 10 + level
        if level == depth - 1:
            body, resources = b"0 0 m 1 1 l S\n", b"<< >>"
        else:
            body = b"q /X%d Do Q\n" % (level + 1) * fanout
            resources = b"<< /XObject << /X%d %d 0 R >> >>" % (level + 1, number + 1)
        objects[number] = (
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 595 842] /Resources "
            + resources
            + b" /Length %d >>\nstream\n" % len(body)
            + body
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    size = max(objects) + 1
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += (
            b"%010d 00000 n \n" % offsets[number] if number in offsets else b"0000000000 65535 f \n"
        )
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    path.write_bytes(bytes(out))
    return path
