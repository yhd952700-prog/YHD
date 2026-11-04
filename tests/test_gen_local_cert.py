"""本地 HTTPS 自签证书生成脚本的行为测试。"""
from __future__ import annotations

from cryptography import x509

from scripts import gen_local_cert as glc


def test_lan_ipv4_excludes_loopback():
    addrs = glc.lan_ipv4_addresses()
    for ip in addrs:
        assert not ip.startswith("127.")
        assert ip != "0.0.0.0"


def test_generate_certificate_writes_files_with_sans(tmp_path):
    cert = tmp_path / "liuhao-local.pem"
    key = tmp_path / "liuhao-local.key"

    result = glc.generate_certificate(cert, key, extra_sans=["myhost.local"])

    assert cert.is_file()
    assert key.is_file()
    assert "localhost" in result["sans"]
    assert "127.0.0.1" in result["sans"]
    assert "myhost.local" in result["sans"]

    # 证书可解析，SAN 包含预期项。
    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    san_ext = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    ip_names = [str(i) for i in san_ext.value.get_values_for_type(x509.IPAddress)]
    assert "localhost" in dns_names
    assert "myhost.local" in dns_names
    assert "127.0.0.1" in ip_names


def test_generate_certificate_key_is_pem_private(tmp_path):
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.key"
    glc.generate_certificate(cert, key)
    text = key.read_text(encoding="utf-8")
    assert "BEGIN RSA PRIVATE KEY" in text or "BEGIN PRIVATE KEY" in text


def test_main_returns_zero(tmp_path, capsys):
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.key"
    rc = glc.main(["--cert", str(cert), "--key", str(key)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "自签证书已生成" in out
