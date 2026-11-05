# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Scoped secrets in the encrypted OS credential store."""

import subprocess
import sys


def credential(host: str, value: str | None = None, *, service: str) -> str | None:
    if not host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
        raise ValueError("Invalid credential scope")
    if sys.platform == "darwin":
        if value is None:
            result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service,
                                     "-a", host, "-w"], capture_output=True, text=True, timeout=5)
            return result.stdout.strip() if result.returncode == 0 else None
        # Host is validated by connect; secret bytes travel on stdin, not argv.
        command = f'add-generic-password -U -s {service} -a {host} -T /usr/bin/security -w {value}\n'
        subprocess.run(["/usr/bin/security", "-i"], input=command, text=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if credential(host, service=service) != value:
            raise RuntimeError("System credential store did not retain the session")
        return value
    import keyring
    backend = keyring.get_keyring()
    if type(backend).__module__ not in {
        "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.kwallet",
    }:
        raise RuntimeError("No supported encrypted system credential store is available")
    try:
        if value is not None:
            keyring.set_password(service, host, value)
            return value
        return keyring.get_password(service, host)
    except keyring.errors.KeyringError as error:
        raise RuntimeError("The encrypted credential store is unavailable") from error

