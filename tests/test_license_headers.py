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
