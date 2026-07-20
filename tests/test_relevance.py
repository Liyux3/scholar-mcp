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
    """arXiv category tags like cs.CL should map to Computer Science."""
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


def test_deduplicate_tracks_source_count():
    """Deduplication should track how many sources found each paper."""
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
    """tag_source_ranks should annotate each paper with its position."""
    papers = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    relevance.tag_source_ranks(papers, "s2")
    assert papers[0]["_source_ranks"] == {"s2": 0}
    assert papers[1]["_source_ranks"] == {"s2": 1}
    assert papers[2]["_source_ranks"] == {"s2": 2}
    assert papers[0]["source"] == "s2"


def _paper(title, rerank=0.5, cites=0, year=2020, sources=("openalex",)):
    """Build a paper as it looks leaving deduplicate(), which sets both
    _source_ranks and _source_count. rank_final reads the latter.
    """
    return {
        "title": title,
        "_rerank_score": rerank,
        "citation_count": cites,
        "year": year,
        "_source_ranks": {s: i for i, s in enumerate(sources)},
        "_source_count": len(sources),
    }


class TestRankFinal:
    """rank_final applies metadata adjustments on top of the reranker score:

        score = rerank^γ × (1 + α·log(cites+1)) × (1 + β·sources/N) × (1 + δ·recency)

    Each factor is tested in isolation by holding the others constant.
    """

    def setup_method(self):
        relevance._rank_params = None

    def teardown_method(self):
        relevance._rank_params = None

    def test_rerank_score_dominates(self):
        ranked = relevance.rank_final([
            _paper("weak", rerank=0.2),
            _paper("strong", rerank=0.9),
        ])
        assert [p["title"] for p in ranked] == ["strong", "weak"]

    def test_citations_break_ties(self):
        ranked = relevance.rank_final([
            _paper("uncited", rerank=0.5, cites=0),
            _paper("cited", rerank=0.5, cites=1000),
        ])
        assert ranked[0]["title"] == "cited"

    def test_moderate_citations_do_not_overturn_rerank(self):
        """At typical citation counts the boost stays subordinate to relevance.
        A 100-cite paper gains roughly 23%, enough to break near-ties only.
        """
        ranked = relevance.rank_final([
            _paper("relevant but uncited", rerank=0.9, cites=0),
            _paper("cited but less relevant", rerank=0.6, cites=100),
        ])
        assert ranked[0]["title"] == "relevant but uncited"

    def test_extreme_citations_do_overturn_rerank(self):
        """The boost is unbounded: at 100k citations alpha=0.05 yields +58%,
        which flips a 0.9-vs-0.6 rerank gap.

        This looks like a bug and was investigated as one. Capping it makes
        retrieval worse. Re-ranking the cached LitSearch results offline
        (eval/citation_boost_sweep.py) gives, on the expansion cache:

            unbounded (current)   R@5 0.620   R@10 0.680   R@20 0.700
            clamped at 0.25       R@5 0.580   R@10 0.640   R@20 0.700
            saturating            R@5 0.520   R@10 0.580   R@20 0.660
            no citation term      R@5 0.400   R@10 0.500   R@20 0.580

        The reason is that ground-truth papers are far more cited than the
        pool they are drawn from, 20x the median on the pre-expansion cache.
        LitSearch ground truth is work that papers actually cite, so citation
        count is a strong positive signal on that benchmark.

        That conclusion does not generalise, and a later benchmark said so.
        On PaSa the relationship inverts: ground-truth papers have a citation
        median of 101 against the pool's 386, i.e. 0.3x rather than 20x. PaSa
        asks for work supporting a specific claim, and the answers are often
        recent papers that have not accumulated citations yet. One of its
        queries wants "When Less is More" (7 citations) and "How to Train
        Data-Efficient LLMs" (4 citations); we return LAION-5B and InstructGPT.

        So this test pins behaviour that is right for LitSearch-shaped queries
        and wrong for PaSa-shaped ones. The citation weight is not a constant
        to be tuned once; it depends on whether the user wants established
        work or current work, which the pipeline cannot currently tell apart.
        """
        ranked = relevance.rank_final([
            _paper("relevant but uncited", rerank=0.9, cites=0),
            _paper("famous but off-topic", rerank=0.6, cites=100_000),
        ])
        assert ranked[0]["title"] == "famous but off-topic"

    def test_source_agreement_breaks_ties(self):
        ranked = relevance.rank_final([
            _paper("one source", rerank=0.5, sources=("openalex",)),
            _paper("three sources", rerank=0.5, sources=("openalex", "arxiv", "s2")),
        ])
        assert ranked[0]["title"] == "three sources"

    def test_recency_breaks_ties(self):
        from datetime import datetime
        now = datetime.now().year
        ranked = relevance.rank_final([
            _paper("old", rerank=0.5, year=now - 30),
            _paper("new", rerank=0.5, year=now),
        ])
        assert ranked[0]["title"] == "new"

    def test_writes_final_score(self):
        ranked = relevance.rank_final([_paper("a"), _paper("b")])
        assert all("_final_score" in p for p in ranked)
        assert ranked[0]["_final_score"] >= ranked[1]["_final_score"]

    def test_handles_missing_metadata(self):
        """Papers arrive from 13 heterogeneous sources; year, citations and
        rank info are all routinely absent. Missing fields must not raise.
        """
        ranked = relevance.rank_final([{"title": "bare"}, {"title": "also bare"}])
        assert len(ranked) == 2
        assert all("_final_score" in p for p in ranked)

    def test_empty_input(self):
        assert relevance.rank_final([]) == []


