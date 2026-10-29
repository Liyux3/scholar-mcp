"""Record identity, publication versions and provenance shared by every backend."""

import re
import unicodedata
from html import unescape


def normalize_title(title: str) -> str:
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
        "other": "Other", "audiovisual": "Audiovisual", "presentation": "Presentation",
        "courtdecision": "CourtDecision", "clinicaltrialregistration": "ClinicalTrialRegistration",
    }
    return sorted({aliases.get(re.sub(r"[^a-z]", "", str(value).casefold()), str(value))
                   for value in (values or []) if value})


def known_citations(paper: dict) -> int | None:
    value = paper.get('citation_count')
    known = paper.get('_citation_count_known')
    return value if (known if known is not None else (value or 0) > 0) else None


_EXTERNAL_ID_ALIASES = {
    "PMID": "PubMed",
    "ArXivId": "ArXiv",
    "CorpusID": "CorpusId",
    "S2CorpusId": "CorpusId",
    "PMCID": "PubMedCentral",
    "PMC": "PubMedCentral",
}


def normalize_id(kind: str, value: object) -> str:
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


def pmc_id(value: object) -> str:
    """Canonical work ID for a typed PMC value; document versions stay separate."""
    match = re.fullmatch(r"(?:PMC:?)?(\d+)(?:\.\d+)?", str(value or "").strip(), re.I)
    return f"PMC{match[1]}" if match else ""


def external_ids(paper: dict) -> dict[str, str]:
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
        elif lowered.startswith('pmc') and pmc_id(paper_id):
            normalized.setdefault('PubMedCentral', paper_id)
        elif ':' in paper_id:
            kind = {'pmid': 'PubMed', 'pubmed': 'PubMed', 'pmc': 'PubMedCentral',
                    'openreview': 'OpenReview', 'eric': 'ERIC', 'courtlistener': 'CourtListener',
                    'clinicaltrials': 'ClinicalTrials', 'openedition': 'OpenEdition'}.get(lowered.split(':', 1)[0])
            if kind:
                normalized.setdefault(kind, paper_id.split(':', 1)[1])
    arxiv_doi = re.fullmatch(r"10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)",
                             normalize_id("DOI", normalized.get("DOI", "")), re.I)
    if arxiv_doi:
        normalized.setdefault("ArXiv", arxiv_doi[1])
    return normalized


def paper_identity_keys(paper: dict, include_title: bool = True) -> set[str]:
    """Return stable cross-source identity keys for a paper."""
    keys = {
        f"{kind.casefold()}:{normalized}"
        for kind, value in external_ids(paper).items()
        if (normalized := normalize_id(kind, value))
    }
    # Only declared manifestations of the same work supply identity aliases.
    # Citations, corrections, book editions and supplements stay separate.
    for relation in paper.get('relations') or []:
        kind = re.sub(r'[^a-z]', '', str(relation.get('type', '')).casefold())
        if kind in {'ispreprintof', 'haspreprint', 'ismanuscriptof', 'hasmanuscript', 'isidenticalto'}:
            id_type = str(relation.get('id_type') or '').casefold()
            name = {'doi': 'DOI', 'arxiv': 'ArXiv'}.get(id_type)
            if name and (value := normalize_id(name, relation.get('id', ''))):
                keys.add(f'{name.casefold()}:{value}')
    for version in paper.get('versions') or []:
        keys.update(f'{kind.casefold()}:{value}' for kind, raw in external_ids(version).items()
                    if (value := normalize_id(kind, raw)))
    # Keep legacy persisted keys intact. Equivalent typed PMC forms add work
    # aliases instead of replacing the indexes of saved notes and PDF copies.
    for key in tuple(keys):
        if key.startswith('pubmedcentral:') and (canonical := pmc_id(key.split(':', 1)[1])):
            keys.update({f'pubmedcentral:{canonical.casefold()}', f'pubmedcentral:{canonical[3:]}'})
    paper_id = str(paper.get("paper_id") or "").strip()
    if paper_id and not keys:
        keys.add(f"paper_id:{paper_id.casefold()}")
    if include_title:
        title = normalize_title(paper.get("title", ""))
        if len(title) > 10:
            keys.add(f"title:{title}")
    return keys


