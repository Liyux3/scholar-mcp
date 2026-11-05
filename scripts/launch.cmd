@echo off
rem SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
rem Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
rem Process-scoped policy only; never change the user's PowerShell policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
exit /b %errorlevel%
