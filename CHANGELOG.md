# Changelog

## 0.9.0

- Register DeepSeek Harness through its native Cordis MCP plugin, preserving
  existing YAML and expressions. Add TRAE CLI 2.0 and MiMo Code through the
  shared TOML and OpenCode renderers, including their config-path overrides.
- The CPU reranker no longer holds gigabytes after a search: its ONNX session runs
  without ONNX Runtime's memory arena, and passages go in batches sized in tokens
  (4096 per run: 32 at 128 tokens, 10 at 384), because attention grows with batch
  times length squared. A pool of 400 read at 128 and then 384 tokens fell from
  2.5 GB to about 0.45 GB, about a fifth slower; the order moves less than the
  quantized model's own noise from batch composition (Spearman 0.9987 and 9 of the
  top 10 against one batch of 200).
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
- The local ranking model reads the top 100 candidates a second time with their
  whole abstract, after a quick first pass over the pool.
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
- Search every source in its own lane: several queries share one flow, each
  source answering them in order at its own pace (arXiv keeps its
  three-second spacing), and sources run side by side. Seed-title expansion
  and field discovery no longer wait for the slowest source between queries,
  and a source that misses the seed deadline still joins the final ranking.
- Citation and reference lookups and PDF resolution run in the same per-source
  lanes as searches, with the same deadline, so a stuck provider no longer
  holds `paper_info` or a read. Each provider's published rate limit lives in
  its client, so metadata lookups keep the same spacing as searches.
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
- Hydrate expansion's citation and reference results eight at a time instead
  of two; provider gates still bound the lookups (expansion 27 s to 19 s and
  18 s to 13 s on the two profiled searches).
- Keep Europe PMC result formatting offline. A PMC dataset lookup ran for
  each search result and could hold a search for two minutes.
- Fetch publisher PDFs through the library proxy over plain HTTP first, with
  the installed browser's user agent (old agents were bounced by IOP's bot
  wall). Browser checks from Cloudflare, Radware and Elsevier are named in the
  failure reason alongside a library link to open in your browser. The proxy uses
  your exported cookies. Elsevier's linkinghub hop maps to the article, and the
  Elsevier Article API is used when it reports full-text access.
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
