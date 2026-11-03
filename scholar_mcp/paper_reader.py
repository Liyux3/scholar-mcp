"""Structured, page-aware reading for downloaded research papers."""

import io
import re
import threading

import pypdfium2 as pdfium
from PIL import Image, ImageChops
from pdftext.extraction import dictionary_output
from pypdf import PdfReader

from . import config

DEFAULT_READ_PAGE_COUNT = 10
DEFAULT_READ_PAGES = f"1-{DEFAULT_READ_PAGE_COUNT}"
MAX_READ_PAGES = 20
PDF_RENDER_SCALE = 2.5
_pdfium_lock = threading.Lock()

_CAPTION_LABEL = (
    r"^(?:(?P<section>Extended\s+Data|Supplementary)\s+)?(?P<kind>Figure|Fig\.?|Table)\s*"
    r"(?P<label>(?:[A-Za-z]\.?)?\d+(?:\.\d+)*(?:[A-Za-z])?)"
)
_CAPTION_RE = re.compile(_CAPTION_LABEL + r"\s*[:.\-|]\s*(?P<caption>.+)$", re.I | re.S)
_TYPESET_CAPTION_RE = re.compile(_CAPTION_LABEL + r"\s+(?P<caption>.+)$", re.I | re.S)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?P<number>(?:[A-Z]\.)?\d+(?:\.\d+)*)\s+(?P<title>\S.+)$"
)
_NAMED_HEADINGS = {
    "abstract", "acknowledgment", "acknowledgments", "acknowledgement",
    "acknowledgements", "appendix", "conclusion", "conclusions",
    "limitations", "references", "bibliography",
}


def _parse_page_range(pages: str, total_pages: int) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", pages)
    if not match:
        raise ValueError("pages must be a page number or range such as '1-10'")

    start = int(match.group(1))
    requested_end = int(match.group(2) or start)
    if start < 1 or requested_end < start:
        raise ValueError("pages must be an ascending, one-indexed range")
    if requested_end - start + 1 > MAX_READ_PAGES:
        raise ValueError(f"read at most {MAX_READ_PAGES} pages per call")
    if start > total_pages:
        raise ValueError(f"paper has {total_pages} pages; requested page {start}")

    return start, min(requested_end, total_pages)


def _lines_text(lines_data: list[dict]) -> str:
    lines = []
    for line in lines_data:
        text = "".join(span.get("text", "") for span in line.get("spans", []))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if lines and lines[-1].endswith("-") and text[:1].islower():
            lines[-1] = lines[-1][:-1] + text
        else:
            lines.append(text)
    return " ".join(lines)


def _block_text(block: dict) -> str:
    return _lines_text(block.get("lines", []))


def _weighted_font_size(block: dict) -> float:
    sizes = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            size = float((span.get("font") or {}).get("size") or 0)
            if text and size:
                sizes.append((size, len(text)))
    if not sizes:
        return 0.0
    sizes.sort()
    midpoint = sum(weight for _, weight in sizes) / 2
    cumulative = 0
    for size, weight in sizes:
        cumulative += weight
        if cumulative >= midpoint:
            return size
    return sizes[-1][0]


def _body_font_size(layout_pages: list[dict]) -> float:
    weighted = []
    for page in layout_pages:
        for block in page.get("blocks", []):
            size = _weighted_font_size(block)
            text = _block_text(block)
            if size and len(text) >= 40:
                weighted.append((size, min(len(text), 500)))
    if not weighted:
        return 10.0
    weighted.sort()
    midpoint = sum(weight for _, weight in weighted) / 2
    cumulative = 0
    for size, weight in weighted:
        cumulative += weight
        if cumulative >= midpoint:
            return size
    return weighted[-1][0]


