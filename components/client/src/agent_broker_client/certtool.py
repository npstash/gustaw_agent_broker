"""Make a new key pair for the broker.

config/config.yaml holds the default values. The file of --config replaces the values of the same
keys. Keep the file of a host, with its names, in the deployment project of the host.
Write the certificate to cert.file. A relative path starts at the folder of its configuration file.
Write the private key directly to the vault. Never show the key.
The names in the certificate are cert.names of the configuration. A name that is an IP address
becomes an IP address entry. Each other name becomes a DNS name.

Usage: run.sh cert [--config FILE] [--vault-home DIR] [--entry NAME] [--cert FILE] [--days N]

The clone key of this clone opens the vault. Without it, the vault tool asks for the
passphrase, but only in an interactive terminal.
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import config as cfg
from . import vault
from .jsontypes import JsonValue

PROJECT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT / "config" / "config.yaml"
COMMON_NAME = "agent-broker"
AUTHORITY_INFO = "Private TLS key of the Agent Broker"
DAYS_PER_YEAR = 365


def names_validator(value: JsonValue) -> None:
    """A non-empty list of non-empty strings."""
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise cfg.ConfigError("the value must be a non-empty list of names")


VALIDATORS = {
    "cert.file": lambda v: None,
    "cert.names": names_validator,
    "cert.validity_years": cfg.number_range(low=1),
    "credentials.vault": lambda v: None,
    "credentials.secrets.tls_private_key": cfg.secret_ref,
}

KeyWriter = Callable[[str, Path, str, str], None]


def alt_names(names: Sequence[str]) -> list[x509.GeneralName]:
    """The subject alternative names: an IP address entry for an IP address, else a DNS name."""
    result: list[x509.GeneralName] = []
    for name in names:
        name = name.strip()
        try:
            result.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            result.append(x509.DNSName(name))
    return result


def make_key_pair(names: Sequence[str], days: int) -> tuple[bytes, str]:
    """Return (certificate PEM, private key PEM). The key is ECDSA P-256."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, COMMON_NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alt_names(names)), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False, content_commitment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")
    return cert_pem, key_pem


def write_key_to_vault(key_pem: str, vault_home: Path, entry: str, key_name: str) -> None:
    """Write the key into the vault entry. This is the only place that gets the key."""
    credstore = vault.load_credstore(vault.vault_tool(PROJECT))
    store = credstore.Store(vault_home, interactive=sys.stdin is not None and sys.stdin.isatty())
    store.set_system(entry, authority_info=[AUTHORITY_INFO], secrets={key_name: key_pem})


def cert_base(conf: cfg.ConfigFile) -> Path:
    """The folder of the configuration file that sets cert.file: the start of a relative path."""
    if conf.local_path is not None and conf.local is not None \
            and any(key == "cert.file" for key, _ in cfg.leaves(conf.local)):
        return conf.local_path.resolve().parent
    return PROJECT


def configured_entry(conf: cfg.ConfigFile) -> str:
    """The vault entry of the private key. The project names no vault entry: the file of --config gives it."""
    entry = conf.get("credentials.secrets.tls_private_key.entry", "")
    if not isinstance(entry, str) or not entry:
        raise cfg.ConfigError("give the vault entry of the private key: credentials.secrets.tls_private_key.entry "
                              "in the file of --config, or --entry")
    return entry


def main(argv: Sequence[str] | None = None, key_writer: KeyWriter = write_key_to_vault) -> int:
    """Run certtool. ``key_writer`` stores the key; tests give a fake vault."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--vault-home", type=Path, help="default: credentials.vault of the configuration")
    parser.add_argument("--entry", help="default: credentials.secrets.tls_private_key.entry of the file of --config")
    parser.add_argument("--cert", type=Path, help="default: cert.file of the configuration")
    parser.add_argument("--days", type=int, help="default: cert.validity_years of the configuration")
    parser.add_argument("--config", type=Path, help="the configuration of the host; it replaces default values")
    args = parser.parse_args(argv)
    try:
        if args.config is not None and not args.config.is_file():
            raise cfg.ConfigError(f"configuration file not found: {args.config}")
        conf = cfg.ConfigFile(CONFIG_FILE, VALIDATORS, local_path=args.config)
        key_name = conf.get_str("credentials.secrets.tls_private_key.key")
        entry: str = args.entry or configured_entry(conf)
        vault_home: Path = args.vault_home or vault.find(conf.get_str("credentials.vault"), PROJECT)
        cert: Path = args.cert or vault.resolve_path(cert_base(conf), conf.get_str("cert.file"))
        names = cfg.as_str_list("cert.names", conf.get("cert.names"))
        days: int = args.days or round(conf.get_float("cert.validity_years") * DAYS_PER_YEAR)
    except (cfg.ConfigError, vault.SecretError) as exc:
        parser.error(str(exc))
    cert_pem, key_pem = make_key_pair(names, days)
    key_writer(key_pem, vault_home, entry, key_name)
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_bytes(cert_pem)
    print(f"Certificate written to {cert}")
    print(f"Private key written to the vault entry '{entry}' in {vault_home}")
    print("Commit the certificate and the vault change. Deploy the certificate. Then restart the broker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
