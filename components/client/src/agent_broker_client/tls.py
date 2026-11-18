"""The TLS context of a client of the broker.

The certificate of the broker is self-signed. It is also the only trust anchor.
The client checks the host name against the names in the certificate (CERT_NAMES).
"""
from __future__ import annotations

import ssl
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_CERT_FILE = PROJECT / "tls" / "server.crt"


def client_context(cert_file: Path | str = DEFAULT_CERT_FILE) -> ssl.SSLContext:
    """Trust only the certificate in ``cert_file``. Check the host name."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=str(cert_file))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context