def _caption(block: dict, page_number: int) -> dict | None:
    lines = block.get("lines", [])
    for index, line in enumerate(lines):
        text = _lines_text([line])
        pattern = _CAPTION_RE
        match = pattern.match(text)
        if not match:
            # Some publishers separate a caption label by typography, not a colon.
            # Require an isolated label span and a font change into the caption;
            # plain body references such as "Figure 2 shows ..." still do not match.
            candidate = _TYPESET_CAPTION_RE.match(text)
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if candidate and len(spans) >= 2:
                label_text = text[:candidate.start("caption")].strip()
                label_font = (spans[0].get("font") or {}).get("name")
                body_font = (spans[1].get("font") or {}).get("name")
                if (spans[0]["text"].strip() == label_text
                        and label_font and body_font and label_font != body_font):
                    pattern, match = _TYPESET_CAPTION_RE, candidate
        if not match:
            continue
        kind = "figure" if match.group("kind").lower().startswith("fig") else "table"
        prefix = "Figure" if kind == "figure" else "Table"
        if match.group('section'):
            prefix = ' '.join(match.group('section').split()).title() + ' ' + prefix
        label = match.group("label")
        consumed = [line]
        bbox = [float(value) for value in line.get("bbox", [0, 0, 0, 0])]
        font_size = _weighted_font_size({"lines": [line]})
        previous_bbox = bbox
        next_index = index + 1
        while next_index < len(lines):
            next_line = lines[next_index]
            next_text = _lines_text([next_line])
            next_bbox = [
                float(value) for value in next_line.get("bbox", [0, 0, 0, 0])
            ]
            gap = next_bbox[1] - previous_bbox[3]
            same_column = _horizontal_overlap(previous_bbox, tuple(next_bbox)) >= 0.7
            next_size = _weighted_font_size({"lines": [next_line]})
            same_font = font_size and abs(next_size - font_size) <= max(0.8, font_size * 0.08)
            if not (
                next_text
                and -2 <= gap <= max(4, font_size * 0.55)
                and same_column
                and same_font
            ):
                break
            consumed.append(next_line)
            bbox = [
                min(bbox[0], next_bbox[0]), min(bbox[1], next_bbox[1]),
                max(bbox[2], next_bbox[2]), max(bbox[3], next_bbox[3]),
            ]
            previous_bbox = next_bbox
            next_index += 1
        caption_text = _lines_text(consumed)
        caption_match = pattern.match(caption_text)
        remainder = lines[:index] + lines[next_index:]
        return {
            "selector": f"{prefix} {label}",
            "page": page_number,
            "kind": kind,
            "caption": caption_match.group("caption").strip(),
            "_bbox": bbox,
            "_remainder_text": _lines_text(remainder),
        }
    return None


def _find_captions(layout_pages: list[dict]) -> list[dict]:
    found = []
    for page in layout_pages:
        page_number = int(page.get("page", 0)) + 1
        blocks = page.get("blocks", [])
        index = 0
        while index < len(blocks):
            block = blocks[index]
            item = _caption(block, page_number)
            if item:
                item["_anchor_bbox"] = list(item["_bbox"])
                item["_continuation_bboxes"] = []
                font_size = _weighted_font_size(block)
                previous_bbox = item["_bbox"]
                next_index = index + 1
                while next_index < len(blocks):
                    next_block = blocks[next_index]
                    next_text = _block_text(next_block)
                    next_bbox = [
                        float(value) for value in next_block.get("bbox", [0, 0, 0, 0])
                    ]
                    gap = next_bbox[1] - previous_bbox[3]
                    same_column = _horizontal_overlap(previous_bbox, tuple(next_bbox)) >= 0.7
                    next_size = _weighted_font_size(next_block)
                    same_font = font_size and abs(next_size - font_size) <= max(0.8, font_size * 0.08)
                    if not (
                        next_text
                        and -1 <= gap <= max(4, font_size * 0.55)
                        and same_column
                        and same_font
                        and len(item["caption"]) + len(next_text) <= 1200
                    ):
                        break
                    item["caption"] += " " + next_text
                    item["_continuation_bboxes"].append(next_bbox)
                    item["_bbox"] = [
                        min(item["_bbox"][0], next_bbox[0]),
                        min(item["_bbox"][1], next_bbox[1]),
                        max(item["_bbox"][2], next_bbox[2]),
                        max(item["_bbox"][3], next_bbox[3]),
                    ]
                    previous_bbox = next_bbox
                    next_index += 1
                found.append(item)
                index = next_index
                continue
            index += 1
    return found


