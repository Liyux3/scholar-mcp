---
name: deep-research
description: Conduct long-horizon, evidence-grounded research with Scholar MCP. Use for literature reviews, field maps, recent-frontier analysis, technical landscape studies, claim verification, paper lineage tracing, benchmark comparison, or any investigation that requires primary academic evidence and iterative search.
license: Apache-2.0
---

# Deep Research

Build an evolving understanding of the question. Go beyond paper summaries into framing, source judgment, mechanism-level reading, contradiction analysis, and synthesis. Use Scholar MCP as the academic retrieval and citation-tracing specialist.

## Operating principles

- Prefer primary papers, official code, datasets, benchmarks, standards, and author materials.
- Separate observed facts, source claims, inferences, and recommendations.
- Preserve identifiers and URLs so every important claim can be retraced.
- Seek disconfirming evidence with the same effort as confirming evidence.
- Judge results in their actual setup: dataset, split, metric, baseline, compute, model size, and evaluation protocol.
- Treat timing as evidence. Track when ideas appeared, what they inherited, and what happened afterward.
- Look for anomalies, missing comparisons, abandoned ideas, and contradictory results. Each is a search lead.
- Read decision-relevant papers at mechanism level. Use abstracts and snippets for discovery, then verify decisive claims in the primary material.
- Scale conclusions to the breadth and quality of the evidence.
- Keep final understanding with the main researcher. Agents may collect material; the main researcher inspects the evidence that determines the conclusion.

## Choose the research depth

- **Focused verification:** answer one bounded claim with a small set of decisive primary sources.
- **Technical review:** map approaches, lineages, benchmarks, and disagreements around a defined question.
- **Frontier scan:** emphasize recent work, weak signals, active groups, follow-up evidence, and claims that remain unsettled.

Match the search breadth and artifact size to the request. Keep focused verification bounded; give technical reviews enough breadth to establish the field structure.

## Scholar MCP tool routing

| Need | Tool and approach |
|---|---|
| Check coverage | Read `scholar://status` when source availability matters. |
| Map an unfamiliar field | Search the topic with `intent=survey`, `foundational`, and `recent`; merge the resulting vocabulary, works, and open questions. |
| Search literature | Use `search_papers`; preserve the natural-language question and apply year, venue, field, type, OA, and intent filters only when justified. |
| Inspect a paper | Use `paper_info` with `detail,citations,references`. Prefer DOI, arXiv, OpenAlex, or S2 identifiers over title matching. |
| Find topical neighbors | Use `recommend_papers` with `relation=similar`. |
| Recover foundations | Use `paper_info` with `references`. |
| Recover descendants | Use `paper_info` with `citations`. |
| Recover intellectual peers | Use `relation=peers` for co-citation. |
| Find methodological kin | Use `relation=kin` for bibliographic coupling across vocabulary boundaries. |
| Build a bounded lineage | Resolve stable seed IDs, then use `build_paper_graph`; inspect analytics and open bridge or pivot papers. |
| Read primary evidence | Use `read_paper` for temporary online reading. Use `download_paper` only when the PDF should remain on disk. |
| Curate durable evidence | Use `paper_library` to save selected papers, search collections, update notes/tags, or export a vault. |

`search_papers` routes raw, short, and compressed queries to different source types. Preserve the natural-language question and let the source adapters choose their query form. Read `_meta.source_coverage`, `_meta.reranker`, and `_meta.sources_unavailable`; distinguish low recall from throttling, timeout, missing optional credentials, and a genuinely empty result. Use `debug=true` only when source-level yield, latency, or ranking provenance changes the diagnosis.

## Dynamic field discovery

Treat field discovery as an evolving research workflow rather than one fixed query or citation threshold.

1. Search the natural-language question with balanced, survey, foundational, and recent intents where each view is relevant.
2. Build a small query portfolio around distinct mechanisms, datasets, evaluation settings, and counter-positions learned from the first results. Keep mechanisms separate instead of joining every term into one over-constrained query.
3. Inspect candidate papers through stable IDs. Record why each paper matters, what it changes, and what evidence could disconfirm it.
4. Follow references, citations, similar papers, co-citation peers, and bibliographic kin selectively. Stop a branch when it drifts from the question or repeats known structure.
5. Build citation graphs only after the seeds are resolved and relevant. Use the graph to expose bridges and lineages, then inspect the papers behind those positions.
6. Save only selected evidence to `paper_library`. Apply collections, notes, and tags deliberately; field mapping never auto-saves the raw candidate pool.
7. Stop when major branches are stable, further searches yield diminishing novelty, and open disagreements are explicit.

Keep the evolving state in the evidence ledger. This preserves every decision-relevant retrieval step while letting the investigation adapt its breadth and depth.

Keep optional API credentials inside the MCP runtime. Redact them from prompts, reports, logs, command arguments, and generated files. When an optional key is absent, continue with the available coverage and report the affected source briefly.

## Research loop

### 1. Frame

- State the actual decision or understanding the research should enable.
- Split it into technical subquestions, boundary conditions, and adjacent fields.
- Fix the timeframe and define what would change the conclusion.
- Record initial assumptions and label them as hypotheses.
- Define an evidence bar for each major claim: direct experiment, replicated result, formal derivation, official specification, or informed inference.

### 2. Map

- Recover the field vocabulary, major approaches, canonical works, active groups, datasets, benchmarks, and open problems.
- Build a rough lineage before optimizing the search for narrow terms.
- Search both canonical work and the middle zone: recent, consequential papers whose citation signals are still forming.

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

Examine unusually clean gains, weak baselines, incomparable settings, hidden filtering, cherry-picked subsets, and claims with little follow-up especially closely.

### 5. Read and probe

For papers that affect the conclusion, extract:

- The exact claim and its scope.
- Mechanism, assumptions, and mathematical or algorithmic definition.
- Training and evaluation setup.
- Strongest quantitative evidence.
- Failure modes and boundary conditions.
- Comparability to adjacent work.
- Omissions, unexplained choices, and contradictions.

Use citation count to locate influential work. Judge evidence quality from the work itself and the evidence that followed it.

Ask after each round:

- What surprised me?
- What is conspicuously absent?
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
- Compare methods under compatible settings and name incompatibilities explicitly.
- Put citations next to the claims they support.
- State residual uncertainty and the evidence that would resolve it.
- Highlight conclusions that changed during the investigation and why.

## Depth and stopping

Continue when the field map is shallow, important branches are missing, recent changes may alter the answer, credible sources conflict, or decisive claims lack primary evidence.

Stop when the major structure is stable, important searches yield diminishing novelty, key claims are supported or bounded, contradictions are explained or isolated, and the user can act on the result.

For a time-bounded run, reserve the final portion for consolidating the evidence state. Expand another branch only when it can still change the conclusion.

## Deliverable

When a writable workspace is available, grow one cumulative research artifact while searching. Otherwise maintain the same evidence state in context and return the synthesis directly.

Lead with the answer, then provide the field map or comparison, decisive evidence, counterevidence, uncertainty, and next actions. Include raw source links and compact excerpts where they materially improve auditability. Use verified bibliographic details and mark unresolved metadata explicitly.
