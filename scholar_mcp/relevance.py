"""Query preprocessing, reranking, deduplication, and field filtering."""

import math
import os
import re
import sys
import time
from datetime import datetime
from html import unescape
from urllib.parse import urlsplit

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
    "us", "we", "our", "me", "my", "you", "your", "they", "their", "them",
    "let", "like", "new", "make", "get", "use", "way",
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


# Words carrying little information on their own, stripped before KeyBERT so
# it does not spend its phrase slots on scaffolding.
#
# Comparatives are deliberately absent. They look like filler but often carry
# the entire claim: "smaller dataset ... better models than bigger datasets"
# compressed to "models bigger datasets pre training result", inverting the
# meaning and retrieving the largest dataset papers in existence.
WEAK_WORDS = frozenset({
    "wondering", "simply", "question", "whether", "think", "believe",
    "approach", "problem", "paper", "work", "method", "proposed", "propose",
    "study", "studies", "explore", "any", "research", "results", "recent", "existing", "current",
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
    "high", "highest", "low", "lowest", "large", "small",
    "fully", "enough", "already", "particularly", "especially", "primarily",
    "point", "making", "fundamentally", "inherent", "inherently",
    "ceiling", "incremental", "ever", "baked",
    "there", "papers", "uses", "used", "using", "based", "like",
    "find", "found", "show", "shown", "given", "take", "know",
    "called", "related", "available", "also", "well", "still",
    "recommend", "suggest", "list", "refer", "direct",
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


def extract_lexical_anchors(query: str, max_anchors: int = 3) -> list[str]:
    """Keep exact names that semantic compression is most likely to erase.

    Dataset names, shared-task names, acronyms, camel-case identifiers, and
    short proper-noun phrases are often the only bridge from an indirect user
    description to an older paper title. They are cheap lexical signals, so
    preserve a few without turning the whole natural-language question into a
    brittle exact query.
    """
    candidates = []
    candidates.extend(re.findall(r'["“]([^"”]{3,60})["”]', query))
    candidates.extend(re.findall(
        r"\b(?:[A-Z]{2,}[A-Za-z0-9-]*|[A-Za-z]*[a-z][A-Z][A-Za-z0-9-]*|"
        r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+)\b",
        query,
    ))
    candidates.extend(re.findall(
        r"\b[A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+){1,3}\b",
        query,
    ))

    anchors = []
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.;:()[]")
        normalized = candidate.casefold()
        words = re.findall(r"[A-Za-z0-9][\w-]*", normalized)
        if not words or all(word in STOPWORDS | WEAK_WORDS for word in words):
            continue
        if normalized not in seen:
            seen.add(normalized)
            anchors.append(candidate)
        if len(anchors) >= max_anchors:
            break
    return anchors


def keyword_query(query: str, max_words: int) -> str:
    """Build a bounded keyword query with exact lexical anchors first."""
    words = []
    seen = set()
    values = extract_lexical_anchors(query)
    values.extend(
        word for word in extract_keywords(query, max_keywords=max_words * 2)
        if word not in STOPWORDS and word not in WEAK_WORDS
    )
    for value in values:
        for word in re.findall(r"[A-Za-z0-9][\w-]*", value):
            normalized = word.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            words.append(word)
            if len(words) >= max_words:
                return " ".join(words)
    return " ".join(words)


_keybert_model = None

# Queries at or below this length already read like keyword queries; compressing
# them further only discards signal.
COMPRESS_THRESHOLD_WORDS = 12

# Two-stage compression parameters, tuned by the 2026-05-11 sensitivity sweep.
EK_PRE_MAX = 15      # keyword budget for the noise-stripping stage
KB_TOP_N = 2         # keyphrases KeyBERT selects from the cleaned text
KB_DIVERSITY = 0.5   # MMR diversity; higher values drift off-topic
KB_NGRAM_RANGE = (1, 3)

# Used only when KeyBERT is unavailable. Wider than the two-stage output
# because plain keyword extraction has no salience ranking to lean on.
FALLBACK_MAX_KEYWORDS = 10


def _word_count(text: str) -> int:
    return len(re.findall(r"[a-zA-Z0-9][\w\-]*", text))


def optimize_query(query: str) -> str:
    """Compress a long natural-language query into a keyword query.

    Keyword-based APIs (OpenAlex, arXiv, Crossref) lose recall sharply as the
    query grows: an unshortened 25-word query scores near zero on all of them.
    Semantic sources bypass this via the raw-query route in sources._pick_query.
    """
    if _word_count(query) <= COMPRESS_THRESHOLD_WORDS:
        return query

    compressed = _keybert_extract(query)
    if compressed:
        anchor_words = " ".join(extract_lexical_anchors(query)).split()
        present = set(re.findall(r"[A-Za-z0-9][\w-]*", compressed.casefold()))
        missing = [word for word in anchor_words if word.casefold() not in present]
        return " ".join([compressed, *missing]).strip()

    return keyword_query(query, max_words=FALLBACK_MAX_KEYWORDS)


def optimize_query_short(query: str, max_words: int = 8) -> str:
    """Ultra-short keyword query for APIs with tight length limits (e.g. S2 ~10 words)."""
    if _word_count(query) <= max_words:
        return query
    return keyword_query(query, max_words=max_words)


def _load_keybert():
    """Import and cache the KeyBERT model. Returns None if the optional
    dependency is missing, so callers can fall back to keyword extraction.
    """
    global _keybert_model
    if _keybert_model is None:
        try:
            from keybert import KeyBERT
            from model2vec import StaticModel
        except ImportError:
            return None
        _keybert_model = KeyBERT(StaticModel.from_pretrained("minishlab/potion-base-8M"))
    return _keybert_model


def _keybert_extract(query: str, top_n: int = KB_TOP_N) -> str | None:
    """Strip noise words, then rank the survivors by embedding salience.

    Stripping first matters: it keeps KeyBERT from spending its few phrase
    slots on scaffolding like "are there any papers that".
    """
    model = _load_keybert()
    if model is None:
        return None

    cleaned = " ".join(extract_keywords(query, max_keywords=EK_PRE_MAX))
    if not cleaned:
        return None

    keyphrases = model.extract_keywords(
        cleaned, keyphrase_ngram_range=KB_NGRAM_RANGE, stop_words="english",
        top_n=top_n, use_mmr=True, diversity=KB_DIVERSITY,
    )
    if not keyphrases:
        # Cleaned text is still a valid keyword query, just unranked.
        return cleaned

    compressed = " ".join(kw for kw, _ in keyphrases)
    return _restore_contrast(query, compressed)


# Comparatives that carry a claim rather than describe magnitude. KeyBERT
# ranks by embedding salience, and these score low on their own, so a query
# built around a contrast can lose the half that makes it a contrast.
CONTRAST_WORDS = frozenset({
    "smaller", "larger", "bigger", "fewer", "less", "lower", "shorter",
    "better", "worse", "faster", "slower", "cheaper", "weaker", "stronger",
})


def _restore_contrast(original: str, compressed: str) -> str:
    """Re-insert contrast words that compression dropped one side of.

    "smaller dataset ... better than bigger datasets" compressed to
    "models bigger datasets pre training result": KeyBERT kept "bigger" and
    dropped "smaller", inverting the claim, so the search returned the largest
    datasets in existence. Only restores a word when its counterpart survived,
    since that asymmetry is what signals a broken comparison.
    """
    present = set(re.findall(r"[a-zA-Z0-9][\w\-]*", compressed.lower()))
    missing = [
        word for word in re.findall(r"[a-zA-Z0-9][\w\-]*", original.lower())
        if word in CONTRAST_WORDS and word not in present
    ]
    if not missing or not (present & CONTRAST_WORDS):
        return compressed
    # Preserve first-appearance order, without duplicates.
    ordered = list(dict.fromkeys(missing))
    return " ".join([compressed] + ordered)



def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for dedup matching."""
    # Punctuation is a word boundary, not nothing: deleting the hyphen in
    # "retrieval-augmented" creates "retrievalaugmented" and prevents the
    # same title without a hyphen from deduplicating.
    t = re.sub(r"[^\w\s]", " ", unescape(title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def normalize_publication_types(values) -> list[str]:
    """Normalize provider labels without inferring a paper type from its title."""
    if isinstance(values, str):
        values = [values]
    aliases = {
        "journalarticle": "JournalArticle", "journalarticles": "JournalArticle",
        "article": "Article", "review": "Review", "systematicreview": "Review",
        "metaanalysis": "Review",
        "conference": "Conference", "proceedingsarticle": "Conference",
        "conferenceandworkshoppapers": "Conference", "proceedings": "Conference",
        "book": "Book", "booksandtheses": "Book", "bookchapter": "BookChapter",
        "dataset": "Dataset", "postedcontent": "Preprint", "preprint": "Preprint",
    }
    return sorted({aliases.get(re.sub(r"[^a-z]", "", str(value).casefold()), str(value))
                   for value in (values or []) if value})


_EXTERNAL_ID_ALIASES = {
    "PMID": "PubMed",
    "ArXivId": "ArXiv",
    "CorpusID": "CorpusId",
    "S2CorpusId": "CorpusId",
    "PMCID": "PubMedCentral",
    "PMC": "PubMedCentral",
}


def _normalized_identifier(kind: str, value: object) -> str:
    """Normalize one identifier for equality without changing display values."""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if kind == "DOI":
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if lowered.startswith(prefix):
                lowered = lowered[len(prefix):]
                break
        return lowered
    if kind == "ArXiv":
        lowered = re.sub(r"^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)", "", lowered)
        lowered = lowered.removesuffix(".pdf")
        return re.sub(r"v\d+$", "", lowered)
    if kind == "OpenAlex":
        return lowered.rsplit("/", 1)[-1]
    return lowered


def _external_ids(paper: dict) -> dict[str, str]:
    normalized: dict[str, str] = {}
    raw = paper.get("external_ids") or {}
    if isinstance(raw, dict):
        for original_kind, value in raw.items():
            kind = _EXTERNAL_ID_ALIASES.get(str(original_kind), str(original_kind))
            if value not in (None, "") and kind not in normalized:
                normalized[kind] = str(value).strip()

    paper_id = str(paper.get("paper_id") or "").strip()
    lowered = paper_id.casefold()
    if paper_id:
        if re.match(r"^(?:https?://doi\.org/|doi:)?10\.\d{4,9}/", lowered):
            normalized.setdefault("DOI", paper_id)
        elif lowered.startswith(
            ("arxiv:", "https://arxiv.org/abs/", "https://arxiv.org/pdf/")
        ) or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", paper_id):
            normalized.setdefault("ArXiv", paper_id)
        elif lowered.startswith(("https://openalex.org/w", "w")) and re.search(r"w\d+$", lowered):
            normalized.setdefault("OpenAlex", paper_id)
        elif lowered.startswith("corpusid:"):
            normalized.setdefault("CorpusId", paper_id.split(":", 1)[1])
    arxiv_doi = re.fullmatch(r"10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)",
                             _normalized_identifier("DOI", normalized.get("DOI", "")), re.I)
    if arxiv_doi:
        normalized.setdefault("ArXiv", arxiv_doi[1])
    return normalized


def paper_identity_keys(paper: dict, include_title: bool = True) -> set[str]:
    """Return stable cross-source identity keys for a paper."""
    keys = {
        f"{kind.casefold()}:{normalized}"
        for kind, value in _external_ids(paper).items()
        if (normalized := _normalized_identifier(kind, value))
    }
    paper_id = str(paper.get("paper_id") or "").strip()
    if paper_id and not keys:
        keys.add(f"paper_id:{paper_id.casefold()}")
    if include_title:
        title = _normalize_title(paper.get("title", ""))
        if len(title) > 10:
            keys.add(f"title:{title}")
    return keys


def best_paper_id(paper: dict) -> str:
    """Choose an identifier callers can feed back into Scholar MCP."""
    ids = _external_ids(paper)
    if ids.get("DOI"):
        return _normalized_identifier("DOI", ids["DOI"])
    if ids.get("ArXiv"):
        return f"ARXIV:{_normalized_identifier('ArXiv', ids['ArXiv'])}"
    if ids.get("OpenAlex"):
        return _normalized_identifier("OpenAlex", ids["OpenAlex"]).upper()
    if ids.get("CorpusId"):
        return f"CorpusId:{ids['CorpusId']}"
    if ids.get("PubMed"):
        return f"PMID:{ids['PubMed']}"
    if ids.get("OpenReview"):
        return f"OpenReview:{ids['OpenReview']}"
    return str(paper.get("paper_id") or "")


def tag_source_ranks(papers: list[dict], source_name: str, facet: str = "") -> list[dict]:
    """Tag each paper with its rank position from the given source.
    Call this on each source's results before merging.

    facet distinguishes several queries sent to one source. Keying ranks by
    source alone would let the second query overwrite the first, and would
    count one source answering twice as two independent sources agreeing,
    which is the opposite of what source agreement is supposed to mean.
    """
    channel = f"{source_name}::{facet}" if facet else source_name
    for rank, p in enumerate(papers):
        if "_source_ranks" not in p:
            p["_source_ranks"] = {}
        p["_source_ranks"][channel] = rank
        p.setdefault("_physical_sources", set()).add(source_name)
        if "source" not in p or not p["source"]:
            p["source"] = source_name
        p["_source_count"] = len(p["_physical_sources"])
    return papers


def physical_source_count(paper: dict) -> int:
    """How many distinct APIs returned this paper, ignoring per-query channels."""
    physical = paper.get("_physical_sources")
    if physical:
        return len(physical)
    ranks = paper.get("_source_ranks") or {}
    return len({channel.split("::", 1)[0] for channel in ranks}) or 1


def _merge_two(a: dict, b: dict) -> dict:
    """Merge two paper dicts for the same paper from different sources.
    Take the best of each field: longest abstract, most authors, highest cites.
    Track source count and per-source ranks for RRF.
    """
    merged = dict(a)
    if (a.get("title") and b.get("title")
            and _normalize_title(a["title"]) != _normalize_title(b["title"])):
        merged.update(_title_conflict=True, _needs_metadata=True)
    a_ids = _external_ids(merged)
    b_ids = _external_ids(b)
    merged["external_ids"] = {**b_ids, **a_ids}

    for field in ("year", "publication_date", "paper_id", "url", "pdf_path"):
        if not merged.get(field) and b.get(field):
            merged[field] = b[field]
    try:
        if b.get("year") and merged.get("year") and int(b["year"]) < int(merged["year"]):
            merged["year"] = int(b["year"])
            merged["publication_date"] = b.get("publication_date") or str(b["year"])
            if b_ids.get("DOI"):
                merged["external_ids"]["DOI"] = b_ids["DOI"]
    except (TypeError, ValueError):
        pass
    if (not merged.get("venue") or str(merged.get("venue")).casefold() == "arxiv") and b.get("venue"):
        merged["venue"] = b["venue"]
    if not merged.get("title") and b.get("title"):
        merged["title"] = b["title"]
    b_abs = b.get("abstract") or ""
    if len(b_abs) > len(merged.get("abstract") or ""):
        merged["abstract"] = b_abs
    if len(b.get("authors") or []) > len(merged.get("authors") or []):
        merged["authors"] = b["authors"]
    if (
        b.get("_citation_count_known")
        and not merged.get("_citation_count_known")
    ) or (b.get("citation_count") or 0) > (merged.get("citation_count") or 0):
        merged["citation_count"] = b["citation_count"]
        merged["_citation_count_known"] = b.get("_citation_count_known", True)
    b_topics = set(b.get("fields_of_study") or [])
    a_topics = set(merged.get("fields_of_study") or [])
    if b_topics - a_topics:
        merged["fields_of_study"] = list(a_topics | b_topics)
    types = sorted(set(normalize_publication_types(merged.get("publication_types")))
                   | set(normalize_publication_types(b.get("publication_types"))))
    if types:
        merged["publication_types"] = types
    checked = set(a.get("_metadata_checked_fields") or ()) | set(b.get("_metadata_checked_fields") or ())
    if checked:
        merged["_metadata_checked_fields"] = checked
    if not merged.get("open_access_url") and b.get("open_access_url"):
        merged["open_access_url"] = b["open_access_url"]
    merged["is_open_access"] = bool(
        merged.get("is_open_access") or b.get("is_open_access")
        or merged.get("open_access_url")
    )
    if not merged.get("tldr") and b.get("tldr"):
        merged["tldr"] = b["tldr"]
    updates = []
    seen_updates = set()
    for update in (merged.get("updates") or []) + (b.get("updates") or []):
        key = (str(update.get("DOI") or "").casefold(), str(update.get("type") or "").casefold())
        if key not in seen_updates:
            seen_updates.add(key)
            updates.append(update)
    if updates:
        merged["updates"] = updates
    a_sources = set((merged.get("source") or "").split("+")) - {""}
    b_sources = set((b.get("source") or "").split("+")) - {""}
    all_sources = a_sources | b_sources
    merged["source"] = "+".join(sorted(all_sources))
    a_ranks = merged.get("_source_ranks") or {}
    b_ranks = b.get("_source_ranks") or {}
    channels = set(a_ranks) | set(b_ranks)
    merged["_source_ranks"] = {
        channel: min(rank for rank in (a_ranks.get(channel), b_ranks.get(channel)) if rank is not None)
        for channel in channels
    }
    merged["_physical_sources"] = (
        (merged.get("_physical_sources") or set())
        | (b.get("_physical_sources") or set())
        | all_sources
    )
    merged["_source_count"] = len(merged["_physical_sources"])
    merged["canonical_id"] = best_paper_id(merged)
    return merged


def deduplicate(papers: list[dict]) -> list[dict]:
    """Deduplicate papers by DOI or normalized title, merging metadata from duplicates.
    Tracks _source_count for consensus scoring.
    """
    for p in papers:
        if "_source_count" not in p:
            p["_source_count"] = 1

    groups: list[dict | None] = []
    key_to_group: dict[str, int] = {}
    title_to_groups: dict[str, set[int]] = {}

    def compatible_title_match(existing: dict, candidate: dict) -> bool:
        a_year, b_year = existing.get("year"), candidate.get("year")
        try:
            if not (a_year and b_year and abs(int(a_year) - int(b_year)) > 1):
                return True
        except (TypeError, ValueError):
            return True
        # Reprints/redeposits can be many years later. Exact, specific titles
        # plus multiple matching author surnames can establish the same work.
        # Generic annual report titles still stay separate across years.
        if len(_normalize_title(existing.get("title", "")).split()) < 5:
            return False
        def surnames(paper):
            names = set()
            for author in paper.get("authors") or []:
                tokens = re.findall(r"[^\W\d_]+", str(author).casefold())
                meaningful = [token for token in tokens if len(token) > 1]
                if meaningful:
                    names.add(meaningful[0] if "," in str(author) else meaningful[-1])
            return names
        a_names, b_names = surnames(existing), surnames(candidate)
        common = a_names & b_names
        return len(common) >= 2 and len(common) >= min(len(a_names), len(b_names)) / 2

    for paper in papers:
        keys = paper_identity_keys(paper)
        identifier_keys = {key for key in keys if not key.startswith("title:")}
        matched = {key_to_group[key] for key in identifier_keys if key in key_to_group}
        if not matched:
            for key in keys - identifier_keys:
                for group in title_to_groups.get(key, set()):
                    existing = groups[group]
                    if existing is not None and compatible_title_match(existing, paper):
                        matched.add(group)

        if not matched:
            group_index = len(groups)
            merged = dict(paper)
            merged["external_ids"] = _external_ids(merged)
            merged.setdefault("_physical_sources", set((merged.get("source") or "").split("+")) - {""})
            merged["_source_count"] = physical_source_count(merged)
            merged["canonical_id"] = best_paper_id(merged)
            groups.append(merged)
        else:
            group_index = min(matched)
            merged = groups[group_index] or {}
            for other_index in sorted(matched - {group_index}):
                other = groups[other_index]
                if other is not None:
                    merged = _merge_two(merged, other)
                    groups[other_index] = None
            merged = _merge_two(merged, paper)
            groups[group_index] = merged
            if len(matched) > 1:
                for key, index in list(key_to_group.items()):
                    if index in matched:
                        key_to_group[key] = group_index

        for key in paper_identity_keys(merged):
            if key.startswith("title:"):
                title_to_groups.setdefault(key, set()).add(group_index)
            else:
                key_to_group[key] = group_index

    return [paper for paper in groups if paper is not None]


_flashrank_ranker = None
_rank_params = None


# Ranking weights for rank_final. A fitted rank_params.json overrides any
# subset of these; unspecified keys keep the default.
DEFAULT_RANK_PARAMS = {"gamma": 1.0, "alpha": 0.05, "beta": 0.02, "delta": 0.10}


def _load_rank_params() -> dict:
    """Load learned ranking parameters from JSON file, backfilled with defaults."""
    global _rank_params
    if _rank_params is not None:
        return _rank_params
    import json
    try:
        with open(config.RANK_PARAMS_PATH) as f:
            loaded = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        loaded = {}
    _rank_params = {**DEFAULT_RANK_PARAMS, **loaded}
    return _rank_params


INTENT_INSTRUCTS = {
    "foundational": "Given a scientific literature search query, retrieve the most relevant research papers. Among equally relevant papers, prefer seminal and highly-cited works that established this field or method.",
    "recent": "Given a scientific literature search query, retrieve the most relevant research papers. Among equally relevant papers, prefer more recent publications with novel contributions.",
    "survey": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer survey, review, and tutorial papers that provide comprehensive overviews.",
    "method": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer papers that propose specific methods or techniques directly addressing the query.",
    "dataset": "Given a scientific literature search query, retrieve the most relevant research papers. Prefer papers that introduce, document, or evaluate the requested dataset or benchmark, with exact task and resource identity over general popularity.",
    "": "Given a scientific literature search query, retrieve the most relevant research papers whose methods, findings, or contributions directly address the query topic.",
}


_dashscope_warning_shown = False
_reranker_state = {
    "provider": None,
    "model": None,
    "dashscope_configured": bool(config.DASHSCOPE_API_KEY),
    "fallback_reason": None,
    "latency_ms": None,
}


def reranker_status() -> dict:
    """Return safe runtime state for status/debug output."""
    return dict(_reranker_state)


def _warn_dashscope_down(reason: str) -> None:
    """Print once per process when the primary reranker stops working.

    A silent fallback to FlashRank costs roughly 3x latency and measurably
    worse ranking, with no other symptom. Account arrearage in particular is
    permanent until someone acts, so it needs to be visible somewhere. stderr
    is safe: the MCP protocol uses stdout.
    """
    global _dashscope_warning_shown
    if not _dashscope_warning_shown:
        _dashscope_warning_shown = True
        print(f"scholar-mcp: Primary reranker unavailable ({reason}), "
              f"trying the local FlashRank fallback",
              file=sys.stderr)


def rerank_document(paper: dict) -> str:
    """Stable model input shared by hosted rerankers and offline evaluation."""
    parts = [f"Title: {paper.get('title') or ''}"]
    venue = paper.get("venue") or ""
    date = paper.get("publication_date") or str(paper.get("year") or "")
    if venue or date:
        parts.append(f"Venue: {venue}, Published: {date}".strip(", "))
    if paper.get("abstract"):
        parts.append(f"Abstract: {paper['abstract']}")
    return "\n".join(parts)[:2000]


def _apply_rerank_results(items: list[dict], papers: list[dict], top_n: int,
                          provider: str, model: str) -> list[dict]:
    """Validate a complete response before mutating any candidate scores.

    The fusion policy expects finite 0-1 relevance scores, not raw logits.
    Normalization belongs to the serving model, where its score semantics
    are known. A partial or malformed response must not erase candidates.
    """
    expected = min(top_n, len(papers))
    if not isinstance(items, list) or len(items) < expected:
        raise ValueError("Incomplete reranker response")
    checked = []
    seen = set()
    for item in items:
        index = item["index"]
        raw = item["relevance_score"]
        if type(index) is not int or not 0 <= index < len(papers) or index in seen:
            raise ValueError("Invalid or repeated reranker index")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("Invalid reranker score")
        score = float(raw)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Reranker must return finite scores in [0, 1]")
        seen.add(index)
        checked.append((index, score))
    checked.sort(key=lambda item: -item[1])
    result = []
    for rank, (index, score) in enumerate(checked[:expected], 1):
        paper = papers[index]
        paper.update(_rerank_score=score, _rerank_rank=rank,
                     _reranker_provider=provider, _reranker_model=model)
        result.append(paper)
    return result


def _rerank_remote(query: str, papers: list[dict], top_n: int, intent: str,
                   *, url: str, model: str, api_key: str | None,
                   provider: str, timeout: float) -> list[dict] | None:
    """One compatible HTTP contract for cloud and self-hosted rerankers."""
    started = time.monotonic()
    try:
        import httpx
        body = {
            "query": query[:500],
            "documents": [rerank_document(paper) for paper in papers],
            "top_n": min(top_n, len(papers)),
        }
        if model:
            body["model"] = model
        if provider == "dashscope":
            body["instruct"] = INTENT_INSTRUCTS.get(intent, INTENT_INSTRUCTS[""])
        elif intent:
            # Standard rerank APIs have no shared `instruct` field.
            body["query"] = INTENT_INSTRUCTS.get(intent, "") + "\n" + query[:500]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = httpx.post(
            url, headers=headers, json=body, timeout=timeout,
            trust_env=urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"},
        )
        # A provider can enforce a smaller token/payload budget than its
        # advertised document count. Split only size failures, on the same
        # endpoint/model, rather than silently degrading the whole pass.
        size_error = resp.status_code == 413 or (
            resp.status_code == 400 and any(marker in resp.text.casefold() for marker in (
                "too many", "too large", "too long", "maximum", "max_tokens", "token limit", "length limit",
            ))
        )
        if size_error and len(papers) > 1:
            middle = len(papers) // 2
            ranked = []
            for half in (papers[:middle], papers[middle:]):
                result = _rerank_remote(query, half, len(half), intent, url=url, model=model,
                                        api_key=api_key, provider=provider, timeout=timeout)
                if result is None:
                    return None
                ranked.extend(result)
            ranked.sort(key=lambda paper: -paper["_rerank_score"])
            return ranked[:top_n]
        resp.raise_for_status()
        data = resp.json()
        reranked = _apply_rerank_results(data.get("results"), papers, top_n, provider, model)
        _reranker_state.update(
            provider=provider,
            model=model or None,
            dashscope_configured=bool(config.DASHSCOPE_API_KEY),
            fallback_reason=None,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return reranked
    except Exception as e:
        reason = _dashscope_reason(e)
        _reranker_state.update(
            provider=None,
            model=None,
            dashscope_configured=bool(config.DASHSCOPE_API_KEY),
            fallback_reason=reason,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        _warn_dashscope_down(reason)
        return None


def _rerank_dashscope(query: str, papers: list[dict], top_n: int, intent: str = "") -> list[dict] | None:
    """Existing default, using the same adapter as a self-hosted model."""
    if not config.DASHSCOPE_API_KEY:
        _reranker_state.update(provider=None, model=None, dashscope_configured=False,
                               fallback_reason="not configured", latency_ms=None)
        return None
    return _rerank_remote(
        query, papers, top_n, intent,
        url="https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        model="qwen3-rerank", api_key=config.DASHSCOPE_API_KEY,
        provider="dashscope", timeout=15,
    )


def _dashscope_reason(exc: Exception) -> str:
    """Name the failure, separating states that need action from transient ones."""
    body = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    if "Arrearage" in body or "overdue" in body.lower():
        return "account in arrearage, top up at model-studio"
    if "InvalidApiKey" in body or "401" in body:
        return "invalid API key"
    return f"{type(exc).__name__}"


def _rerank_flashrank(query: str, papers: list[dict], top_n: int) -> list[dict]:
    """Rerank via FlashRank MiniLM (ONNX). Fallback when DashScope unavailable."""
    started = time.monotonic()
    try:
        from flashrank import Ranker, RerankRequest
        global _flashrank_ranker
        if _flashrank_ranker is None:
            # Keep the measured portable fallback. A different input format
            # or token budget needs paired ranking evidence before promotion.
            _flashrank_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2",
                                       max_length=FLASHRANK_MAX_TOKENS,
                                       cache_dir=os.path.join(config.DATA_DIR, "models"),
                                       log_level="WARNING")
        passages = [{"id": i, "text": ((p.get("title") or "") + ". " + (p.get("abstract") or ""))[:1000]} for i, p in enumerate(papers)]
        request = RerankRequest(query=query, passages=passages)
        ranked = _flashrank_ranker.rerank(request)
        reranked = _apply_rerank_results(
            [{"index": item["id"], "relevance_score": float(item["score"])} for item in ranked],
            papers, top_n, "flashrank", "ms-marco-MiniLM-L-12-v2",
        )
    except Exception as error:
        for p in papers:
            p["_rerank_score"] = 0.5
            p["_reranker_provider"] = "unavailable"
            p["_reranker_model"] = None
            p.pop("_rerank_rank", None)
        _reranker_state.update(
            provider="unavailable",
            model=None,
            fallback_reason=f"local reranker unavailable ({type(error).__name__})",
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return papers[:top_n]
    _reranker_state.update(
        provider="flashrank",
        model="ms-marco-MiniLM-L-12-v2",
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return reranked


DASHSCOPE_CAP = 500
DASHSCOPE_TOKEN_BUDGET = 110_000  # Leave room below the documented 120K request cap.
FLASHRANK_CAP = 150
FLASHRANK_MAX_TOKENS = 128

def rerank(query: str, papers: list[dict], top_n: int = 50, intent: str = "") -> list[dict]:
    """Score every candidate in provider-sized batches, then select globally."""
    if not papers:
        return papers
    batch_size = config.RERANK_BATCH_SIZE if config.RERANK_URL else DASHSCOPE_CAP
    batches = []
    batch, tokens = [], 0
    for paper in papers:
        # Avoid a tokenizer/model dependency just to schedule HTTP requests.
        # This conservative estimate handles multilingual text; a provider
        # size rejection still triggers exact splitting above, without cuts.
        text = query[:500] + rerank_document(paper)
        non_ascii = sum(ord(character) > 127 for character in text)
        estimated = (len(text) - non_ascii + 2) // 3 + non_ascii * 3 + 128
        if batch and (len(batch) >= batch_size or (
                not config.RERANK_URL and tokens + estimated > DASHSCOPE_TOKEN_BUDGET)):
            batches.append(batch)
            batch, tokens = [], 0
        batch.append(paper)
        tokens += estimated
    if batch:
        batches.append(batch)
    if len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda batch: _remote_batch(query, batch, len(batch), intent), batches))
        if any(result is None for result in outcomes):
            # All cloud work has joined before one consistent local fallback.
            return _local_batches(query, papers, top_n)
        ranked = [paper for result in outcomes for paper in result]
        ranked.sort(key=lambda p: -(p.get("_rerank_score") or 0))
        for rank, paper in enumerate(ranked, 1):
            paper["_rerank_rank"] = rank
        return ranked[:top_n]
    result = _remote_batch(query, papers, top_n, intent)
    if result is not None:
        return result
    return _local_batches(query, papers, top_n)


def _remote_batch(query: str, papers: list[dict], top_n: int, intent: str) -> list[dict] | None:
    if config.RERANK_URL:
        return _rerank_remote(
            query, papers, top_n, intent, url=config.RERANK_URL,
            model=config.RERANK_MODEL, api_key=config.RERANK_API_KEY,
            provider="custom", timeout=config.RERANK_TIMEOUT,
        )
    return _rerank_dashscope(query, papers, top_n, intent=intent)


def _local_batches(query: str, papers: list[dict], top_n: int) -> list[dict]:
    if len(papers) <= FLASHRANK_CAP:
        return _rerank_flashrank(query, papers, top_n)
    ranked = []
    for start in range(0, len(papers), FLASHRANK_CAP):
        batch = papers[start:start + FLASHRANK_CAP]
        ranked.extend(_rerank_flashrank(query, batch, len(batch)))
    if any(p.get("_reranker_provider") == "unavailable" for p in ranked):
        for paper in ranked:
            paper.update(_rerank_score=0.5, _reranker_provider="unavailable", _reranker_model=None)
            paper.pop("_rerank_rank", None)
        return rank_final(ranked)[:top_n]
    ranked.sort(key=lambda p: -(p.get("_rerank_score") or 0))
    for rank, paper in enumerate(ranked, 1):
        paper["_rerank_rank"] = rank
    return ranked[:top_n]


def rank_final(papers: list[dict]) -> list[dict]:
    """Apply learnable composite ranking formula and sort.

    final = rerank_score^γ × (1 + α × log(citations+1)) × (1 + β × source_count/N) × (1 + δ × recency)
    γ,α,β,δ loaded from rank_params.json (learned via scipy.optimize on LitSearch GT).
    """
    params = _load_rank_params()
    gamma, alpha, beta, delta = (
        params["gamma"], params["alpha"], params["beta"], params["delta"]
    )

    current_year = datetime.now().year
    # Count distinct APIs, not query channels: one source answering four
    # queries is one source, and treating it as four inflates agreement.
    n_sources = max(len({
        channel.split("::", 1)[0]
        for p in papers for channel in (p.get("_source_ranks") or {})
    }), 1)

    for p in papers:
        r = max(p.get("_rerank_score", 0.0), 1e-6)
        cites = p.get("citation_count", 0) or 0
        src_count = physical_source_count(p)
        try:
            year = int(p.get("year") or current_year)
        except (TypeError, ValueError):
            year = current_year
        recency = max(0, 1.0 - (current_year - year) / 10.0)

        score = (r ** gamma) * (1 + alpha * math.log(cites + 1)) * (1 + beta * src_count / n_sources) * (1 + delta * recency)
        p["_final_score"] = score

    papers.sort(key=lambda p: -p["_final_score"])

    if papers:
        max_s = papers[0]["_final_score"]
        if max_s > 0:
            for p in papers:
                p["_final_score"] = p["_final_score"] / max_s

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
