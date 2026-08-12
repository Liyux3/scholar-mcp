---
name: deep-research
description: Conduct long-horizon, evidence-grounded research with Scholar MCP. Use for literature reviews, field maps, recent-frontier analysis, technical landscape studies, claim verification, paper lineage tracing, benchmark comparison, or any investigation that requires primary academic evidence and iterative search.
license: MIT
---

# Deep Research

Build an evolving understanding of the question, not a pile of paper summaries. Own the framing, source judgment, mechanism-level reading, contradiction analysis, and final synthesis. Use Scholar MCP as the academic retrieval and citation-tracing specialist.

## Operating principles

- Prefer primary papers, official code, datasets, benchmarks, standards, and author materials.
- Separate observed facts, source claims, inferences, and recommendations.
- Preserve identifiers and URLs so every important claim can be retraced.
- Seek disconfirming evidence with the same effort as confirming evidence.
- Judge results in their actual setup: dataset, split, metric, baseline, compute, model size, and evaluation protocol.
- Treat timing as evidence. Track when ideas appeared, what they inherited, and what happened afterward.
- Look for anomalies, missing comparisons, abandoned ideas, and contradictory results. Each is a search lead.
- Read decision-relevant papers at mechanism level. Abstracts and snippets are discovery aids, not evidence substitutes.
- Do not infer broad consensus from a small result sample or from search ranking.
- Do not delegate the final understanding. Agents may collect material; the main researcher must inspect the evidence that determines the conclusion.

## Choose the research depth

- **Focused verification:** answer one bounded claim with a small set of decisive primary sources.
- **Technical review:** map approaches, lineages, benchmarks, and disagreements around a defined question.
- **Frontier scan:** emphasize recent work, weak signals, active groups, follow-up evidence, and claims that remain unsettled.

Match the search breadth and artifact size to the request. Do not turn a focused verification into an exhaustive survey, and do not answer a review request from a single search page.

## Scholar MCP tool routing

| Need | Tool and approach |
|---|---|
| Check coverage | Call `scholar_status` once. Record active and unavailable sources. |
| Map an unfamiliar field | Start with `discover_field`, then verify its important papers individually. |
| Search literature | Use `search_papers`; preserve the natural-language question and apply year, venue, field, type, OA, and intent filters only when justified. |
| Inspect a paper | Use `paper_info` with `detail,citations,references`. Prefer DOI, arXiv, OpenAlex, or S2 identifiers over title matching. |
| Find topical neighbors | Use `recommend_papers` with `relation=similar`. |
| Recover foundations | Use `relation=foundations` and inspect references. |
| Recover descendants | Use `relation=descendants` and inspect citations. |
| Recover intellectual peers | Use `relation=peers` for co-citation. |
| Find methodological kin | Use `relation=kin` for bibliographic coupling across vocabulary boundaries. |
| Trace a lineage | Use `build_paper_graph`, then open the bridge and pivot papers that affect the argument. |
| Read primary evidence | Use `read_paper` selectively for papers capable of changing the judgment. |

`search_papers` routes raw, short, and compressed queries to different source types. Do not pre-compress every query into one keyword string. Read `_meta.sources_used` and `_meta.sources_unavailable`; distinguish low recall from throttling, timeout, missing optional credentials, and a genuinely empty result.

Optional API credentials belong to the MCP runtime. Never print, quote, persist, or place credentials in prompts, reports, logs, command arguments, or generated files. A missing optional key means reduced coverage, not permission to invent or request a secret unnecessarily.

## Research loop

### 1. Frame

- State the actual decision or understanding the research should enable.
- Split it into technical subquestions, boundary conditions, and adjacent fields.
- Fix the timeframe and define what would change the conclusion.
- List initial assumptions as hypotheses, not facts.
- Define an evidence bar for each major claim: direct experiment, replicated result, formal derivation, official specification, or informed inference.

### 2. Map

- Recover the field vocabulary, major approaches, canonical works, active groups, datasets, benchmarks, and open problems.
- Build a rough lineage before optimizing the search for narrow terms.
- Search both canonical work and the middle zone: recent, consequential papers that have not accumulated obvious citation signals yet.

### 3. Retrieve iteratively

- Run broad searches first, then update queries using terminology learned from results.
- Use several query formulations when the question spans mechanisms, applications, and evaluation.
- Follow citations, references, semantic neighbors, co-citation, and bibliographic coupling.
- Track each search round and what new vocabulary or hypothesis it introduced.
- Preserve rejected candidates and the reason they were rejected when that decision may matter later.
- Search for the strongest counter-position explicitly. Queries such as failure, limitation, replication, rebuttal, negative result, and benchmark name often expose evidence missed by topical search alone.

### 4. Triage evidence

Prioritize each candidate by:

1. Relevance to the real question.
2. Evidence quality and reproducibility.
3. Novelty of mechanism or conclusion.
4. Importance in the lineage.
5. Follow-up evidence, implementations, criticism, or replication.

Be suspicious of unusually clean gains, weak baselines, incomparable settings, hidden filtering, cherry-picked subsets, and claims without later adoption.

### 5. Read and probe

For papers that affect the conclusion, extract:

- The exact claim and its scope.
- Mechanism, assumptions, and mathematical or algorithmic definition.
- Training and evaluation setup.
- Strongest quantitative evidence.
- Failure modes and boundary conditions.
- Comparability to adjacent work.
- What is omitted, unexplained, or contradicted.

Do not treat citation count as evidence quality. Use it to locate influential work, then inspect the work itself and the evidence that followed it.

Ask after each round:

- What surprised me?
- What should exist but does not?
- Where do credible sources disagree, and which setup difference explains it?
- Which apparently similar methods are actually solving different problems?
- What single unknown could still overturn the conclusion?

### 6. Connect and consolidate

Maintain a compact evidence state:

```text
Question and decision
Current field map
Paper and code ledger
Claims and supporting evidence
Counterevidence
Timeline and lineages
Benchmark comparability
Anomalies and missing work
Confidence by claim
Open questions
Next searches
```

For each important claim, record paper identifiers, URLs, relevant location or result, confidence, and whether it is published, inferred, or estimated. Update earlier judgments when new evidence changes the field map.

A compact claim ledger is usually enough:

```text
Claim | Evidence | Counterevidence | Scope | Confidence | Source IDs
```

### 7. Synthesize

- Answer the actual question directly.
- Explain the field structure and development lineage.
- Distinguish mature knowledge from tentative frontier signals.
- Compare methods only under compatible settings; name incompatibilities explicitly.
- Put citations next to the claims they support.
- State residual uncertainty and the evidence that would resolve it.
- Highlight conclusions that changed during the investigation and why.

## Depth and stopping

Continue when the field map is shallow, important branches are missing, recent changes may alter the answer, credible sources conflict, or decisive claims lack primary evidence.

Stop when the major structure is stable, important searches yield diminishing novelty, key claims are supported or bounded, contradictions are explained or isolated, and the user can act on the result.

For a time-bounded run, spend the final portion consolidating the evidence state rather than opening many weak new branches.

## Deliverable

When a writable workspace is available, grow one non-redundant research artifact while searching. Otherwise maintain the same evidence state in context and return the synthesis directly.

Lead with the answer, then provide the field map or comparison, decisive evidence, counterevidence, uncertainty, and next actions. Include raw source links and compact excerpts only where they materially improve auditability. Never fabricate bibliographic details; mark unresolved metadata instead.