def _normalize_selector(value: str) -> str:
    value = re.sub(r"\bfig\.?(?=\s*[A-Za-z]?\d)", "figure", value.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _layout_pages(file_path: str, page_numbers: list[int]) -> list[dict]:
    with _pdfium_lock:
        return dictionary_output(
            file_path,
            sort=False,
            page_range=[number - 1 for number in page_numbers],
            keep_chars=False,
            workers=1,
        )


def _locate_visual(file_path: str, selector: str, total_pages: int) -> dict:
    page_match = re.fullmatch(r"\s*page\s+(\d+)\s*", selector, re.I)
    if page_match:
        page_number = int(page_match.group(1))
        if not 1 <= page_number <= total_pages:
            raise ValueError(f"paper has {total_pages} pages; requested page {page_number}")
        return {
            "selector": f"page {page_number}", "page": page_number,
            "kind": "page", "caption": "", "_bbox": None,
        }

    normalized = _normalize_selector(selector)
    for chunk_start in range(1, total_pages + 1, MAX_READ_PAGES):
        chunk_end = min(chunk_start + MAX_READ_PAGES - 1, total_pages)
        layout = _layout_pages(file_path, list(range(chunk_start, chunk_end + 1)))
        for caption in _find_captions(layout):
            if _normalize_selector(caption["selector"]) == normalized:
                return caption
    raise ValueError(
        f"visual '{selector}' was not found; use a selector returned by read_paper"
    )


def _horizontal_overlap(first: list[float], second: tuple[float, ...]) -> float:
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    width = max(1.0, min(first[2] - first[0], second[2] - second[0]))
    return overlap / width


def _clean_table_rows(rows: list[list[str | None]]) -> list[list[str]]:
    cleaned = []
    for row in rows:
        values = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
        if any(values):
            cleaned.append(values)
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    keep = [index for index in range(width) if any(row[index] for row in cleaned)]
    return [[row[index] for index in keep] for row in cleaned]


def _markdown_table(rows: list[list[str]]) -> str:
    escaped = [
        [cell.replace("|", "\\|").replace("\n", "<br>") for cell in row]
        for row in rows
    ]
    header = escaped[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
    return "\n".join(lines)


def _horizontal_table_region(page, caption_bbox: list[float]) -> tuple[list[float], str] | None:
    lines = []
    for item in [*page.lines, *page.rects]:
        x0 = float(item.get("x0", 0))
        x1 = float(item.get("x1", 0))
        top = float(item.get("top", 0))
        bottom = float(item.get("bottom", top))
        if x1 - x0 >= page.width * 0.25 and bottom - top <= 3:
            if abs(top - caption_bbox[3]) <= page.height * 0.7:
                lines.append((top, x0, x1))
    if len(lines) < 2:
        return None
    lines.sort()
    clusters = []
    current = [lines[0]]
    for line in lines[1:]:
        if line[0] - current[-1][0] <= page.height * 0.14:
            current.append(line)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [line]
    if len(current) >= 2:
        clusters.append(current)
    if not clusters:
        return None

    def distance(cluster):
        top = cluster[0][0]
        bottom = cluster[-1][0]
        return min(abs(top - caption_bbox[3]), abs(caption_bbox[1] - bottom))

    cluster = min(clusters, key=distance)
    bbox = [
        min(line[1] for line in cluster), cluster[0][0],
        max(line[2] for line in cluster), cluster[-1][0],
    ]
    text = page.crop(tuple(bbox)).extract_text(
        layout=True,
        x_tolerance=1,
        x_density=7.25,
        y_density=13,
    ) or ""
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text.splitlines()) < 2 or not re.search(r"\S {2,}\S", text):
        return None
    return bbox, f"```text\n{text}\n```"


def _structured_tables(file_path: str, captions: list[dict]) -> dict[str, dict]:
    table_captions = [item for item in captions if item["kind"] == "table"]
    if not table_captions:
        return {}
    import pdfplumber

    matched = {}
    with pdfplumber.open(file_path) as document:
        by_page = {}
        for caption in table_captions:
            by_page.setdefault(caption["page"], []).append(caption)
        for page_number, page_captions in by_page.items():
            page = document.pages[page_number - 1]
            candidates = page.find_tables()
            used = set()
            for caption in page_captions:
                bbox = caption["_bbox"]
                ranked = []
                for index, table in enumerate(candidates):
                    if index in used:
                        continue
                    overlap = _horizontal_overlap(bbox, table.bbox)
                    gap = min(
                        abs(table.bbox[1] - bbox[3]),
                        abs(bbox[1] - table.bbox[3]),
                    )
                    direction_penalty = page.height * 0.05 if table.bbox[1] < bbox[1] else 0
                    score = gap + (1 - overlap) * page.height * 0.25 + direction_penalty
                    ranked.append((score, index, table))
                if ranked:
                    score, index, table = min(ranked, key=lambda item: item[0])
                    rows = _clean_table_rows(table.extract())
                    if (
                        score <= page.height * 0.4
                        and len(rows) >= 2
                        and len(rows[0]) >= 2
                    ):
                        used.add(index)
                        matched[caption["selector"]] = {
                            "page": page_number,
                            "bbox": [float(value) for value in table.bbox],
                            "format": "markdown",
                            "markdown": _markdown_table(rows),
                        }
                        continue
                spatial = _horizontal_table_region(page, bbox)
                if spatial:
                    table_bbox, table_text = spatial
                    matched[caption["selector"]] = {
                        "page": page_number,
                        "bbox": table_bbox,
                        "format": "spatial_text",
                        "markdown": table_text,
                    }
    return matched


def _intersects(first: list[float], second: list[float]) -> bool:
    return not (
        first[2] <= second[0] or second[2] <= first[0]
        or first[3] <= second[1] or second[3] <= first[1]
    )


def _expanded_intersects(first: list[float], second: list[float], pad: float = 14) -> bool:
    expanded = [first[0] - pad, first[1] - pad, first[2] + pad, first[3] + pad]
    return _intersects(expanded, second)


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    ]


