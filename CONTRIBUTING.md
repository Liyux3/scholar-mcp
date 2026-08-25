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
Run only the relevant integration tests when changing a source client.

## Source connector contributions

A useful source contribution includes:

- normalized paper identities and metadata;
- declared query style and capabilities;
- explicit timeout and error behavior;
- deterministic fixtures for successful, empty, unavailable, and malformed responses;
- no new MCP tool when the source fits an existing primitive;
- documentation and capability-matrix updates.

## Pull requests

Include the problem, the chosen mechanism, user-visible effects, exact validation
commands, and any remaining upstream limitations. Preserve compact response shapes and
avoid exposing credentials or raw provider URLs containing keys.
