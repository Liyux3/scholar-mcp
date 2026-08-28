"""Tests for Semantic Scholar snippet (full-text passage) search.

This is the only source that searches paper body text, so its distinguishing
behaviour is that the matched passage comes back as the abstract field and
several results can share one paper.
"""

import httpx
import pytest

from scholar_mcp import s2_snippet_client as snippet


@pytest.fixture(autouse=True)
def isolate_s2_metadata_enrichment(monkeypatch):
    monkeypatch.setattr(
        snippet.s2_client,
        "get_papers_batch",
        lambda ids: [None] * len(ids),
    )


def _item(title="A Paper", kind="body", section="Methods", text="passage text",
          corpus_id=123, doi=None, score=0.8):
    paper = {"corpusId": corpus_id, "title": title,
             "authors": [{"name": "Author One"}], "openAccessInfo": {}}
    if doi:
        paper["externalIds"] = {"DOI": doi}
    return {"score": score, "paper": paper,
            "snippet": {"text": text, "snippetKind": kind, "section": section}}


def _mock_search(monkeypatch, payload):
    monkeypatch.setattr(snippet.s2_client, "_get", lambda *args, **kwargs: payload)


class TestFormatting:
    def test_passage_becomes_the_abstract(self, monkeypatch):
        """The endpoint returns no abstract. The matched passage is more
        on-point anyway, being the text that actually matched.
        """
        _mock_search(monkeypatch, {
            "data": [_item(text="distillation fails when capacity gaps are large")],
        })
        paper = snippet.search_papers("q")[0]
        assert "distillation fails" in paper["abstract"]
        assert paper["source"] == "s2_snippet"

    def test_section_is_prefixed_for_context(self, monkeypatch):
        _mock_search(monkeypatch, {
            "data": [_item(section="Related Work", text="prior approaches")],
        })
        assert snippet.search_papers("q")[0]["abstract"].startswith("[Related Work]")

    def test_missing_section_omits_the_prefix(self, monkeypatch):
        _mock_search(monkeypatch, {"data": [_item(section=None, text="bare text")]})
        assert snippet.search_papers("q")[0]["abstract"] == "bare text"

    def test_prefers_doi_as_paper_id(self, monkeypatch):
        _mock_search(monkeypatch, {"data": [_item(doi="10.1234/abc")]})
        assert snippet.search_papers("q")[0]["paper_id"] == "10.1234/abc"

    def test_falls_back_to_corpus_id(self, monkeypatch):
        _mock_search(monkeypatch, {"data": [_item(corpus_id=999)]})
        paper = snippet.search_papers("q")[0]
        assert paper["paper_id"] == "CorpusId:999"
        assert paper["external_ids"]["CorpusId"] == "999"

    def test_skips_untitled_entries(self, monkeypatch):
        _mock_search(monkeypatch, {
            "data": [_item(title=""), _item(title="Real Paper")],
        })
        results = snippet.search_papers("q")
        assert [p["title"] for p in results] == ["Real Paper"]

    def test_truncates_long_passages(self, monkeypatch):
        _mock_search(monkeypatch, {"data": [_item(section=None, text="x" * 5000)]})
        assert len(snippet.search_papers("q")[0]["abstract"]) == snippet.SNIPPET_TEXT_CHARS

    def test_retains_snippet_provenance(self, monkeypatch):
        _mock_search(monkeypatch, {
            "data": [_item(kind="body", section="Methods", score=0.91)],
        })
        paper = snippet.search_papers("q")[0]
        assert paper["_snippet_kind"] == "body"
        assert paper["_snippet_section"] == "Methods"
        assert paper["_snippet_score"] == 0.91

    def test_batch_enrichment_fills_bibliographic_metadata(self, monkeypatch):
        _mock_search(monkeypatch, {
            "data": [_item(text="matched full-text evidence")],
        })
        monkeypatch.setattr(
            snippet.s2_client,
            "get_papers_batch",
            lambda ids: [{
                "paperId": "s2-paper",
                "corpusId": 123,
                "title": "A Paper",
                "authors": [{"name": "Author One"}],
                "year": 2026,
                "venue": "ICML",
                "citationCount": 0,
                "externalIds": {"DOI": "10.1/paper"},
            }],
        )

        papers = snippet.search_papers("q")
        snippet.enrich_metadata(papers)
        paper = papers[0]

        assert paper["abstract"].endswith("matched full-text evidence")
        assert paper["authors"] == ["Author One"]
        assert paper["year"] == 2026
        assert paper["venue"] == "ICML"
        assert paper["citation_count"] == 0
        assert paper["_citation_count_known"] is True
        assert paper["external_ids"]["DOI"] == "10.1/paper"


class TestRequest:
    def test_caps_limit_at_api_maximum(self, monkeypatch):
        seen = {}

        def fake_get(url, params=None, **kwargs):
            seen.update(params)
            return {"data": []}

        monkeypatch.setattr(snippet.s2_client, "_get", fake_get)
        snippet.search_papers("q", limit=5000)
        assert seen["limit"] == snippet.SNIPPET_MAX_LIMIT

    def test_propagates_http_errors(self, monkeypatch):
        """Silent [] would make an outage look like a query with no matches."""
        def fail(*args, **kwargs):
            raise httpx.HTTPStatusError("429", request=None, response=None)

        monkeypatch.setattr(snippet.s2_client, "_get", fail)
        with pytest.raises(httpx.HTTPStatusError):
            snippet.search_papers("q")


def test_registered_as_a_raw_query_source():
    """Measured: raw questions score 0.82/0.77/0.72 with all results on topic,
    compressed keywords 0.59/0.59/0.57 with an unrelated third result.
    """
    from scholar_mcp import sources
    src = sources.get("s2_snippet")
    assert src is not None, "s2_snippet is no longer registered"
    assert src.query_style == sources.QUERY_RAW
