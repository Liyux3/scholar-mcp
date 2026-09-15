"""Tests for arXiv fallback client."""

import pytest
import httpx
from scholar_mcp import arxiv_client


@pytest.mark.integration
def test_search_papers():
    results = arxiv_client.search_papers("transformer", max_results=3)
    assert len(results) > 0
    paper = results[0]
    assert paper["source"] == "arxiv"
    assert paper["is_open_access"] is True
    assert "arxiv.org/pdf/" in paper["open_access_url"]


def test_get_pdf_url():
    url = arxiv_client.get_pdf_url("1706.03762")
    assert url == "https://arxiv.org/pdf/1706.03762.pdf"


def test_output_format_matches_s2(monkeypatch):
    """Validate the Atom-to-paper contract without consuming live API quota."""
    atom = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>https://arxiv.org/abs/1810.04805</id><title>BERT</title>
      <published>2018-10-11T00:00:00Z</published><summary>Pre-training language representations.</summary>
      <author><name>Jacob Devlin</name></author><category term="cs.CL"/>
      <link href="https://arxiv.org/pdf/1810.04805" type="application/pdf"/>
      </entry></feed>'''
    response = httpx.Response(200, text=atom, request=httpx.Request("GET", arxiv_client.ARXIV_API_URL))
    monkeypatch.setattr(arxiv_client.httpx, "get", lambda *a, **kw: response)
    results = arxiv_client.search_papers("BERT", max_results=1)
    assert len(results) > 0
    paper = results[0]
    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "_citation_count_known", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(paper.keys())
