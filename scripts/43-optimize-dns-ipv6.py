#!/usr/bin/env python3
"""43 · 修复「刷视频老是缓冲」：DNS 分流 + AAAA 过滤 + 关闭 LAN IPv6。

现象
----
外网能上，但刷视频 / 打开网页经常先卡几秒，然后才突然开始加载。

根因（两条叠加）
----
1) **IPv6 假阳性**：路由器 LAN 侧广播 IPv6 RA / DHCPv6，但 WAN 侧并没有 IPv6 上游。
   内网设备拿到 IPv6 地址、DNS 又返回了 AAAA 记录，于是客户端优先尝试 IPv6，
   连接超时后才回退 IPv4 —— 每次首包都白白慢一拍。
2) **DNS 又慢又不稳**：WAN 的 DHCP 下发了一个又慢又不稳的校园 DNS，
   外部域名解析要等好几秒才回来。

本脚本做三件事
----
a) 关闭 LAN 侧 IPv6 RA + DHCPv6（WAN 无 IPv6 上游时不该向内网广播 IPv6）
b) dnsmasq `filter_aaaa=1`：不返回 AAAA 记录，客户端直接用 IPv4
c) dnsmasq `noresolv=1` + 分流：
     默认外部域名走公共 DNS（223.5.5.5 / 119.29.29.29）
     校内域名（CAMPUS_DOMAINS）定向到校园 DNS（CAMPUS_DNS）

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 43-optimize-dns-ipv6.py

    # 校内域名 / 校园 DNS 可覆盖（默认是示例占位，务必改成你学校的）
    export CAMPUS_DOMAINS='example.edu.cn chinatelecom.com'
    export CAMPUS_DNS='172.16.0.1'
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run  # noqa: E402

CAMPUS_DOMAINS = os.environ.get("CAMPUS_DOMAINS", "example.edu.cn chinatelecom.com").split()
CAMPUS_DNS = os.environ.get("CAMPUS_DNS", "172.16.0.1")

# 动态拼出「校内域名 → 校园 DNS」的分流规则
_SERVER_LINES = "\n".join(
    "uci add_list dhcp.@dnsmasq[0].server='/%s/%s'" % (d, CAMPUS_DNS) for d in CAMPUS_DOMAINS
)
_PROBE_DOMAINS = " ".join(CAMPUS_DOMAINS)

CMD = r"""
echo '=== BEFORE ==='
uci show dhcp.lan 2>/dev/null | grep -E '\.ra=|\.dhcpv6=|\.ndp=' || echo '  (LAN IPv6 用默认值)'
echo -n "  filter_aaaa: "; uci get dhcp.@dnsmasq[0].filter_aaaa 2>/dev/null || echo '(未设置)'
echo -n "  noresolv: "; uci get dhcp.@dnsmasq[0].noresolv 2>/dev/null || echo '(未设置)'
echo -n "  WAN 全局 IPv6 地址数: "; ip -6 addr show dev wan 2>/dev/null | grep -c 'inet6.*scope global'

echo
echo '=== a) 关闭 LAN IPv6 RA / DHCPv6 ==='
uci set dhcp.lan.ra='disabled'
uci set dhcp.lan.dhcpv6='disabled'
uci set dhcp.lan.ndp='disabled'
uci commit dhcp
echo '  已关闭'

echo
echo '=== b) AAAA 过滤 + c) DNS 分流 ==='
uci set dhcp.@dnsmasq[0].filter_aaaa='1'
uci set dhcp.@dnsmasq[0].noresolv='1'
uci -q delete dhcp.@dnsmasq[0].server
uci add_list dhcp.@dnsmasq[0].server='223.5.5.5'
uci add_list dhcp.@dnsmasq[0].server='119.29.29.29'
__SERVER_LINES__
uci commit dhcp
/etc/init.d/dnsmasq restart
sleep 2
echo '  已写入并重启 dnsmasq'

echo
echo '=== AFTER 验证 ==='
echo '--- 配置 ---'
uci show dhcp.@dnsmasq[0] 2>/dev/null | grep -E 'filter_aaaa|noresolv|server' | sed 's/^/  /'
echo '--- AAAA 查询（应被过滤）---'
for d in www.bilibili.com www.taobao.com; do
  echo -n "  $d : "
  r=$(nslookup -query=AAAA $d 127.0.0.1 2>/dev/null | awk '/^Address: [0-9a-f]/{print $2}')
  [ -n "$r" ] && echo "$r" || echo '(无 AAAA，已过滤)'
done
echo '--- A 查询（应正常返回 IPv4）---'
nslookup -query=A www.bilibili.com 127.0.0.1 2>/dev/null | awk '/^Address: [0-9]/{print "  "$2; f=1} END{if(!f)print "  (无 A!)"}'
echo '--- 外部域名解析耗时（应秒回，走公共 DNS）---'
for d in www.qq.com www.bilibili.com; do
  t0=$(date +%s); nslookup $d 127.0.0.1 >/dev/null 2>&1; t1=$(date +%s)
  echo "  $d : $((t1-t0))s"
done
echo '--- 校内域名（应走校园 DNS 拿到内网 IP）---'
for d in __PROBE_DOMAINS__; do
  echo -n "  $d : "; nslookup $d 127.0.0.1 2>/dev/null | awk '/^Address: [0-9]/{print $2; exit}'
done
echo '--- 服务状态 ---'
echo -n "  dnsmasq: "; /etc/init.d/dnsmasq status 2>&1 | head -1
echo -n "  外网: "; ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 && echo 通 || echo 不通
"""


def main():
    client = connect()
    try:
        cmd = CMD.replace("__SERVER_LINES__", _SERVER_LINES).replace("__PROBE_DOMAINS__", _PROBE_DOMAINS)
        rc, out, err = run(client, cmd, timeout=180)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n提示：LAN IPv6 关闭后，内网设备需重连 WiFi / 重续租约才会丢掉旧的 IPv6 地址。")
    finally:
        client.close()


if __name__ == "__main__":
    main()