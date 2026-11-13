# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Author search: which source answers and how the people are ordered."""

import pytest

from scholar_mcp import records, server


def _author(name, citations, source="openalex"):
    return {"author_id": name, "name": name, "affiliations": [], "paper_count": 1,
            "citation_count": citations, "h_index": 1, "url": "", "source": source}


@pytest.mark.parametrize(("query", "name", "score"), [
    ("Fei-Fei Li", "Li Fei-Fei", 3),
    ("Fei-Fei Li", "Feifei Li", 3),
    ("Fei-Fei Li", "Fei-Fei Li", 3),
    ("Geoffrey Hinton", "Geoffrey E. Hinton", 2),
    ("José García", "Jose Garcia", 3),
    ("Fei-Fei Li", "F. Li", 1),
    ("Fei-Fei Li", "Feida Li", 1),
    ("Fei-Fei Li", "Silvio Savarese", 0),
])
def test_name_match(query, name, score):
    assert records.author_name_match(query, name) == score


def test_the_person_whose_name_matches_comes_first(monkeypatch):
    pool = [_author("Fei Li", 34603), _author("Silvio Savarese", 78708), _author("Li Fei-Fei", 250985),
            _author("Fei Li", 9356)]
    monkeypatch.setattr(server.openalex, "search_authors", lambda query, limit: list(pool))
    monkeypatch.setattr(server.s2, "search_authors", lambda *a, **k: pytest.fail("OpenAlex answered"))
    names = [a["name"] for a in server.search_authors("Fei-Fei Li", limit=3)["authors"]]
    assert names == ["Li Fei-Fei", "Fei Li", "Fei Li"]


def test_semantic_scholar_answers_when_openalex_cannot(monkeypatch):
    def unavailable(query, limit):
        raise RuntimeError("budget spent")

    pool = [_author("Feida Li", 265, "semantic_scholar"), _author("Fei-Fei Li", 80, "semantic_scholar")]
    monkeypatch.setattr(server.openalex, "search_authors", unavailable)
    monkeypatch.setattr(server.s2, "search_authors", lambda query, limit: list(pool))
    authors = server.search_authors("Fei-Fei Li", limit=2)["authors"]
    assert [a["name"] for a in authors] == ["Fei-Fei Li", "Feida Li"]


def test_both_sources_failing_is_reported(monkeypatch):
    def unavailable(query, limit):
        raise RuntimeError("down")

    monkeypatch.setattr(server.openalex, "search_authors", unavailable)
    monkeypatch.setattr(server.s2, "search_authors", unavailable)
    error = server.search_authors("Fei-Fei Li")["error"]
    assert "OpenAlex" in error and "Semantic Scholar" in error