def best_paper_id(paper: dict) -> str:
    """Choose an identifier callers can feed back into Scholar MCP."""
    ids = external_ids(paper)
    if ids.get("DOI"):
        return normalize_id("DOI", ids["DOI"])
    if ids.get("ArXiv"):
        return f"ARXIV:{normalize_id('ArXiv', ids['ArXiv'])}"
    if ids.get("OpenAlex"):
        return normalize_id("OpenAlex", ids["OpenAlex"]).upper()
    if ids.get("CorpusId"):
        return f"CorpusId:{ids['CorpusId']}"
    if ids.get("PubMed"):
        return f"PMID:{ids['PubMed']}"
    if ids.get("OpenReview"):
        return f"OpenReview:{ids['OpenReview']}"
    return str(paper.get("paper_id") or "")


def tag_source_ranks(papers: list[dict], source_name: str, facet: str = "", positions: list[int] | None = None) -> list[dict]:
    """Tag each paper with its rank position from the given source.
    Call this on each source's results before merging.

    facet distinguishes several queries sent to one source. Keying ranks by
    source alone would let the second query overwrite the first, and would
    count one source answering twice as two independent sources agreeing,
    which is the opposite of what source agreement is supposed to mean.
    """
    channel = f"{source_name}::{facet}" if facet else source_name
    for rank, p in zip(positions if positions is not None else range(len(papers)), papers):
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


def normalize_authors(value: object) -> list[str]:
    """Keep author names consistent when sources or enrichment return objects."""
    entries = value if isinstance(value, (list, tuple)) else [value]
    names = []
    for author in entries:
        if isinstance(author, dict):
            author = next((author[key] for key in (
                "name", "fullName", "full_name", "display_name", "text"
            ) if isinstance(author.get(key), str) and author[key].strip()), None) or " ".join(
                part.strip() for part in (author.get("given"), author.get("family"))
                if isinstance(part, str) and part.strip()
            )
        if isinstance(author, str) and author.strip():
            name = " ".join(author.split())
            if name not in names:
                names.append(name)
    return names


def _author_names(paper: dict) -> set[tuple[str, str]]:
    """Family and first given name, retaining initials across provider formats."""
    names = set()
    for author in normalize_authors(paper.get("authors")):
        folded = ''.join(c for c in unicodedata.normalize('NFKD', author.casefold()) if not unicodedata.combining(c))
        family, comma, given = folded.partition(",")
        tokens = re.findall(r"[^\W\d_]+", family)
        if not tokens:
            continue
        given_tokens = re.findall(r"[^\W\d_]+", given) if comma else tokens[:-1]
        names.add((tokens[-1], given_tokens[0] if given_tokens else ""))
    return names


def _author_overlap(existing: dict, candidate: dict) -> bool:
    a_names, b_names = (_author_names(p) for p in (existing, candidate))
    return any(a_family == b_family and (not a_given or not b_given or a_given == b_given
                or min(len(a_given), len(b_given)) == 1 and a_given[0] == b_given[0])
               for a_family, a_given in a_names for b_family, b_given in b_names)


def _matching_authors(existing: dict, candidate: dict) -> bool:
    a_names, b_names = ({family for family, _ in _author_names(p)} for p in (existing, candidate))
    common = a_names & b_names
    return len(common) >= 2 and len(common) >= min(len(a_names), len(b_names)) / 2


def matches_paper(existing: dict, candidate: dict | None) -> bool:
    """Repository searches return candidates, not proof of a paper's identity."""
    if not candidate:
        return False
    return bool(paper_identity_keys(existing, include_title=False)
                & paper_identity_keys(candidate, include_title=False)) or compatible_title_match(existing, candidate)


PUBLICATION_VERSIONS = ('published', 'accepted', 'unknown', 'preprint')


def normalize_version(value) -> str:
    value = re.sub(r'[^a-z]', '', str(value or '').casefold())
    return {'published': 'published', 'publishedversion': 'published', 'vor': 'published',
            'versionofrecord': 'published', 'accepted': 'accepted', 'acceptedversion': 'accepted',
            'aam': 'accepted', 'am': 'accepted', 'preprint': 'preprint',
            'submittedversion': 'preprint'}.get(value, 'unknown')


def publication_version(paper: dict) -> str:
    """Bibliographic manifestation, distinct from the version of an attached PDF."""
    if paper.get('version') in PUBLICATION_VERSIONS:
        return paper['version']
    types = set(normalize_publication_types(paper.get('publication_types')))
    relations = {re.sub(r'[^a-z]', '', str(r.get('type', '')).casefold()) for r in paper.get('relations') or []}
    if 'ispreprintof' in relations or 'Preprint' in types or str(paper.get('venue', '')).casefold() == 'arxiv':
        return 'preprint'
    if 'haspreprint' in relations or types & {'JournalArticle', 'Conference', 'Review', 'Book', 'BookChapter'}:
        return 'published'
    return 'unknown'