def _figure_region(file_path: str, target: dict) -> list[float] | None:
    import pdfplumber

    with pdfplumber.open(file_path) as document:
        page = document.pages[target["page"] - 1]
        caption = target["_bbox"]
        center = (caption[0] + caption[2]) / 2
        left, right = page.width * 0.02, page.width * 0.98
        above = caption[1] >= page.height * 0.2
        search = [
            left,
            max(0, caption[1] - page.height * 0.65) if above else caption[3],
            right,
            caption[1] if above else min(page.height, caption[3] + page.height * 0.65),
        ]
        objects = []
        for item in [*page.images, *page.rects, *page.curves, *page.lines]:
            bbox = [
                float(item.get("x0", 0)),
                float(item.get("top", 0)),
                float(item.get("x1", 0)),
                float(item.get("bottom", item.get("top", 0))),
            ]
            if bbox[2] <= bbox[0]:
                bbox[2] = bbox[0] + 0.5
            if bbox[3] <= bbox[1]:
                bbox[3] = bbox[1] + 0.5
            if _intersects(bbox, search):
                # A caption may occupy one column under a spanning figure.
                # Search selects primitives; it must not cut their geometry.
                objects.append(bbox)
        if not objects:
            return None

        components = []
        for bbox in objects:
            overlapping = [
                index for index, component in enumerate(components)
                if _expanded_intersects(component, bbox)
            ]
            if not overlapping:
                components.append(bbox)
                continue
            merged = _union_bbox([bbox, *(components[index] for index in overlapping)])
            components = [
                component for index, component in enumerate(components)
                if index not in overlapping
            ]
            components.append(merged)

        def distance(component: list[float]) -> float:
            vertical = (
                abs(caption[1] - component[3])
                if above else abs(component[1] - caption[3])
            )
            horizontal = max(
                0,
                abs((component[0] + component[2]) / 2 - center)
                - (caption[2] - caption[0]) / 2,
            )
            return vertical + horizontal * 0.25

        viable = [
            component for component in components
            if (component[2] - component[0]) * (component[3] - component[1])
            >= page.width * page.height * 0.002
        ]
        if not viable:
            return None
        region = min(viable, key=distance)
        if distance(region) > page.height * 0.25:
            return None
        return _union_bbox([region, caption])


def _page_title(page: dict, page_height: float) -> tuple[str, set[tuple[float, ...]]]:
    candidates = []
    for block in page.get("blocks", []):
        for line in block.get("lines", []):
            text = _lines_text([line])
            size = _weighted_font_size({"lines": [line]})
            bbox = tuple(float(value) for value in line.get("bbox", []))
            if bbox and not 4 <= size <= 48:
                size = bbox[3] - bbox[1]
            if (
                text
                and not re.match(r"^(?:arXiv:|Published as|Proceedings of)", text, re.I)
                and 4 <= size <= 48
                and bbox
                and bbox[1] < page_height * 0.25
            ):
                candidates.append((bbox[1], bbox[0], size, bbox, text))
    if not candidates:
        return "", set()
    largest = max(item[2] for item in candidates)
    selected = sorted(
        (item for item in candidates if item[2] >= largest * 0.9 and len(item[4]) <= 240),
        key=lambda item: (item[0], item[1]),
    )
    if not selected:
        return "", set()
    title_lines = [selected[0]]
    for item in selected[1:]:
        previous = title_lines[-1]
        gap = item[0] - previous[3][3]
        if -2 <= gap <= max(10, previous[2] * 0.9):
            title_lines.append(item)
        else:
            break
    return (
        " ".join(item[4] for item in title_lines),
        {item[3] for item in title_lines},
    )


def _heading(text: str, font_size: float, body_size: float) -> str | None:
    normalized = text.strip().rstrip(":").lower()
    if normalized in _NAMED_HEADINGS:
        return f"## {text}"
    match = _NUMBERED_HEADING_RE.match(text)
    if match and len(text) <= 180:
        number = match.group("number")
        level = min(4, 2 + number.count("."))
        return f"{'#' * level} {text}"
    if len(text) <= 120 and font_size >= body_size * 1.18:
        return f"## {text}"
    return None


