import re
import time
from datetime import datetime

import feedparser
import httpx
from bs4 import BeautifulSoup

from .. import access
from ..cache import DEFAULT_TTL, PartialRecords, _disk, cached
from . import pacing

ARXIV_API_URL = "https://export.arxiv.org/api/query"


class _AtomUnavailable(RuntimeError):
    """The native website can still serve this source while Atom recovers."""


def _atom(params):
    key = 'arxiv.atom.pause'
    if _disk(key):
        raise _AtomUnavailable('arXiv Atom service is waiting for its next probe')
    pacing.wait('arxiv')
    try:
        # arXiv has its own website fallback. Browser recovery belongs on that
        # human-facing endpoint, rather than on an unavailable Atom service.
        response = access.get(ARXIV_API_URL, kind='xml', params=params, timeout=15, host_recovery=False)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ValueError('arXiv returned malformed Atom data')
        return feed.entries
    except httpx.HTTPStatusError as error:
        if error.response.status_code not in (403, 406, 429, 500, 502, 503, 504):
            raise
    except (httpx.TransportError, access.AccessError, ValueError):
        pass
    _disk(key, (time.time() + DEFAULT_TTL, True))
    raise _AtomUnavailable('arXiv Atom service did not return usable records')


@cached(ttl=3600, miss_ttl=30)
def get_paper(paper_id: str) -> dict | None:
    value = re.sub(r"^(?:arxiv:|10\.48550/arxiv\.|https?://arxiv\.org/abs/)", "", paper_id, flags=re.I)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?", value):
        return None
    pacing.wait("arxiv")
    response = access.get(f"https://arxiv.org/abs/{value}", kind="html", timeout=20, follow_redirects=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    def meta(name):
        return [m.get("content", "") for m in soup.select(f'meta[name="{name}"]')]
    titles = meta("citation_title")
    if not titles:
        return None
    dates = meta("citation_date")
    date = dates[0].replace("/", "-") if dates else ""
    abstract = soup.select_one("blockquote.abstract")
    paper = {"paper_id": value, "title": titles[0], "authors": meta("citation_author"),
            "year": int(date[:4]) if date[:4].isdigit() else None, "publication_date": date or None,
            "venue": "arXiv",
            "abstract": abstract.get_text(" ", strip=True).removeprefix("Abstract:").strip() if abstract else "",
            "external_ids": {"ArXiv": value}, "source": "arxiv",
             "is_open_access": True,
            "open_access_url": f"https://arxiv.org/pdf/{value}", "url": f"https://arxiv.org/abs/{value}"}
    doi = next((link.get('href', '').split('doi.org/', 1)[-1] for link in soup.select('td.doi a[href*="doi.org/"]')), '')
    _published_link(paper, doi or next((d for d in meta('citation_doi') if not d.casefold().startswith('10.48550/')), ''))
    return paper


def _published_link(paper: dict, doi: str) -> None:
    if doi and not doi.casefold().startswith('10.48550/'):
        paper['external_ids']['DOI'] = doi
        paper['relations'] = [{'type': 'is-preprint-of', 'id': doi, 'id_type': 'doi', 'source': 'arxiv'}]


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Search arXiv. Returns results in the same dict format as s2."""
    words = query.split()
    if len(words) <= 10:
        search_q = f"all:{query}"
    else:
        kw = " ".join(words[:8])
        search_q = f"all:{kw}"
    params = {
        "search_query": search_q,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    if max_results <= 0:
        return []
    try:
        entries = _atom(params)
    except _AtomUnavailable:
        return _search_web(query, max_results)
    return [_entry_paper(entry) for entry in entries]


def _entry_paper(entry) -> dict:
    """One Atom entry as a paper record."""
    arxiv_id = entry.id.split("/abs/")[-1]
    pdf_url = next(
        (link.href for link in getattr(entry, "links", [])
         if link.get("type") == "application/pdf"),
        f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    )
    authors = [a.get("name", "") for a in getattr(entry, "authors", [])]
    try:
        year = int(entry.published[:4])
    except (ValueError, TypeError, AttributeError):
        year = None
    paper = {
        "paper_id": arxiv_id,
        "title": entry.title.replace("\n", " ").strip(),
        "authors": authors,
        "abstract": getattr(entry, "summary", "").replace("\n", " ").strip(),
        "year": year,
        "venue": "arXiv",
        "is_open_access": True,
        "open_access_url": pdf_url,
        "fields_of_study": [tag.term for tag in getattr(entry, "tags", [])],
        "publication_date": entry.published[:10] if getattr(entry, "published", None) else None,
        "external_ids": {"ArXiv": arxiv_id},
        "url": entry.id,
        "source": "arxiv",
    }
    _published_link(paper, getattr(entry, 'arxiv_doi', '') or '')
    return paper


def get_papers(ids: list[str]) -> dict[str, dict]:
    """Native records for many arXiv ids, keyed by id without version.

    The API takes a hundred ids per request, so completing a search's arXiv
    records costs one paced request instead of one per paper.
    """
    bare = list(dict.fromkeys(re.sub(r"v\d+$", "", i) for i in ids if i))
    found = {}
    for offset in range(0, len(bare), 100):
        chunk = bare[offset:offset + 100]
        try:
            entries = _atom({"id_list": ",".join(chunk), "max_results": len(chunk)})
        except _AtomUnavailable:
            return PartialRecords(found)
        for entry in entries:
            if "/abs/" in getattr(entry, "id", ""):  # error entries carry no paper
                paper = _entry_paper(entry)
                found[re.sub(r"v\d+$", "", paper["paper_id"])] = paper
    return found


def _search_web(query: str, limit: int) -> list[dict]:
    """Use arXiv's own HTTPS relevance search when its Atom service is unavailable."""
    page_size = next((size for size in (25, 50, 100, 200) if size >= limit), 200)
    papers = []
    seen = set()
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        while len(papers) < limit:
            pacing.wait('arxiv')
            response = access.get("https://arxiv.org/search/", kind="html", client=client, params={
                "query": query, "searchtype": "all", "abstracts": "show",
                "order": "", "size": page_size, "start": len(seen),
            })
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select("li.arxiv-result")
            if not cards and not soup.select_one("form[action='/search/'], #search-form"):
                raise ValueError("arXiv returned an unexpected search page")
            added = 0
            for card in cards:
                paper = _web_paper(card)
                if not paper or paper["paper_id"] in seen:
                    continue
                seen.add(paper["paper_id"])
                papers.append(paper)
                added += 1
                if len(papers) == limit:
                    break
            if not added or not soup.select_one("a.pagination-next[href]"):
                break
    return papers


def _web_paper(card) -> dict | None:
    identity = card.select_one(".list-title a[href*='/abs/']")
    title = card.select_one("p.title")
    if not identity or not title:
        return None
    arxiv_id = identity["href"].split("/abs/")[-1]
    abstract = card.select_one(".abstract-full")
    if abstract:
        for control in abstract.select("a"):
            control.decompose()
    date_text = " ".join(p.get_text(" ", strip=True) for p in card.select("p.is-size-7"))
    date = None
    original = re.search(r"originally announced\s+(\w+ \d{4})", date_text)
    submitted = re.search(r"Submitted\s+(\d{1,2} \w+, \d{4})", date_text)
    try:
        if original:
            date = datetime.strptime(original[1], "%B %Y").strftime("%Y-%m")
        elif submitted:
            date = datetime.strptime(submitted[1], "%d %B, %Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    return {
        "paper_id": arxiv_id, "title": title.get_text(" ", strip=True),
        "authors": [a.get_text(" ", strip=True) for a in card.select(".authors a")],
        "abstract": abstract.get_text(" ", strip=True) if abstract else "",
        "year": int(date[:4]) if date else None, "venue": "arXiv",

         "is_open_access": True,
        "open_access_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "fields_of_study": [tag.get_text(strip=True) for tag in card.select(".tags .tag")],
        "publication_date": date,  "external_ids": {"ArXiv": arxiv_id},
        "url": f"https://arxiv.org/abs/{arxiv_id}", "source": "arxiv",
    }


def get_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
