#!/usr/bin/env python3
"""21 · NTP 重定向：强制内网 NTP 请求走路由器。

1) 开启路由器 NTP 服务端模式（enable_server=1，ntpd 加 -l）；
2) 用独立 nft 表 ntp_redirect 把 br-lan 的 UDP 123 请求劫持到路由器本地。

这样内网所有设备共享同一个时钟源，抹平时钟偏差 / NTP 出口会话数差异。

注意：不要设置 system.ntp.interface，避免破坏路由器自身的外网 NTP 同步。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload, LAN_SUBNET  # noqa: E402

INIT_TMPL = """#!/bin/sh /etc/rc.common
# Force LAN clients' NTP (UDP 123) queries to the router itself so that every
# device behind the router shares a single clock source.

START=99
STOP=10

NFT_TABLE=ntp_redirect

start() {
    nft delete table inet "$NFT_TABLE" 2>/dev/null
    nft -f - <<EOF
table inet $NFT_TABLE {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname "br-lan" ip daddr != __LAN_SUBNET__ udp dport 123 counter redirect to :123
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


def main():
    init = INIT_TMPL.replace("__LAN_SUBNET__", LAN_SUBNET)
    client = connect()
    try:
        upload(client, init, "/tmp/ntp-redirect.init")

        steps = [
            ("lan subnet", "ip -4 addr show dev br-lan | grep inet"),
            ("enable ntp server",
             "uci set system.ntp.enable_server='1' && uci commit system && echo UCI_OK"),
            ("restart sysntpd", "/etc/init.d/sysntpd restart && sleep 3 && echo RESTARTED"),
            ("ntpd cmdline", "pgrep -af ntpd | grep -v ujail"),
            ("udp 123 listener",
             "netstat -lnup 2>/dev/null | grep ':123 ' || ss -lnup 2>/dev/null | grep ':123 '"),
            ("install init",
             "cp /tmp/ntp-redirect.init /etc/init.d/ntp-redirect && "
             "chmod 755 /etc/init.d/ntp-redirect && echo INSTALL_OK"),
            ("enable init", "/etc/init.d/ntp-redirect enable && echo ENABLED"),
            ("start init", "/etc/init.d/ntp-redirect start && echo STARTED"),
            ("rc.d", "ls -l /etc/rc.d/ | grep -iE 'ntp'"),
            ("table", "nft list table inet ntp_redirect"),
            ("all tables", "nft list tables"),
        ]
        for label, cmd in steps:
            rc, out, err = run(client, cmd)
            print("=== %s (rc=%d) ===" % (label, rc))
            print(out.strip())
            if err.strip():
                print("  STDERR:", err.strip())

        print("\n验证：内网设备向不可路由地址（如 203.0.113.1）发 NTP 请求，")
        print("      若能收到合法应答，说明请求被劫持到路由器。")
    finally:
        client.close()


if __name__ == "__main__":
    main()