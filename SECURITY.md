# Security Policy

## Reporting

Report vulnerabilities privately through GitHub Security Advisories for
`Liyux3/scholar-mcp`. Do not open a public issue for credential exposure, arbitrary file
access, archive traversal, command execution, or transport-authentication problems.

## Supported release

The latest published minor release receives security fixes. Pre-release branches are
supported only until their corresponding public release is published.

## Security boundaries

- Paper text, abstracts, LaTeX, metadata, URLs, and provider errors are untrusted input.
- MCP stdio reserves stdout for protocol messages.
- API keys remain process environment or secure MCPB user configuration values.
- Downloaded files are validated as PDFs and atomically published from staging files.
- Persistent paths derive from explicit configuration and remain outside the repository.
- Streamable HTTP binds to loopback by default; public hosting requires an explicit
  deployment security review.

Include package version, operating system, Python version, transport, minimal
reproduction, and redacted logs in a report.