def _markdown_content(
    layout_pages: list[dict],
    captions: list[dict],
    tables: dict[str, dict],
) -> tuple[str, list[dict], list[dict]]:
    body_size = _body_font_size(layout_pages)
    caption_by_page_bbox = {
        (item["page"], tuple(item.get("_anchor_bbox", item["_bbox"]))): item
        for item in captions
    }
    caption_continuations = {
        (item["page"], tuple(bbox))
        for item in captions
        for bbox in item.get("_continuation_bboxes", [])
    }
    parts = []
    visuals = []
    table_index = []
    for page in layout_pages:
        page_number = int(page.get("page", 0)) + 1
        page_height = float((page.get("bbox") or [0, 0, 0, 792])[3])
        parts.append(f"--- Page {page_number} ---")
        page_table_bboxes = [
            entry["bbox"] for entry in tables.values()
            if entry["page"] == page_number
        ]
        title_text, title_lines = (
            _page_title(page, page_height) if page_number == 1 else ("", set())
        )
        title_emitted = False
        for block in page.get("blocks", []):
            text = _block_text(block)
            if not text:
                continue
            bbox = [float(value) for value in block.get("bbox", [0, 0, 0, 0])]
            if re.fullmatch(r"\d+", text) and bbox[1] > page_height * 0.9:
                continue
            bbox_key = (page_number, tuple(bbox))
            if bbox_key in caption_continuations:
                continue
            block_title_lines = [
                line for line in block.get("lines", [])
                if tuple(float(value) for value in line.get("bbox", [])) in title_lines
            ]
            if block_title_lines:
                if not title_emitted:
                    parts.append(f"# {title_text}")
                    title_emitted = True
                remainder_lines = [
                    line for line in block.get("lines", [])
                    if tuple(float(value) for value in line.get("bbox", [])) not in title_lines
                ]
                remainder_text = _lines_text(remainder_lines)
                if remainder_text:
                    parts.append(remainder_text)
                continue
            caption = caption_by_page_bbox.get(bbox_key)
            if caption:
                label = caption["selector"]
                parts.append(f"**{label}.** {caption['caption']}")
                table = tables.get(label)
                if table:
                    parts.append(table["markdown"])
                    table_index.append({
                        "selector": label,
                        "page": page_number,
                        "structured": True,
                        "format": table["format"],
                    })
                elif caption["kind"] == "table":
                    table_index.append({
                        "selector": label, "page": page_number, "structured": False,
                    })
                    visuals.append({key: caption[key] for key in (
                        "selector", "page", "kind", "caption"
                    )})
                else:
                    visuals.append({key: caption[key] for key in (
                        "selector", "page", "kind", "caption"
                    )})
                if caption.get("_remainder_text"):
                    parts.append(caption["_remainder_text"])
                continue
            if any(_intersects(bbox, table_bbox) for table_bbox in page_table_bboxes):
                continue
            heading = _heading(text, _weighted_font_size(block), body_size)
            parts.append(heading or text)
    content = "\n\n".join(parts)
    content = re.sub(r"(?<=\w)-\n\n(?=[a-z])", "", content)
    return content, visuals, table_index


