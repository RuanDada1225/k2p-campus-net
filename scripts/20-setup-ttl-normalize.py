#!/usr/bin/env python3
"""20 · TTL / HopLimit 归一化。

在 WAN 出口把 IPv4 TTL 和 IPv6 HopLimit 统一设为 64，
抹平 Windows(128) / Linux(64) / macOS(64) 等系统差异。

用独立的 nft 表 ttl_normalize + 开机脚本，避免被 fw4 / UA2F 重启清掉。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

INIT = """#!/bin/sh /etc/rc.common
# Normalize IPv4 TTL / IPv6 hop-limit on WAN egress to defeat
# multi-device detection based on OS TTL fingerprinting.

START=99
STOP=10

NFT_TABLE=ttl_normalize

start() {
    nft delete table inet "$NFT_TABLE" 2>/dev/null
    nft -f - <<EOF
table inet $NFT_TABLE {
    chain postrouting {
        type filter hook postrouting priority mangle + 10; policy accept;
        oifname "wan" ip ttl set 64
        oifname "wan" ip6 hoplimit set 64
    }
}
EOF
}

stop() {
    nft delete table inet "$NFT_TABLE" 2>/dev/null
}

reload() {
    stop
    start
}

boot() {
    start
}
"""

PROBE = """table inet ttl_probe {
    chain postrouting {
        type filter hook postrouting priority mangle + 20; policy accept;
        oifname "br-lan" ip ttl set 55
    }
}
"""


def main():
    client = connect()
    try:
        upload(client, INIT, "/tmp/ttl-normalize.init")
        upload(client, PROBE, "/tmp/ttl_probe.nft")

        for label, cmd in [
            ("install", "cp /tmp/ttl-normalize.init /etc/init.d/ttl-normalize && "
                        "chmod 755 /etc/init.d/ttl-normalize && echo INSTALL_OK"),
            ("enable", "/etc/init.d/ttl-normalize enable && echo ENABLED"),
            ("start", "/etc/init.d/ttl-normalize start && echo STARTED"),
            ("rc.d", "ls -l /etc/rc.d/ | grep ttl"),
            ("table", "nft list table inet ttl_normalize"),
        ]:
            rc, out, err = run(client, cmd)
            print("=== %s (rc=%d) ===" % (label, rc))
            print(out.strip())
            if err.strip():
                print("  STDERR:", err.strip())

        print("\n--- 反证法验证（可选，需在 WAN 侧抓包配合）---")
        print("加载反证表，把 LAN 出向 TTL 改成 55：")
        print(run(client, "nft -f /tmp/ttl_probe.nft && echo PROBE_OK")[1].strip())
        print("此时在内网 ping 外网并在 WAN 侧抓包，TTL 应仍为 64。")
        print("验证完请删除反证表：nft delete table inet ttl_probe")
    finally:
        client.close()


if __name__ == "__main__":
    main()