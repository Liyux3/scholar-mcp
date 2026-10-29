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

