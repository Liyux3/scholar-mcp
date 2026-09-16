"""Resolve sparse relation records before ranking, without depending on S2 quota."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re

from . import crossref_client as crossref, openalex_client as oa, relevance
from .cache import cached


@cached(ttl=3600)
def _bibliographic(text: str, title: str, author: str, year: str, page: str) -> dict | None:
    if not text.strip():
        return None
    try:
        found = crossref.match_reference(text, title, author, year, page)
        if found:
            return found
    except Exception:
        pass
    # Titles supplied by another index are hints only. Require literal title
    # evidence in the original citation and validate the final record's year.
    normalized = relevance._normalize_title(text)
    titles = [title] if title else []
    if not titles:
        try:
            for record in crossref.reference_candidates(text):
                candidate = (record.get("title") or [""])[0]
                nt = relevance._normalize_title(candidate)
                if len(nt) >= 20 and nt in normalized:
                    titles.append(candidate)
        except Exception:
            pass
        # Citation prose can provide candidate title sentences even when the
        # catalog has no DOI deposit. These are lookup hints, never accepted
        # metadata until an external record matches the title and year.
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
            sentence = sentence.strip().rstrip(".")
            words = re.findall(r"[A-Za-z]{2,}", sentence)
            if (5 <= len(words) and len(sentence) <= 240
                    and not sentence.startswith(("In ", "This ", "See "))):
                titles.append(sentence)
            if len(titles) >= 3:
                break
    words = [w for w in re.findall(r"[^\W\d_]+", author.split(",")[0]) if len(w) > 1]
    surname = words[-1].casefold() if words else ""
    from . import dblp_client, s2_client
    query = title or (f"{surname} {year}" if surname and year else "")
    if query:
        try:
            matches = [p for p in dblp_client.search_papers(query, limit=20)
                       if len(relevance._normalize_title(p.get("title", ""))) >= 20
                       and relevance._normalize_title(p["title"]) in normalized
                       and (not year or str(p.get("year")) == year)]
            unique = relevance.deduplicate(matches)
            if len(unique) == 1:
                return unique[0]
        except Exception:
            pass
    if s2_client.is_healthy():
        for candidate in dict.fromkeys(titles):
            try:
                paper = s2_client.search_match(candidate)
                if (paper and relevance._normalize_title(paper.get("title", "")) == relevance._normalize_title(candidate)
                        and (not year or str(paper.get("year")) == year)):
                    return paper
            except Exception:
                break
    return None


@cached(ttl=3600)
def _doi(doi: str) -> dict | None:
    return crossref.get_paper(doi)


@cached(ttl=3600)
def _batch_dois(dois: list[str]) -> dict[str, dict]:
    result = {}
    for offset in range(0, len(dois), 40):
        batch = dois[offset:offset + 40]
        params = oa._params_base()
        params.update(filter="doi:" + "|".join(batch), per_page=len(batch))
        try:
            response = oa._request("https://api.openalex.org/works", params, timeout=20)
            response.raise_for_status()
            for work in response.json().get("results", []):
                doi = crossref.doi_id(work.get("doi") or "").casefold()
                if doi in batch:
                    result[doi] = oa.format_paper(work)
        except Exception:
            # Stop batch traffic during overload; exact Crossref lookup remains available.
            break
    return result


def hydrate(papers: list[dict]) -> list[dict]:
    pending = [p for p in papers if p.get("_needs_metadata") or not p.get("title")]
    if not pending:
        return papers
    dois = list(dict.fromkeys(crossref.doi_id((p.get("external_ids") or {}).get("DOI", "")).casefold()
                             for p in pending if not p.get("_title_conflict")))
    resolved = _batch_dois([doi for doi in dois if doi])
    pmids = list(dict.fromkeys(str((p.get("external_ids") or {}).get("PMID") or (p.get("external_ids") or {}).get("PubMed") or "") for p in pending))
    from . import europepmc_client as epmc
    med = {}
    pmids = [pid for pid in pmids if pid.isdigit()]
    for offset in range(0, len(pmids), 40):
        query = "(" + " OR ".join(f"EXT_ID:{pid}" for pid in pmids[offset:offset + 40]) + ") AND SRC:MED"
        try:
            med.update({str(item.get("id")): epmc.format_paper(item) for item in epmc._request(query, 100)})
        except Exception:
            break

    def lookup(paper):
        ids = paper.get("external_ids") or {}
        doi = crossref.doi_id(ids.get("DOI", "")).casefold()
        try:
            if paper.get("_title_conflict"):
                if ids.get("ArXiv"):
                    from .arxiv_client import get_paper
                    return get_paper("ArXiv:" + relevance._normalized_identifier("ArXiv", ids["ArXiv"]))
                if doi:
                    return _doi(doi)
            pmid = str(ids.get("PMID") or ids.get("PubMed") or "")
            if med.get(pmid):
                return med[pmid]
            if doi:
                return resolved.get(doi) or _doi(doi)
            if ids.get("ArXiv"):
                # Native arXiv metadata resolves identifiers outside DOI catalogs.
                from .arxiv_client import get_paper
                return get_paper("ArXiv:" + relevance._normalized_identifier("ArXiv", ids["ArXiv"]))
            ref = paper.get("_reference") or {}
            text = ref.get("unstructured") or " ".join(str(ref.get(k) or "") for k in ("article-title", "author", "year", "journal-title", "first-page"))
            return _bibliographic(text, ref.get("article-title", ""), ref.get("author", ""),
                                  str(ref.get("year", "")), str(ref.get("first-page", "")))
        except Exception:
            return None

    # Deduplicate work across relation providers before issuing exact lookups.
    unique = {}
    for paper in pending:
        ids = paper.get("external_ids") or {}
        key = str(ids.get("DOI") or ids.get("ArXiv") or ("PMID:" + str(ids["PMID"]) if ids.get("PMID") else "") or paper.get("_reference") or paper.get("title"))
        unique.setdefault(key, []).append(paper)
    with ThreadPoolExecutor(max_workers=4) as pool:
        groups = list(unique.values())
        for group, found in zip(groups, pool.map(lookup, (g[0] for g in groups))):
            if not found:
                continue
            for paper in group:
                # Metadata resolution does not create another independent search vote.
                for key in ("title", "authors", "abstract", "year", "venue", "publication_date",
                            "citation_count", "_citation_count_known", "open_access_url", "is_open_access", "url"):
                    if key in {"citation_count", "_citation_count_known"}:
                        if not found.get("_citation_count_known"):
                            continue
                        if key == "citation_count" and (paper.get("citation_count") or 0) > (found.get(key) or 0):
                            continue
                    if found.get(key) is not None and (not paper.get(key) or key in {"citation_count", "_citation_count_known"}
                                                       or key in {"title", "authors", "abstract", "year", "publication_date", "open_access_url", "url"}
                                                       and paper.get("_title_conflict") and found.get(key)):
                        paper[key] = deepcopy(found[key])
                paper["external_ids"] = {**found.get("external_ids", {}), **paper.get("external_ids", {})}
                if not paper.get("paper_id") and found.get("paper_id"):
                    paper["paper_id"] = found["paper_id"]
                paper["paper_id"] = relevance.best_paper_id(paper)
                paper["_metadata_source"] = found.get("source")
                paper.pop("_needs_metadata", None)
                paper.pop("_title_conflict", None)
    return papers