class TestRankParams:
    def setup_method(self):
        relevance._rank_params = None

    def teardown_method(self):
        relevance._rank_params = None

    def test_defaults_when_no_file(self, monkeypatch):
        monkeypatch.setattr(relevance.config, "RANK_PARAMS_PATH", "/nonexistent/path.json")
        assert relevance._load_rank_params() == relevance.DEFAULT_RANK_PARAMS

    def test_partial_file_backfills_defaults(self, tmp_path, monkeypatch):
        """A hand-written config specifying only some keys must inherit the
        rest, not fall through to a second, different set of defaults.
        """
        cfg = tmp_path / "rank_params.json"
        cfg.write_text('{"gamma": 1.5}')
        monkeypatch.setattr(relevance.config, "RANK_PARAMS_PATH", str(cfg))

        params = relevance._load_rank_params()
        assert params["gamma"] == 1.5
        assert params["alpha"] == relevance.DEFAULT_RANK_PARAMS["alpha"]
        assert params["beta"] == relevance.DEFAULT_RANK_PARAMS["beta"]
        assert params["delta"] == relevance.DEFAULT_RANK_PARAMS["delta"]

    def test_malformed_file_falls_back(self, tmp_path, monkeypatch):
        cfg = tmp_path / "rank_params.json"
        cfg.write_text("{not json")
        monkeypatch.setattr(relevance.config, "RANK_PARAMS_PATH", str(cfg))
        assert relevance._load_rank_params() == relevance.DEFAULT_RANK_PARAMS


def test_merge_preserves_source_ranks():
    """Deduplication should merge _source_ranks from both copies."""
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


class TestRerankCapping:
    """Papers reach rerank concatenated in source order, so capping by list
    position discards candidates arbitrarily. The DashScope path always
    pre-ranked by metadata; the FlashRank fallback used to slice.
    """

    def _pool(self, n=200, n_good=40):
        """Good papers deliberately placed last, as a low-priority source
        appended to the end of the pool would be.
        """
        papers = []
        for i in range(n):
            good = i >= n - n_good
            papers.append({
                "title": f"{'good' if good else 'filler'}-{i}",
                "_rerank_score": 0.0,
                "citation_count": 5000 if good else 0,
                "year": 2024 if good else 2005,
                "_source_ranks": {"a": 0, "b": 1, "c": 2} if good else {"z": 99},
                "_source_count": 3 if good else 1,
            })
        return papers

    def test_pre_rank_cap_keeps_promising_papers(self):
        capped = relevance._pre_rank_cap(self._pool(), 150)
        kept = sum(1 for p in capped if p["title"].startswith("good"))
        assert len(capped) == 150
        assert kept == 40, f"metadata capping dropped {40 - kept} strong candidates"

    def test_pre_rank_cap_is_a_noop_below_the_cap(self):
        pool = self._pool(n=10, n_good=2)
        assert len(relevance._pre_rank_cap(pool, 150)) == 10

    def test_flashrank_fallback_caps_by_rank_not_position(self, monkeypatch):
        """Regression guard: with DashScope unavailable, the papers handed to
        FlashRank must be the metadata-best ones, not the first 150.
        """
        monkeypatch.setattr(relevance, "_rerank_dashscope",
                            lambda *a, **kw: None)
        seen = {}

        def fake_flashrank(query, papers, top_n):
            seen["papers"] = papers
            return papers[:top_n]

        monkeypatch.setattr(relevance, "_rerank_flashrank", fake_flashrank)
        relevance.rerank("q", self._pool(), top_n=20)

        handed = seen["papers"]
        assert len(handed) == relevance.FLASHRANK_CAP
        kept = sum(1 for p in handed if p["title"].startswith("good"))
        assert kept == 40, f"only {kept}/40 strong candidates survived the cap"


