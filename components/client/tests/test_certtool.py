"""certtool (DESIGN.md, sections 6.5, 8.3, 8.4): the names, the validity and the key. The vault is a fake."""
import datetime
import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from agent_broker_client import certtool, vault
from agent_broker_client import config as cfg

NAMES = ["broker-host", "localhost", "127.0.0.1", "192.0.2.10", "broker.example.net"]
CONFIG_TEXT = """\
cert:
  file: tls/server.crt
  names: [{names}]
  validity_years: {years}
credentials:
  vault: vault-agent_broker
  secrets:
    tls_private_key: {{entry: "Agent Broker TLS 01", key: private_key}}
"""


class FakeVault:
    """Records each call. It stands for the vault."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, str, str]] = []

    def write(self, key_pem: str, vault_home: Path, entry: str, key_name: str) -> None:
        self.calls.append((key_pem, vault_home, entry, key_name))


def san(cert: x509.Certificate) -> tuple[set[str], set[str]]:
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(ext.get_values_for_type(x509.DNSName)), {str(a) for a in ext.get_values_for_type(x509.IPAddress)}


def make_config(tmp_path: Path, names: list[str] = NAMES, years: int = 10) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT.format(names=", ".join(names), years=years), encoding="utf-8")
    return path


def test_names_are_told_apart() -> None:
    cert_pem, _ = certtool.make_key_pair(NAMES, days=2)
    dns, ips = san(x509.load_pem_x509_certificate(cert_pem))
    assert dns == {"broker-host", "localhost", "broker.example.net"}
    assert ips == {"127.0.0.1", "192.0.2.10"}


def test_ipv6_name_is_an_address() -> None:
    cert_pem, _ = certtool.make_key_pair(["::1", "host"], days=2)
    dns, ips = san(x509.load_pem_x509_certificate(cert_pem))
    assert dns == {"host"} and ips == {str(ipaddress.ip_address("::1"))}


def test_certificate_kind_and_extensions() -> None:
    cert_pem, key_pem = certtool.make_key_pair(NAMES, days=3650)
    cert = x509.load_pem_x509_certificate(cert_pem)
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "agent-broker"
    public = cert.public_key()
    assert isinstance(public, ec.EllipticCurvePublicKey) and public.curve.name == "secp256r1"
    key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
    assert key.public_key().public_numbers() == public.public_numbers()   # type: ignore[union-attr]
    basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.critical and basic.value.ca and basic.value.path_length == 0
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.digital_signature and usage.value.key_cert_sign
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    days = (cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days
    assert 3649 <= days <= 3650


def test_default_configuration_follows_the_design() -> None:
    conf = cfg.ConfigFile(certtool.CONFIG_FILE, certtool.VALIDATORS)
    assert conf.get_list("cert.names") == NAMES
    assert conf.get_int("cert.validity_years") == 10
    assert conf.get_str("credentials.vault") == "vault-agent_broker"
    assert conf.get_str_map("credentials.secrets.tls_private_key") == {
        "entry": "Agent Broker TLS 01", "key": "private_key"}
    assert vault.resolve_path(certtool.PROJECT, conf.get_str("cert.file")) == certtool.PROJECT / "tls" / "server.crt"


def test_main_uses_the_configured_names_and_validity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    names = ["a.example", "10.0.0.7"]
    fake = FakeVault()
    cert_file = tmp_path / "out" / "server.crt"
    home = tmp_path / "vault-home"
    args = ["--config", str(make_config(tmp_path, names, years=2)), "--vault-home", str(home), "--cert", str(cert_file)]
    assert certtool.main(args, key_writer=fake.write) == 0
    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    assert san(cert) == ({"a.example"}, {"10.0.0.7"})
    days = (cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days
    assert 729 <= days <= 730
    assert len(fake.calls) == 1
    key_pem, vault_home, entry, key_name = fake.calls[0]
    assert (vault_home, entry, key_name) == (home, "Agent Broker TLS 01", "private_key")
    assert key_pem.startswith("-----BEGIN PRIVATE KEY-----")
    # The key goes only to the vault function: not to the output, not to a file.
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert key_pem.splitlines()[1] not in captured.out + captured.err
    body = key_pem.splitlines()[1]
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert body.encode() not in file.read_bytes(), file
    assert b"PRIVATE" not in cert_file.read_bytes()


def test_entry_option_replaces_the_configured_entry(tmp_path: Path) -> None:
    fake = FakeVault()
    args = ["--config", str(make_config(tmp_path)), "--vault-home", str(tmp_path), "--cert", str(tmp_path / "c.crt"),
            "--entry", "Other entry", "--days", "5"]
    assert certtool.main(args, key_writer=fake.write) == 0
    assert fake.calls[0][2] == "Other entry"
    cert = x509.load_pem_x509_certificate((tmp_path / "c.crt").read_bytes())
    assert (cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days in (4, 5)


@pytest.mark.parametrize("names", [[], ["", "x"]])
def test_bad_names_stop_before_any_write(tmp_path: Path, names: list[str]) -> None:
    fake = FakeVault()
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT.format(names=", ".join(f'"{n}"' for n in names), years=10), encoding="utf-8")
    with pytest.raises(SystemExit):
        certtool.main(["--config", str(path), "--vault-home", str(tmp_path), "--cert", str(tmp_path / "c.crt")],
                      key_writer=fake.write)
    assert fake.calls == [] and not (tmp_path / "c.crt").exists()



def test_certtool_writes_key_to_a_temporary_vault(tmp_path: Path) -> None:
    """The real vault tool with a vault in tmp_path (as voice client does). Not a real vault."""
    credstore = vault.load_credstore(vault.vault_tool(certtool.PROJECT))
    home = tmp_path / "vault-home"
    home.mkdir()
    credstore.Store(home, passphrase="pw").create_vault()
    (home / "credentials.json").write_text("{}", encoding="utf-8")
    credstore.Store(home, passphrase="pw").enrol("test", None)
    cert = tmp_path / "out" / "server.crt"
    args = ["--vault-home", str(home), "--cert", str(cert)]
    assert certtool.main(args) == 0
    key = credstore.Store(home).get_credential("Agent Broker TLS 01").secrets()["private_key"]
    assert key.startswith("-----BEGIN PRIVATE KEY-----")
    assert "PRIVATE" not in (home / "credentials.json").read_text(encoding="utf-8")
    assert san(x509.load_pem_x509_certificate(cert.read_bytes())) == (
        {"broker-host", "localhost", "broker.example.net"}, {"127.0.0.1", "192.0.2.10"})
