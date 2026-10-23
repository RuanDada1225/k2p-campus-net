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

本脚本做四件事
----
a) 关闭 LAN 侧 IPv6 RA + DHCPv6（WAN 无 IPv6 上游时不该向内网广播 IPv6）
b) dnsmasq `filter_aaaa=1`：不返回 AAAA 记录，客户端直接用 IPv4；顺带写 `cachesize`
c) `noresolv=1` + 上游：默认取 `common/target.py` 的 DNS_UPSTREAM（本线路就近运营商 DNS），
   校内域名（CAMPUS_DOMAINS）定向到校园 DNS（CAMPUS_DNS）
d) 可选：按域名强制覆盖 CDN 节点（K2P_FORCE_DOMAIN / K2P_FORCE_IP，见 03 篇坑 21）

幂等（2026-10 修订，修掉「重跑一次就把运营商 DNS 冲掉」）
------------------------------------------------------
以前是「删掉所有 server，再只写 223.5.5.5 / 119.29.29.29」：

- 重跑会冲掉手工加的就近运营商 DNS（如 202.96.128.86），`91` 体检的 dns_up 立刻红；
- 而 03 篇坑 22 的结论恰恰是「公共/CNNIC 上游曾把全屋解析搞挂」，等于一键退回故障态。

现在：

- 默认上游 = `DNS_UPSTREAM`（环境变量）> 现有非 `/域名/ip` 上游 > 公共 DNS 兜底；
- 现有 `/域名/ip` 分流规则原样保留；只有显式给了 `CAMPUS_DOMAINS` / `CAMPUS_DNS` 才重建；
- 末尾逐项断言（上游非空 / filter_aaaa / noresolv / cachesize / ra / dnsmasq 活着 / 外部可解析），
  任一不符 → 退出码 1，避免「改坏了还显示成功」。

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 43-optimize-dns-ipv6.py

    # 可选覆盖（默认取 common/target.py）
    export DNS_UPSTREAM='202.96.128.86'            # 本线路就近运营商 DNS
    export CAMPUS_DOMAINS='example.edu.cn chinatelecom.com'
    export CAMPUS_DNS='172.16.0.1'
    export DNS_CACHESIZE=4000
    # 按域名强制覆盖 CDN 节点（兜底手段）。典型场景：公共 DNS 把域名调度到一个
    # 校园网不可达的 CDN 节点，而学校 DNS 给出的节点恰好可达（见 03 篇坑 21）。
    # 两个都非空才生效；留空 = 保持路由器上现有覆盖不动。
    export K2P_FORCE_DOMAIN='napi.ithome.com' K2P_FORCE_IP='119.188.212.41'
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
import target  # noqa: E402
from ssh_helper import connect, run  # noqa: E402

# 校内域名：显式给了环境变量才重建现有分流规则，否则原样保留（幂等）
_campus_env = os.environ.get("CAMPUS_DOMAINS", "").strip()
CAMPUS_DOMAINS = _campus_env.split() if _campus_env else ["example.edu.cn"]
CAMPUS_DNS = os.environ.get("CAMPUS_DNS", "172.16.0.1")
CAMPUS_EXPLICIT = "1" if (_campus_env or os.environ.get("CAMPUS_DNS")) else "0"

# 默认上游：单一来源在 target.py（91 体检按同一个值判定）
DNS_UPSTREAM = os.environ.get("DNS_UPSTREAM", target.DNS_UPSTREAM).split()
DNS_CACHESIZE = os.environ.get("DNS_CACHESIZE", target.DNS_CACHESIZE)

FORCE_DOMAIN = os.environ.get("K2P_FORCE_DOMAIN", target.FORCE_DOMAIN).strip()
FORCE_IP = os.environ.get("K2P_FORCE_IP", target.FORCE_IP).strip()

