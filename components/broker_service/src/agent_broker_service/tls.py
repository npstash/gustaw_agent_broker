"""The TLS context of the broker (DESIGN.md, sections 6.1.1 and 9).

The certificate is the committed file of the component client. The private key comes from the vault.
"""
from __future__ import annotations

import contextlib
import os
import ssl
import uuid
from pathlib import Path


def server_context(cert_file: Path, key_pem: str, scratch_dir: Path) -> ssl.SSLContext:
    """Load the certificate and the private key.

    Python reads a key only from a file. Write the key to a file that only this account
    can read, load it, then delete the file at once.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    key_file = scratch_dir / f"key-{uuid.uuid4().hex}.pem"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(key_pem)
        context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    finally:
        with contextlib.suppress(FileNotFoundError):
            key_file.unlink()
    return context
