"""Source registry for academic search APIs.

Each source registers its capabilities (search, citations, references).
The registry dispatches queries to appropriate sources based on availability
and priority, making it easy to add new sources without touching server.py.
"""

from dataclasses import dataclass, field
from typing import Callable

from . import config


@dataclass
class Source:
    name: str
    search: Callable | None = None
    get_citations: Callable | None = None
    get_references: Callable | None = None
    get_paper: Callable | None = None
    priority: int = 50
    domains: list[str] = field(default_factory=lambda: ["all"])
    requires_key: bool = False
    key_available: Callable | None = None

    def available(self) -> bool:
        if not self.requires_key:
            return True
        if self.key_available:
            return self.key_available()
        return True


_registry: dict[str, Source] = {}


def register(source: Source):
    _registry[source.name] = source


def get(name: str) -> Source | None:
    return _registry.get(name)


def all_sources() -> list[Source]:
    return sorted(_registry.values(), key=lambda s: -s.priority)


def search_sources() -> list[Source]:
    return [s for s in all_sources() if s.search and s.available()]


def citation_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_citations and s.available()]


def reference_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_references and s.available()]


def _register_defaults():
    from . import s2_client, arxiv_client, openalex_client, crossref_client
    from . import core_client, pubmed_client
    from . import europepmc_client, dblp_client, inspirehep_client

    register(Source(
        name="semantic_scholar",
        search=lambda q, limit, **kw: s2_client.search_papers(q, limit=limit, **kw),
        get_citations=s2_client.get_citations,
        get_references=s2_client.get_references,
        get_paper=s2_client.get_paper,
        priority=90,
        domains=["all"],
        requires_key=False,
        key_available=lambda: bool(config.get_s2_api_key()),
    ))

    register(Source(
        name="openalex",
        search=lambda q, limit, **kw: openalex_client.search_papers(q, limit=limit, **kw),
        get_citations=openalex_client.get_citations,
        get_references=openalex_client.get_references,
        get_paper=openalex_client.get_paper_by_id,
        priority=80,
        domains=["all"],
    ))

    register(Source(
        name="arxiv",
        search=lambda q, limit, **kw: arxiv_client.search_papers(q, max_results=limit),
        priority=70,
        domains=["computer science", "physics", "mathematics", "statistics"],
    ))

    register(Source(
        name="crossref",
        search=lambda q, limit, **kw: crossref_client.search_papers(q, limit=limit),
        priority=30,
        domains=["all"],
    ))

    register(Source(
        name="pubmed",
        search=lambda q, limit, **kw: pubmed_client.search_papers(q, max_results=limit),
        priority=40,
        domains=["medicine", "biology", "healthcare"],
    ))

    register(Source(
        name="europepmc",
        search=lambda q, limit, **kw: europepmc_client.search_papers(q, limit=limit),
        priority=35,
        domains=["medicine", "biology", "healthcare", "biochemistry"],
    ))

    register(Source(
        name="dblp",
        search=lambda q, limit, **kw: dblp_client.search_papers(q, limit=limit),
        priority=25,
        domains=["computer science"],
    ))

    register(Source(
        name="inspirehep",
        search=lambda q, limit, **kw: inspirehep_client.search_papers(q, limit=limit),
        priority=15,
        domains=["physics", "astronomy", "high-energy physics"],
    ))

    register(Source(
        name="core",
        search=lambda q, limit, **kw: core_client.search_papers(q, limit=limit),
        priority=10,
        domains=["all"],
        requires_key=True,
        key_available=lambda: bool(config.CORE_API_KEY),
    ))


_register_defaults()