def _version_record(paper: dict) -> dict:
    return {key: value for key, value in {
        'id': best_paper_id(paper), 'title': paper.get('title'), 'year': paper.get('year'),
        'authors': normalize_authors(paper.get('authors')),
        'version': publication_version(paper), 'venue': paper.get('venue'),
        'url': paper.get('url'), 'pdf_url': paper.get('open_access_url'),
        'external_ids': external_ids(paper),
    }.items() if value not in (None, '', {})}


def compatible_title_match(existing: dict, candidate: dict) -> bool:
    """A title is a lookup hint; known document identity and type still matter."""
    a_title, b_title = (normalize_title(p.get("title", "")) for p in (existing, candidate))
    if not a_title or a_title.replace(" ", "") != b_title.replace(" ", ""):
        return False
    a_authors, b_authors = (_author_names(p) for p in (existing, candidate))
    if a_authors and b_authors and not _author_overlap(existing, candidate):
        return False
    if a_title != b_title and not _matching_authors(existing, candidate):
        return False
    a_types, b_types = (set(normalize_publication_types(p.get("publication_types"))) for p in (existing, candidate))
    # Books, datasets and other media are separate resources even when a talk
    # repeats its paper's title. Untyped records remain eligible; an explicit
    # shared identifier still joins before this title-only check.
    separate = {"Book", "BookChapter", "Dataset", "Other", "Audiovisual", "Presentation",
                'CourtDecision', 'ClinicalTrialRegistration'}
    if not a_types.intersection(b_types) and (a_types | b_types) & separate:
        return False
    a_doi, b_doi = (normalize_id("DOI", external_ids(p).get("DOI")) for p in (existing, candidate))
    if a_doi and b_doi and a_doi != b_doi and not _author_overlap(existing, candidate):
        return False
    if len(a_title.split()) < 5:
        if a_doi and b_doi and a_doi != b_doi:
            return False
    a_year, b_year = existing.get("year"), candidate.get("year")
    try:
        if not (a_year and b_year and abs(int(a_year) - int(b_year)) > 1):
            return True
    except (TypeError, ValueError):
        return True
    return len(a_title.split()) >= 5 and _matching_authors(existing, candidate)


def merge_records(a: dict, b: dict) -> dict:
    """Merge two paper dicts for the same paper from different sources.
    Take the best of each field: longest abstract, most authors, highest cites.
    Track source count and per-source ranks for RRF.
    """
    merged = dict(a)
    stages = {'unknown': -1, 'preprint': 0, 'accepted': 1, 'published': 2}
    av, bv = publication_version(a), publication_version(b)
    version_transition = av != bv and 'unknown' not in {av, bv}
    if (a.get("title") and b.get("title")
            and normalize_title(a["title"]).replace(" ", "")
            != normalize_title(b["title"]).replace(" ", "") and not version_transition):
        merged.update(_title_conflict=True, _needs_metadata=True)
    a_ids = external_ids(merged)
    b_ids = external_ids(b)
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
    def text_quality(value):
        text = str(value or "")
        return (not bool(re.search(r"…|\.{3}", text)), len(text))

    venue = merged.get("venue") or ""
    if b.get("venue") and (not venue or str(venue).casefold().startswith("arxiv")
                            or text_quality(b["venue"])[0] > text_quality(venue)[0]):
        merged["venue"] = b["venue"]
    if not merged.get("title") and b.get("title"):
        merged["title"] = b["title"]
    b_abs = b.get("abstract") or ""
    if b_abs and (not merged.get("abstract") or text_quality(b_abs) > text_quality(merged["abstract"])):
        merged["abstract"] = b_abs
    b_authors = normalize_authors(b.get("authors"))
    a_authors = normalize_authors(merged.get("authors"))
    merged["authors"] = a_authors
    if (len(b_authors), text_quality(" ".join(b_authors))) > (len(a_authors), text_quality(" ".join(a_authors))):
        merged["authors"] = b_authors
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
    paths = set(a.get('_retrieval_ranks') or {}) | set(b.get('_retrieval_ranks') or {})
    if paths:
        merged['_retrieval_ranks'] = {path: min(p['_retrieval_ranks'][path] for p in (a, b)
                                             if path in (p.get('_retrieval_ranks') or {})) for path in paths}
    support = set(a.get('_relation_support') or {}) | set(b.get('_relation_support') or {})
    if support:
        merged['_relation_support'] = {key: max(p['_relation_support'][key] for p in (a, b)
                                              if key in (p.get('_relation_support') or {})) for key in support}
        merged['_relation_strength'] = sum(merged['_relation_support'].values())
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
    for field in ("relations", "publication_dates", "full_text_locations"):
        if b.get(field):
            merged[field] = list(a.get(field) or [])
            for value in b[field]:
                if value not in merged[field]:
                    merged[field].append(value)
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
    if version_transition or a.get('versions') or b.get('versions'):
        chosen = b if stages[bv] > stages[av] else a
        if av != bv:
            for field in ('title', 'year', 'publication_date', 'paper_id', 'venue', 'url', 'open_access_url'):
                if chosen.get(field):
                    merged[field] = chosen[field]
            if doi := external_ids(chosen).get('DOI'):
                merged['external_ids']['DOI'] = doi
        if av != bv:
            if chosen.get('authors'):
                merged['authors'] = normalize_authors(chosen['authors'])
            if chosen.get('abstract') and chosen.get('abstract_kind') != 'search_excerpt' and text_quality(chosen['abstract'])[0]:
                merged['abstract'] = chosen['abstract']
        versions = {}
        for record in (a.get('versions') or [_version_record(a)]) + (b.get('versions') or [_version_record(b)]):
            key = (record.get('id'), record.get('version'))
            previous = versions.get(key, {})
            versions[key] = {**record, **{k: v for k, v in previous.items() if v}}
        merged['version'] = publication_version(chosen)
        merged['versions'] = list(versions.values())
        merged['canonical_id'] = best_paper_id(merged)
    return merged


