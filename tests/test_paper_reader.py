"""Tests for structured, multimodal paper reading."""

import tempfile

import pytest
from pypdf import PdfWriter

from scholar_mcp import paper_reader, pdf_utils


@pytest.mark.integration
def test_read_paper():
    """Download a known paper and extract text."""
    paper_info = {
        "paper_id": "1706.03762",
        "open_access_url": None,
        "external_ids": {"ArXiv": "1706.03762"},
        "url": "",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        dl = pdf_utils.download_paper(paper_info, tmpdir)
        if dl["success"]:
            result = paper_reader.read(dl["file_path"])
            assert len(result["content"]) > 100
            assert result["pages"] == "1-10"
            content = result["content"].lower()
            assert "attention" in content or "transformer" in content
            assert result["visuals"][0]["selector"] == "Figure 1"
            assert result["tables"][0]["structured"] is True


def test_reader_returns_reusable_page_ranges(tmp_path):
    path = tmp_path / "paper.pdf"
    writer = PdfWriter()
    for _ in range(24):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as stream:
        writer.write(stream)

    result = paper_reader.read(str(path))

    assert result["pages"] == "1-10"
    assert result["total_pages"] == 24
    assert result["next_pages"] == "11-20"


def test_caption_requires_a_caption_separator():
    caption = {
        "bbox": [10, 20, 100, 40],
        "lines": [{"spans": [{
            "text": "Figure 2: Model architecture.",
            "font": {"size": 10},
        }]}],
    }
    prose = {
        **caption,
        "lines": [{"spans": [{
            "text": "Figure 2 shows the model architecture.",
            "font": {"size": 10},
        }]}],
    }

    assert paper_reader._caption(caption, 3)["selector"] == "Figure 2"
    assert paper_reader._caption(prose, 3) is None


def test_caption_isolates_same_block_column_text():
    block = {
        "bbox": [10, 10, 500, 200],
        "lines": [
            {"bbox": [300, 100, 500, 112], "spans": [{
                "text": "Figure 1: Accuracy by epoch.", "font": {"size": 10},
            }]},
            {"bbox": [10, 80, 280, 92], "spans": [{
                "text": "The surrounding argument remains in the body.",
                "font": {"size": 10},
            }]},
        ],
    }

    caption = paper_reader._caption(block, 4)

    assert caption["caption"] == "Accuracy by epoch."
    assert caption["_remainder_text"] == "The surrounding argument remains in the body."


def test_title_uses_line_height_when_type3_font_size_is_invalid():
    page = {
        "blocks": [{"lines": [
            {"bbox": [80, 100, 530, 114], "spans": [{
                "text": "A Reliable Multimodal Paper Reader", "font": {"size": 1},
            }]},
            {"bbox": [150, 140, 460, 151], "spans": [{
                "text": "Ada Author and Alan Author", "font": {"size": 1},
            }]},
        ]}],
    }

    title, lines = paper_reader._page_title(page, 792)

    assert title == "A Reliable Multimodal Paper Reader"
    assert lines == {(80.0, 100.0, 530.0, 114.0)}


def test_replacement_characters_trigger_page_local_fallback():
    class Page:
        def extract_text(self):
            return "clean fallback text " * 20

    reader = type("Reader", (), {"pages": [Page()]})()
    content = "--- Page 1 ---\n\n" + ("broken � text " * 20)

    repaired, pages = paper_reader._replace_low_quality_pages(content, reader)

    assert "�" not in repaired
    assert pages == [1]