def _render_visual(file_path: str, target: dict, table: dict | None = None) -> bytes:
    with _pdfium_lock:
        document = pdfium.PdfDocument(file_path)
        page = document[target["page"] - 1]
        width, height = page.get_size()
        image = page.render(scale=PDF_RENDER_SCALE).to_pil()
        page.close()
        document.close()

    crop_kind = "full-page"
    pixel_left = pixel_top = 0
    crop = None
    if table:
        caption_bbox = target.get("_bbox") or table["bbox"]
        crop = [
            min(table["bbox"][0], caption_bbox[0]),
            min(table["bbox"][1], caption_bbox[1]),
            max(table["bbox"][2], caption_bbox[2]),
            max(table["bbox"][3], caption_bbox[3]),
        ]
        crop_kind = "table-bbox"
    elif target.get("kind") == "figure" and target.get("_bbox"):
        crop = _figure_region(file_path, target)
        if crop:
            crop_kind = "figure-geometry"
    if crop is None and target.get("_bbox"):
        x0, y0, x1, y1 = target["_bbox"]
        margin = 14.0
        center = (x0 + x1) / 2
        if center < width * 0.42:
            crop_left, crop_right = width * 0.035, width * 0.52
        elif center > width * 0.58:
            crop_left, crop_right = width * 0.48, width * 0.965
        else:
            crop_left, crop_right = width * 0.035, width * 0.965
        if target["kind"] == "figure" and y0 >= height * 0.2:
            crop = [crop_left, max(0, y0 - height * 0.6), crop_right, y1 + 8]
        elif target["kind"] == "figure":
            crop = [crop_left, max(0, y0 - margin), crop_right, min(height, y1 + height * 0.6)]
        elif y1 <= height * 0.8:
            crop = [crop_left, max(0, y0 - margin), crop_right, min(height, y1 + height * 0.6)]
        else:
            crop = [crop_left, max(0, y0 - height * 0.6), crop_right, min(height, y1 + margin)]
        crop_kind = "caption-guided"
    if crop:
        margin = 4.0 if target.get("kind") == "figure" else 10.0
        left = max(0, crop[0] - margin) * PDF_RENDER_SCALE
        top = max(0, crop[1] - margin) * PDF_RENDER_SCALE
        right = min(width, crop[2] + margin) * PDF_RENDER_SCALE
        bottom = min(height, crop[3] + margin) * PDF_RENDER_SCALE
        image = image.crop((round(left), round(top), round(right), round(bottom)))
        pixel_left, pixel_top = round(left), round(top)
    background = Image.new(image.mode, image.size, "white")
    content_bbox = ImageChops.difference(image, background).getbbox()
    if content_bbox:
        pad = 18
        pixel_left += max(0, content_bbox[0] - pad)
        pixel_top += max(0, content_bbox[1] - pad)
        image = image.crop((
            max(0, content_bbox[0] - pad),
            max(0, content_bbox[1] - pad),
            min(image.width, content_bbox[2] + pad),
            min(image.height, content_bbox[3] + pad),
        ))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    target["crop"] = crop_kind
    target["image_size"] = list(image.size)
    target['rendered_bbox'] = [round(v / PDF_RENDER_SCALE, 3) for v in
                               (pixel_left, pixel_top, pixel_left + image.width, pixel_top + image.height)]
    return buffer.getvalue()


def _pypdf_fallback(reader: PdfReader, start: int, end: int) -> str:
    parts = []
    for page_number in range(start, end + 1):
        text = reader.pages[page_number - 1].extract_text()
        if text:
            parts.append(f"--- Page {page_number} ---\n{text}")
    return "\n\n".join(parts) if parts else "(No text could be extracted from this PDF.)"


def _replace_low_quality_pages(
    content: str,
    reader: PdfReader,
) -> tuple[str, list[int]]:
    sections = re.split(r"(?m)(?=^--- Page \d+ ---$)", content)
    fallback_pages = []
    repaired = []
    for section in sections:
        match = re.match(r"--- Page (\d+) ---", section)
        if not match:
            if section:
                repaired.append(section)
            continue
        page_number = int(match.group(1))
        replacements = section.count("�")
        if replacements < 3 or replacements / max(1, len(section)) <= 0.001:
            repaired.append(section)
            continue
        fallback = reader.pages[page_number - 1].extract_text() or ""
        fallback_ratio = fallback.count("�") / max(1, len(fallback))
        if (
            fallback
            and fallback_ratio < replacements / len(section)
            and len(fallback) >= len(section) * 0.5
        ):
            repaired.append(f"--- Page {page_number} ---\n\n{fallback.strip()}\n\n")
            fallback_pages.append(page_number)
        else:
            repaired.append(section)
    return "".join(repaired).strip(), fallback_pages


def read(
    file_path: str,
    pages: str = DEFAULT_READ_PAGES,
    visual: str = "",
    budget: int | None = None,
) -> dict:
    """Extract page-aware Markdown and an optional focused visual from a PDF."""
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)
    target = _locate_visual(file_path, visual, total_pages) if visual else None
    if target:
        start = end = target["page"]
    else:
        start, end = _parse_page_range(pages, total_pages)

    try:
        layout = _layout_pages(file_path, list(range(start, end + 1)))
        captions = _find_captions(layout)
        tables = _structured_tables(file_path, captions)
        content, visuals, table_index = _markdown_content(layout, captions, tables)
        content, fallback_pages = _replace_low_quality_pages(content, reader)
    except Exception:
        if target and target["kind"] != "page":
            raise
        content = _pypdf_fallback(reader, start, end)
        visuals = []
        table_index = []
        tables = {}
        fallback_pages = list(range(start, end + 1))

    result = {
        "content": content,
        "pages": f"{start}-{end}" if start != end else str(start),
        "total_pages": total_pages,
    }
    if visuals:
        result["visuals"] = visuals
    if table_index:
        result["tables"] = table_index
    if fallback_pages:
        result["text_fallback_pages"] = fallback_pages
    if target:
        matching_table = tables.get(target["selector"])
        image = _render_visual(file_path, target, matching_table)
        result["visual"] = {key: value for key, value in target.items() if not key.startswith("_")}
        result["_image_bytes"] = image
        return result
    end = _fit_budget(result, start, end, budget)
    if end < total_pages:
        next_end = min(end + DEFAULT_READ_PAGE_COUNT, total_pages)
        result["next_pages"] = f"{end + 1}-{next_end}"
    return result


