"""
pdf_engine.py

All the heavy lifting for editMyPdf-lite:
  - extracting editable text "lines" from a PDF page (pdfplumber)
  - rendering a page (with in-progress edits baked in) to a PNG (pypdfium2)
  - "editing" text = whiting-out the original glyphs + drawing new glyphs on
    top, then flattening that onto the real page (reportlab + pypdf)
  - saving the fully-edited document to disk

No PyMuPDF is used (not available offline in every environment); the
redact-and-overlay approach below is the same technique most lightweight
PDF text editors use under the hood.
"""

import io
import os
import copy

import pdfplumber
import pypdf
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
import pypdfium2 as pdfium
from PIL import Image, ImageDraw


# --------------------------------------------------------------------------
# Font helpers
# --------------------------------------------------------------------------

STANDARD_FONTS = {
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
}


def map_font(fontname, bold_hint=False, italic_hint=False):
    """Map an arbitrary embedded font name to one of the 14 standard PDF fonts."""
    name = (fontname or "").lower()
    bold = bold_hint or "bold" in name or "black" in name or "heavy" in name
    italic = italic_hint or "italic" in name or "oblique" in name

    if any(k in name for k in ("courier", "mono", "consolas", "menlo")):
        if bold and italic:
            return "Courier-BoldOblique"
        if bold:
            return "Courier-Bold"
        if italic:
            return "Courier-Oblique"
        return "Courier"

    if any(k in name for k in ("times", "serif", "georgia", "garamond", "cambria", "minion")):
        if bold and italic:
            return "Times-BoldItalic"
        if bold:
            return "Times-Bold"
        if italic:
            return "Times-Italic"
        return "Times-Roman"

    # default: sans-serif (Helvetica/Arial/Calibri/Verdana/etc.)
    if bold and italic:
        return "Helvetica-BoldOblique"
    if bold:
        return "Helvetica-Bold"
    if italic:
        return "Helvetica-Oblique"
    return "Helvetica"


def normalize_color(color):
    """Convert a pdfplumber color tuple (gray / rgb / cmyk, 0-1 floats) to (r,g,b) 0-1."""
    try:
        if not color:
            return (0.0, 0.0, 0.0)
        if isinstance(color, (int, float)):
            g = float(color)
            return (g, g, g)
        color = tuple(color)
        if len(color) == 1:
            g = float(color[0])
            return (g, g, g)
        if len(color) == 3:
            return tuple(float(c) for c in color)
        if len(color) == 4:
            c, m, y, k = [float(v) for v in color]
            r = (1 - c) * (1 - k)
            g = (1 - m) * (1 - k)
            b = (1 - y) * (1 - k)
            return (r, g, b)
    except Exception:
        pass
    return (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def get_page_count(path):
    reader = PdfReader(path)
    return len(reader.pages)


def get_page_sizes(path):
    """Return list of {width, height} in PDF points for every page."""
    sizes = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            sizes.append({"width": float(page.width), "height": float(page.height)})
    return sizes


def extract_lines(path, page_num):
    """
    Extract editable text lines for a page (1-indexed).
    Returns list of dicts: id, text, bbox [x0, top, x1, bottom], font, size, color
    Coordinates use pdfplumber's top-left-origin convention.
    """
    results = []
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_num - 1]
        lines = page.extract_text_lines(return_chars=True, strip=True)
        for i, line in enumerate(lines):
            text = (line.get("text") or "").strip()
            if not text:
                continue
            chars = line.get("chars") or []
            fontname = chars[0].get("fontname") if chars else "Helvetica"
            size = float(chars[0].get("size")) if chars else 12.0
            color = normalize_color(chars[0].get("non_stroking_color")) if chars else (0, 0, 0)
            results.append({
                "id": f"L{page_num}_{i}",
                "text": text,
                "bbox": [float(line["x0"]), float(line["top"]), float(line["x1"]), float(line["bottom"])],
                "font": map_font(fontname),
                "size": round(size, 2),
                "color": list(color),
            })
    return results


# --------------------------------------------------------------------------
# Overlay building / rendering
# --------------------------------------------------------------------------

def _build_overlay_reader(page_width, page_height, edits):
    """Build a single-page reportlab PDF (as a pypdf reader) with white-out
    rectangles + replacement text for the given list of edit dicts."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    for e in edits:
        etype = e.get("type")
        bbox = e.get("bbox") or [0, 0, 0, 0]
        x0, top, x1, bottom = bbox

        if etype in ("replace", "delete"):
            pad = 1.5
            rect_x = x0 - pad
            rect_y = page_height - bottom - pad
            rect_w = (x1 - x0) + pad * 2
            rect_h = (bottom - top) + pad * 2
            if rect_w > 0 and rect_h > 0:
                c.setFillColorRGB(1, 1, 1)
                c.rect(rect_x, rect_y, rect_w, rect_h, fill=1, stroke=0)

        if etype in ("replace", "add") and e.get("text"):
            font = e.get("font") or "Helvetica"
            if font not in STANDARD_FONTS:
                font = "Helvetica"
            size = float(e.get("size") or 12)
            color = e.get("color") or (0, 0, 0)
            c.setFillColorRGB(*color)
            try:
                c.setFont(font, size)
            except Exception:
                c.setFont("Helvetica", size)
            baseline_y = page_height - bottom + size * 0.2
            c.drawString(x0, baseline_y, e["text"])

    c.save()
    buf.seek(0)
    return PdfReader(buf)


def render_page_with_edits(path, page_num, page_sizes, edits_for_page, deleted, scale=1.6):
    """Render a single page (edits baked in) to PNG bytes."""
    dims = page_sizes[page_num - 1]
    w, h = dims["width"], dims["height"]

    if deleted:
        img = Image.new("RGB", (int(w * scale), int(h * scale)), (238, 239, 241))
        draw = ImageDraw.Draw(img)
        msg = "Page will be deleted"
        try:
            bbox = draw.textbbox((0, 0), msg)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = (200, 20)
        draw.text(((img.width - tw) / 2, (img.height - th) / 2), msg, fill=(150, 150, 155))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    reader = PdfReader(path)
    base_page = copy.deepcopy(reader.pages[page_num - 1])

    if edits_for_page:
        overlay_reader = _build_overlay_reader(w, h, edits_for_page)
        base_page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    writer.add_page(base_page)
    tmp = io.BytesIO()
    writer.write(tmp)
    tmp.seek(0)

    pdf = pdfium.PdfDocument(tmp.read())
    page = pdf[0]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    out = io.BytesIO()
    pil_image.save(out, format="PNG")
    pdf.close()
    return out.getvalue()


def save_document(path, page_sizes, all_edits, deleted_pages, out_path):
    """Flatten every edit into the document and write the final PDF to out_path."""
    reader = PdfReader(path)
    writer = PdfWriter()

    for i, _ in enumerate(reader.pages):
        page_num = i + 1
        if page_num in deleted_pages:
            continue
        page = copy.deepcopy(reader.pages[i])
        edits_for_page = all_edits.get(str(page_num), [])
        if edits_for_page:
            w = page_sizes[i]["width"]
            h = page_sizes[i]["height"]
            overlay_reader = _build_overlay_reader(w, h, edits_for_page)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    if len(writer.pages) == 0:
        # Never produce an empty PDF - fall back to keeping at least one page
        writer.add_page(copy.deepcopy(reader.pages[0]))

    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path