def deduplicate(papers: list[dict]) -> list[dict]:
    """Deduplicate papers by DOI or normalized title, merging metadata from duplicates.
    Tracks _source_count for consensus scoring.
    """
    groups: list[dict | None] = []
    first_seen: list[int] = []
    key_to_group: dict[str, int] = {}
    title_to_groups: dict[str, set[int]] = {}

    def identity_keys(paper: dict) -> set[str]:
        keys = paper_identity_keys(paper)
        title = normalize_title(paper.get("title", ""))
        if len(title.split()) >= 5:
            keys.add("title:joined:" + title.replace(" ", ""))
        return keys

    # Establish identified, authored records before attaching weak title-only
    # hits. Their arrival order must not let an empty record join two works.
    ordered = sorted(enumerate(papers), key=lambda item: (
        not bool(normalize_authors(item[1].get("authors"))), not bool(external_ids(item[1]))))
    for input_index, paper in ordered:
        keys = identity_keys(paper)
        identifier_keys = {key for key in keys if not key.startswith("title:")}
        matched = {key_to_group[key] for key in identifier_keys if key in key_to_group}
        if not matched:
            for key in keys - identifier_keys:
                for group in title_to_groups.get(key, set()):
                    existing = groups[group]
                    if existing is not None and compatible_title_match(existing, paper):
                        matched.add(group)
            if len(matched) > 1:
                from itertools import combinations
                if any(not compatible_title_match(groups[a], groups[b]) for a, b in combinations(matched, 2)):
                    matched.clear()

        if not matched:
            group_index = len(groups)
            merged = dict(paper)
            if "authors" in merged:
                merged["authors"] = normalize_authors(merged["authors"])
            merged["external_ids"] = external_ids(merged)
            merged.setdefault("_physical_sources", set((merged.get("source") or "").split("+")) - {""})
            merged["_source_count"] = physical_source_count(merged)
            merged["canonical_id"] = best_paper_id(merged)
            groups.append(merged)
            first_seen.append(input_index)
        else:
            group_index = min(matched)
            first_seen[group_index] = min(input_index, *(first_seen[i] for i in matched))
            merged = groups[group_index] or {}
            for other_index in sorted(matched - {group_index}):
                other = groups[other_index]
                if other is not None:
                    merged = merge_records(merged, other)
                    groups[other_index] = None
            merged = merge_records(merged, paper)
            groups[group_index] = merged
            if len(matched) > 1:
                for key, index in list(key_to_group.items()):
                    if index in matched:
                        key_to_group[key] = group_index

        for key in identity_keys(merged):
            if key.startswith("title:"):
                title_to_groups.setdefault(key, set()).add(group_index)
            else:
                key_to_group[key] = group_index

    return [paper for _, paper in sorted(zip(first_seen, groups), key=lambda item: item[0]) if paper is not None]