class TestDashScopeFailureVisibility:
    """A silent fallback to FlashRank costs roughly 3x latency and worse
    ranking with no other symptom. Arrearage in particular is permanent until
    someone tops up the account, so it has to surface somewhere.
    """

    def setup_method(self):
        relevance._dashscope_warning_shown = False

    def teardown_method(self):
        relevance._dashscope_warning_shown = False

    def test_names_arrearage(self):
        exc = Exception('{"code":"Arrearage","message":"Access denied"}')
        assert "arrearage" in relevance._dashscope_reason(exc).lower()

    def test_names_bad_key(self):
        exc = Exception("InvalidApiKey: no such key")
        assert "invalid api key" in relevance._dashscope_reason(exc).lower()

    def test_falls_back_to_exception_type(self):
        assert relevance._dashscope_reason(TimeoutError("slow")) == "TimeoutError"

    def test_reads_response_body_when_present(self):
        class Resp:
            text = '{"code":"Arrearage"}'

        exc = Exception("400 Bad Request")
        exc.response = Resp()
        assert "arrearage" in relevance._dashscope_reason(exc).lower()

    def test_warns_only_once(self, capsys):
        relevance._warn_dashscope_down("test reason")
        relevance._warn_dashscope_down("test reason")
        assert capsys.readouterr().err.count("scholar-mcp:") == 1

    def test_warning_goes_to_stderr(self, capsys):
        """stdout carries the MCP protocol; writing there corrupts it."""
        relevance._warn_dashscope_down("test reason")
        captured = capsys.readouterr()
        assert "FlashRank" in captured.err
        assert captured.out == ""


class TestContrastPreservation:
    """Comparatives look like filler and often carry the whole claim.

    A PaSa query asking for papers where a *smaller* dataset beats a bigger
    one compressed to "models bigger datasets pre training result": KeyBERT
    kept "bigger", dropped "smaller", and the search then asked for the
    largest datasets in existence.
    """

    def test_restores_the_dropped_side_of_a_contrast(self):
        original = ("using a smaller dataset can result in better models "
                    "than using bigger datasets")
        restored = relevance._restore_contrast(original, "models bigger datasets")
        assert "smaller" in restored
        assert "bigger" in restored, "the surviving side must be kept too"

    def test_leaves_untouched_when_no_contrast_survived(self):
        """Nothing to repair if compression dropped the comparison entirely;
        re-inserting one side alone would invent a claim.
        """
        original = "smaller datasets beat bigger datasets"
        assert relevance._restore_contrast(original, "dataset scaling") == "dataset scaling"

    def test_leaves_untouched_when_nothing_was_dropped(self):
        original = "smaller models outperform larger models"
        compressed = "smaller models larger"
        assert relevance._restore_contrast(original, compressed) == compressed

    def test_ignores_one_sided_comparatives(self):
        """"Training larger models" is not a contrast, so there is nothing to
        restore and no reason to pad the query.
        """
        original = "training larger and larger vision transformers"
        assert relevance._restore_contrast(original, "larger vision transformers") == \
            "larger vision transformers"

    def test_does_not_duplicate(self):
        original = "smaller smaller smaller than bigger"
        restored = relevance._restore_contrast(original, "bigger models")
        assert restored.split().count("smaller") == 1

    def test_end_to_end_keeps_both_sides(self):
        q = ("Give me papers which show that using a smaller dataset in large "
             "language model pre-training can result in better models than "
             "using bigger datasets.")
        optimized = relevance.optimize_query(q).lower()
        assert "smaller" in optimized, f"claim inverted: {optimized}"
