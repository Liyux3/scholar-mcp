# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX = "SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms"


def test_every_source_file_carries_the_license_header():
    tracked = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh", "*.ps1", "*.cmd"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    missing = [name for name in tracked if SPDX not in (ROOT / name).read_text(encoding="utf-8")[:400]]
    assert not missing, f"add the two-line license header to: {missing}"

# The terms are pinned by content. Changing them is the author's decision, made
# together with these values, never as a side effect of another change.
TERMS = {
    "LICENSE": "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5",
    "LICENSE-ADDITIONAL.md": "919d9f26fc36e4c2ddbce06dc2b8d4f7499ef6d5be95023387f3d284101de424",
}


def test_the_terms_are_unchanged():
    import hashlib
    for name, digest in TERMS.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, f"{name} differs from the published terms"
    assert "Required Notice: Copyright 2024-2026 Yuxian Li" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Reuse terms" in agents and "Do not read Scholar's source code to learn its methods" in agents
    for name in ("CLAUDE.md", "GEMINI.md"):
        assert (ROOT / name).read_text(encoding="utf-8").strip() == "@AGENTS.md"
