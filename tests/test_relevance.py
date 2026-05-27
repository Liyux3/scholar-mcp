"""Tests for relevance scoring, deduplication, query optimization, and field filtering."""

from scholar_mcp import relevance


def test_extract_keywords_removes_stopwords():
    kws = relevance.extract_keywords("how does the attention mechanism work in transformers")
    assert "how" not in kws
    assert "the" not in kws
    assert "attention" in kws
    assert "mechanism" in kws
    assert "transformers" in kws


def test_extract_keywords_limits_count():
    long_query = "hybrid sliding window linear attention sparse transformer mamba state space model architecture long context retrieval benchmark efficiency"
    kws = relevance.extract_keywords(long_query, max_keywords=6)
    assert len(kws) == 6


def test_optimize_query_shortens_long():
    long_q = "I am wondering how does the hybrid attention mechanism with sliding window and linear attention approaches affect long context retrieval performance in modern frontier large language models and what are the tradeoffs"
    optimized = relevance.optimize_query(long_q)
    words = optimized.split()
    assert len(words) <= 12


def test_optimize_query_preserves_short():
    short_q = "Mamba transformer hybrid"
    assert relevance.optimize_query(short_q) == short_q


def test_optimize_query_targets_ek_kb_2_length():
    """ek_kb_2 yields ~6 words. Longer output means we regressed to kb_5,
    which scored OA=0 on the 20q sweep. Guard the upper bound tightly.
    """
    long_q = ("Are there any research papers on methods to compress large-scale "
              "language models while preserving their task-agnostic knowledge "
              "through distillation techniques")
    optimized = relevance.optimize_query(long_q)
    words = optimized.split()
    assert 3 <= len(words) <= 8, f"expected ~6 words, got {len(words)}: {optimized}"


def test_optimize_query_strips_boilerplate():
    """extract_keywords must remove the interrogative scaffolding before
    KeyBERT sees the text, otherwise KeyBERT wastes phrase slots on noise.
    """
    q = ("Are there any studies that explore post-hoc techniques for "
         "hallucination detection in token level sequence generation tasks")
    optimized = relevance.optimize_query(q).lower()
    for noise in ("are", "there", "any", "studies", "that", "explore"):
        assert noise not in optimized.split(), f"noise word {noise!r} survived: {optimized}"


def test_keybert_extract_falls_back_to_cleaned():
    """When KeyBERT is unavailable the caller must still get usable keywords,
    never the raw 25-word query (raw scored 4/70 on the sweep).
    """
    q = ("I am wondering how does the hybrid attention mechanism with sliding "
         "window and linear attention approaches affect long context retrieval "
         "performance in modern frontier large language models")
    optimized = relevance.optimize_query(q)
    assert len(optimized.split()) <= 12
    assert optimized != q


def test_deduplicate_by_doi():
    papers = [
        {"title": "Paper A", "external_ids": {"DOI": "10.1234/abc"}, "source": "s2"},
        {"title": "Paper A copy", "external_ids": {"DOI": "10.1234/abc"}, "source": "arxiv"},
        {"title": "Paper B", "external_ids": {"DOI": "10.1234/def"}, "source": "s2"},
    ]
    result = relevance.deduplicate(papers)
    assert len(result) == 2
    assert result[0]["title"] == "Paper A"
    assert result[1]["title"] == "Paper B"


def test_deduplicate_by_title():
    papers = [
        {"title": "Attention Is All You Need", "external_ids": {}},
        {"title": "Attention is all you need", "external_ids": {}},
        {"title": "Different Paper", "external_ids": {}},
    ]
    result = relevance.deduplicate(papers)
    assert len(result) == 2


def test_deduplicate_tracks_source_count():
    papers = [
        {"title": "Same Paper Title Here", "abstract": "Test", "source": "s2",
         "external_ids": {"DOI": "10.1234/test"}, "citation_count": 100},
        {"title": "Same Paper Title Here", "abstract": "Test abstract longer",
         "source": "openalex", "external_ids": {"DOI": "10.1234/test"}, "citation_count": 50},
        {"title": "Different Paper", "abstract": "Other", "source": "arxiv",
         "external_ids": {}},
    ]
    deduped = relevance.deduplicate(papers)
    merged = [p for p in deduped if "same" in p["title"].lower()]
    assert len(merged) == 1
    assert merged[0].get("_source_count", 1) == 2


def test_tag_source_ranks():
    papers = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    relevance.tag_source_ranks(papers, "s2")
    assert papers[0]["_source_ranks"] == {"s2": 0}
    assert papers[1]["_source_ranks"] == {"s2": 1}
    assert papers[2]["_source_ranks"] == {"s2": 2}
    assert papers[0]["source"] == "s2"


