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


def extract_keywords(query: str, max_keywords: int = 8) -> list[str]:
    """Extract informative keywords from a query. Helps S2/arXiv search."""
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query.lower())
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 1]
    if len(keywords) <= max_keywords:
        return keywords
    return keywords[:max_keywords]


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


def deduplicate(papers: list[dict]) -> list[dict]:
    """Remove duplicate papers by DOI or normalized title."""
    seen_dois = set()
    seen_titles = set()
    unique = []

    for p in papers:
        doi = (p.get("external_ids") or {}).get("DOI", "")
        if doi:
            doi_lower = doi.lower()
            if doi_lower in seen_dois:
                continue
            seen_dois.add(doi_lower)

        norm_title = _normalize_title(p.get("title", ""))
        if norm_title and len(norm_title) > 10:
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

        unique.append(p)

    return unique


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

    Weights: keyword_match=0.50, citations=0.20, venue=0.15, recency=0.15
    Papers below min_score are dropped. Returns sorted descending.
    """
    scored = []
    for p in papers:
        kw = _keyword_score(query, p)
        ci = _citation_score(p)
        ve = _venue_score(p)
        re_ = _recency_score(p)
        total = 0.50 * kw + 0.20 * ci + 0.15 * ve + 0.15 * re_
        if total >= min_score:
            p["_relevance_score"] = round(total, 3)
            scored.append(p)

    scored.sort(key=lambda x: x["_relevance_score"], reverse=True)
    return scored


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
