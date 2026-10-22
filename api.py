"""Toolbox backend: file conversions for the Toolbox brain-app. Created 2026-10-22.

PURE COMPUTATION, on purpose. App backends are reached without the owner key, so any app frame
could call this router. It is therefore stateless: no imports from `api.*` (no memory, settings or
keys), no network, no disk writes except `tempfile` that is cleaned up. Input is capped (25 MB
decoded, 200 pages). Every error is JSON `{"detail": "..."}` with 400 or 413. Results come back as
base64 plus a suggested filename and mime. Mounted by the brain at /apps/toolbox/api/.

Uses only pypdf, pymupdf, python-docx and pillow (the brain's own Python) plus the standard library.
"""
import base64
import binascii
import io
import re
import zipfile
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 200
MAX_BODY = int(MAX_BYTES * 4 / 3) + 4096 + 64 * 1024  # base64 text plus json framing
MAX_PIXELS = 60_000_000

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_B64_OK = re.compile(r"^[A-Za-z0-9+/=\s]*$")


# ---- helpers ----
def _fail(code: int, detail: str):
    raise HTTPException(status_code=code, detail=detail)


def _guard(request: Request) -> None:
    """Refuse an oversized body before it is parsed, when the client says how big it is."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY * 2:
        _fail(413, "That is over 25 MB. Use a smaller file.")


def _decode(b64: str, label: str = "The file") -> bytes:
    if not isinstance(b64, str) or not b64:
        _fail(400, f"{label} is empty.")
    if len(b64) > MAX_BODY:
        _fail(413, f"{label} is over 25 MB. Use a smaller file.")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    if not _B64_OK.match(b64):
        _fail(400, f"{label} did not arrive intact. Try again.")
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        _fail(400, f"{label} did not arrive intact. Try again.")
    if not raw:
        _fail(400, f"{label} is empty.")
    if len(raw) > MAX_BYTES:
        _fail(413, f"{label} is over 25 MB. Use a smaller file.")
    return raw


def _stem(name: str, fallback: str = "file") -> str:
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)
    base = re.sub(r"[^\w .()\-]+", "_", base, flags=re.UNICODE).strip(" ._")
    return (base[:80] or fallback)


def _out(name: str, mime: str, data: bytes, **extra) -> dict:
    if len(data) > MAX_BYTES * 2:
        _fail(413, "The result came out too large. Try a smaller file.")
    d = {"name": name, "mime": mime, "base64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}
    d.update(extra)
    return d


def _clean(s: str) -> str:
    return _BAD_XML.sub("", s or "")


def _open_fitz(raw: bytes, label: str = "The PDF"):
    import pymupdf  # noqa: WPS433

    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception:
        _fail(400, f"{label} could not be read. It may be damaged.")
    if doc.needs_pass:
        doc.close()
        _fail(400, f"{label} is password protected. Remove the password first.")
    if doc.page_count > MAX_PAGES:
        n = doc.page_count
        doc.close()
        _fail(413, f"{label} has {n} pages. Toolbox takes up to {MAX_PAGES}.")
    if doc.page_count == 0:
        doc.close()
        _fail(400, f"{label} has no pages.")
    return doc


def _open_pypdf(raw: bytes, label: str = "The PDF"):
    from pypdf import PdfReader

    try:
        r = PdfReader(io.BytesIO(raw))
        if r.is_encrypted:
            try:
                ok = r.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                _fail(400, f"{label} is password protected. Remove the password first.")
        n = len(r.pages)
    except HTTPException:
        raise
    except Exception:
        _fail(400, f"{label} could not be read. It may be damaged.")
    if n > MAX_PAGES:
        _fail(413, f"{label} has {n} pages. Toolbox takes up to {MAX_PAGES}.")
    if n == 0:
        _fail(400, f"{label} has no pages.")
    return r


def _pdf_pages_text(raw: bytes) -> List[str]:
    """Text of each page, paragraphs separated by a blank line, hard line wraps joined."""
    doc = _open_fitz(raw)
    try:
        pages = []
        for page in doc:
            paras = []
            for b in page.get_text("blocks", sort=True):
                if len(b) > 6 and b[6] != 0:
                    continue  # image block
                t = _clean(b[4]).strip()
                if not t:
                    continue
                t = re.sub(r"-\n(?=[a-z])", "", t)  # re-join hyphenated line ends
                t = re.sub(r"\s*\n\s*", " ", t)
                paras.append(t.strip())
            pages.append("\n\n".join(paras))
        return pages
    finally:
        doc.close()


def parse_ranges(spec: str, n: int) -> List[int]:
    """'1-3, 5, 8-' -> zero-based page indexes in the order given, no repeats. Raises ValueError."""
    out: List[int] = []
    seen = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d*))?", part)
        if not m:
            raise ValueError(f"I could not read '{part}'. Use pages like 1-3, 5.")
        a = int(m.group(1))
        if m.group(2) is None:
            b = a
        elif m.group(2) == "":
            b = n
        else:
            b = int(m.group(2))
        if a < 1 or b < 1:
            raise ValueError("Pages start at 1.")
        if b < a:
            raise ValueError(f"'{part}' goes backwards. Write it as {b}-{a}.")
        if b > n:
            raise ValueError(f"Page {b} is outside this PDF, which has {n} pages.")
        for p in range(a, b + 1):
            if p - 1 not in seen:
                seen.add(p - 1)
                out.append(p - 1)
    if not out:
        raise ValueError("No pages were given.")
    return out


# ---- request models ----
class FileIn(BaseModel):
    name: str = "file"
    base64: str


class SplitIn(FileIn):
    ranges: Optional[str] = None


class MergeIn(BaseModel):
    files: List[FileIn]


class ImageIn(FileIn):
    quality: str = "balanced"  # small | balanced | high
    max_width: Optional[int] = None  # None = keep size
    format: str = "keep"  # keep | jpeg | webp


# ---- routes ----
@router.get("/ping")
def ping():
    return {"ok": True, "app": "toolbox"}


@router.post("/pdf/text")
def pdf_text(body: FileIn, request: Request):
    _guard(request)
    pages = _pdf_pages_text(_decode(body.base64))
    text = "\n\n".join(p for p in pages if p).strip()
    if not text:
        _fail(400, "This PDF has no text in it. It may be a scan, and Toolbox cannot read scans yet.")
    return _out(_stem(body.name) + ".txt", "text/plain;charset=utf-8", text.encode("utf-8"), pages=len(pages), chars=len(text), text=text)


@router.post("/pdf/docx")
def pdf_docx(body: FileIn, request: Request):
    _guard(request)
    from docx import Document

    pages = _pdf_pages_text(_decode(body.base64))
    if not any(p.strip() for p in pages):
        _fail(400, "This PDF has no text in it. It may be a scan, and Toolbox cannot read scans yet.")
    doc = Document()
    for i, page in enumerate(pages):
        if i:
            doc.add_page_break()
        for para in [p for p in page.split("\n\n") if p.strip()]:
            doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return _out(
        _stem(body.name) + ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buf.getvalue(),
        pages=len(pages),
    )


@router.post("/doc/text")
def doc_text(body: FileIn, request: Request):
    """Plain text out of a PDF, Word or text file, for summarize and translate. No file is returned."""
    _guard(request)
    raw = _decode(body.base64)
    name = (body.name or "").lower()
    if raw[:5] == b"%PDF-" or name.endswith(".pdf"):
        text = "\n\n".join(p for p in _pdf_pages_text(raw) if p).strip()
    elif name.endswith(".docx") or raw[:2] == b"PK":
        from docx import Document

        try:
            d = Document(io.BytesIO(raw))
        except Exception:
            _fail(400, "That Word file could not be read. It may be damaged.")
        parts = [_clean(p.text) for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(_clean(c.text).strip() for c in row.cells))
        text = "\n\n".join(p for p in parts if p.strip()).strip()
    else:
        text = _clean(raw.decode("utf-8", errors="replace")).strip()
    if not text:
        _fail(400, "There is no text in that file.")
    return {"text": text, "chars": len(text)}


@router.post("/pdf/split")
def pdf_split(body: SplitIn, request: Request):
    _guard(request)
    from pypdf import PdfWriter

    r = _open_pypdf(_decode(body.base64))
    n = len(r.pages)
    stem = _stem(body.name)
    spec = (body.ranges or "").strip()
    if spec:
        try:
            idx = parse_ranges(spec, n)
        except ValueError as e:
            _fail(400, str(e))
        w = PdfWriter()
        for i in idx:
            w.add_page(r.pages[i])
        buf = io.BytesIO()
        w.write(buf)
        label = re.sub(r"[^0-9,\-]+", "", spec.replace(" ", "")).replace(",", "_")[:40] or "pages"
        return _out(f"{stem}-pages-{label}.pdf", "application/pdf", buf.getvalue(), pages=len(idx), of=n)
    if n == 1:
        _fail(400, "This PDF has one page. There is nothing to split.")
    zbuf = io.BytesIO()
    width = max(3, len(str(n)))
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(n):
            w = PdfWriter()
            w.add_page(r.pages[i])
            b = io.BytesIO()
            w.write(b)
            z.writestr(f"{stem}-p{str(i + 1).zfill(width)}.pdf", b.getvalue())
    return _out(f"{stem}-pages.zip", "application/zip", zbuf.getvalue(), pages=n, of=n)


@router.post("/pdf/merge")
def pdf_merge(body: MergeIn, request: Request):
    _guard(request)
    from pypdf import PdfWriter

    if len(body.files) < 2:
        _fail(400, "Merging needs at least two PDFs.")
    if len(body.files) > 50:
        _fail(413, "Merge up to 50 PDFs at a time.")
    total = 0
    w = PdfWriter()
    pages = 0
    for f in body.files:
        raw = _decode(f.base64, f"'{f.name}'")
        total += len(raw)
        if total > MAX_BYTES:
            _fail(413, "Together the PDFs are over 25 MB. Merge fewer at a time.")
        r = _open_pypdf(raw, f"'{f.name}'")
        pages += len(r.pages)
        if pages > MAX_PAGES:
            _fail(413, f"Together the PDFs pass {MAX_PAGES} pages. Merge fewer at a time.")
        w.append(r)
    buf = io.BytesIO()
    w.write(buf)
    return _out(_stem(body.files[0].name) + "-merged.pdf", "application/pdf", buf.getvalue(), pages=pages, files=len(body.files))


_QUALITY = {
    "small": {"jpeg": 60, "webp": 55, "png_colors": 256},
    "balanced": {"jpeg": 78, "webp": 75, "png_colors": 0},
    "high": {"jpeg": 90, "webp": 88, "png_colors": 0},
}


@router.post("/image/compress")
def image_compress(body: ImageIn, request: Request):
    _guard(request)
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    q = _QUALITY.get(body.quality)
    if not q:
        _fail(400, "Quality must be small, balanced or high.")
    if body.format not in ("keep", "jpeg", "webp"):
        _fail(400, "Format must be keep, jpeg or webp.")
    if body.max_width is not None and not (16 <= body.max_width <= 20000):
        _fail(400, "The maximum width is out of range.")
    raw = _decode(body.base64)
    try:
        img = Image.open(io.BytesIO(raw))
        src = (img.format or "").upper()
        if src not in ("JPEG", "PNG", "WEBP"):
            _fail(400, "Toolbox compresses JPEG, PNG and WebP images.")
        if getattr(img, "n_frames", 1) > 1:
            _fail(400, "That image is animated. Toolbox compresses still images.")
        img.load()
    except HTTPException:
        raise
    except Exception:
        _fail(400, "That image could not be read. It may be damaged.")
    img = ImageOps.exif_transpose(img)
    if body.max_width and img.width > body.max_width:
        h = max(1, round(img.height * body.max_width / img.width))
        img = img.resize((body.max_width, h), Image.LANCZOS)
    target = {"keep": src.lower(), "jpeg": "jpeg", "webp": "webp"}[body.format]
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    buf = io.BytesIO()
    if target == "jpeg":
        if has_alpha:
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=q["jpeg"], optimize=True, progressive=True)
        ext, mime = ".jpg", "image/jpeg"
    elif target == "webp":
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.save(buf, "WEBP", quality=q["webp"], method=4)
        ext, mime = ".webp", "image/webp"
    else:
        if q["png_colors"]:
            base = img.convert("RGBA" if has_alpha else "RGB")
            img = base.quantize(colors=q["png_colors"], method=Image.Quantize.FASTOCTREE if has_alpha else Image.Quantize.MEDIANCUT)
        img.save(buf, "PNG", optimize=True)
        ext, mime = ".png", "image/png"
    data = buf.getvalue()
    return _out(
        f"{_stem(body.name)}-compressed{ext}", mime, data,
        before=len(raw), after=len(data), width=img.width, height=img.height,
    )
