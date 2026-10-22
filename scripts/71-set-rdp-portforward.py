#!/usr/bin/env python3
"""71 · 端口转发：把 WAN 侧端口映射到内网设备的 RDP(3389)。

用途
----
从外网（机房 / 家里）用远程桌面连接内网的多台电脑。
每台电脑在 WAN 侧占用一个独立端口，避免 3389 冲突：

    WAN:33890  ->  192.168.1.201:3389
    WAN:33891  ->  192.168.1.202:3389
    WAN:33892  ->  192.168.1.203:3389
    WAN:33893  ->  192.168.1.204:3389

前提
----
- 先跑 70-set-static-ip.py 把目标设备 IP 固定，否则转发目标会漂移。
- 目标电脑要各自开启「远程桌面」并放行 3389。
- 只放行必要端口；WAN 区域入向策略保持 REJECT / DROP。

用法
----
先按自己的电脑修改 DEVICES，再执行：

    python 71-set-rdp-portforward.py

脚本是幂等的：同名规则会先删后加。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
import target  # noqa: E402
from ssh_helper import connect, run, upload  # noqa: E402

# 设备清单：(规则名, WAN 侧端口, 内网 IP)
# ⚠️ 示例占位，请改成你自己的设备名与 IP
DEVICES = [
    ("RDP-PC-1", "33890", "192.168.1.201"),
    ("RDP-PC-2", "33891", "192.168.1.202"),
    ("RDP-PC-3", "33892", "192.168.1.203"),
    ("RDP-PC-4", "33893", "192.168.1.204"),
]

RDP_PORT = "3389"

TMPL = r"""#!/bin/sh
echo '=== BEFORE: 现有转发 ==='
uci show firewall 2>/dev/null | grep -E 'redirect\[[0-9]+\]\.(name|src_dport|dest_ip)' || echo '(无)'

echo
echo '=== 删除同名旧规则（保证幂等） ==='
for name in __NAMES__; do
    for idx in $(uci show firewall 2>/dev/null \
                 | sed -n "s/^firewall\.\(@redirect\[[0-9]*\]\)\.name='$name'\$/\1/p"); do
        uci -q delete "firewall.$idx"
        echo "deleted firewall.$idx  ($name)"
    done
done

echo
echo '=== 写入新规则 ==='
__ADDS__

uci commit firewall
/etc/init.d/firewall reload
sleep 4

echo
echo '=== AFTER: 转发规则 ==='
uci show firewall 2>/dev/null | grep -E 'redirect\[[0-9]+\]\.(name|src_dport|dest_ip|dest_port)'

echo
echo '=== nftables 中的 DNAT ==='
nft list ruleset 2>/dev/null | grep -E '__PORTS_RE__'

echo
echo '=== WAN 入向策略（应保持 REJECT / DROP） ==='
uci show firewall 2>/dev/null | grep -E '@zone\[[0-9]+\]\.(name|input|forward)'

echo
echo '=== WAN 地址（外部要连的就是它） ==='
ip -4 addr show dev $(uci get network.wan.device 2>/dev/null || echo wan) 2>/dev/null | grep inet

echo
echo '=== 校验（不一致就退出 1）==='
bad=0
for name in __NAMES__; do
    n=$(uci show firewall 2>/dev/null | grep -cE "^firewall\.@redirect\[[0-9]+\]\.name='$name'\$")
    [ "$n" = "1" ] || { echo "FAIL: $name 规则 $n 条（期望 1）"; bad=1; }
done
# WAN 端口撞车时后一条会静默覆盖/失效，现场表现为「某台怎么都连不上」
dup=$(uci show firewall 2>/dev/null | sed -n "s/^firewall\.@redirect\[[0-9]*\]\.src_dport='\(.*\)'\$/\1/p" | sort | uniq -d)
[ -z "$dup" ] || { echo "FAIL: WAN 端口重复：$dup"; bad=1; }
# DNAT 是否真的落到了 nft（uci 写进去 ≠ fw4 生成规则）
n=$(nft list ruleset 2>/dev/null | grep -cE '__PORTS_RE__')
[ "${n:-0}" -ge 1 ] || { echo 'FAIL: nft 里找不到这些端口的 DNAT 规则'; bad=1; }
[ "$bad" = 0 ] && echo "VERIFY=ok（每条规则 1 份，端口不重复，nft 里已生成 $n 条）" || { echo 'VERIFY=fail'; exit 1; }
"""


def validate():
    """动手前拦下「会把转发写坏」的错误，省得连上路由器才发现。

    以前没有这一步：端口撞车/目标不在 LAN 网段时，脚本照样把规则写进去并回显
    「成功」，现场表现是「某台电脑怎么都连不上」。
    """
    errors = []
    ports = [p for _, p, _ in DEVICES]
    for name, port, ip in DEVICES:
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            errors.append("%s 的 WAN 端口不合法：%r" % (name, port))
        elif port == RDP_PORT:
            errors.append("%s 用了 %s（等于内网 RDP 端口），WAN 侧应换一个" % (name, port))
        if not ip.startswith(target.LAN_PREFIX + "."):
            errors.append("%s 的目标 %s 不在 LAN 网段 %s.0/24（先跑 70 固定 IP）"
                          % (name, ip, target.LAN_PREFIX))
        if not name or " " in name or "'" in name:
            errors.append("规则名不能为空、不能含空格或单引号：%r" % name)
    dup = sorted({p for p in ports if ports.count(p) > 1})
    if dup:
        errors.append("WAN 端口重复：%s（后写的会覆盖前一条）" % "、".join(dup))
    return errors


def build_script():
    names = " ".join(d[0] for d in DEVICES)
    adds = "\n".join(
        "uci add firewall redirect >/dev/null\n"
        "uci set firewall.@redirect[-1].name='%s'\n"
        "uci set firewall.@redirect[-1].target='DNAT'\n"
        "uci set firewall.@redirect[-1].src='wan'\n"
        "uci set firewall.@redirect[-1].src_dport='%s'\n"
        "uci set firewall.@redirect[-1].dest='lan'\n"
        "uci set firewall.@redirect[-1].dest_ip='%s'\n"
        "uci set firewall.@redirect[-1].dest_port='%s'\n"
        "uci set firewall.@redirect[-1].proto='tcp'\n"
        "uci set firewall.@redirect[-1].family='ipv4'"
        % (name, dport, ip, RDP_PORT)
        for name, dport, ip in DEVICES
    )
    ports_re = "|".join(d[1] for d in DEVICES)
    return (
        TMPL.replace("__NAMES__", names)
        .replace("__ADDS__", adds)
        .replace("__PORTS_RE__", ports_re)
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
        upload(client, build_script(), "/tmp/71-set-rdp-portforward.sh")
        rc, out, err = run(client, "sh /tmp/71-set-rdp-portforward.sh", timeout=180)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n提示：连接格式为  <WAN地址>:<端口>  ，例如 1.2.3.4:33890。")
    finally:
        client.close()


if __name__ == "__main__":
    main()