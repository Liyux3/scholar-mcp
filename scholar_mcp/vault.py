"""Obsidian-compatible markdown vault export for saved papers.

The JSONL knowledge base is good for querying and bad for everything else: it
cannot be browsed, annotated, or linked, and a human cannot open it. A vault
of markdown files is the opposite, and the two are cheap to maintain in
parallel since the JSONL stays the source of truth.

The design borrows three ideas from GitNexus's code-graph schema, which solves
the same problem for source code:

  one edge type with a `type` property rather than a table per relation, so
  new relations cost nothing;

  clusters as first-class nodes, since a network is unreadable but a grouped
  network is not;

  linear traces through the graph, because the one shape humans read fluently
  is a sequence.

A network has no good flat text representation. What works is projecting it:
for the agent, an adjacency list it can grep and walk; for the human, a tree
rooted wherever attention currently is. Both projections come from the same
files, which is what makes a vault better than a rendered diagram.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import s2_client

DEFAULT_VAULT_DIR = os.environ.get(
    "SCHOLAR_VAULT_DIR", os.path.expanduser("~/.scholar-mcp/vault"))

# Relation names shared with traversal.py, used as wikilink section headers.
RELATION_HEADINGS = {
    "foundations": "Builds on",
    "descendants": "Cited by",
    "peers": "Cited alongside",
    "kin": "Shares references with",
    "similar": "Semantically similar",
    "mentions": "Discusses",
    "mentioned_by": "Discussed by",
    "coauthored": "Shares authors with",
}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def vault_dir(collection: str = "default") -> Path:
    safe = _slug(collection) or "default"
    return Path(DEFAULT_VAULT_DIR) / safe


def note_name(title: str) -> str:
    """Filename stem for a paper, stable across runs.

    Obsidian resolves [[wikilinks]] by filename, so this doubles as the link
    target and must be derived from the title alone, not from any id that
    varies by source.
    """
    # Collapse whitespace first. Newlines and tabs are also control characters,
    # so stripping unsafe characters first would delete the word boundary and
    # turn "Deep Residual\nLearning" into "Deep ResidualLearning", quietly
    # breaking every wikilink to a paper whose title wrapped.
    name = _UNSAFE.sub("", _WHITESPACE.sub(" ", title or "")).strip()
    return (name[:120].rstrip(" .") or "untitled")


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text or "").strip("_")


def _link_key(title: str) -> str:
    """Identity key for deduplicating links across title spellings."""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _yaml_value(value):
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', "'").replace("\n", " ")
    return f'"{text}"'


def _frontmatter(paper: dict) -> list[str]:
    external = paper.get("external_ids") or {}
    fields = {
        "title": paper.get("title", ""),
        "year": paper.get("year"),
        "citations": paper.get("citation_count", 0),
        "venue": paper.get("venue", ""),
        "doi": external.get("DOI", ""),
        "arxiv": external.get("ArXiv", ""),
        "url": paper.get("url", ""),
        "authors": ", ".join(paper.get("authors") or [])[:300],
        "source": paper.get("source", ""),
        "added": date.today().isoformat(),
    }
    lines = ["---"]
    lines += [f"{k}: {_yaml_value(v)}" for k, v in fields.items()]
    # Tags make Obsidian's graph view and search useful without extra tooling.
    tags = ["paper"]
    if paper.get("_relation"):
        tags.append(f"via/{paper['_relation']}")
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    return lines


def render_note(paper: dict, relations: dict[str, list[dict]] | None = None,
                notes: str = "") -> str:
    """Render one paper as an Obsidian note.

    relations maps a relation name to the papers reached through it. Each
    becomes a section of wikilinks, which is what turns a directory of notes
    into a navigable graph.
    """
    lines = _frontmatter(paper)
    lines.append("")
    lines.append(f"# {paper.get('title', 'Untitled')}")
    lines.append("")

    abstract = (paper.get("abstract") or "").strip()
    if abstract:
        lines += ["## Abstract", "", abstract, ""]

    self_key = _link_key(paper.get("title", ""))
    for relation, papers in (relations or {}).items():
        if not papers:
            continue
        # Sources disagree on capitalisation and punctuation, so the same
        # paper can arrive under several spellings. Compare on a normalised
        # key to avoid a note linking to itself or listing one paper twice.
        seen = {self_key}
        links = []
        for related in papers:
            key = _link_key(related.get("title", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            strength = related.get("_relation_strength")
            suffix = f" ({strength}x)" if strength else ""
            links.append(f"- [[{note_name(related['title'])}]]{suffix}")
        if not links:
            continue
        heading = RELATION_HEADINGS.get(relation, relation.replace("_", " ").title())
        lines += [f"## {heading}", ""] + links + [""]

    # Everything below stays untouched on re-export so hand-written notes and
    # Obsidian's own backlinks survive a refresh.
    lines += ["## Notes", "", notes.strip() if notes else "", ""]
    return "\n".join(lines)


NOTES_MARKER = "## Notes"


def write_note(paper: dict, collection: str = "default",
               relations: dict[str, list[dict]] | None = None) -> Path:
    """Write or refresh a paper's note, preserving anything under ## Notes."""
    directory = vault_dir(collection)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{note_name(paper.get('title', ''))}.md"

    existing_notes = ""
    if path.exists():
        _, _, tail = path.read_text(encoding="utf-8").partition(NOTES_MARKER)
        existing_notes = tail.strip()

    path.write_text(render_note(paper, relations, existing_notes), encoding="utf-8")
    return path


