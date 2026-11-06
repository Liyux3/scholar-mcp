# Changelog

## 0.10.0

- New terms from this release: Scholar MCP is source-available under the PolyForm
  Noncommercial License 1.0.0 with additional terms. Personal research,
  universities, research institutes, government and nonprofits use it free of
  charge; companies need a commercial license (LICENSE-ADDITIONAL.md). Building a
  competing tool from its code, and training models on its code or documentation,
  are not licensed. Releases 0.9.0 and earlier remain Apache-2.0.
- A source whose connection hangs no longer holds up searches: connections get at
  most 10 seconds, and a host that cannot be reached is skipped for a while and
  then tried again.
- OpenAlex's daily allowance lasts about four times as long, because the
  citation-following step no longer spends it on seed titles.
- AGENTS.md tells coding agents the terms and what to offer instead, and the
  documentation describes what each stage does for you.

## 0.9.0

- Use Pi's native MCP configuration and agent-directory override, with the
  appropriate timeout units for native Pi and pi-mcp-adapter.
- Support CourtListener Token authentication and its default rolling quotas.
  Honor OpenAlex's explicit shared daily-budget reset across processes,
  avoiding sibling-key retries when the provider's client budget is exhausted.
- Register DeepSeek Harness through its native Cordis MCP plugin, preserving
  existing YAML and expressions. Add TRAE CLI 2.0 and MiMo Code through the
  shared TOML and OpenCode renderers, including their config-path overrides.
- The local ranking model uses far less memory (about 0.45 GB instead of 3.7 GB
  on a large search) and runs a little faster.
- Add a command line for research and setup: `search`, `info`, `related`,
  `authors`, `read`, `download` and `graph` call the same functions as the MCP
  tools; `install` registers Scholar in Pi, Claude Code, Claude Desktop, Codex,
  Cursor, VS Code, Gemini CLI, OpenCode, Antigravity, Windsurf, Kiro, Zed, Qwen Code,
  Kimi Code, Qoder CLI, CodeBuddy Code, iFlow CLI, MiniMax Code, Cline CLI, Kilo Code,
  Amp, GitHub Copilot CLI, Factory Droid, Junie CLI, Augment CLI, Warp and LM Studio with backups,
  atomic writes and dry-run diffs; `uninstall`, `doctor` (with `--network`
  probes), `update` and `serve` complete it. Running without arguments still
  serves MCP over stdio.
- Add `scholar_mcp.api`: the tools as Python functions returning dicts. Tools
  now return data and the MCP boundary renders YAML text plus structured
  content once.
- Accept every identifier Scholar returns, including OpenReview and
  Google Scholar ids, plus paper URLs and exact titles, in every tool.
  Reading and downloading reuse the merged record of a recent result.
- Keep ten-page reads whole in every client: `read_paper` declares a
  120,000-character result size to Claude Code (which otherwise saves results
  over 50,000 characters to a file) and the installer raises Codex's per-tool
  output limit (Codex had cut the middle out of a quarter of reads). A
  100,000-character ceiling, cut at a page boundary with `next_pages`, guards
  unusually long ranges (`SCHOLAR_READ_CHAR_BUDGET`).
- The local ranking model reads the leading candidates' whole abstracts, which
  ranks them more precisely.
- Fail fast on an unreachable remote reranker (10 s connect timeout) and rank
  locally for five minutes before retrying, instead of waiting out the full
  timeout on every pass.
- Write multi-line text as YAML literal blocks, which reads naturally and
  uses about 6% fewer tokens on paper text.
- Reuse private Google browser profiles and parse verified pages directly.
  Failed verification leaves no artificial retry freeze; optional host access
  can repair the request through its configured routes.
- Retain proven preprint/publication identities and prefer available versions
  of record. PDF upgrades keep separate files and preserve existing annotations.
  Temporary reading can reuse retained copies without saving a new file.
- Add native ERIC education search, CourtListener US case-law search and
  ClinicalTrials.gov registrations, protocols and linked papers, plus
  OpenEdition journals and books. Specialist
  records keep their own document types and identifiers.
- Ask Claude Code to load every tool upfront instead of behind tool search
  (about 2,800 tokens for all eight); `"alwaysLoad": false` in the server
  entry still defers them.
- A slow source no longer holds up the others: searches, citation and
  reference lookups and PDF resolution keep to each provider's limits, and a
  source that answers late still joins the final ranking. A stuck provider no
  longer holds `paper_info` or a read.
- Search zbMATH Open, the reference index for mathematics, in every search.
  Its author summaries and expert reviews serve as abstracts for ranking.
- Add a global key file: `scholar-mcp keys set NAME` saves a source key in
  `<data>/keys.env`, which every client that starts Scholar reads.
- Add opt-in NASA ADS, Springer Nature, Web of Science Starter and Lens sources;
  each joins every search once its key is set and leaves installs without the
  key unchanged.
- Searches with `intent="dataset"` also search Zenodo's datasets, ranked by
  best match, and searches written in Japanese also search CiNii Research;
  sources can now declare which searches they serve.
- Keep a bounded local record of tool calls (`<data>/usage/`, about 4 MB at
  most, no query text unless `SCHOLAR_USAGE_LOG=full`). `doctor` and
  `scholar://status` read it and name what changed for the worse: a source that
  started failing, went quiet, lost its abstracts or slowed down, a ranking
  model that fell back, reads that found no PDF, a tool that raised.
- A source that fails, for example out of credits or rate limited, is reported
  as failed rather than as a search that found no papers (Exa, arxiv.gg and
  OpenAlex citations and references used to answer with an empty list).
- `scholar-mcp doctor --network` probes every search, citation and reference
  source, the ranking model and the PDF chain live, and exits 1 on a problem.
  A daily workflow runs it and opens an issue when a probe fails.
- Following citations and references is faster (about 27 s to 19 s on a
  profiled search).
- Europe PMC results no longer hold a search for up to two minutes.
- Publisher PDFs through your library proxy work for more publishers, and
  failures name the publisher's bot check when one blocked the download.
- `search_authors` returns `{authors: [...]}`.

## 0.8.5

- Recover Google Scholar sessions automatically with the optional `google`
  extra, then reuse ordinary HTTP for searches and pagination. Verification is
  bounded, isolated in a temporary browser and coordinated across processes.
- Fall back to arXiv's own HTTPS search when its Atom service is unavailable.
  Preserve original publication dates, authors and full abstracts.
- Handle bounded DBLP verification redirects and decode bibliography titles.
- Add `scholar-mcp sources --check` for live provider diagnostics without
  exposing credentials in error output.
- Correct Google Scholar metadata parsing around nonbreaking spaces.
- Include local reranking in MCPB builds and retain macOS Intel compatibility.
- Clarify installation profiles, reranker configuration and benchmark metrics.

Public tool names and schemas are unchanged.
