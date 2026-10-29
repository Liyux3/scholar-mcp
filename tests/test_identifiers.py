"""Every identifier Scholar returns, and common paper URLs, resolve back."""

import pytest

from scholar_mcp import server


@pytest.fixture(autouse=True)
def empty_recent(monkeypatch):
    monkeypatch.setattr(server, "_recent", server.OrderedDict())


@pytest.mark.parametrize("value, expected", [
    ("https://arxiv.org/abs/2303.04137v2", "arXiv:2303.04137v2"),
    ("https://arxiv.org/pdf/2606.12366", "arXiv:2606.12366"),
    ("https://arxiv.org/pdf/2606.12366.pdf", "arXiv:2606.12366"),
    ("https://doi.org/10.1038/s41586-021-03819-2", "10.1038/s41586-021-03819-2"),
    ("https://openreview.net/pdf?id=vlhoswksBO", "OpenReview:vlhoswksBO"),
    ("https://openreview.net/forum?id=c1iJqjmvMu&noteId=x", "OpenReview:c1iJqjmvMu"),
    ("https://openalex.org/W7167747110", "W7167747110"),
    ("https://www.semanticscholar.org/paper/Title-Slug/a1fb8a4c937ab584bfd21a06565b5420a668cb06",
     "a1fb8a4c937ab584bfd21a06565b5420a668cb06"),
    ("  10.1/x  ", "10.1/x"),
])
def test_paper_urls_normalize_to_identifiers(value, expected):
    assert server._normalize_identifier(value) == expected


def test_arxiv_and_doi_spellings_share_a_key():
    keys = {server._paper_key(v) for v in ("ARXIV:2303.04137", "arXiv:2303.04137v3",
                                           "10.48550/arXiv.2303.04137", "https://arxiv.org/abs/2303.04137")}
    assert keys == {"arxiv:2303.04137"}
    assert server._paper_key("DOI:10.1/ABC") == server._paper_key("10.1/abc")


def test_search_only_ids_are_recalled_for_reading(monkeypatch):
    paper = {"paper_id": "gs_3f7c3cfa5fbb", "title": "Only on Google Scholar", "external_ids": {}}
    server._remember([paper])
    monkeypatch.setattr(server.sources, "all_sources", lambda: [])
    assert server._find_paper("gs_3f7c3cfa5fbb") == paper
    recalled = server._find_paper("GS_3F7C3CFA5FBB", prefer_recent=True)
    recalled["tags"] = ["mine"]
    assert "tags" not in server._find_paper("gs_3f7c3cfa5fbb")


def test_reading_prefers_the_merged_record(monkeypatch):
    merged = {"title": "Merged", "external_ids": {"ArXiv": "2303.04137", "DOI": "10.1/x"}}
    server._remember([merged])
    calls = []
    monkeypatch.setattr(server.sources, "all_sources", lambda: calls.append(1) or [])
    monkeypatch.setattr(server.metadata, 'published_record', lambda paper: paper)
    assert server._find_paper("arXiv:2303.04137", prefer_recent=True) == merged
    assert server._find_paper("https://doi.org/10.1/X", prefer_recent=True) == merged
    assert calls == []


def test_explicit_arxiv_revision_keeps_its_own_artifact(monkeypatch):
    from scholar_mcp.sources import arxiv
    server._remember([{'title': 'Formal record', 'external_ids': {'ArXiv': '2303.04137'}}])
    pinned = {'title': 'Version two', 'external_ids': {'ArXiv': '2303.04137v2'}}
    monkeypatch.setattr(arxiv, 'get_paper', lambda value: pinned)
    result = server._find_paper('arxiv:2303.04137v2', prefer_recent=True)
    assert result['_requested_arxiv'] == '2303.04137v2'
    server._remember([result])
    assert server._recalled('arxiv:2303.04137')['title'] == 'Formal record'


def test_index_redirect_to_formal_doi_retains_requested_manuscript(monkeypatch):
    from types import SimpleNamespace
    preprint = {'title': 'Initial title', 'paper_id': '10.1234/preprint', 'external_ids': {'DOI': '10.1234/preprint'},
                'publication_types': ['Preprint'], 'relations': [{'type': 'is-preprint-of', 'id_type': 'doi', 'id': '10.1234/published'}]}
    formal = {'title': 'Final title', 'paper_id': '10.1234/published', 'external_ids': {'DOI': '10.1234/published'},
              'publication_types': ['JournalArticle']}
    monkeypatch.setattr(server.sources, 'all_sources', lambda: [SimpleNamespace(get_paper=lambda _: formal, available=lambda: True)])
    monkeypatch.setattr(server.metadata, '_doi', lambda value: preprint if value == '10.1234/preprint' else formal)
    result = server._find_paper('10.1234/preprint')
    assert result['version'] == 'published' and len(result['versions']) == 2
    server._remember([result])
    assert server._recalled('10.1234/preprint')['title'] == 'Final title'


def test_missing_explicit_revision_does_not_substitute_a_different_copy(monkeypatch):
    from scholar_mcp.sources import arxiv
    monkeypatch.setattr(arxiv, 'get_paper', lambda value: None)
    monkeypatch.setattr(server.sources, 'all_sources', lambda: pytest.fail('an exact revision stays exact'))
    assert server._find_paper('arxiv:2303.04137v99', prefer_recent=True) is None


def test_exact_title_falls_back_to_search(monkeypatch):
    monkeypatch.setattr(server.sources, "all_sources", lambda: [])
    found = {"title": "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion"}
    monkeypatch.setattr(server, "_pipeline", lambda *a, **k: ([{"title": "Other"}, found], []))
    assert server._find_paper("Diffusion Policy Visuomotor Policy Learning via Action Diffusion") is found
    assert server._find_paper("no-spaces-id") is None


def test_unresolved_reads_explain_accepted_identifiers(monkeypatch):
    monkeypatch.setattr(server, "_find_paper", lambda *a, **k: None)
    result = server.read_paper("nonsense")
    assert result["error"] == "Could not find paper 'nonsense'"
    assert "arXiv" in result["hint"]


@pytest.mark.parametrize("client, budget", [("codex-mcp-client", 36_000), ("claude-code", None), (None, None)])
def test_read_budget_follows_the_calling_client(monkeypatch, client, budget):
    from types import SimpleNamespace

    import fastmcp.server.dependencies as dependencies

    def context():
        if client is None:
            raise RuntimeError("no MCP request")
        info = SimpleNamespace(clientInfo=SimpleNamespace(name=client))
        return SimpleNamespace(session=SimpleNamespace(client_params=info))

    monkeypatch.setattr(dependencies, "get_context", context)
    monkeypatch.delenv("SCHOLAR_READ_CHAR_BUDGET", raising=False)
    assert server._client_read_budget() == budget
    # A budget the user sets applies to every client, Codex included.
    monkeypatch.setenv("SCHOLAR_READ_CHAR_BUDGET", "80000")
    assert server._client_read_budget() is None
