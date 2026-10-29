"""Tests for structured, multimodal paper reading."""

import tempfile
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from scholar_mcp import paper_reader, pdf_utils


JATS = b'''<article xmlns:xlink="http://www.w3.org/1999/xlink">
<front><article-meta><title-group><article-title>A structured trial</article-title></title-group>
<abstract><p>Summary of the experiment.</p></abstract></article-meta></front>
<body><sec id="methods"><title>Methods</title><p id="p1">Participants followed
<italic>early feeding</italic>; see <xref rid="F1" ref-type="fig">Fig. 1</xref>.</p>
<sec id="analysis"><title>Analysis</title><p>Matched controls and sensitivity analysis.</p>
<disp-formula id="E1"><alternatives><tex-math>y=x+1</tex-math>
<graphic xlink:href="equation.gif"/></alternatives></disp-formula></sec></sec>
<table-wrap id="T1"><label>Table 1</label><caption><p>Participants</p></caption>
<table><thead><tr><th>Group</th><th>N</th></tr></thead><tbody>
<tr><td>Early</td><td>8</td></tr></tbody></table></table-wrap></body>
<floats-group><fig id="F1"><label>Figure 1</label><caption><p>Study design.</p></caption>
<graphic xlink:href="original.jpg"/></fig></floats-group></article>'''


def test_structured_read_preserves_native_relations_and_visual_selection():
    result = paper_reader.read_jats(JATS)
    assert result['format'] == 'jats' and result['pages'] is None and result['total_pages'] is None
    assert '[Fig. 1](xml:F1)' in result['content'] and '*early feeding*' in result['content']
    assert '$y=x+1$' in result['content'] and 'equation.gif' not in result['content']
    display = paper_reader.read_jats(JATS.replace(b'y=x+1', b'$$y=x+1$$'))
    assert '$$y=x+1$$' in display['content'] and '$$$' not in display['content']
    assert result['content'].count('Study design.') == 1
    assert result['tables'][0]['content'].splitlines()[-1] == '| Early | 8 |'
    analysis = paper_reader.read_jats(JATS, pages='xml:analysis')
    assert 'Matched controls' in analysis['content'] and 'Participants followed' not in analysis['content']
    figure = paper_reader.read_jats(JATS, visual='Fig. 1')
    assert figure['visual']['xml_id'] == 'F1' and figure['visual']['asset'] == 'original.jpg'
    assert figure['content'] == '**Figure 1** Study design.'
    assert 'error' in paper_reader.read_jats(JATS, pages='11-20')
    assert 'error' in paper_reader.read_jats(JATS, visual='page 5')


def test_structured_continuation_keeps_original_order_and_table_spans():
    result = paper_reader.read_jats(JATS, budget=70)
    text = [result['content']]
    while 'next_pages' in result:
        result = paper_reader.read_jats(JATS, pages=result['next_pages'], budget=70)
        text.append(result['content'])
    assert '\n\n'.join(text) == paper_reader.read_jats(JATS, budget=0)['content']
    span = JATS.replace(b'<td>Early</td>', b'<td rowspan="2">Early</td>')
    table = paper_reader.read_jats(span, visual='Table 1')['tables'][0]
    assert table['format'] == 'html' and 'rowspan="2"' in table['content']
    embedded = JATS.replace(b'<floats-group>', b'<body><p>Floating figure: ').replace(b'</floats-group>', b'</p></body>')
    embedded = embedded.replace(b'</body>\n<body>', b'\n')
    read = paper_reader.read_jats(embedded, visual='Figure 1')
    assert read['visual']['xml_id'] == 'F1' and read['content'].count('Study design.') == 1


def test_read_paper_structured_copy_uses_existing_multimodal_boundary(monkeypatch):
    from scholar_mcp import server
    from scholar_mcp.sources import europepmc
    monkeypatch.setattr(server, '_find_paper', lambda *a, **k: {'external_ids': {'PMC': '123'}})
    monkeypatch.setattr(pdf_utils, 'download_paper', lambda *a, **k: pytest.fail('explicit XML locators skip PDF acquisition'))
    monkeypatch.setattr(europepmc, 'structured_text', lambda _: {
        'content': JATS, 'source': 'europepmc', 'version': 'accepted', 'document': {'id': 'PMC123.1'}})
    monkeypatch.setattr(europepmc, 'structured_visual', lambda *a: {'_image_bytes': b'original-image'})
    result = server.read_paper('PMC123', pages='xml:F1')
    assert result['image_png'] == b'original-image' and result['copy']['version'] == 'accepted'
    assert result['document']['id'] == 'PMC123.1' and result['content_notice']