def _fit_budget(result: dict, start: int, end: int, budget: int | None = None) -> int:
    """Stop at a page boundary once the text exceeds the response budget.

    Clients persist or reject large tool results (Claude Code above roughly
    50 KB), which costs the agent a second step or the whole read. The pages
    left out are reported through next_pages, never dropped silently.
    """
    budget = config.READ_CHAR_BUDGET if budget is None else budget
    if budget <= 0 or len(result["content"]) <= budget:
        return end
    sections = re.split(r"(?m)(?=^--- Page \d+ ---$)", result["content"])
    kept, size, last = [], 0, None
    for section in filter(None, sections):
        match = re.match(r"--- Page (\d+) ---", section)
        if match and last is not None and size + len(section) > budget:
            break
        kept.append(section)
        size += len(section)
        if match:
            last = int(match.group(1))
    if last is None or last >= end:
        return end
    result["content"] = "".join(kept).rstrip()
    result["pages"] = f"{start}-{last}" if start != last else str(start)
    result["budget_note"] = f"Stopped after page {last} to keep the response small; continue with next_pages."
    for key in ("visuals", "tables"):
        if key in result:
            result[key] = [item for item in result[key] if not isinstance(item, dict) or item.get("page", 0) <= last]
            if not result[key]:
                del result[key]
    return last


