# Architecture

Scholar MCP connects an agent's research question to papers, citation relationships,
full text, and a persistent research library. This is the developer reference; the
[site](https://liyux3.github.io/scholar-mcp/architecture/) explains the same design
for users.

## Retrieval

```text
Research question
    ↓
Source-specific query forms (raw sentence, keywords, short keywords)
    ↓
One lane per source, lanes in parallel → shared paper identities and merged metadata
    ↓
First relevance ranking → select expansion seeds
    ↓
Follow references, citations and related work; late source answers join here
    ↓
Rerank the combined candidates → apply relevance and metadata ordering
    ↓
Papers with identifiers for reading, graph exploration and saving
```

Keyword-oriented sources receive a compact query, sized per source where
measurements found an optimum (`keyword_words`); semantic sources receive the
original question. Source adapters return supported record fields; the shared output boundary supplies
unknown values for clients. `records.py` owns identifier normalization, authors,
publication versions, compatible identity joins and provenance. It uses the
standard library and has no model, network or MCP dependency. Ranking and storage
use that same record contract.

Interactive search shares a 40-second acquisition window across source fan-out,
expansion and metadata work, with headroom for final scoring toward a 50-second
response target. Completed records remain available when a later stage reaches
that window. This is a latency target, with deployment and load affecting the
observed tail. CUDA uses the larger close reader; MPS and CPU use the lightweight
MiniLM reader for interactive work. Every acquired candidate is still scored
before the final output cut.

The first ranking selects promising seeds. Expansion adds connected papers, and
the final ranking scores the combined candidates together. It does not average
scores from different model passes.

Provider batch limits do not cap the candidate pool. Large pools are scored in
batches, then ranked globally using one provider per pass. Sparse reference
records are resolved through DOI/PMID batches, native identifiers and citation
matching before ranking. Metadata lookups are not counted as extra search votes.
The output limit is applied after complete-pool scoring, composite ranking and
requested filters. arXiv DOI aliases and author-backed redeposit matches keep
one work from occupying multiple result slots; conflicting titles are checked
against native identifier records.
Title-based joins reject conflicting known authors. Different DOI records
without compatible author evidence remain separate, and a weak title-only hit
cannot join two mutually incompatible works. Identified records are considered
first while output order retains each group's first occurrence.

```text
Source records and citation references
    ↓
Normalize DOI, arXiv versions and identifier aliases
    ↓
Match shared identifiers, then compatible exact titles
    ↓
Merge metadata and retain independent source provenance
    ↓
Resolve missing titles and conflicting records only
    ├─ DOI → OpenAlex batches → exact Crossref fallback
    ├─ PMID → Europe PMC batches
    ├─ OpenAlex work ID → native work batches
    ├─ arXiv → native record
    └─ citation text → validated bibliographic match
    ↓
Rank and follow connections
    ↓
Enrich incomplete CorpusId records when S2 is healthy
    ↓
Merge newly linked identities → filters → final results
```

Identity matching and merging are local work. Each completed source starts its
metadata work while slower sources are still retrieving, on a pool of eight
hydration orchestrators. DOI and PMID batches also run concurrently with native
arXiv and citation lookups, using shared bounded workers and coalesced caches.
Exact identifiers and bibliographic fallback use separate bounded I/O pools.
This keeps a slow citation match from holding the displayed shortlist behind
it. Available cached matches and misses finish before entering that queue.
Already usable records can enter ranking while the remaining metadata resolves;
later snapshots keep the source's original positions. Only unresolved records
and fields needed by an explicit filter trigger this work. S2 snippet enrichment
remains low priority. Year, venue, field, citation,
publication-type and open-access filters are applied to the combined pool before
the output limit, including expansion candidates. Unknown type or access status
cannot satisfy an explicit strict filter.

`sort` selects the final ordering: relevance, citation count, or date.
`intent` guides semantic reranking and expansion toward a research purpose such
as foundational work, recent work, methods, surveys, or datasets.
The default is relevance ordering with balanced intent.

## Sources and scheduling

The source registry (`sources/__init__.py`) declares each source's search, paper lookup,
citation, reference and PDF capabilities, its query form and its group (index,
semantic, fulltext, preprint, discipline, repository, regional, dataset, web).
An adapter supplies only what its provider supports. Adding a source does not
require another MCP tool.

```text
           search          citations / references        PDF resolution
              \                    |                            /
               +---------  SourceFlow (one lane per source)  ---+
                           bounded shared pool, one deadline
                           per call, timeouts kept, late answers
                           merged, every answer recorded for health
                                      |
              provider clients (sources/<name>.py): every request first
              asks pacing.wait(provider, key) for its start time
                                      |
              pacing.LIMITS: N requests in any T seconds per provider,
              without and with a key; one slot per identity (the address,
              or the key) in <data>/pace/, shared by every Scholar process
```

`SourceFlow` runs every registry operation. Each source gets one lane in a
shared bounded pool; a lane makes its calls one at a time, in order, so the
provider sees exactly the requests sequential work would send, while different
sources run side by side and a slow source holds only its own lane. `take()`
collects what has arrived when the deadline passes (`SCHOLAR_SOURCE_BUDGET_S`
per call, 30 seconds by default). A lane still out keeps its request, and a
later `take()` before the final merge adds its papers and replaces its timeout
report. Web pagination keeps running while API seeds are ranked and expanded,
then joins the final scoring pool. Keyword compression runs in the keyword
lanes, so loading the optional model does not delay semantic API requests.
The optional keyword model is initialized once across concurrent callers and
uses installed assets first. Unavailable assets leave plain keyword extraction
working, with a later retry rather than repeated downloads in every lane.
Seed title searches in expansion share one flow and one round budget rather
than multiplying the full wait by the number of seeds; citation and
reference lookups and PDF resolution use the same class with one call per
lane, so they also stop at the deadline instead of waiting on a hung provider.
Nested expansion flows publish completed records before their slowest provider
finishes. Partial expansion is reported separately from initial search coverage.
Peer records retain authors, abstracts, native identifiers and relation strength;
merging a repeated snapshot does not count its relation support twice.

Completed registry responses are saved atomically in `<data>/cache.sqlite3`:
registry calls for five minutes; native clients retain their own lookup TTLs.
The cache holds at most 500 entries and 16 MB of response payloads, uses
owner-only file permissions on POSIX, and tolerates unavailable storage.
Repeating an interrupted request reuses its completed source calls and retries
the failed ones. Google also checkpoints each validated result page, so a later
request can continue after completed pages even across a process restart. Partial
responses remain marked partial and are never cached as complete searches.
Exact metadata misses use a short, in-memory 30-second cache. Within one tool
operation, explicit lookup misses are shared across worker threads and retained
through the final merge, so a long expansion cannot trigger the same failed
lookup again merely because that short TTL expired.
Metadata batches use canonical identifier sets, so ranking cannot create a new
lookup by changing their order. Interrupted batches retain their completed
records and remain eligible for recovery. Abstract-page recovery fills the
identity, author, date and summary fields that the page supplies.

Every request a client sends first asks `pacing.wait(provider, key)` for a
start time, so a search lane, a citation lookup and metadata completion obey the
same limits. The limits sit in one table, `pacing.LIMITS`, taken from the
providers' published terms; where a provider publishes nothing, one request per interval keeps
us polite. A limit reads N requests in any T seconds, and a provider may have
several, such as CORE's ten a minute and a hundred a day without a key.
Providers count per identity, the calling address without a key and the key
when one is sent, so a keyed request goes to that key's own slot under the
key's limits, and a pool of keys (Semantic Scholar, OpenAlex) spreads its load
over several slots. Metadata completion asks arXiv for up to a hundred records
in one request.

A slot is a file under `<data>/pace/` holding the identity's recent start
times, shared by every Scholar process on the machine. A caller reserves the
earliest start that keeps every limit under a file lock and sleeps outside it.
When that start is further off than the caller will wait (30 s by default, 6 s
for Semantic Scholar during expansion), nothing is reserved and `Paced` is
raised. The lane reports `paced`, which health reads as the provider's limit at
work rather than a failure, and a spent hourly or daily budget skips the source
instead of stalling the search.

Limits belong to an address or a key, not to a process, which decides how
Scholar scales. Agents on one machine already share the slots. Machines behind
one address, as in a lab or a company, should share one Scholar server
(`scholar-mcp serve --http`): one set of slots, one cache and one ranking model
then serve every agent, and on a machine with a CUDA GPU that server can rank
with a larger model in under a second. Past one server, the slots move to a
shared store such as Redis; `_reserve` is the whole contract (append, in one
atomic step, the earliest start that keeps every limit), which a short
server-side script can honour.

Routing is free: `applies(intent, text)` keeps a source out before any request.
Zenodo joins only `intent="dataset"`, and CiNii only questions containing
hiragana or katakana. Twenty sources form the full fleet. Opt-in sources (NASA
ADS, Springer Nature, Web of Science Starter, Lens) join only when their key is
set and do not count toward it; when keys are missing, the remaining sources are
asked for proportionally more results, capped at 300.

Crossref supplies deposited references; Europe PMC supplies citation relations
and can recover references from its own open-access JATS full text. INSPIRE-HEP
provides native citation traversal and batched reference records. All feed the
same graph traversal as Semantic Scholar and OpenAlex. Cached requests are
namespaced by provider implementation and shared across concurrent callers. A
failed request is recorded apart from a search that found nothing, and error
text never carries request URLs, which for several providers hold the key.

ERIC joins education searches with its native authors, year, venue and DOI.
OpenEdition supplies journals and books through its native API, with explicit
chapter identity and publication year, excluding blogs and event notices.
CourtListener joins explicit US case-law tasks; ClinicalTrials.gov joins trial
registration and protocol tasks. These routed sources leave ordinary paper
searches and their page sizes unchanged. Native IDs go directly to their own
getter. Court opinions retain courts, case numbers and legal citations; trial
registrations retain study design, planned outcomes and posted-result status.
They remain distinct from articles, and missing article authors never trigger
unrelated metadata enrichment. Trial-linked PMID papers enter the shared
reference hydration and graph flow. Health probes use a known native fixture
for each specialist source.

arXiv can fall back from Atom to its own HTTPS search and abstract pages. An
unavailable Atom endpoint is retried after five minutes; the source continues
through its website during that interval. Native fallbacks run before generic
browser recovery. DBLP retains its verified
HTTP session and handles bounded same-origin verification through either HTTP
Refresh headers or HTML meta tags. Google queries and the isolated HTTP worker share one client factory. The worker
uses it without importing the provider fleet; HTTP cookies remain local to each
client. The optional Google recovery worker runs
in a separate process and reuses a private browser profile bound to its route,
browser and mode. HTTP keeps its own cookie scope; verified browser pages are
parsed directly instead of replaying browser cookies in a different transport.
Recovery starts headless; macOS can retry a declined challenge in a hidden CDP
page. Chromium versions that require an existing frame get a temporary,
already-minimized bootstrap window, closed immediately after the hidden target
is created. Only `SCHOLAR_GOOGLE_RECOVERY=headed` permits a normal visible window.
Verification follows DOM states, and a failed challenge leaves no artificial
retry freeze. Missing optional browser dependencies keep the server usable.
The search acquisition window bounds recovery; completed pages remain reusable.

### Host access

`SCHOLAR_ACCESS_COMMAND` optionally names a host-owned executable as a JSON argv
array. It is invoked directly, without a shell. Each invocation receives one JSON
request on stdin and returns one JSON receipt on stdout. Browser engines and
credential storage remain with that host; the Scholar package adds no required
browser dependencies.

```json
{"op":"fetch","url":"https://example.org/resource","kind":"json","anonymous":true,"out":"/private/temp/response","timeout":30}
```

Google Scholar follows the configured `SCHOLAR_GOOGLE_PROXY`, then inherited
proxy settings. The explicit value `direct` uses a direct connection for this
source without changing the other providers' routes. Browser profiles and saved session metadata are isolated by route. HTTP and
browser cookies stay in their own transports.

The host writes response bytes to the new absolute `out` path. `kind` is `json`,
`xml`, `html` or `pdf`; PDF requests use authorized host identity. A successful receipt
contains `ok: true`, and can include `status`, `content_type` and `final_url`.
Failures return `ok: false` and an `error` object with `code` and `detail`.
`AUTH_REQUIRED` and `CHALLENGE` report blocked access, `RATE_LIMITED` reports
pacing, and `TIMEOUT` remains a timeout. A partial positive response is not cached
as a complete search. An optional `error.retry_after` in seconds is shared
across server processes, preserving the host's own recovery policy.

Provider GETs share a keep-alive transport and host recovery through `access.get`.
Public JSON/XML APIs and HTML pages can recover through the host; a recently
recovered host reuses that route directly, and can return to native HTTP if it
fails. API credentials, provider status handling and quota gates stay native.
Google Scholar uses its existing session-aware client with the same host
boundary; native verification shares one browser-owner lock and route-bound
session state with the host adapter. Google can use its browser session for HTML and pagination,
and PDF acquisition can try the best known location after ordinary archive and
institutional routes. All PDF bytes still pass the common validation and atomic
store. This does not grant subscriptions or make an expired institutional login
valid; the host must already have, or obtain, authorized access.

Keys come from the process environment and from `<data>/keys.env`, which
`scholar-mcp keys set NAME` writes with owner-only permissions. The file is read
once at import and never overrides a variable the environment already sets, so a
client's `env` block or the shell wins.

## Ranking models

DashScope `qwen3-rerank` is the configured cloud default. `DASHSCOPE_HTTP_BASE_URL`
selects the workspace/regional API base, and `SCHOLAR_RERANK_MODEL` selects the
model. Compatible and native Alibaba envelopes use the same HTTP transport,
batching and score validation, with no additional SDK or provider hierarchy.
After a connection failure, rejected key or account arrearage the remote reranker is paused for
five minutes and the local model ranks, so an unreachable endpoint costs one 10-second
connect timeout instead of the full request timeout on every pass. A compatible
hosted or local reranker can be selected through `SCHOLAR_RERANK_URL`; FlashRank
provides the portable local fallback when the `rerank` extra is installed. It reads
the whole pool at 128 tokens, then rereads the top 100 at 384, which holds the
title and whole abstract for most papers. When torch can reach a GPU (MPS on
Apple silicon, where the extra installs torch, or CUDA where the user added it),
the same MiniLM checkpoint skims there in half precision, about ten times faster
with the same order (Spearman 0.99 against the quantized ONNX), and a stronger
model reads the leaders: GTE-ModernBERT (`Alibaba-NLP/gte-reranker-modernbert-base`,
149M, Apache 2.0) over the top 300 instead of 100. On 73 frozen real Scholar pools
that lifted nDCG@10 from 0.248 to 0.330 with each collection held out; six of seven
collections gained, and maths (eight questions whose gold is a cited review's
references) lost 0.05. Its scores carry their own fusion weights (`MODEL_RANK_PARAMS`:
citations 0.02 instead of 0.05, no recency term), since a sharper reader needs
less help from citations; with the defaults, citations pushed a new paper an
agent named below older neighbours. A GPU that cannot load GTE reads with MiniLM
for the rest of the process; any GPU error sends skimming back to ONNX on the CPU.
MiniLM's own scores are cooled before fusion (temperature 8 on the logit, also in
`MODEL_RANK_PARAMS`): its probabilities sit near 1 for every plausible candidate,
so citations used to decide among them and a named new paper sank. Chosen on the
first scenario suite and tested on the second, it raised nDCG@10 from 0.416 to
0.563, nearly all of it on searches for one known paper.
Cached GPU model/tokenizer files load locally first. A missing checkpoint can
trigger the initial public download; an installed checkpoint can load and score
with network connections disabled.
Custom endpoints use a separate credential and do not silently fall back to the
cloud. `SCHOLAR_RERANK_BATCH_SIZE` adapts their per-request capacity without
reducing the candidate pool. The built-in Qwen adapter also budgets estimated
request tokens; payload-size rejections split the batch on the same model.
Scores must be comparable across batches from that model; listwise or
batch-normalized endpoints require a different adapter contract. See
[Configuration](https://liyux3.github.io/scholar-mcp/configuration/#reranker)
for the request contract.

Account and credential rejections retain their pause across server restarts,
scoped to a digest of the endpoint, model and credential. A replacement credential can
be tried immediately. Local passes reuse only identical query/document inputs
at the same model and token length, before close reading or composite ranking
changes scores. This avoids rescoring the unchanged seed pool in the final pass.

Reranker responses are validated before candidate scores are updated. Final
relevance ordering also considers citations, source agreement and recency.
These are ranking signals, not guarantees about a paper's quality.

## Reading and keeping papers

`read_paper` resolves a PDF, reads it in temporary storage and cleans up the
temporary file. The default returns pages 1-10 as page-aware Markdown, up to
100,000 characters per response, which keeps a typical ten-page read whole; a
longer range stops at a page boundary and returns `next_pages`. The tool
declares its result size to Claude Code, which otherwise saves results over
50,000 characters to a file; clients with a smaller known limit, such as Codex,
get a read budget that fits it, and the installer raises Codex's per-tool output
limit. An explicit page range reaches later sections, while figure, table and
page selectors support focused visual inspection. Recoverable tables are
returned as structured text.

If no usable PDF is available, the reader can consume PMC's original JATS.
The provider acquires an exact PMCID/version and checks its DOI, declared asset
checksum and XML identity. The pure reader preserves section IDs, references,
tables and equations, and returns source-part continuation locators. PDF page
fields stay null. A requested figure uses its declared dataset asset or its
exact first-party article image, with URL, digest and pixel dimensions.


A paper usually exists in several copies, and they differ in version: the
publisher's version of record, an accepted manuscript in a repository, a
preprint. `download_paper` finds every known copy at once (the record's open
link, arXiv, preprint servers, the PMC open-access dataset on AWS, the
registered repositories, and every Unpaywall location with the version it
states) and downloads them side by side, at most six at a time. The best
version wins: once a copy arrives, better versions still downloading get 15
seconds more, then the best copy so far is kept. Each copy lands under its own
request-owned staging name. The chosen copy gets an immutable version/hash
path, so upgrading a paper preserves an older PDF and its annotations. Copy
receipts live in the existing SQLite library independently of collection entries;
temporary reads can reuse retained copies without retaining a new file. The
result names the delivered version. Repository hostnames alone do not establish
a version: undeclared copies remain `unknown`.

The institutional routes, the Elsevier Article API when a key is set and the
library proxy, can supply a version of record when the paper is published. They join the race only
when no free copy is the published one, and fail silently, so a free preprint
still arrives when the library session has expired. An upgrade over a free
copy takes a library slot only if one is free at once (`library-proxy` in
`pacing.LIMITS`: one request every 3 s, 20 an hour, 150 a day), because
publishers flag systematic downloading through a proxy and can suspend the
account or the whole institution; when the library is the only route, the
read may queue for a slot. When a free published copy exists but fails to
download, the institutional routes follow. Host access can also upgrade an available manuscript; the opt-in Sci-Hub route
remains a last resort when no copy was obtained. The library proxy uses plain HTTP
with the configured login prefix and the user's exported, proxy-scoped cookies.
Expired sessions and publisher browser checks are named in the response, which
also includes a library link the user can open. Linkinghub links are mapped to ScienceDirect. Every candidate streams through the same PDF validation and an
atomic write. Repository search results first pass the shared paper-identity
check: a matching native identifier, or a compatible title with the available
author, year and document-type evidence. Similar titles and conflicting
same-title records cannot supply another paper's PDF. This applies to Europe PMC,
DOAJ, OpenAIRE, HAL, Zenodo and CORE; changing the PDF cache namespace retires
previous unchecked candidates while preserving completed search work.

`download_paper` retains the PDF in the configured papers directory and indexes
it in a library collection unless indexing is disabled. The returned path is
absolute even when a caller supplies a relative directory, so the saved
attachment does not depend on the next session's working directory.

The library uses SQLite in WAL mode with FTS5 for persistent records and
full-text search. JSONL supports migration and snapshots. PDF attachments and
Markdown vaults remain separate files. Obsidian, Zotero and Notion integrations
project or synchronize library records through explicit connector operations,
and notes edited in a linked Obsidian file or in Scholar's Notion note are read
back on the next library read. Stored records retain the supplied author list
and full abstract. Repeated saves preserve distinct notes without appending
duplicates; an explicit annotation update replaces a note. Display previews are
not the library's authority.

## MCP surface

The core profile exposes six tools:

| Tool | Research task |
|---|---|
| `search_papers` | Discover papers from a question, topic or remembered idea |
| `paper_info` | Inspect a paper and follow citations or references |
| `recommend_papers` | Explore semantic neighbours, co-cited peers or shared references |
| `search_authors` | Find researchers and their profiles |
| `read_paper` | Read text and inspect figures or tables |
| `download_paper` | Keep a PDF and attach it to a collection |

The `research` extension adds `build_paper_graph` and `paper_library`. Every
tool asks Claude Code to load it upfront (`anthropic/alwaysLoad`) rather than
behind tool search, since all eight definitions together are about 2,800 tokens.
`scholar://status` exposes diagnostics and health findings as a resource.
Citation-graph PageRank uses one bounded implementation with dangling-node
redistribution; installing optional scientific packages does not change its
algorithm. NetworkX supplies the graph structure and betweenness analysis.

Tools return plain data. The MCP boundary renders it once as YAML text, with
multi-line text in literal blocks, plus structured content; the CLI and the
Python API receive the same dicts. Any identifier a tool returns, a paper URL or
an exact title resolves in every other tool; reads and downloads reuse the
merged record of a recent result before asking the sources again.

Normal search responses contain papers and a short warning when coverage or
ranking is degraded. `debug=true` adds per-source status and ranking provenance.
Bibliographic fields remain present independently of debug output.
Missing abstracts on the final shortlist reuse native-ID metadata lookup and
its cache; discarded candidates do not trigger extra summary lookups.

HTTP processes exit cleanly after `SCHOLAR_IDLE_EXIT` idle minutes (default 20,
0 disables), allowing a supervisor to reclaim model and runtime memory by
restarting them. `http_server.py` counts HTTP requests through the full ASGI
response and tool workers through their return, including disconnected clients.
Uvicorn checks the monotonic idle window once per second and drains accepted
requests without a shutdown deadline. Its normal lifespan shutdown runs before
the process returns with code 0. Stdio retains its existing lifecycle. Torch,
Transformers, FlashRank and KeyBERT load only when a ranking or compression path
needs them, so a restarted process starts with empty model caches.

## Health

Two views answer whether every link still works, both in `health.py`.

Real use leaves a record. Each MCP tool call writes one line to
`<data>/usage/calls.jsonl` (`usage_log.py`, rotated at 2 MB with one backup):
the tool, client, duration and answer size, and for searches and relations how
each source answered as `[status, ms, count, abstract share]`, the error of a
failed source, which reranker ranked and why it fell back, where a PDF came
from, and for an exception the deepest frame inside the package. No query text
is kept unless `SCHOLAR_USAGE_LOG=full`. `health.findings()` reads the record
and names what changed for the worse, each against the source's own history: a
source that started failing, kept meeting its own published limit, went silent
after returning papers, never returned any, lost its abstracts or slowed down; a reranker that fell back; reads that
found no PDF; a tool that raised. `scholar-mcp doctor` and `scholar://status`
show them.

A record only shows what was used, so `health.probe()` asks the rest
directly: each search source with a fixed query, each citation and reference
source with a paper they all index, the reranker with two documents, and the
PDF chain (an arXiv download, the PMC dataset listing, Unpaywall and the
repository resolvers). The arXiv check downloads its native PDF URL directly,
so a successful alternate repository cannot disguise that route's failure.
It asks only what real use has not shown working in the
last day, and never a link billed per request: Exa and a cloud reranker are
judged from real searches alone, so the check itself costs nothing. Every
fixture is known to have answers wherever it goes, so an empty answer is a
problem too, named apart from a failed request, and a link that has not
answered within 90 seconds counts as timed out instead of holding the run.
`scholar-mcp doctor --network` runs it and exits 1 on a problem;
`.github/workflows/health.yml` runs it daily on a clean machine with only free
keys, about one small paced request per provider, and opens or updates one
`source-health` issue when a probe fails.

## Code map

| Module | Responsibility |
|---|---|
| `server.py` | Tool implementations returning data, orchestration, identifier resolution and the MCP boundary |
| `api.py`, `cli.py` | Python API and command line over the same tool functions |
| `install.py` | Client registration with backups, atomic writes and an ownership ledger |
| `sources/` | The registry, `SourceFlow` and routing (`__init__.py`), provider pacing (`pacing.py`), one module per provider |
| `records.py` | Shared record identity, publication versions, author normalization and provenance merge |
| `relevance.py`, `expansion.py` | Query preparation, ranking and citation expansion |
| `output.py` | Shared YAML output for the CLI and MCP boundary |
| `metadata.py` | Automatic hydration of sparse records |
| `traversal.py`, `graph.py` | Citation relationships and graph construction |
| `pdf_utils.py`, `paper_reader.py` | The PDF chain, structured reading and visuals |
| `scholar_session.py` | Shared Google verification worker and session state |
| `credentials.py` | Scoped OS credential storage for external library connectors |
| `knowledge_base.py`, `library_store.py` | Persistent research records and search |
| `vault.py`, `library_connectors.py` | External library projections and synchronization |
| `usage_log.py`, `health.py` | The call record, findings and live probes |
| `config.py`, `cache.py` | Configuration, the key file and response caching |

See [Contributing](../CONTRIBUTING.md) for adapter and validation expectations.
