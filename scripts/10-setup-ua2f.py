#!/usr/bin/env python3
"""10 · 安装并配置 UA2F（NFQUEUE 模式）。

前提：ua2f 及其依赖已通过 apk 安装（见 docs/02-部署实战.md）。
本脚本只负责写入配置并重启服务。

注意：custom_ua 用 'Mozilla'（7 字节）。UA2F 是等长替换，
custom_ua 越短覆盖范围越广，详见 docs/01-检测原理.md。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

CUSTOM_UA = "Mozilla"

CONFIG = """config ua2f 'enabled'
    option enabled '1'

config ua2f 'firewall'
    option handle_fw '1'
    option handle_tls '0'
    option handle_intranet '1'

config ua2f 'main'
    option mode 'NFQUEUE'
    option listen_port '10010'
    option nfqueue_workers '1'
    option proxy_workers '0'
    option custom_ua '%s'
    option disable_connmark '0'
    option max_http_sessions '0'
    option session_ttl '300'
""" % CUSTOM_UA

SCRIPT = r"""#!/bin/sh
set -e
cp /tmp/ua2f.conf.new /etc/config/ua2f
uci commit ua2f
/etc/init.d/ua2f enable
/etc/init.d/ua2f restart
sleep 3

echo "=== uci show ua2f ==="
uci show ua2f
echo "=== process ==="
pgrep -af ua2f
echo "=== nft queue rule ==="
nft list table inet ua2f 2>/dev/null | grep -E 'queue|dport 80'
echo "=== version ==="
ua2f --version 2>/dev/null | head -2 || true
"""


def main():
    client = connect()
    try:
        upload(client, CONFIG, "/tmp/ua2f.conf.new")
        upload(client, SCRIPT, "/tmp/setup_ua2f.sh")
        rc, out, err = run(client, "sh /tmp/setup_ua2f.sh", timeout=120)
        print("RC=%d" % rc)
        print(out)
        if err.strip():
            print("--- STDERR ---")
            print(err)
        print("\n提示：下一步务必执行 11-patch-ua2f-lan-only.py，")
        print("      否则 UA2F 会改写路由器自身的认证流量，导致校园网认证失败。")
    finally:
        client.close()


if __name__ == "__main__":
    main()