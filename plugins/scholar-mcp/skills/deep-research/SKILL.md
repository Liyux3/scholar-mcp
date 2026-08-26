---
name: deep-research
description: Conduct evidence-grounded literature reviews and technical field investigations with Scholar MCP. Use for iterative academic search, multi-paper claim verification, frontier mapping, benchmark comparison, or citation lineage tracing. Route ordinary paper lookup directly to Scholar MCP.
license: Apache-2.0
---

# Deep Research

The goal is not just to find facts, but to build an evolving, auditable
understanding of a field. Use Scholar MCP for academic discovery, identity
resolution, citation traversal, primary-text reading, and selected research memory.

## Choose the depth

| Mode | Starting scope |
|---|---|
| Focused verification | One bounded claim, one or two searches, a small set of decisive primary sources |
| Technical review | Complementary search views, five to eight inspected candidates, two or three expansion seeds |
| Frontier scan | Recent work, active groups, weak signals, counterevidence, and follow-up activity |

These are starting budgets. Walk one step, see what's there, then decide the next
step. Expand when a new branch can change the field map or conclusion; stop a branch
when it repeats known structure or drifts from the question.

## Scholar MCP routing

| Need | Tool |
|---|---|
| Check live coverage | Read `scholar://status` |
| Search literature | `search_papers`; preserve the natural-language question and select an intent only when it changes ranking |
| Recover surveys, foundations, recent work, methods, or datasets | `search_papers` with `intent=survey`, `foundational`, `recent`, `method`, or `dataset` |
| Inspect a paper | `paper_info` with the needed parts of `detail,citations,references` |
| Find topical neighbors | `recommend_papers` with `relation=similar` |
| Find intellectual peers | `recommend_papers` with `relation=peers` |
| Cross vocabulary boundaries | `recommend_papers` with `relation=kin` |
| Build a lineage | Resolve stable seed IDs, then use `build_paper_graph` |
| Read primary evidence | `read_paper`; continue with `next_start` when the relevant section extends beyond one chunk |
| Retain a PDF | `download_paper` |
| Curate durable evidence | `paper_library` for selected papers, notes, tags, collections, and vault export |

Scholar routes raw, short, and compressed query forms to different source types.
Keep the original question intact. Use source filters when the research question
justifies them. Read `_meta.source_coverage`, `_meta.reranker`, and degradation
details before interpreting an empty or thin result. Enable `debug=true` when
source-level yield, latency, or ranking provenance changes the diagnosis.

## Adaptive research loop

### 1. Frame

- State the decision or understanding the research should enable.
- Separate technical subquestions, boundary conditions, timeframe, and assumptions.
- Define what evidence would support or overturn each important claim.

### 2. Map

- Start with two or three complementary views, each centered on a distinct mechanism,
  dataset, evaluation setting, or counter-position.
- Build a field map, not just a source summary. Recover field vocabulary, major
  approaches, canonical works, datasets, benchmarks, active groups, and disagreements.
- Let each source update the next search.
- Include the middle zone: recent consequential work whose citation signal is still
  forming.

### 3. Inspect

- Resolve promising papers through DOI, arXiv, OpenAlex, S2, or other stable IDs.
- For papers that affect the conclusion, inspect the exact claim, mechanism,
  assumptions, training and evaluation setup, strongest evidence, and failure modes.
- When something matters, push into the modeling choices, actual math, implementation
  details, requirements, and what breaks when an assumption changes.
- Compare results only under compatible datasets, splits, metrics, baselines,
  model sizes, compute, and evaluation protocols.

### 4. Expand selectively

- Follow references to recover foundations and citations to recover descendants.
- Use similar papers, co-citation peers, and bibliographic kin when they add a distinct
  route into the question.
- Build a graph after the seeds are resolved and relevant. Treat graph position as a
  lead for inspection, while evidence remains in the underlying papers.
- Search explicitly for limitations, replications, rebuttals, negative results, and
  the strongest counter-position. Each anomaly, absence, or contradiction is a lead.

### 5. Consolidate

- Update the vocabulary, field structure, claims, counterevidence, and open questions
  after each meaningful round. Ask what surprised you, what should exist but does not,
  where sources disagree, and which unknown could still overturn the conclusion.
- Record why a decisive paper matters and why close alternatives were rejected.
- Save selected evidence to `paper_library`; keep the unreviewed candidate pool transient.
- Important cognitive work stays with the main researcher, who inspects the evidence
  that determines the conclusion.

### 6. Synthesize

- Lead with the answer to the actual question.
- Explain the field structure, development lineage, and meaningful disagreements.
- Separate observed facts, source claims, inferences, and recommendations.
- Put citations beside the claims they support and preserve stable identifiers and URLs.
- State confidence, residual uncertainty, and the evidence that would resolve it.

## Evidence state

Maintain one compact ledger while the investigation evolves:

```text
Question and decision
Field map and vocabulary
Paper and code ledger
Claims and supporting evidence
Counterevidence and boundary conditions
Timeline and lineages
Benchmark comparability
Anomalies and missing work
Confidence and open questions
Next searches
```

Prefer primary papers, official code, datasets, benchmarks, standards, and author
materials. Use abstracts and snippets for discovery, then read decision-relevant work
at mechanism level. Treat unusually clean gains, weak baselines, incomparable settings,
hidden filtering, and claims with little follow-up as leads for deeper inspection.
Timing is signal: track when an idea appeared, what it inherited, and what happened
afterward. Preserve uncertainty instead of forcing premature closure.

## Stopping and delivery

Stop when the major branches are stable, additional searches yield little novelty,
important claims are supported or bounded, and unresolved disagreements are explicit.

A research run delivers one cumulative artifact, grown while searching, when a writable
workspace is available. Otherwise maintain the same evidence state in context. Match
the final artifact to the chosen depth: concise for focused verification, broader for
technical reviews and frontier scans.