def test_full_width_graphics_are_not_clipped_to_caption_column(monkeypatch):
    import pdfplumber
    from contextlib import nullcontext
    page = SimpleNamespace(width=600, height=800, rects=[], curves=[], lines=[],
                           images=[{'x0': 30, 'top': 40, 'x1': 570, 'bottom': 200}])
    monkeypatch.setattr(pdfplumber, 'open', lambda _: nullcontext(SimpleNamespace(pages=[page])))
    region = paper_reader._figure_region('fixture.pdf', {'page': 1, '_bbox': [30, 210, 280, 240]})
    assert region == [30, 40, 570, 240]


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


@pytest.mark.parametrize('separator', [':', '|'])
def test_caption_requires_a_caption_separator(separator):
    caption = {
        "bbox": [10, 20, 100, 40],
        "lines": [{"spans": [{
            "text": f"Figure 2{separator} Model architecture.",
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


@pytest.mark.parametrize("label, selector", [("Fig. 2", "Figure 2"), ("Table 4", "Table 4")])
def test_caption_accepts_typeset_label_without_punctuation(label, selector):
    block = {"lines": [{"bbox": [10, 50, 300, 62], "spans": [
        {"text": label, "font": {"name": "SubsetDisplay", "size": 1}},
        {"text": " ", "font": {"name": "", "size": 1}},
        {"text": "Seasonal response distributions.", "font": {"name": "SubsetBody", "size": 1}},
    ]}]}
    caption = paper_reader._caption(block, 7)
    assert caption["selector"] == selector
    assert caption["caption"] == "Seasonal response distributions."
    # A span boundary alone is not a typeset caption separator.
    block["lines"][0]["spans"][0]["font"]["name"] = "SubsetBody"
    assert paper_reader._caption(block, 7) is None


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


def test_read_budget_stops_at_a_page_boundary(monkeypatch):
    from scholar_mcp import config

    monkeypatch.setattr(config, "READ_CHAR_BUDGET", 250)
    pages = [f"--- Page {n} ---\n" + "x" * 100 + "\n" for n in range(3, 8)]
    result = {"content": "".join(pages), "pages": "3-7", "total_pages": 12,
              "visuals": [{"selector": "Figure 1", "page": 4}, {"selector": "Figure 2", "page": 6}]}
    last = paper_reader._fit_budget(result, 3, 7)
    assert last == 4
    assert result["pages"] == "3-4"
    assert "--- Page 5 ---" not in result["content"]
    assert [v["selector"] for v in result["visuals"]] == ["Figure 1"]
    assert "page 4" in result["budget_note"]


def test_read_budget_keeps_small_reads_and_the_first_page(monkeypatch):
    from scholar_mcp import config

    monkeypatch.setattr(config, "READ_CHAR_BUDGET", 10)
    result = {"content": "--- Page 1 ---\n" + "y" * 50 + "\n--- Page 2 ---\nz", "pages": "1-2", "total_pages": 2}
    assert paper_reader._fit_budget(result, 1, 2) == 1
    assert result["pages"] == "1" and "y" * 50 in result["content"]
    monkeypatch.setattr(config, "READ_CHAR_BUDGET", 1000)
    result = {"content": "--- Page 1 ---\na\n--- Page 2 ---\nb", "pages": "1-2", "total_pages": 2}
    assert paper_reader._fit_budget(result, 1, 2) == 2 and "budget_note" not in result
    monkeypatch.setattr(config, "READ_CHAR_BUDGET", 0)
    result = {"content": "--- Page 1 ---\na\n--- Page 2 ---\nb", "pages": "1-2", "total_pages": 2}
    assert paper_reader._fit_budget(result, 1, 2) == 2
