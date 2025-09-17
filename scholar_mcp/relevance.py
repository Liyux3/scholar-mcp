"""Query preprocessing, reranking, deduplication, and field filtering."""

import math
import re
from datetime import datetime

from . import config

STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "that", "which", "who", "whom", "this", "these", "those", "it", "its",
    "not", "no", "nor", "as", "if", "then", "than", "too", "very", "so",
    "just", "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "over", "out", "up", "down",
    "how", "what", "when", "where", "why", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "also", "using", "based", "via", "etc",
    "us", "we", "our", "me", "my", "your", "they", "their", "them",
    "let", "like", "new", "make", "get", "use", "way", "does",
    "show", "know", "take", "come", "see", "look", "find", "give",
    "tell", "think", "say", "try", "ask", "seem", "help", "keep",
    "really", "actually", "still", "even", "much", "well",
})

TOP_VENUES = frozenset({
    "neurips", "nips", "icml", "iclr", "aaai", "ijcai", "cvpr", "iccv",
    "eccv", "acl", "emnlp", "naacl", "sigir", "kdd", "www", "icse",
    "fse", "osdi", "sosp", "sigcomm", "sigmod", "vldb", "nature",
    "science", "cell", "pnas", "lancet", "bmj", "jama", "nejm",
    "transactions", "journal of machine learning research", "jmlr",
})


WEAK_WORDS = frozenset({
    "wondering", "simply", "question", "whether", "think", "believe",
    "approach", "problem", "paper", "work", "method", "proposed", "propose",
    "study", "research", "results", "recent", "existing", "current",
    "different", "various", "several", "multiple", "many", "possible",
    "important", "significant", "main", "key", "novel", "particular",
    "general", "specific", "common", "typical", "standard", "basic",
    "first", "second", "third", "one", "two", "three", "four", "five",
    "requires", "require", "required", "need", "needs", "needed",
    "able", "unable", "enable", "consider", "considered", "considering",
    "always", "never", "often", "sometimes", "usually", "increasingly",
    "however", "therefore", "although", "despite", "beyond", "within",
    "across", "along", "among", "towards", "toward", "without",
    "achieve", "achieves", "address", "addresses", "aim", "aims",
    "better", "best", "worse", "worst", "high", "higher", "highest",
    "low", "lower", "lowest", "large", "larger", "small", "smaller",
    "fully", "enough", "already", "particularly", "especially", "primarily",
    "point", "making", "fundamentally", "inherent", "inherently",
    "ceiling", "incremental", "ever", "baked",
})


def extract_keywords(query: str, max_keywords: int = 8) -> list[str]:
    """Extract informative keywords from a query. Helps S2/arXiv search.
    For long queries, prioritizes technical terms over generic language.
    """
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query.lower())
    all_kw = [w for w in words if w not in STOPWORDS and len(w) > 1]
    if len(all_kw) <= max_keywords:
        return all_kw

    strong = [w for w in all_kw if w not in WEAK_WORDS and len(w) > 3]
    freq: dict[str, int] = {}
    for w in strong:
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(set(strong), key=lambda w: (-freq[w], strong.index(w)))
    if len(ranked) >= max_keywords:
        return ranked[:max_keywords]
    remaining = [w for w in all_kw if w not in set(ranked)]
    return (ranked + remaining)[:max_keywords]