def read_jats(content: bytes, pages: str = DEFAULT_READ_PAGES, visual: str = '',
              budget: int | None = None) -> dict:
    """Render original article structure with stable locators, never invented PDF pages."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(content)
    href = '{http://www.w3.org/1999/xlink}href'
    parts, sections, visuals, tables = [], [], [], []

    def tag(node):
        return node.tag.rsplit('}', 1)[-1]

    def inline(node):
        if tag(node) in {'fig', 'table-wrap'}:
            label = node.find('label')
            text = inline(label) if label is not None else node.get('id', '')
            return f"[{text}](xml:{node.get('id', '')})"
        if tag(node) == 'alternatives':
            preferred = next((child for child in node if tag(child) == 'tex-math'), None)
            if preferred is None:
                preferred = next((child for child in node if tag(child) == 'math'), None)
            return inline(preferred) if preferred is not None else ''.join(node.itertext())
        if tag(node) == 'tex-math':
            text = ''.join(node.itertext()).strip()
            match = re.search(r'\\begin\{document\}(.*?)\\end\{document\}', text, re.S)
            text = match[1].strip() if match else text
            return text if text.startswith(('$', r'\(', r'\[')) else '$' + text + '$'
        if tag(node) == 'math':
            return ET.tostring(node, encoding='unicode')
        text = (node.text or '') + ''.join(
            (' ' if tag(child) in {'p', 'title', 'list-item'} else '') + inline(child) + (child.tail or '')
            for child in node)
        text = re.sub(r'\s+', ' ', text).strip()
        name = tag(node)
        if name in {'italic', 'bold'}:
            mark = '*' if name == 'italic' else '**'
            return f'{mark}{text}{mark}'
        if name in {'sup', 'sub'}:
            return f'<{name}>{text}</{name}>'
        if name == 'xref' and node.get('rid'):
            return f"[{text}](xml:{node.get('rid').split()[0]})"
        if name in {'ext-link', 'uri'} and node.get(href):
            return f'[{text}]({node.get(href)})'
        return text

    def add(node, text):
        if text.strip():
            parts.append({'text': text.strip(), 'node': node})

    def walk(node, level=2):
        name, xml_id = tag(node), node.get('id', '')
        if name in {'sec', 'abstract', 'ref-list', 'ack', 'app'}:
            title = node.find('title')
            heading = inline(title) if title is not None else {'abstract': 'Abstract', 'ref-list': 'References', 'ack': 'Acknowledgments'}.get(name, '')
            if heading:
                if xml_id:
                    sections.append({'title': heading, 'selector': f'xml:{xml_id}'})
                add(node, '#' * min(level, 6) + ' ' + heading)
            for child in node:
                if tag(child) != 'title':
                    walk(child, level + 1)
        elif name in {'fig', 'table-wrap'}:
            label = node.find('label')
            caption = node.find('caption')
            selector = inline(label) if label is not None else f'xml:{xml_id}'
            item = {'selector': selector, 'xml_id': xml_id,
                    'caption': inline(caption) if caption is not None else ''}
            graphic = node.find('.//graphic')
            if graphic is not None:
                item['asset'] = graphic.get(href, '')
            text = f"**{selector}** {item['caption']}"
            if name == 'table-wrap':
                table = node.find('.//table')
                if table is not None:
                    cells = list(table.iter('td')) + list(table.iter('th'))
                    spans = any(cell.get(key, '1') != '1' for cell in cells for key in ('rowspan', 'colspan'))
                    rows = [[inline(cell) for cell in row if tag(cell) in {'td', 'th'}]
                            for row in table.iter('tr')]
                    rows = [row for row in rows if row]
                    if rows and not spans:
                        width = max(map(len, rows))
                        item['format'] = 'markdown'
                        item['content'] = _markdown_table([row + [''] * (width - len(row)) for row in rows])
                    else:
                        item['format'] = 'html'
                        item['content'] = ET.tostring(table, encoding='unicode')
                    text += '\n\n' + item['content']
                tables.append(item)
            else:
                visuals.append(item)
            add(node, text)
        elif name == 'list':
            add(node, '\n'.join(f'- {inline(child)}' for child in node if tag(child) == 'list-item'))
        elif name in {'p', 'disp-formula', 'ref', 'statement', 'disp-quote', 'preformat'}:
            add(node, inline(node))
            # Publishers may embed floating figures/tables inside a paragraph.
            for child in node.iter():
                if child is not node and tag(child) in {'fig', 'table-wrap'}:
                    walk(child, level)
        elif name not in {'label', 'title', 'caption', 'graphic', 'media'}:
            for child in node:
                walk(child, level)

    title = root.find('./front/article-meta/title-group/article-title')
    if title is not None:
        add(title, '# ' + inline(title))
    for abstract in root.findall('./front/article-meta/abstract'):
        walk(abstract)
    for name in ('body', 'back', 'floats-group'):
        container = root.find(name)
        if container is not None:
            walk(container)
    if not parts:
        raise ValueError('JATS contains no readable article text')

    result = {'format': 'jats', 'pages': None, 'total_pages': None,
              'section_index': sections, 'visuals': visuals, 'tables': tables}
    selected = list(range(len(parts)))
    selector = visual or (pages[4:] if pages.startswith('xml:') else '')
    if selector:
        normalized = _normalize_selector(selector.removeprefix('xml:'))
        target = next((item for item in [*visuals, *tables]
                       if normalized in {_normalize_selector(item['selector']), _normalize_selector(item['xml_id'])}), None)
        xml_id = target['xml_id'] if target else selector.removeprefix('xml:')
        node = next((node for node in root.iter() if node.get('id') == xml_id), None)
        if node is None:
            return {**result, 'error': f'No native article selection matches {selector!r}.',
                    'hint': 'Use a visual selector or xml:ID from the indexes; PDF page numbers are unavailable.'}
        descendants = set(node.iter())
        selected = [index for index, part in enumerate(parts) if part['node'] in descendants]
        if not selected:
            return {**result, 'error': 'This XML element has no standalone readable text.',
                    'hint': 'Select its enclosing section or a figure from the indexes.'}
        if target:
            result['visual'] = target
    elif pages.startswith('parts:'):
        match = re.fullmatch(r'parts:(\d+)(?:-(\d+))?', pages)
        if not match:
            raise ValueError('Use parts:N-M or xml:ID for a structured article')
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= len(parts):
            raise ValueError(f'This structured article has {len(parts)} parts')
        selected = list(range(start - 1, end))
    elif pages != DEFAULT_READ_PAGES:
        return {**result, 'error': f'PDF pages {pages!r} are unavailable for this structured copy.',
                'hint': 'Use a section xml:ID or parts:N-M; parts refer to source text blocks, not pages.'}
    else:
        result['selection_note'] = 'Read from the native structured article. This copy has no PDF page boundaries.'
    limit = config.READ_CHAR_BUDGET if budget is None else budget
    kept, size = [], 0
    for index in selected:
        text = parts[index]['text']
        if kept and limit > 0 and size + len(text) > limit:
            result['next_pages'] = f'parts:{index + 1}-{selected[-1] + 1}'
            break
        kept.append(text)
        size += len(text) + 2
    result['content'] = '\n\n'.join(kept)
    result['parts'] = f'{selected[0] + 1}-{selected[len(kept) - 1] + 1}' if kept else ''
    result['total_parts'] = len(parts)
    return result