def write_index(papers: list[dict], collection: str = "default") -> Path:
    """Write a collection index, sorted by citation count.

    A flat list is the honest default view: any richer grouping (by subfield,
    by method lineage) needs clustering the vault does not have yet.
    """
    directory = vault_dir(collection)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "_index.md"

    ranked = sorted(papers, key=lambda p: -(p.get("citation_count") or 0))
    lines = [
        "---", f'collection: "{collection}"', f"papers: {len(papers)}",
        f'updated: "{date.today().isoformat()}"', "tags: [index]", "---", "",
        f"# {collection}", "",
        f"{len(papers)} papers. Open the graph view to see how they connect.", "",
    ]
    for paper in ranked:
        year = paper.get("year") or "n.d."
        cites = paper.get("citation_count") or 0
        lines.append(f"- [[{note_name(paper.get('title', ''))}]] ({year}, {cites}c)")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# A title that appears in this many other abstracts is not being cited, it is
# the name of the field. In a 451-paper RAG collection, "Retrieval-Augmented
# Generation" matched 128 abstracts and "Retrieval-Augmented Generation (RAG)"
# matched 112, while every genuine mention matched exactly one. The gap is a
# cliff, not a gradient, so the threshold does not need to be delicate.
MAX_MENTION_HITS = 5

# Below this many words a title is a phrase rather than a name, and phrases
# recur in prose for reasons that have nothing to do with citation.
MIN_MENTION_WORDS = 5

# Authors this prolific stop indicating a real connection between two papers.
# A survey with two hundred authors would otherwise link to everything.
MAX_AUTHORS_FOR_COAUTHOR_EDGE = 30


