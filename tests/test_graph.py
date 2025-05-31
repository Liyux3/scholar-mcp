"""Tests for citation graph building and analytics."""

from scholar_mcp import graph


def _make_paper(title, paper_id="", year=2024, citation_count=0, source="test"):
    return {
        "paper_id": paper_id or title[:10].replace(" ", ""),
        "title": title,
        "authors": ["Author A"],
        "abstract": "",
        "year": year,
        "citation_count": citation_count,
        "external_ids": {},
        "source": source,
    }


def test_paper_to_node():
    p = _make_paper("Test Paper", paper_id="abc123", year=2023, citation_count=50)
    node = graph._paper_to_node(p, depth=1)
    assert node["title"] == "Test Paper"
    assert node["depth"] == 1
    assert node["citation_count"] == 50


def test_expansion_priority_favors_new_hot():
    old_classic = _make_paper("Old", year=2010, citation_count=10000)
    new_hot = _make_paper("New", year=2025, citation_count=500)
    assert graph._expansion_priority(new_hot) > graph._expansion_priority(
        _make_paper("New low", year=2025, citation_count=5)
    )
    p_old = graph._expansion_priority(old_classic)
    p_new = graph._expansion_priority(new_hot)
    assert p_old > p_new


def test_matches_topic():
    paper = _make_paper("Attention Mechanism in Transformers")
    assert graph._matches_topic(paper, ["attention", "transformer"])
    assert graph._matches_topic(paper, ["neural"])  == False
    assert graph._matches_topic(paper, []) == True


def test_short_label():
    assert graph._short_label("Short") == "Short"
    long = "This is a very long paper title that should be truncated"
    assert len(graph._short_label(long, max_len=20)) <= 20
    assert graph._short_label(long, max_len=20).endswith("...")


def test_summarize_basic():
    nodes = [
        {"id": "a", "title": "Seed Paper", "year": 2024, "citation_count": 100, "depth": 0, "authors": ["X"]},
        {"id": "b", "title": "Ref Paper", "year": 2020, "citation_count": 5000, "depth": 1, "authors": ["Y"]},
    ]
    edges = [{"source": "a", "target": "b", "type": "cites"}]
    stats = {"total_nodes": 2, "total_edges": 1, "max_depth": 1}
    summary = graph._summarize(nodes, edges, stats)
    assert "2 papers" in summary
    assert "2020-2024" in summary


def test_to_mermaid_basic():
    nodes = [
        {"id": "a", "title": "Seed Paper", "year": 2024, "citation_count": 100, "depth": 0},
        {"id": "b", "title": "Ref Paper", "year": 2020, "citation_count": 5000, "depth": 1},
    ]
    edges = [{"source": "a", "target": "b", "type": "cites"}]
    mermaid = graph._to_mermaid(nodes, edges)
    assert "graph TD" in mermaid
    assert "n0" in mermaid
    assert "n1" in mermaid
    assert "n0 --> n1" in mermaid
    assert "fill:#e1f5fe" in mermaid


def test_analyze_pagerank():
    nodes = [
        {"id": "a", "title": "A", "year": 2024, "citation_count": 0, "depth": 0},
        {"id": "b", "title": "B", "year": 2023, "citation_count": 0, "depth": 1},
        {"id": "c", "title": "C", "year": 2022, "citation_count": 0, "depth": 1},
        {"id": "d", "title": "D", "year": 2021, "citation_count": 0, "depth": 2},
    ]
    edges = [
        {"source": "a", "target": "b", "type": "cites"},
        {"source": "a", "target": "c", "type": "cites"},
        {"source": "b", "target": "d", "type": "cites"},
        {"source": "c", "target": "d", "type": "cites"},
    ]
    analytics = graph._analyze(nodes, edges)
    assert "pagerank" in analytics
    assert "pivots" in analytics
    assert len(analytics["pagerank"]) > 0


def test_analyze_empty():
    assert graph._analyze([], []) == {}
    assert graph._analyze([{"id": "a"}], []) == {}


def test_mermaid_pivot_styling():
    nodes = [
        {"id": "a", "title": "Seed", "year": 2024, "citation_count": 100, "depth": 0},
        {"id": "b", "title": "Bridge", "year": 2022, "citation_count": 50, "depth": 1},
        {"id": "c", "title": "Leaf", "year": 2020, "citation_count": 10, "depth": 2},
    ]
    edges = [
        {"source": "a", "target": "b"},
        {"source": "b", "target": "c"},
    ]
    analytics = {"pivots": ["b"], "pagerank": {}, "betweenness": {}}
    mermaid = graph._to_mermaid(nodes, edges, analytics)
    assert "fill:#c8e6c9" in mermaid
