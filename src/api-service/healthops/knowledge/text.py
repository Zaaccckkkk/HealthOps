"""Extract plain text from the reference sources and cut it into retrievable chunks.

Chunks are the unit the model may cite. They follow document structure (headings for HTML,
pages for PDFs, paragraphs for Markdown) and are kept between roughly 400 and 1800 characters
so a quotation can be read as one passage. Nothing here is specific to a source; the
per-source licence travels with the chunk as metadata.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

MIN_CHARS = 400
MAX_CHARS = 1800


@dataclass
class Passage:
    section: str
    page: int | None
    text: str


class _TextExtractor(HTMLParser):
    """Keeps headings and paragraph text; drops scripts, styles, navigation and footers."""

    SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "button"}
    HEADINGS = {"h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.passages: list[Passage] = []
        self._skip_depth = 0
        self._heading: str | None = None
        self._section = ""
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.HEADINGS and not self._skip_depth:
            self._flush()
            self._heading = tag
            self._buffer = []
        elif tag in {"p", "li", "br", "div", "td", "th", "tr"} and not self._skip_depth:
            self._buffer.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.HEADINGS and self._heading:
            self._section = " ".join("".join(self._buffer).split())[:300]
            self._heading = None
            self._buffer = []

    def handle_data(self, data):
        if not self._skip_depth:
            self._buffer.append(data)

    def _flush(self):
        text = _clean("".join(self._buffer))
        if text:
            self.passages.append(Passage(section=self._section or "(start)", page=None, text=text))
        self._buffer = []

    def close(self):
        super().close()
        self._flush()


def _clean(text: str) -> str:
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def passages_from_html(raw: str) -> list[Passage]:
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return parser.passages


def passages_from_pdf(path: Path) -> list[Passage]:
    from pypdf import PdfReader  # imported lazily: the API process does not need it

    reader = PdfReader(str(path))
    passages = []
    for number, page in enumerate(reader.pages, start=1):
        text = _clean((page.extract_text() or "").replace("\x00", ""))
        if len(text) >= 80:
            passages.append(Passage(section=f"page {number}", page=number, text=text))
    return passages


def passages_from_markdown(raw: str) -> list[Passage]:
    passages, section, buffer = [], "(start)", []

    def flush():
        text = _clean("\n".join(buffer))
        if text:
            passages.append(Passage(section=section, page=None, text=text))

    for line in raw.splitlines():
        if line.startswith("#"):
            flush()
            buffer = []
            section = line.lstrip("#").strip()[:300]
        elif line.startswith(("Source:", "Licence:")):
            continue  # provenance for human readers; it would only clutter a quotation
        else:
            buffer.append(line)
    flush()
    return passages


def passages_from_apple_json(raw: str) -> list[Passage]:
    """Apple's documentation JSON: abstract plus primary content paragraphs."""
    data = json.loads(raw)
    pieces = []

    def collect(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                pieces.append(node["text"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(data.get("abstract", []))
    collect(data.get("primaryContentSections", []))
    title = (data.get("metadata") or {}).get("title", "Apple documentation")
    text = _clean(" ".join(pieces))
    return [Passage(section=title, page=None, text=text)] if text else []


def extract(path: Path, kind: str) -> list[Passage]:
    if kind == "pdf":
        return passages_from_pdf(path)
    raw = path.read_text(errors="ignore")
    if kind == "html":
        return passages_from_html(raw)
    if kind == "json":
        return passages_from_apple_json(raw)
    if kind == "markdown":
        return passages_from_markdown(raw)
    raise ValueError(f"unsupported source kind {kind!r}")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_passages(passages: list[Passage]) -> list[Passage]:
    """Merge short passages of one section and split long ones at sentence boundaries."""
    chunks: list[Passage] = []
    pending: Passage | None = None
    for passage in passages:
        for piece in _split_long(passage):
            if (
                pending
                and pending.section == piece.section
                and len(pending.text) + len(piece.text) < MIN_CHARS * 2
            ):
                pending = Passage(pending.section, pending.page, pending.text + "\n" + piece.text)
            else:
                if pending:
                    chunks.append(pending)
                pending = piece
    if pending:
        chunks.append(pending)
    return [c for c in chunks if len(c.text) >= 40]


def _split_long(passage: Passage) -> list[Passage]:
    if len(passage.text) <= MAX_CHARS:
        return [passage]
    sentences = _SENTENCE_END.split(passage.text)
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > MAX_CHARS:
            pieces.append(Passage(passage.section, passage.page, current.strip()))
            current = ""
        current += (" " if current else "") + sentence
    if current.strip():
        pieces.append(Passage(passage.section, passage.page, current.strip()))
    return pieces
