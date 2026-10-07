#!/usr/bin/env python3
"""70 · 静态 DHCP 保留：给常用设备固定 IP。

背景
----
动态租约到期、设备重连或路由器重启后，同一台设备可能拿到不同的 IP。
一旦 IP 变了，端口转发、共享、远程访问等「按 IP 写死」的配置全部失效。

做法
----
在 dnsmasq 里按 MAC 绑定固定 IP（静态保留），并把动态地址池缩小到
保留地址之前，避免动态分配和保留地址撞车。

用法
----
先按自己的设备修改下面的 DEVICES，再执行：

    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 70-set-static-ip.py

脚本是幂等的：同名保留会先删后加，重复执行不会产生重复条目。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

# 设备清单：(名称, MAC, 固定 IP)
# ⚠️ 下面全是示例占位，请替换成你自己设备的 MAC（ip neigh / dhcp.leases 里能查到）
DEVICES = [
    ("PC-1", "aa:bb:cc:00:00:01", "192.168.1.201"),
    ("PC-2", "aa:bb:cc:00:00:02", "192.168.1.202"),
    ("PC-3", "aa:bb:cc:00:00:03", "192.168.1.203"),
    ("PC-4", "aa:bb:cc:00:00:04", "192.168.1.204"),
]

# 动态地址池：192.168.1.100 ~ 192.168.1.200
POOL_START = "100"
POOL_LIMIT = "101"

TMPL = r"""#!/bin/sh
echo '=== BEFORE: 静态保留 ==='
uci show dhcp 2>/dev/null | grep -E 'host\[[0-9]+\]\.(name|mac|ip)' || echo '(无静态保留)'

echo
echo '=== 删除同名旧保留（保证幂等） ==='
for name in __NAMES__; do
    for idx in $(uci show dhcp 2>/dev/null \
                 | sed -n "s/^dhcp\.\(host\[[0-9]*\]\)\.name='$name'\$/\1/p"); do
        uci -q delete "dhcp.$idx"
        echo "deleted dhcp.$idx  ($name)"
    done
done

echo
echo '=== 写入新保留 ==='
__ADDS__

echo
echo '=== 缩小动态地址池（__POOL_START__ ~ __POOL_END__） ==='
uci set dhcp.lan.start='__POOL_START__'
uci set dhcp.lan.limit='__POOL_LIMIT__'
uci commit dhcp
echo "committed: start=$(uci get dhcp.lan.start) limit=$(uci get dhcp.lan.limit)"

echo
echo '=== 重启 dnsmasq ==='
/etc/init.d/dnsmasq restart
sleep 3

echo
echo '=== AFTER: 静态保留 ==='
uci show dhcp 2>/dev/null | grep -E 'host\[[0-9]+\]\.(name|mac|ip)'

echo
echo '=== AFTER: 地址池 ==='
uci show dhcp.lan 2>/dev/null | grep -E 'start|limit|leasetime'

echo
echo '=== 当前租约（设备重连后应落到保留 IP） ==='
awk '{printf "%-16s %-18s %s\n", $3, $2, $4}' /tmp/dhcp.leases 2>/dev/null | sort -t. -k4 -n

echo
echo '=== 在线状态（ARP） ==='
ip neigh show 2>/dev/null | grep -E '__POOL_PREFIX__\.' | sort -t. -k4 -n
"""


def build_script():
    names = " ".join(d[0] for d in DEVICES)
    adds = "\n".join(
        "uci add dhcp host >/dev/null\n"
        "uci set dhcp.@host[-1].name='%s'\n"
        "uci set dhcp.@host[-1].mac='%s'\n"
        "uci set dhcp.@host[-1].ip='%s'" % (name, mac, ip)
        for name, mac, ip in DEVICES
    )
    pool_end = int(POOL_START) + int(POOL_LIMIT) - 1
    pool_prefix = DEVICES[0][2].rsplit(".", 1)[0]
    return (
        TMPL.replace("__NAMES__", names)
        .replace("__ADDS__", adds)
        .replace("__POOL_START__", POOL_START)
        .replace("__POOL_LIMIT__", POOL_LIMIT)
        .replace("__POOL_END__", str(pool_end))
        .replace("__POOL_PREFIX__", pool_prefix)
    )


def main():
    client = connect()
    try:
        upload(client, build_script(), "/tmp/70-set-static-ip.sh")
        rc, out, err = run(client, "sh /tmp/70-set-static-ip.sh", timeout=180)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n提示：设备需要重新获取一次 DHCP（重连或 renew）才会落到保留 IP。")
    finally:
        client.close()


if __name__ == "__main__":
    main()