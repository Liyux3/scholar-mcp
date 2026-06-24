"""Tests for OpenAlex API client."""

import httpx
import pytest
from scholar_mcp import openalex_client


def _response(status_code, payload, url):
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_format_paper_basic():
    work = {
        "id": "https://openalex.org/W2741809807",
        "title": "Attention Is All You Need",
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani", "orcid": None}, "institutions": []},
            {"author": {"display_name": "Noam Shazeer", "orcid": None}, "institutions": []},
        ],
        "publication_year": 2017,
        "doi": "https://doi.org/10.5555/3295222.3295349",
        "cited_by_count": 120000,
        "concepts": [{"display_name": "Computer Science"}, {"display_name": "Transformer"}],
        "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1706.03762"},
        "primary_location": {"source": {"display_name": "NeurIPS"}, "landing_page_url": "https://example.com"},
        "publication_date": "2017-06-12",
    }
    result = openalex_client.format_paper(work)
    assert result is not None
    assert result["title"] == "Attention Is All You Need"
    assert result["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert result["year"] == 2017
    assert result["citation_count"] == 120000
    assert result["source"] == "openalex"
    assert result["external_ids"]["DOI"] == "10.5555/3295222.3295349"
    assert result["is_open_access"] is True
    assert "Computer Science" in result["fields_of_study"]


def test_format_paper_with_inverted_abstract():
    work = {
        "id": "https://openalex.org/W123",
        "title": "Test Paper",
        "authorships": [],
        "publication_year": 2024,
        "abstract_inverted_index": {"Hello": [0], "world": [1], "this": [2], "is": [3], "a": [4], "test": [5]},
    }
    result = openalex_client.format_paper(work)
    assert result["abstract"] == "Hello world this is a test"


def test_format_paper_missing_title():
    work = {"id": "W1", "authorships": [], "publication_year": 2024}
    result = openalex_client.format_paper(work)
    assert result is None


def test_search_papers(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _response(200, {
            "results": [{
                "id": "https://openalex.org/W1",
                "title": "Test Paper",
                "authorships": [{"author": {"display_name": "Author A"}}],
                "publication_year": 2025,
                "cited_by_count": 10,
            }]
        }, url)

    monkeypatch.setattr(openalex_client.httpx, "get", fake_get)

    results = openalex_client.search_papers("test query", limit=5)
    assert len(results) == 1
    assert results[0]["source"] == "openalex"
    assert results[0]["title"] == "Test Paper"
    assert "search" in captured["params"]


def test_output_format_matches_s2():
    work = {
        "id": "https://openalex.org/W1",
        "title": "Format Test",
        "authorships": [{"author": {"display_name": "Author"}}],
        "publication_year": 2024,
        "publication_date": "2024-01-01",
    }
    result = openalex_client.format_paper(work)
    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(result.keys())


class TestSearchOperatorStripping:
    """OpenAlex parses ? and * in `search` as wildcards and returns HTTP 400
    rather than treating them literally. A trailing question mark is the
    natural shape of a user's query, so short questions routed to the keyword
    endpoint failed outright while semantic search handled them fine.
    """

    def test_strips_question_mark(self):
        assert openalex_client._strip_search_operators(
            "how does knowledge distillation work?"
        ) == "how does knowledge distillation work"

    def test_strips_asterisk(self):
        assert openalex_client._strip_search_operators(
            "distillation * compression"
        ) == "distillation compression"

    def test_collapses_resulting_whitespace(self):
        assert openalex_client._strip_search_operators("a ? ? b") == "a b"

    def test_preserves_other_punctuation(self):
        """Only ? and * break the API; colons, commas, hyphens and parentheses
        are all accepted and can carry meaning in a title query.
        """
        q = "TinyBERT: distilling BERT, task-agnostic (compressed)"
        assert openalex_client._strip_search_operators(q) == q

    def test_leaves_clean_queries_untouched(self):
        q = "knowledge distillation language model"
        assert openalex_client._strip_search_operators(q) == q


class TestKeyRotationOn429:
    """OpenAlex keys carry a small daily budget and deplete independently.
    get_openalex_api_key picks one at random, so once a key is exhausted it
    fails roughly half of all requests even though a healthy key is
    configured, and OpenAlex carries the largest share of ground-truth hits.
    """

    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                import httpx
                raise httpx.HTTPStatusError(str(self.status_code),
                                            request=None, response=None)

        def json(self):
            return {"results": []}

    def test_retries_with_another_key(self, monkeypatch):
        from scholar_mcp import config, openalex_client
        monkeypatch.setattr(config, "OPENALEX_API_KEYS", ["depleted", "healthy"])

        seen = []

        def fake_get(url, params=None, timeout=None):
            key = params.get("api_key")
            seen.append(key)
            return self._Response(429 if key == "depleted" else 200)

        monkeypatch.setattr(openalex_client.httpx, "get", fake_get)
        resp = openalex_client._get("http://x", {"api_key": "depleted"})

        assert resp.status_code == 200
        assert seen == ["depleted", "healthy"]

    def test_gives_up_when_all_keys_are_depleted(self, monkeypatch):
        import httpx
        from scholar_mcp import config, openalex_client
        monkeypatch.setattr(config, "OPENALEX_API_KEYS", ["a", "b"])
        monkeypatch.setattr(openalex_client.httpx, "get",
                            lambda url, params=None, timeout=None: self._Response(429))

        with pytest.raises(httpx.HTTPStatusError):
            openalex_client._get("http://x", {"api_key": "a"})

    def test_does_not_retry_non_429(self, monkeypatch):
        """A 400 is a malformed request; trying another key would just repeat
        it and burn budget on a request that cannot succeed.
        """
        import httpx
        from scholar_mcp import config, openalex_client
        monkeypatch.setattr(config, "OPENALEX_API_KEYS", ["a", "b"])

        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params.get("api_key"))
            return self._Response(400)

        monkeypatch.setattr(openalex_client.httpx, "get", fake_get)
        with pytest.raises(httpx.HTTPStatusError):
            openalex_client._get("http://x", {"api_key": "a"})
        assert calls == ["a"]

    def test_passes_through_when_no_keys_configured(self, monkeypatch):
        from scholar_mcp import config, openalex_client
        monkeypatch.setattr(config, "OPENALEX_API_KEYS", [])
        monkeypatch.setattr(openalex_client.httpx, "get",
                            lambda url, params=None, timeout=None: self._Response(200))
        assert openalex_client._get("http://x", {}).status_code == 200
