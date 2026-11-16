"""Turn an uploaded file into page images that the OCR engines can read.

- JPG / PNG: one page. The photo is turned upright using its EXIF data
  (phones store the rotation there instead of rotating the pixels).
- PDF: one image per page, rendered with pypdfium2.
"""

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

PDF_DPI = 300  # 300 dots per inch is a common scan quality


def _is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def load_pages(source: str | Path | bytes) -> list[Image.Image]:
    """Return the pages of a file as RGB images."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if _is_pdf(data):
        pdf = pdfium.PdfDocument(data)
        try:
            # pypdfium2 measures in "points" (1/72 inch), so scale = DPI / 72.
            return [page.render(scale=PDF_DPI / 72).to_pil().convert("RGB") for page in pdf]
        finally:
            pdf.close()
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return [img.convert("RGB")]


def upscale_if_small(img: Image.Image, min_width: int) -> Image.Image:
    """Enlarge small images. OCR misses small details like umlaut dots on low-resolution images."""
    if img.width >= min_width:
        return img
    factor = min_width / img.width
    return img.resize((min_width, round(img.height * factor)), Image.LANCZOS)
