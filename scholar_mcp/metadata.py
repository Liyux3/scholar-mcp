"""Resolve sparse relation records before ranking, without depending on S2 quota."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from . import crossref_client as crossref, openalex_client as oa, relevance
from .cache import cached


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
                             for p in pending))
    resolved = _batch_dois([doi for doi in dois if doi])
    pmids = list(dict.fromkeys(str((p.get("external_ids") or {}).get("PMID") or "") for p in pending))
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
            if med.get(str(ids.get("PMID"))):
                return med[str(ids["PMID"])]
            if doi:
                return resolved.get(doi) or _doi(doi)
            if ids.get("ArXiv"):
                # Native Atom id_list returns all authors and abstracts in one record.
                from .arxiv_client import get_paper
                return get_paper("ArXiv:" + ids["ArXiv"])
            ref = paper.get("_reference") or {}
            text = ref.get("unstructured") or " ".join(str(ref.get(k) or "") for k in ("article-title", "author", "year", "journal-title", "first-page"))
            return crossref.match_reference(text, ref.get("article-title", ""), ref.get("author", ""),
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
                            "citation_count", "_citation_count_known", "open_access_url", "is_open_access"):
                    if found.get(key) is not None and (not paper.get(key) or key in {"citation_count", "_citation_count_known"}):
                        paper[key] = deepcopy(found[key])
                paper["external_ids"] = {**found.get("external_ids", {}), **paper.get("external_ids", {})}
                paper["paper_id"] = relevance.best_paper_id(paper)
                paper["_metadata_source"] = found.get("source")
                paper.pop("_needs_metadata", None)
    return papers
