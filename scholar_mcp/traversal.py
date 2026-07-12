"""Citation-graph traversal primitives.

"Find me similar papers" is not one question. These are five different
relations, each answering something different, and collapsing them into a
single similarity call loses most of the structure:

    references      what this paper builds on          -> foundations
    citations       what built on this paper           -> descendants
    co_citation     what gets cited alongside it       -> peers
    bibliographic_coupling  what cites the same works  -> methodological kin
    similar         embedding neighbours (SPECTER2)    -> semantic neighbours

Co-citation and bibliographic coupling are the two that cross field
boundaries. A protein-folding paper and a machine-translation paper can share
a reference list without sharing any vocabulary, so an embedding model will
never connect them, while coupling makes the link obvious. They are also both
computable from OpenAlex, which returns `referenced_works` inline.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import openalex_client as oa

OA_BASE = "https://api.openalex.org/works"

# How many citing papers to inspect when computing co-citation. Each carries
# its full reference list, so this is one request, not N.
CO_CITATION_SAMPLE = 50

# Minimum times two papers must co-occur before the link is meaningful. At 1,
# every reference of every citing paper qualifies and the result is noise.
MIN_CO_OCCURRENCE = 2


def _wid(paper_id: str, title: str = "") -> str | None:
    return oa._resolve_to_wid(paper_id, title=title)


def _fetch_works(wids: list[str], fields: str = "id,title,cited_by_count,publication_year,doi") -> dict:
    """Batch-resolve OpenAlex ids to work records, keyed by full id."""
    if not wids:
        return {}
    params = oa._params_base()
    params["filter"] = "openalex_id:" + "|".join(wids[:50])
    params["select"] = fields
    params["per_page"] = min(len(wids), 50)
    response = oa._request(OA_BASE, params)
    if response.status_code != 200:
        return {}
    return {w["id"]: w for w in response.json().get("results", [])}


def _as_paper(work: dict, relation: str, strength: int) -> dict:
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    return {
        "paper_id": doi or work["id"].split("/")[-1],
        "title": work.get("title") or "",
        "year": work.get("publication_year"),
        "citation_count": work.get("cited_by_count") or 0,
        "external_ids": {**({"DOI": doi} if doi else {}),
                         "OpenAlex": work["id"].split("/")[-1]},
        "url": work.get("id") or "",
        "source": "openalex",
        "_relation": relation,
        "_relation_strength": strength,
    }


def co_citation(paper_id: str, title: str = "", limit: int = 20,
                sample: int = CO_CITATION_SAMPLE) -> list[dict]:
    """Papers frequently cited alongside this one.

    Takes the most-cited papers that cite the seed, counts what else they
    cite, and returns the most frequent. This surfaces the seed's intellectual
    peers: for "Attention Is All You Need" it returns ResNet, ImageNet,
    AlexNet and LSTM, the deep-learning canon that transformer papers cite
    together with it.
    """
    wid = _wid(paper_id, title)
    if not wid:
        return []

    params = oa._params_base()
    params["filter"] = f"cites:{wid}"
    params["sort"] = "cited_by_count:desc"
    params["per_page"] = min(sample, 100)
    params["select"] = "id,referenced_works"
    response = oa._request(OA_BASE, params)
    if response.status_code != 200:
        return []

    seed_full_id = f"https://openalex.org/{wid}"
    counts = Counter()
    for work in response.json().get("results", []):
        for ref in work.get("referenced_works") or []:
            if ref != seed_full_id:
                counts[ref] += 1

    candidates = [(ref, n) for ref, n in counts.most_common(limit * 2)
                  if n >= MIN_CO_OCCURRENCE][:limit]
    works = _fetch_works([ref.split("/")[-1] for ref, _ in candidates])

    out = []
    for ref, n in candidates:
        work = works.get(ref)
        if work and work.get("title"):
            out.append(_as_paper(work, "co_citation", n))
    return out


def bibliographic_coupling(paper_id: str, title: str = "", limit: int = 20) -> list[dict]:
    """Papers that cite many of the same works as this one.

    Shared references indicate shared methodology even when the applications
    are unrelated, which is what makes this the most useful relation for
    crossing domain boundaries: two papers can be coupled without sharing any
    topical vocabulary.
    """
    wid = _wid(paper_id, title)
    if not wid:
        return []

    params = oa._params_base()
    params["select"] = "referenced_works"
    seed = oa._request(f"{OA_BASE}/{wid}", params)
    if seed.status_code != 200:
        return []
    references = seed.json().get("referenced_works") or []
    if not references:
        return []

    # Sample the seed's references, then ask who else cites each. Papers
    # appearing under many of them are strongly coupled.
    sampled = references[:20]

    def citers_of(ref_full_id: str) -> list[str]:
        p = oa._params_base()
        p["filter"] = f"cites:{ref_full_id.split('/')[-1]}"
        p["sort"] = "cited_by_count:desc"
        p["per_page"] = 25
        p["select"] = "id"
        r = oa._request(OA_BASE, p)
        if r.status_code != 200:
            return []
        return [w["id"] for w in r.json().get("results", [])]

    counts = Counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ids in pool.map(citers_of, sampled):
            counts.update(ids)

    seed_full_id = f"https://openalex.org/{wid}"
    candidates = [(wid_, n) for wid_, n in counts.most_common(limit * 3)
                  if wid_ != seed_full_id and n >= MIN_CO_OCCURRENCE][:limit]
    works = _fetch_works([w.split("/")[-1] for w, _ in candidates])

    out = []
    for wid_, n in candidates:
        work = works.get(wid_)
        if work and work.get("title"):
            out.append(_as_paper(work, "bibliographic_coupling", n))
    return out
