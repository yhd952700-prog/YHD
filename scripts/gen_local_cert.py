#!/usr/bin/env python
"""生成本地 HTTPS 自签证书（自用版手机/PWA 访问用）。

为什么需要
----------
PWA 可安装性要求「安全上下文」——即 HTTPS 或 localhost。局域网内用手机访问
桌面的 HTTP 地址不是安全上下文，浏览器不会弹「添加到主屏幕」。所以在网关
之上套一层本地 TLS 反向代理（``scripts/ops/https_proxy.py``，由
``start-liuhao.bat --https`` 拉起）。

本脚本生成一张自签证书，SAN 包含：
- ``localhost`` / ``127.0.0.1`` / ``::1``
- 本机所有非回环 IPv4 地址（局域网 IP，手机通过它访问）

证书写到 ``deploy/cloud/certs/liuhao-local.pem`` + ``.key``（启动器默认找这里）。

自用限制（如实标注）
---------------------
- 自签证书，手机首次访问会有「不受信任」警告，需在手机上**信任该证书**后
  PWA 安装提示才会出现。
- 仅限局域网自用，不要暴露到公网；公网请用正经 CA 签发的证书（不在本脚本范围）。
- 私钥文件权限仅本机可读（类 Unix 下 0o600；Windows 下由 NTFS ACL 决定）。
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CERT_DIR = REPO_ROOT / "deploy" / "cloud" / "certs"
DEFAULT_CERT = DEFAULT_CERT_DIR / "liuhao-local.pem"
DEFAULT_KEY = DEFAULT_CERT_DIR / "liuhao-local.key"


def lan_ipv4_addresses() -> List[str]:
    """返回本机所有非回环 IPv4 地址（去重，跳过 link-local）。"""
    addrs: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            try:
                ip_obj = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if ip_obj.is_loopback or ip_obj.is_link_local:
                continue
            if ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    # 兜底：用 UDP connect 探测默认出口 IP（不真发包）。
    if not addrs:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip not in addrs:
                addrs.append(ip)
        except OSError:
            pass
    return addrs


def generate_certificate(cert_path: Path, key_path: Path,
                         extra_sans: Optional[List[str]] = None,
                         days: int = 3650) -> dict:
    """生成 RSA-2048 自签证书并写盘。返回写入的路径与 SAN 列表。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    sans = ["localhost", "127.0.0.1", "::1"]
    sans.extend(lan_ipv4_addresses())
    if extra_sans:
        for name in extra_sans:
            if name and name not in sans:
                sans.append(name)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "LiuHao Local Self-Signed"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LiuHao AI OS (self-use)"),
    ])
    now = datetime.now(timezone.utc)
    san_list = []
    for name in sans:
        try:
            ipaddress.ip_address(name)
            san_list.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            san_list.append(x509.DNSName(name))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # 私钥：类 Unix 收紧权限。
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows 上 chmod 语义有限，忽略。

    return {"cert": str(cert_path), "key": str(key_path), "sans": sans}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成本地 HTTPS 自签证书（自用/PWA）")
    parser.add_argument("--cert", default=str(DEFAULT_CERT), help="证书输出路径")
    parser.add_argument("--key", default=str(DEFAULT_KEY), help="私钥输出路径")
    parser.add_argument("--san", action="append", default=[],
                        help="额外 SAN（可多次），如 --san myhost.local")
    parser.add_argument("--days", type=int, default=3650, help="有效期天数（默认 3650 ≈ 10 年）")
    args = parser.parse_args(argv)

    result = generate_certificate(Path(args.cert), Path(args.key), args.san, args.days)
    print("=" * 62)
    print("本地 HTTPS 自签证书已生成")
    print("=" * 62)
    print(f"  证书: {result['cert']}")
    print(f"  私钥: {result['key']}")
    print(f"  SAN : {', '.join(result['sans'])}")
    print()
    print("下一步：")
    print("  1) 双击 start-liuhao.bat --https  （或 .venv  python scripts/start_liuhao.py --https）")
    print("  2) 手机浏览器打开 https://<局域网IP>:8443/")
    print("  3) 首次会提示「不受信任」— 点高级/继续访问；")
    print("     iOS: 设置 → 通用 → 关于本机 → 证书信任设置 → 启用该证书；")
    print("     Android: 下载 .pem 后在 设置 → 安全 → 加密与凭据 → 安装证书；")
    print("  4) 信任后刷新页面，浏览器会提示「添加到主屏幕」。")
    print()
    print("限制：自签证书仅供局域网自用，勿暴露公网。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