def build_internal_relations(papers: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Find relations among the papers in a collection, without calling any API.

    A collection is not a random sample: the user saved these papers because
    they belong together, so the connections between them are the ones worth
    drawing. Two are readable straight off the stored metadata.

    A paper whose abstract names another paper's title is discussing it, which
    is a citation in all but form and is directional. Shared authorship is
    weaker but genuine, and it links work that shares no vocabulary.

    Mention edges are rarer than they sound. Authors cite by number or by
    "Lewis et al.", not by copying a full title into their abstract, so almost
    the only titles that match are the ones that are really field names. Those
    are filtered out rather than allowed to dominate the graph.

    Returns {title: {relation: [papers]}}, shaped for export_collection.
    """
    relations: dict[str, dict[str, list[dict]]] = {}

    def add(title: str, relation: str, other: dict) -> None:
        relations.setdefault(title, {}).setdefault(relation, []).append(other)

    by_title = {p["title"]: p for p in papers if p.get("title")}
    candidates = {t.lower(): t for t in by_title
                  if len(t.split()) >= MIN_MENTION_WORDS}

    abstracts = [(p.get("title", ""), (p.get("abstract") or "").lower())
                 for p in papers if p.get("title")]

    hits: dict[str, list[str]] = {}
    for lowered, other_title in candidates.items():
        matched = [title for title, text in abstracts
                   if text and title != other_title and lowered in text]
        if matched and len(matched) <= MAX_MENTION_HITS:
            hits[other_title] = matched

    for other_title, mentioning in hits.items():
        for title in mentioning:
            add(title, "mentions", by_title[other_title])
            add(other_title, "mentioned_by", by_title[title])

    authors: dict[str, list[str]] = {}
    for paper in papers:
        title = paper.get("title", "")
        author_list = paper.get("authors") or []
        if not title or len(author_list) > MAX_AUTHORS_FOR_COAUTHOR_EDGE:
            continue
        for author in author_list:
            authors.setdefault(author, []).append(title)

    linked: set[tuple[str, str]] = set()
    for titles in authors.values():
        for i, first in enumerate(titles):
            for second in titles[i + 1:]:
                if first == second or (first, second) in linked:
                    continue
                linked.add((first, second))
                linked.add((second, first))
                add(first, "coauthored", by_title[second])
                add(second, "coauthored", by_title[first])

    return relations


def build_citation_relations(papers: list[dict], max_workers: int = 8) -> dict:
    """Find which papers in the collection cite which others.

    Costs one request per paper, so this is opt-in, but it is the only source
    of a real graph. Stored metadata yields 26 edges across 71 papers; their
    actual reference lists yield 169. The rest of a note's neighbours simply
    are not visible without asking.
    """
    relations: dict[str, dict[str, list[dict]]] = {}
    by_key = {_link_key(p["title"]): p for p in papers if p.get("title")}

    def references_of(paper: dict) -> tuple[str, list[dict] | None]:
        pid = paper.get("doi") or paper.get("paper_id") or ""
        if not pid:
            return paper.get("title", ""), None
        try:
            return paper.get("title", ""), s2_client.get_references(pid, limit=100)
        except Exception:
            return paper.get("title", ""), None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for title, references in pool.map(references_of, papers):
            if not references or not title:
                continue
            for reference in references:
                cited = by_key.get(_link_key(reference.get("title", "")))
                if not cited or cited["title"] == title:
                    continue
                relations.setdefault(title, {}).setdefault(
                    "foundations", []).append(cited)
                relations.setdefault(cited["title"], {}).setdefault(
                    "descendants", []).append(by_key[_link_key(title)])
    return relations


def export_collection(papers: list[dict], collection: str = "default",
                      relations_by_title: dict[str, dict] | None = None,
                      link_internally: bool = True,
                      link_citations: bool = False) -> dict:
    """Export a whole collection to markdown. Returns a summary.

    link_internally adds relations readable from stored metadata, at no
    network cost. link_citations additionally resolves each paper's reference
    list, which is slower but is what actually connects the graph: without it
    most notes stay isolated and the graph view is a cloud of unlinked dots.
    """
    relations_by_title = dict(relations_by_title or {})

    discovered: dict[str, dict[str, list[dict]]] = {}
    if link_internally:
        discovered = build_internal_relations(papers)
    if link_citations:
        for title, found in build_citation_relations(papers).items():
            merged = discovered.setdefault(title, {})
            for relation, related in found.items():
                merged.setdefault(relation, []).extend(related)

    for title, found in discovered.items():
        merged = dict(relations_by_title.get(title) or {})
        for relation, related in found.items():
            merged.setdefault(relation, []).extend(related)
        relations_by_title[title] = merged

    written = edges = 0
    for paper in papers:
        title = paper.get("title", "")
        if not title:
            continue
        relations = relations_by_title.get(title)
        write_note(paper, collection, relations)
        written += 1
        edges += sum(len(v) for v in (relations or {}).values())
    write_index(papers, collection)
    return {
        "collection": collection,
        "notes_written": written,
        "links_written": edges,
        "vault_path": str(vault_dir(collection)),
    }