def optimize_query(query: str) -> str:
    """Shorten very long queries to core keywords for better API results.
    Preserves queries up to 20 words (typical agent-generated queries).
    Only truncates user-pasted paragraphs or research questions.
    """
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query)
    if len(words) <= 20:
        return query
    keywords = extract_keywords(query, max_keywords=12)
    return " ".join(keywords)


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for dedup matching."""
    t = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def tag_source_ranks(papers: list[dict], source_name: str) -> list[dict]:
    """Tag each paper with its rank position from the given source.
    Call this on each source's results before merging.
    """
    for rank, p in enumerate(papers):
        if "_source_ranks" not in p:
            p["_source_ranks"] = {}
        p["_source_ranks"][source_name] = rank
        if "source" not in p or not p["source"]:
            p["source"] = source_name
        if "_source_count" not in p:
            p["_source_count"] = 1
    return papers


def _merge_two(a: dict, b: dict) -> dict:
    """Merge two paper dicts for the same paper from different sources.
    Take the best of each field: longest abstract, most authors, highest cites.
    Track source count and per-source ranks for RRF.
    """
    merged = dict(a)
    b_abs = b.get("abstract") or ""
    if len(b_abs) > len(merged.get("abstract") or ""):
        merged["abstract"] = b_abs
    if len(b.get("authors") or []) > len(merged.get("authors") or []):
        merged["authors"] = b["authors"]
    if (b.get("citation_count") or 0) > (merged.get("citation_count") or 0):
        merged["citation_count"] = b["citation_count"]
    b_topics = set(b.get("fields_of_study") or [])
    a_topics = set(merged.get("fields_of_study") or [])
    if b_topics - a_topics:
        merged["fields_of_study"] = list(a_topics | b_topics)
    if not merged.get("open_access_url") and b.get("open_access_url"):
        merged["open_access_url"] = b["open_access_url"]
        merged["is_open_access"] = True
    if not merged.get("tldr") and b.get("tldr"):
        merged["tldr"] = b["tldr"]
    a_sources = set((merged.get("source") or "").split("+")) - {""}
    b_sources = set((b.get("source") or "").split("+")) - {""}
    all_sources = a_sources | b_sources
    merged["source"] = "+".join(sorted(all_sources))
    merged["_source_count"] = len(all_sources)
    a_ranks = merged.get("_source_ranks") or {}
    b_ranks = b.get("_source_ranks") or {}
    merged["_source_ranks"] = {**a_ranks, **b_ranks}
    return merged


def deduplicate(papers: list[dict]) -> list[dict]:
    """Deduplicate papers by DOI or normalized title, merging metadata from duplicates.
    Tracks _source_count for consensus scoring.
    """
    for p in papers:
        if "_source_count" not in p:
            p["_source_count"] = 1

    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    unique = []

    for p in papers:
        doi = (p.get("external_ids") or {}).get("DOI", "")
        if doi:
            doi_lower = doi.lower()
            if doi_lower in by_doi:
                by_doi[doi_lower] = _merge_two(by_doi[doi_lower], p)
                continue
            by_doi[doi_lower] = p

        norm_title = _normalize_title(p.get("title", ""))
        if norm_title and len(norm_title) > 10:
            if norm_title in by_title:
                by_title[norm_title] = _merge_two(by_title[norm_title], p)
                continue
            by_title[norm_title] = p

        unique.append(p)

    seen_ids = set()
    result = []
    for p in unique:
        doi = (p.get("external_ids") or {}).get("DOI", "")
        if doi:
            merged = by_doi.get(doi.lower(), p)
            pid = doi.lower()
        else:
            nt = _normalize_title(p.get("title", ""))
            merged = by_title.get(nt, p)
            pid = nt
        if pid not in seen_ids:
            seen_ids.add(pid)
            result.append(merged)

    return result


_flashrank_ranker = None
_rank_params = None


def _load_rank_params() -> dict:
    """Load learned ranking parameters from JSON file, or use defaults."""
    global _rank_params
    if _rank_params is not None:
        return _rank_params
    import json
    try:
        with open(config.RANK_PARAMS_PATH) as f:
            _rank_params = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _rank_params = {"gamma": 1.0, "alpha": 0.05, "beta": 0.10, "delta": 0.0}
    return _rank_params


INTENT_INSTRUCTS = {
    "foundational": "Given a scientific literature search query, retrieve seminal and highly-cited research papers that established this field or method.",
    "recent": "Given a scientific literature search query, retrieve the most recent papers with novel contributions. Prioritize papers from the last 2 years.",
    "survey": "Given a scientific literature search query, retrieve survey and review papers that provide comprehensive overviews of this topic.",
    "method": "Given a scientific literature search query, retrieve papers that propose specific methods or techniques directly addressing the query.",
    "": "Given a scientific literature search query, retrieve relevant research papers that answer the query. Prioritize papers whose methods, findings, or contributions directly address the query topic.",
}


def _rerank_dashscope(query: str, papers: list[dict], top_n: int, intent: str = "") -> list[dict] | None:
    """Rerank via DashScope qwen3-rerank API. Returns None on failure."""
    api_key = config.DASHSCOPE_API_KEY
    if not api_key:
        return None

    documents = []
    for p in papers:
        title = p.get("title") or ""
        abstract = p.get("abstract") or ""
        venue = p.get("venue") or ""
        year = p.get("year") or ""
        parts = [f"Title: {title}"]
        pub_date = p.get("publication_date") or str(year) if year else ""
        if venue or pub_date:
            parts.append(f"Venue: {venue}, Published: {pub_date}".strip(", "))
        if abstract:
            parts.append(f"Abstract: {abstract}")
        documents.append("\n".join(parts)[:2000])

    instruct = INTENT_INSTRUCTS.get(intent, INTENT_INSTRUCTS[""])

    try:
        import httpx
        resp = httpx.post(
            "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "qwen3-rerank",
                "query": query[:500],
                "documents": documents[:500],
                "top_n": min(top_n, len(documents)),
                "instruct": instruct,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        reranked = []
        for item in data.get("results", []):
            idx = item["index"]
            score = float(item["relevance_score"])
            paper = papers[idx]
            paper["_rerank_score"] = round(score, 4)
            reranked.append(paper)
        return reranked
    except Exception:
        return None


def _rerank_flashrank(query: str, papers: list[dict], top_n: int) -> list[dict]:
    """Rerank via FlashRank MiniLM (ONNX). Fallback when DashScope unavailable."""
    try:
        from flashrank import Ranker, RerankRequest
    except ImportError:
        for p in papers:
            p["_rerank_score"] = 0.5
        return papers[:top_n]

    global _flashrank_ranker
    if _flashrank_ranker is None:
        _flashrank_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", max_length=512)

    passages = [{"id": i, "text": ((p.get("title") or "") + ". " + (p.get("abstract") or ""))[:1000]} for i, p in enumerate(papers)]
    request = RerankRequest(query=query, passages=passages)
    ranked = _flashrank_ranker.rerank(request)

    reranked = []
    for item in ranked[:top_n]:
        paper = papers[item["id"]]
        paper["_rerank_score"] = round(float(item["score"]), 4)
        reranked.append(paper)
    return reranked


def rerank(query: str, papers: list[dict], top_n: int = 50, intent: str = "") -> list[dict]:
    """Rerank papers. Tries DashScope qwen3-rerank first, falls back to FlashRank."""
    if not papers:
        return papers
    result = _rerank_dashscope(query, papers, top_n, intent=intent)
    if result is not None:
        return result
    return _rerank_flashrank(query, papers, top_n)


def rank_final(papers: list[dict]) -> list[dict]:
    """Apply learnable composite ranking formula and sort.

    final = rerank_score^γ × (1 + α × log(citations+1)) × (1 + β × source_count/N) × (1 + δ × recency)
    γ,α,β,δ loaded from rank_params.json (learned via scipy.optimize on LitSearch GT).
    """
    params = _load_rank_params()
    gamma = params.get("gamma", 1.0)
    alpha = params.get("alpha", 0.05)
    beta = params.get("beta", 0.10)
    delta = params.get("delta", 0.0)

    current_year = datetime.now().year
    n_sources = max(len(set(s for p in papers for s in (p.get("_source_ranks") or {}).keys())), 1)

    for p in papers:
        r = max(p.get("_rerank_score", 0.0), 1e-6)
        cites = p.get("citation_count", 0) or 0
        src_count = p.get("_source_count", 1)
        year = p.get("year") or current_year
        recency = max(0, 1.0 - (current_year - year) / 10.0)

        score = (r ** gamma) * (1 + alpha * math.log(cites + 1)) * (1 + beta * src_count / n_sources) * (1 + delta * recency)
        p["_final_score"] = round(score, 4)

    papers.sort(key=lambda p: -p["_final_score"])
    return papers


DOMAIN_KEYWORDS = {
    "computer science": {
        "algorithm", "neural", "network", "transformer", "attention",
        "model", "learning", "deep", "machine", "compute", "software",
        "code", "programming", "compiler", "architecture", "inference",
        "training", "benchmark", "llm", "language model", "gpu", "cpu",
        "optimization", "gradient", "embedding", "token", "bert", "gpt",
        "diffusion", "reinforcement", "classification", "detection",
        "segmentation", "generation", "retrieval", "reasoning", "dataset",
    },
    "mathematics": {
        "theorem", "proof", "conjecture", "algebra", "topology",
        "geometry", "calculus", "equation", "polynomial", "matrix",
        "convergence", "optimization", "combinatorics", "probability",
    },
    "physics": {
        "quantum", "particle", "relativity", "thermodynamic",
        "electromagnetic", "photon", "entropy", "hamiltonian",
        "lagrangian", "field theory", "cosmology",
    },
    "biology": {
        "gene", "protein", "cell", "genome", "mutation", "enzyme",
        "organism", "species", "evolution", "dna", "rna", "molecular",
        "phylogenetic", "transcription", "metabolic",
    },
    "medicine": {
        "patient", "clinical", "treatment", "diagnosis", "therapy",
        "disease", "symptom", "drug", "pharmaceutical", "surgery",
        "pathology", "epidemiology", "biomarker",
    },
}


ARXIV_CATEGORY_MAP = {
    "cs.": "computer science", "stat.ml": "computer science",
    "stat.": "mathematics", "math.": "mathematics",
    "physics.": "physics", "hep-": "physics", "astro-": "physics",
    "cond-mat": "physics", "quant-ph": "physics", "gr-qc": "physics",
    "q-bio.": "biology", "q-fin.": "economics",
    "eess.": "engineering", "nlin.": "physics",
}


def _normalize_field(field: str) -> str:
    """Map arXiv category tags and partial names to canonical field names."""
    fl = field.lower().strip()
    for prefix, canonical in ARXIV_CATEGORY_MAP.items():
        if fl.startswith(prefix):
            return canonical
    return fl


def filter_by_fields(papers: list[dict],
                     fields: list[str] | None) -> list[dict]:
    """Filter papers to match requested fields of study.
    Handles arXiv category tags (cs.CL, math.AG, etc.) by mapping to canonical names.
    If a paper has no field info, check title+abstract for domain keywords.
    """
    if not fields:
        return papers

    field_lower = {f.lower() for f in fields}

    filtered = []
    for p in papers:
        raw_fields = p.get("fields_of_study") or []
        if raw_fields:
            normalized = {_normalize_field(f) for f in raw_fields}
            if normalized & field_lower or any(
                any(fl in nf for fl in field_lower) for nf in normalized
            ):
                filtered.append(p)
            continue

        text = (p.get("title", "") + " " + p.get("abstract", "")).lower()
        for fl in field_lower:
            keywords = DOMAIN_KEYWORDS.get(fl)
            if keywords and any(kw in text for kw in keywords):
                filtered.append(p)
                break

    return filtered