CMD = r"""
echo '=== BEFORE ==='
uci show dhcp.lan 2>/dev/null | grep -E '\.ra=|\.dhcpv6=|\.ndp=' || echo '  (LAN IPv6 用默认值)'
echo -n "  filter_aaaa: "; uci -q get dhcp.@dnsmasq[0].filter_aaaa 2>/dev/null || echo '(未设置)'
echo -n "  noresolv: "; uci -q get dhcp.@dnsmasq[0].noresolv 2>/dev/null || echo '(未设置)'
echo -n "  cachesize: "; uci -q get dhcp.@dnsmasq[0].cachesize 2>/dev/null || echo '(未设置)'
echo -n "  server: "; uci -q get dhcp.@dnsmasq[0].server 2>/dev/null || echo '(未设置)'
echo -n "  WAN 全局 IPv6 地址数: "; ip -6 addr show dev __WAN__ 2>/dev/null | grep -c 'inet6.*scope global'

echo
echo '=== a) 关闭 LAN IPv6 RA / DHCPv6 ==='
uci set dhcp.lan.ra='disabled'
uci set dhcp.lan.dhcpv6='disabled'
uci set dhcp.lan.ndp='disabled'
echo '  已关闭'

echo
echo '=== b) AAAA 过滤 + DNS 缓存 ==='
uci set dhcp.@dnsmasq[0].filter_aaaa='1'
uci set dhcp.@dnsmasq[0].cachesize='__CACHESIZE__'
echo '  filter_aaaa=1  cachesize=__CACHESIZE__'

echo
echo '=== c) DNS 上游（幂等：不冲掉现有运营商 DNS / 校内域名规则）==='
CUR=$(uci -q get dhcp.@dnsmasq[0].server)
KEEP_DOMAIN=""
KEEP_UP=""
for e in $CUR; do
    case "$e" in
        /*) KEEP_DOMAIN="$KEEP_DOMAIN $e" ;;
        *)  KEEP_UP="$KEEP_UP $e" ;;
    esac
done

UP="__UPSTREAM__"
if [ -n "$UP" ]; then
    echo "  按 DNS_UPSTREAM 写入: $UP"
else
    UP="$KEEP_UP"
    echo "  未指定 DNS_UPSTREAM，沿用现有上游:${UP:-（无）}"
fi
if [ -z "$UP" ]; then
    UP="223.5.5.5 119.29.29.29"
    echo "  路由器上没有任何上游，回落到公共 DNS: $UP（建议改成就近运营商 DNS）"
fi

uci -q delete dhcp.@dnsmasq[0].server
for u in $UP; do
    uci add_list dhcp.@dnsmasq[0].server="$u"
done

if [ "__CAMPUS_EXPLICIT__" = "1" ]; then
    echo "  按 CAMPUS_DOMAINS 重建校内域名分流 -> __CAMPUS_DNS__"
    for d in __PROBE_DOMAINS__; do
        uci add_list dhcp.@dnsmasq[0].server="/$d/__CAMPUS_DNS__"
    done
else
    echo "  保留现有校内域名分流规则（未显式设置 CAMPUS_DOMAINS）"
    for e in $KEEP_DOMAIN; do
        uci add_list dhcp.@dnsmasq[0].server="$e"
    done
fi

uci set dhcp.@dnsmasq[0].noresolv='1'
uci commit dhcp
/etc/init.d/dnsmasq restart
sleep 2
echo '  已写入并重启 dnsmasq'

echo
echo '=== d) 按域名强制覆盖 CDN（可选）==='
if [ -n "__FORCE_DOMAIN__" ] && [ -n "__FORCE_IP__" ]; then
    uci -q get dhcp.@domain[0] >/dev/null 2>&1 || uci add dhcp domain >/dev/null
    uci set dhcp.@domain[0].name='__FORCE_DOMAIN__'
    uci set dhcp.@domain[0].ip='__FORCE_IP__'
    uci commit dhcp
    /etc/init.d/dnsmasq restart
    sleep 2
    echo "  __FORCE_DOMAIN__ -> __FORCE_IP__"
else
    echo '  跳过（未同时设置 K2P_FORCE_DOMAIN / K2P_FORCE_IP，现有覆盖保持不动）'
fi

echo
echo '=== AFTER 验证 ==='
echo '--- 配置 ---'
uci show dhcp.@dnsmasq[0] 2>/dev/null | grep -E 'filter_aaaa|noresolv|cachesize|server' | sed 's/^/  /'
echo '--- AAAA 查询（应被过滤）---'
for d in www.bilibili.com www.taobao.com; do
    r=$(nslookup -query=AAAA $d 127.0.0.1 2>/dev/null | awk '/^Address: [0-9a-f]/{print $2; exit}')
    echo "  $d : ${r:-（无 AAAA，已过滤）}"
done
echo '--- A 查询（应正常返回 IPv4）---'
nslookup -query=A www.bilibili.com 127.0.0.1 2>/dev/null | awk '/^Address: [0-9]/{print "  "$2; f=1} END{if(!f)print "  (无 A!)"}'
echo '--- 外部域名解析耗时（应秒回，走默认上游）---'
for d in www.qq.com www.bilibili.com; do
    t0=$(date +%s); nslookup $d 127.0.0.1 >/dev/null 2>&1; t1=$(date +%s)
    echo "  $d : $((t1-t0))s"
done
echo '--- 校内域名（应走校园 DNS 拿到内网 IP）---'
if [ "__CAMPUS_EXPLICIT__" = "1" ]; then
    PROBE_DOMAINS="__PROBE_DOMAINS__"
else
    PROBE_DOMAINS=$(for e in $KEEP_DOMAIN; do echo "$e" | sed 's#^/##; s#/[^/]*$##'; done)
fi
if [ -z "$PROBE_DOMAINS" ]; then
    echo '  (没有校内域名分流规则，跳过)'
else
    for d in $PROBE_DOMAINS; do
        r=$(nslookup "$d" 127.0.0.1 2>/dev/null | awk '/^Address: [0-9]/{print $2; exit}')
        echo "  $d : ${r:-（无应答）}"
    done
fi
echo '--- 服务状态 ---'
echo -n "  dnsmasq: "; /etc/init.d/dnsmasq status 2>&1 | head -1
echo -n "  外网: "; ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 && echo 通 || echo 不通

echo
echo '=== 断言（任一不符就退出 1）==='
bad=0
UPN=$(uci -q get dhcp.@dnsmasq[0].server 2>/dev/null | tr ' ' '\n' | grep -vc '/')
[ "${UPN:-0}" -ge 1 ] || { echo 'FAIL: 没有任何默认上游（noresolv=1 时等于全屋断解析）'; bad=1; }
[ "$(uci -q get dhcp.@dnsmasq[0].filter_aaaa)" = "1" ] || { echo 'FAIL: filter_aaaa 未生效'; bad=1; }
[ "$(uci -q get dhcp.@dnsmasq[0].noresolv)" = "1" ] || { echo 'FAIL: noresolv 未生效'; bad=1; }
[ "$(uci -q get dhcp.@dnsmasq[0].cachesize)" = "__CACHESIZE__" ] || { echo 'FAIL: cachesize 未生效'; bad=1; }
[ "$(uci -q get dhcp.lan.ra)" = "disabled" ] || { echo 'FAIL: dhcp.lan.ra 未生效'; bad=1; }
pgrep -f '/usr/sbin/dnsmasq' >/dev/null || { echo 'FAIL: dnsmasq 未运行'; bad=1; }
nslookup www.qq.com 127.0.0.1 >/dev/null 2>&1 || { echo 'FAIL: 外部域名解析失败'; bad=1; }
[ "$bad" = 0 ] && echo 'VERIFY=ok' || { echo 'VERIFY=fail'; exit 1; }
"""


def main():
    client = connect()
    try:
        cmd = (CMD
               .replace("__WAN__", target.WAN_IF)
               .replace("__UPSTREAM__", " ".join(DNS_UPSTREAM))
               .replace("__CAMPUS_EXPLICIT__", CAMPUS_EXPLICIT)
               .replace("__PROBE_DOMAINS__", " ".join(CAMPUS_DOMAINS))
               .replace("__CAMPUS_DNS__", CAMPUS_DNS)
               .replace("__CACHESIZE__", DNS_CACHESIZE)
               .replace("__FORCE_DOMAIN__", FORCE_DOMAIN)
               .replace("__FORCE_IP__", FORCE_IP))
        rc, out, err = run(client, cmd, timeout=180)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n提示：LAN IPv6 关闭后，内网设备需重连 WiFi / 重续租约才会丢掉旧的 IPv6 地址。")
        if CAMPUS_EXPLICIT == "0":
            print("      校内域名分流沿用了现有规则；要重建请显式设置 CAMPUS_DOMAINS / CAMPUS_DNS。")
    finally:
        client.close()


if __name__ == "__main__":
    main()
