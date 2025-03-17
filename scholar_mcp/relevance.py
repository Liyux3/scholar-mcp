"""Query preprocessing, relevance scoring, deduplication, and field filtering."""

import math
import re
from datetime import datetime

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
    """Shorten long queries to core keywords for better API results."""
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query)
    if len(words) <= 12:
        return query
    keywords = extract_keywords(query, max_keywords=8)
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


def _keyword_score(query: str, paper: dict) -> float:
    """Fraction of query keywords found in title+abstract, with title boost. 0.0 to 1.0.
    Returns 0.0 if fewer than 40% of keywords match (prevents single-word false positives).
    """
    keywords = extract_keywords(query)
    if not keywords:
        return 0.0

    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    full_text = title + " " + abstract

    hits = sum(1 for kw in keywords if kw in full_text)
    hit_ratio = hits / len(keywords)

    if hit_ratio < 0.4:
        return 0.0

    title_hits = sum(1 for kw in keywords if kw in title)
    title_bonus = 0.2 * (title_hits / len(keywords))

    return min(hit_ratio + title_bonus, 1.0)


def _citation_score(paper: dict) -> float:
    """Log-scaled citation score. 0.0 to 1.0."""
    cites = paper.get("citation_count") or 0
    if cites <= 0:
        return 0.0
    return min(math.log10(cites + 1) / 5.0, 1.0)


def _venue_score(paper: dict) -> float:
    """Bonus for known top venues."""
    venue = (paper.get("venue") or "").lower()
    if not venue:
        return 0.0
    for top in TOP_VENUES:
        if top in venue:
            return 1.0
    return 0.0


def _recency_score(paper: dict) -> float:
    """Slight bonus for recent papers. 0.0 to 1.0."""
    year = paper.get("year")
    if not year:
        return 0.0
    current_year = datetime.now().year
    age = current_year - year
    if age <= 0:
        return 1.0
    if age >= 10:
        return 0.0
    return 1.0 - (age / 10.0)


def score_results(query: str, papers: list[dict],
                  min_score: float = 0.1) -> list[dict]:
    """Score and filter papers by relevance to query.

    Combines keyword matching, citation count, venue quality, recency,
    and multi-source consensus (papers appearing in multiple sources get boosted).
    Papers below min_score are dropped. Returns sorted descending.
    """
    scored = []
    for p in papers:
        kw = _keyword_score(query, p)
        ci = _citation_score(p)
        ve = _venue_score(p)
        re_ = _recency_score(p)
        sc = _source_count(p)
        total = 0.45 * kw + 0.18 * ci + 0.12 * ve + 0.12 * re_ + 0.13 * sc
        if total >= min_score:
            p["_relevance_score"] = round(total, 3)
            scored.append(p)

    scored.sort(key=lambda x: x["_relevance_score"], reverse=True)
    return scored


def _source_count(paper: dict) -> float:
    """Consensus bonus: papers found by multiple sources are more likely relevant."""
    count = paper.get("_source_count", 1)
    if count <= 1:
        return 0.0
    if count == 2:
        return 0.5
    return 1.0


RRF_K = 60


def rrf_score(paper: dict, k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion score: sum(1/(k + rank_i)) across sources.
    Cormack et al. 2009. k=60 is the standard default.
    Papers not returned by a source get no contribution from that source.
    """
    ranks = paper.get("_source_ranks") or {}
    if not ranks:
        return 0.0
    return sum(1.0 / (k + r) for r in ranks.values())


def consensus_rrf_score(paper: dict, k: int = RRF_K) -> float:
    """Consensus-weighted RRF: vote_count * rrf_score.
    Papers appearing in multiple sources get a multiplicative boost.
    This implements the Condorcet-inspired ranking from our theory.
    """
    votes = paper.get("_source_count", 1)
    return votes * rrf_score(paper, k)


def rrf_fuse(papers: list[dict], method: str = "rrf",
             k: int = RRF_K) -> list[dict]:
    """Score and sort papers using RRF or consensus-RRF.

    Args:
        papers: deduplicated papers with _source_ranks populated
        method: "rrf" for standard RRF, "consensus" for vote-weighted RRF
        k: RRF smoothing constant (default 60)

    Returns:
        Papers sorted by fusion score descending, with _rrf_score attached.
    """
    score_fn = consensus_rrf_score if method == "consensus" else rrf_score
    for p in papers:
        p["_rrf_score"] = score_fn(p, k)
    papers.sort(key=lambda x: x["_rrf_score"], reverse=True)
    return papers


_flashrank_ranker = None


def rerank(query: str, papers: list[dict], top_n: int = 10) -> list[dict]:
    """Rerank papers using FlashRank if available. Falls back to input order."""
    if not papers:
        return papers
    try:
        from flashrank import Ranker, RerankRequest
    except ImportError:
        return papers[:top_n]

    global _flashrank_ranker
    if _flashrank_ranker is None:
        _flashrank_ranker = Ranker(max_length=256)

    passages = []
    for i, p in enumerate(papers):
        text = (p.get("title") or "") + ". " + (p.get("abstract") or "")
        passages.append({"id": i, "text": text[:1000]})

    request = RerankRequest(query=query, passages=passages)
    ranked = _flashrank_ranker.rerank(request)

    reranked = []
    for item in ranked[:top_n]:
        orig_idx = item["id"]
        paper = papers[orig_idx]
        paper["_relevance_score"] = round(float(item["score"]), 3)
        reranked.append(paper)

    return reranked


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
