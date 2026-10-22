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
    n=0
    while [ "$n" -lt 20 ]; do
        idx=$(uci show dhcp 2>/dev/null \
              | sed -n "s/^dhcp\.\(@host\[[0-9]*\]\)\.name='$name'\$/\1/p" | head -1)
        [ -n "$idx" ] || break
        uci -q delete "dhcp.$idx" || { echo "FAIL: 删除 dhcp.$idx 失败，中止"; exit 1; }
        echo "deleted dhcp.$idx  ($name)"
        n=$((n+1))
    done
    # 兜底：删不干净就停，别让 while 无限转到 SSH 超时
    if [ "$n" -ge 20 ]; then
        echo "FAIL: $name 的旧保留删不干净（已尝试 20 次），中止"
        exit 1
    fi
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

echo
echo '=== 校验（不一致就退出 1）==='
# 以前这里只打印 AFTER 让人肉眼看，看漏就等于没生效；改成断言。
bad=0
for name in __NAMES__; do
    n=$(uci show dhcp 2>/dev/null | grep -cE "^dhcp\.@host\[[0-9]+\]\.name='$name'\$")
    [ "$n" = "1" ] || { echo "FAIL: $name 的保留记录 $n 条（期望 1，重跑可能产生了重复）"; bad=1; }
done
cur_start=$(uci -q get dhcp.lan.start)
cur_limit=$(uci -q get dhcp.lan.limit)
[ "$cur_start" = "__POOL_START__" ] || { echo "FAIL: dhcp.lan.start=$cur_start（期望 __POOL_START__）"; bad=1; }
[ "$cur_limit" = "__POOL_LIMIT__" ] || { echo "FAIL: dhcp.lan.limit=$cur_limit（期望 __POOL_LIMIT__）"; bad=1; }
pgrep -f '[d]nsmasq' >/dev/null || { echo 'FAIL: dnsmasq 未运行'; bad=1; }
[ "$bad" = 0 ] && echo 'VERIFY=ok（每个设备各 1 条保留，地址池在保留段之前）' || { echo 'VERIFY=fail'; exit 1; }
"""


def validate():
    """动手前先把「会把局域网搞挂」的配置错误拦下来。

    最容易犯的是：保留地址写进了动态池范围 → dnsmasq 可能把同一个 IP 既当静态
    又当动态分出去（租约冲突）。这种事现场表现为「某台设备时好时坏」，极难查。
    """
    errors = []
    pool_start = int(POOL_START)
    pool_end = pool_start + int(POOL_LIMIT) - 1

    prefixes = {ip.rsplit(".", 1)[0] for _, _, ip in DEVICES if "." in ip}
    if len(prefixes) != 1:
        errors.append("DEVICES 里的 IP 不在同一网段：%s"
                      % ("、".join(sorted(prefixes)) or "(空)"))
    prefix = next(iter(prefixes), "192.168.1")

    ips = [ip for _, _, ip in DEVICES]
    for name, mac, ip in DEVICES:
        try:
            last = int(ip.rsplit(".", 1)[1])
        except (IndexError, ValueError):
            errors.append("%s 的 IP 格式不对：%r" % (name, ip))
            continue
        if pool_start <= last <= pool_end:
            errors.append(
                "%s 的保留地址 %s 落在动态池 %s.%d-%d 里，会撞车；"
                "请把池缩小到保留地址之前（改 POOL_LIMIT），或把设备挪到池外"
                % (name, ip, prefix, pool_start, pool_end))
        if ips.count(ip) > 1:
            errors.append("%s 与其他设备重复使用 %s" % (name, ip))

    # 只认「脚本里自带的那几个示例 MAC」，不要按前缀猜 ——
    # aa:bb:cc:dd:ee:ff 这类本地管理地址是合法 MAC，按前缀判会误伤
    placeholder_macs = {"aa:bb:cc:00:00:0%d" % i for i in range(1, 5)}
    for name, mac, _ in DEVICES:
        if not mac or mac.lower() in placeholder_macs:
            errors.append("%s 的 MAC 还是示例占位（%s），请换成真实 MAC" % (name, mac))
        # 设备名会拼进路由器端的 for 循环与 uci set，空格/引号会把命令拆坏
        if not name or " " in name or "'" in name:
            errors.append("设备名不能为空、不能含空格或单引号：%r" % name)
    return errors


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
    errors = validate()
    if errors:
        print("配置检查未通过，未做任何修改：")
        for e in errors:
            print("  - %s" % e)
        sys.exit(2)

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