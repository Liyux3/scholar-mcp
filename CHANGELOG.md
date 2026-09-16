# Changelog

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
