# Contributing to Scholar MCP

Scholar MCP welcomes focused improvements to retrieval, source connectors, paper
access, graph workflows, the local research library, and distribution.

## Before opening a change

- Search existing issues and discussions.
- Open an issue before changing public tool names, schemas, or persistent data formats.
- Keep unrelated refactors and feature work in separate pull requests.
- Never include credentials, downloaded papers, private notes, or local indexes.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Live API tests carry the `integration` marker and are excluded from the default suite.
Run only the relevant integration tests when changing a source client, and
`scholar-mcp doctor --network` to probe every source, the ranking model and the
PDF chain at once.

## Source connector contributions

Each provider is one module in `scholar_mcp/sources/`, registered at the end of
`scholar_mcp/sources/__init__.py`, with its tests in `tests/sources/`. A useful
source contribution includes:

- normalized paper identities and metadata;
- declared query style and capabilities;
- explicit timeout and error behavior;
- deterministic fixtures for successful, empty, unavailable, and malformed responses;
- no new MCP tool when the source fits an existing primitive;
- documentation and capability-matrix updates.

## Contributor terms

Scholar MCP is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE) with [additional terms](LICENSE-ADDITIONAL.md). By
submitting a contribution you confirm that you wrote it or have the right to
submit it, and you grant Yuxian Li a perpetual, worldwide, non-exclusive,
royalty-free, irrevocable license to use, reproduce, modify, sublicense and
distribute it under any terms, including the license the project uses at the
time, together with a patent license for your contribution on the same scope.
You keep your copyright. Pull requests confirm this with the checkbox in the
template.

## Pull requests

Include the problem, the chosen mechanism, user-visible effects, exact validation
commands, and any remaining upstream limitations. Preserve compact response shapes and
avoid exposing credentials or raw provider URLs containing keys.