def test_rank_final_sorts_by_composite_score():
    papers = [
        {"title": "Low cite", "_rerank_score": 0.5, "citation_count": 1,
         "_source_count": 1, "year": 2020, "_source_ranks": {"s2": 0}},
        {"title": "High cite", "_rerank_score": 0.5, "citation_count": 10000,
         "_source_count": 3, "year": 2024, "_source_ranks": {"s2": 0, "arxiv": 1, "openalex": 2}},
    ]
    ranked = relevance.rank_final(papers)
    assert ranked[0]["title"] == "High cite"
    assert "_final_score" in ranked[0]
    assert ranked[0]["_final_score"] >= ranked[1]["_final_score"]


def test_rank_final_normalizes_scores():
    papers = [
        {"title": "A", "_rerank_score": 0.8, "citation_count": 50,
         "_source_count": 2, "year": 2024, "_source_ranks": {"s2": 0, "arxiv": 1}},
    ]
    ranked = relevance.rank_final(papers)
    assert ranked[0]["_final_score"] == 1.0


def test_rank_final_multi_source_boost():
    base = {"_rerank_score": 0.5, "citation_count": 10, "year": 2024}
    single = {**base, "title": "Single", "_source_count": 1, "_source_ranks": {"s2": 0}}
    multi = {**base, "title": "Multi", "_source_count": 3, "_source_ranks": {"s2": 0, "arxiv": 1, "openalex": 2}}
    ranked = relevance.rank_final([single, multi])
    assert ranked[0]["title"] == "Multi"


def test_filter_by_fields_keeps_matching():
    papers = [
        {"title": "Neural Networks", "abstract": "Deep learning model", "fields_of_study": ["Computer Science"]},
        {"title": "Bird Migration", "abstract": "Seasonal patterns", "fields_of_study": ["Biology"]},
    ]
    filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Neural Networks"


def test_filter_by_fields_uses_keywords_when_no_field():
    papers = [
        {"title": "Efficient Transformer Architecture", "abstract": "Neural network optimization with attention layers", "fields_of_study": []},
        {"title": "Kampung House Spatial Transformation", "abstract": "Urban dynamics of house modifications", "fields_of_study": []},
    ]
    filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(filtered) == 1
    assert "transformer" in filtered[0]["title"].lower()


def test_filter_by_fields_arxiv_categories():
    papers = [
        {"title": "Language Model Paper", "abstract": "NLP research", "fields_of_study": ["cs.CL", "cs.AI"]},
        {"title": "Pure Math Paper", "abstract": "Topology", "fields_of_study": ["math.AT"]},
        {"title": "Stats ML Paper", "abstract": "Statistical learning", "fields_of_study": ["stat.ML"]},
    ]
    cs_filtered = relevance.filter_by_fields(papers, ["Computer Science"])
    assert len(cs_filtered) == 2
    titles = {p["title"] for p in cs_filtered}
    assert "Language Model Paper" in titles
    assert "Stats ML Paper" in titles

    math_filtered = relevance.filter_by_fields(papers, ["Mathematics"])
    assert len(math_filtered) == 1
    assert math_filtered[0]["title"] == "Pure Math Paper"


def test_filter_by_fields_none_returns_all():
    papers = [{"title": "A"}, {"title": "B"}]
    assert relevance.filter_by_fields(papers, None) == papers


def test_filter_by_fields_biology_keywords():
    papers = [
        {"title": "Gene Expression Analysis", "abstract": "Protein synthesis in cell cultures", "fields_of_study": []},
        {"title": "Neural Network Training", "abstract": "Deep learning optimization", "fields_of_study": []},
    ]
    filtered = relevance.filter_by_fields(papers, ["Biology"])
    assert len(filtered) == 1
    assert "gene" in filtered[0]["title"].lower()


def test_filter_by_fields_with_metadata_match():
    papers = [
        {"title": "Paper A", "abstract": "", "fields_of_study": ["Mathematics"]},
        {"title": "Paper B", "abstract": "", "fields_of_study": ["Computer Science"]},
    ]
    filtered = relevance.filter_by_fields(papers, ["Mathematics"])
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Paper A"


def test_merge_preserves_source_ranks():
    papers = [
        {"title": "Same Paper Title Here", "abstract": "Test", "source": "s2",
         "external_ids": {"DOI": "10.1234/test"}, "citation_count": 100,
         "_source_ranks": {"s2": 0}, "_source_count": 1},
        {"title": "Same Paper Title Here", "abstract": "Test longer",
         "source": "openalex", "external_ids": {"DOI": "10.1234/test"},
         "citation_count": 50, "_source_ranks": {"openalex": 3}, "_source_count": 1},
    ]
    deduped = relevance.deduplicate(papers)
    assert len(deduped) == 1
    ranks = deduped[0].get("_source_ranks", {})
    assert "s2" in ranks and "openalex" in ranks
    assert ranks["s2"] == 0 and ranks["openalex"] == 3
