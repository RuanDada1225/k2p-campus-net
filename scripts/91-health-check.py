#!/usr/bin/env python3
"""91-health-check.py —— K2P 一键体检（在 PC 上执行，不是路由器上）

用法::

    export K2P_PASSWORD='...'
    python scripts/91-health-check.py                 # 全量（路由器 + 内网客户端侧）
    python scripts/91-health-check.py --no-client     # 只查路由器
    python scripts/91-health-check.py --probe-node    # 额外探测 ithome 的 5 个 CDN 节点

为什么不把体检脚本丢到路由器上跑（docs/04 一键体检的原始形态）
----------------------------------------------------------------
UA2F 的 nft 队列规则带 ``iifname "br-lan"``，**路由器自身发出的流量根本不经过 UA2F**。
在路由器上做 UA 回显，只会看到 ``uclient-fetch`` / ``curl`` 自己的 UA——一个假阴性，
足以让人误判「UA2F 没生效」。UA 回显必须在**内网客户端**上做（本脚本默认就这么做）。

同理，CDN 节点是否被校园网出口阻断，要站在「和手机同一个出口的客户端」上测；
本脚本用 PC 的 socket 直连 443，与手机走的是同一条 NAT 出口。

期望值从哪来
------------
`custom_ua` / `nfqueue_workers` / 运营商 DNS / cachesize / 5G 信道 / 日志上限 /
域名覆盖，全部取自 `common/target.py`（都可用同名环境变量覆盖）。
**期望值为空 = 该项不适用，直接跳过**——移植到别的学校时不会因为「本机没有这个基线」
报一堆假 FAIL。改配置也只需改 target.py 一处，不必回来改体检脚本。

退出码：全部通过 0；有 FAIL 1。
"""

import argparse
import os
import socket
import ssl
import sys
import time
import unicodedata
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
import ssh_helper  # noqa: E402
import target  # noqa: E402

ITHOME_NODES = [
    ("江苏", "183.131.62.41"),
    ("北京", "123.244.92.41"),
    ("上海", "218.94.231.41"),
    ("浙江", "122.228.115.41"),
    ("四川", "182.242.223.99"),
]
ITHOME_BLOCKED = ("广东(原节点)", "175.12.90.41")

# 一次往返取全部状态：K2P 上几十条 exec_command 太慢
PROBE_SCRIPT = r"""
echo "now=$(date '+%F %T')"
echo "uptime=$(cut -d. -f1 /proc/uptime)"
echo "wan_ip=$(ip -4 addr show __WAN__ | awk '/inet /{print $2; exit}')"
echo "wan_gw=$(ip route | awk '/default/{print $3; exit}')"
echo "ua2f_pid=$(pgrep -f '[u]a2f' | head -1)"
echo "ua2f_lan=$(nft list table inet ua2f 2>/dev/null | grep -c '__LAN__')"
echo "ua2f_ho=$(nft list table inet ua2f 2>/dev/null | grep -c header-only)"
echo "custom_ua=$(uci -q get ua2f.main.custom_ua)"
echo "workers=$(uci -q get ua2f.main.nfqueue_workers)"
echo "ttl_tbl=$(nft list table inet ttl_normalize >/dev/null 2>&1 && echo yes || echo no)"
echo "ntp_tbl=$(nft list table inet ntp_redirect >/dev/null 2>&1 && echo yes || echo no)"
echo "ntp_hits=$(nft list table inet ntp_redirect 2>/dev/null | grep -o 'packets [0-9]*' | head -1 | awk '{print $2}')"
echo "rmem=$(sysctl -n net.core.rmem_max)"
echo "wmem=$(sysctl -n net.core.wmem_max)"
echo "syn_backlog=$(sysctl -n net.ipv4.tcp_max_syn_backlog)"
echo "netdev_budget=$(sysctl -n net.core.netdev_budget)"
echo "dns_up=$(uci -q show dhcp.@dnsmasq[0].server | tr ' ' '\n' | grep -c '__DNS_UP__')"
echo "dns_cache=$(uci -q get dhcp.@dnsmasq[0].cachesize)"
echo "napi_name=$(uci -q get dhcp.@domain[0].name)"
echo "napi_ip=$(uci -q get dhcp.@domain[0].ip)"
echo "log_file=$(uci -q get system.@system[0].log_file)"
echo "log_size=$(uci -q get system.@system[0].log_size)"
echo "msg_size=$(wc -c < /etc/log/messages 2>/dev/null)"
echo "msg_old_size=$(wc -c < /etc/log/messages.old 2>/dev/null)"
echo "boot_last=$(tail -1 /etc/log/boot-history.log 2>/dev/null)"
echo "boot_prev=$(tail -1 /etc/log/boot-history.log 2>/dev/null | sed 's/.*prev=//')"
echo "rps_wan=$(cat /sys/class/net/__WAN__/queues/rx-0/rps_cpus 2>/dev/null)"
echo "rps_wifi=$(cat /sys/class/net/__WIFI5__/queues/rx-0/rps_cpus 2>/dev/null)"
echo "chan=$(iw dev __WIFI5__ info 2>/dev/null | grep -o 'channel [0-9]*' | awk '{print $2}')"
echo "offload=$(uci -q get firewall.@defaults[0].flow_offloading)"
echo "overlay_free=$(df -k /overlay | tail -1 | awk '{print $4}')"
echo "zram=$(grep -c zram /proc/swaps 2>/dev/null)"
echo "adblock=$(ls /etc/dns-blocklist.hosts 2>/dev/null | wc -l)"
echo "rebind=$(uci -q get dhcp.@dnsmasq[0].rebind_protection)"
echo "ra=$(uci -q get dhcp.lan.ra)"
echo "aaaa=$(uci -q get dhcp.@dnsmasq[0].filter_aaaa)"
echo "auth_pid=$(pgrep -f '[e]surfingclient' | head -1)"
echo "auth_hb=$(grep -i esurfing /etc/log/messages 2>/dev/null | tail -1 | cut -c1-19)"
echo "bootdelay=$(sed -n '/^boot() {/,/^}/p' /etc/init.d/esurfingclient 2>/dev/null | grep -o 'sleep [0-9]*' | grep -o '[0-9]*')"
echo "cron_wd=$(grep -c esurfingclient-watchdog /etc/crontabs/root 2>/dev/null)"
echo "wd_restarts=$(cat /tmp/esurfing-watchdog.log 2>/dev/null | wc -l)"
echo "tz=$(date +%z)"
echo "ua2f_err=$(grep -c 'conntrack catch error' /etc/log/messages 2>/dev/null)"
"""

def ua2f_lan_hint(v):
    """按规则条数反推「缺哪一步」，比统一报「应为 3」有用。"""
    return {
        "0": "ua2f 表里没有 br-lan 规则：11 没跑过（或 ua2f 没启动）",
        "1": "只有队列规则：11 打了，12 的两条旁路没打",
        "2": "只有旁路规则：11 的队列行可能被改坏，重跑 11",
        "3": "",
    }.get(v, "规则数异常（%s），建议重跑 11 → 12" % v)


def build_checks():
    """按 `common/target.py` 的期望值组装检查项（可用同名环境变量覆盖）。

    以前这些期望值直接写死在本文件里：改一个 custom_ua、换一条线路 DNS、
    换一个 5G 信道，体检就红 —— 而红的原因跟「设备坏了」无关。
    现在：期望值集中在 target.py；**期望值为空 = 该项不适用，直接跳过**，
    这样移植到别的学校/设备时不会因为「本机没有这个基线」而误报 FAIL。
    """
    chk = []
    add = chk.append

    # ---- UA2F / 指纹 ----
    add(("ua2f_pid", "UA2F 进程", lambda v: bool(v), "无 PID"))
    add(("ua2f_lan", "UA2F 只处理 br-lan", lambda v: v == "3", ua2f_lan_hint))
    add(("ua2f_ho", "UA2F 短包旁路", lambda v: v == "2", "应为 2 条 header-only"))
    add(("custom_ua", "custom_ua", lambda v: v == target.UA2F_CUSTOM_UA,
         "应为 %s（11 字节上限）" % target.UA2F_CUSTOM_UA))
    add(("workers", "nfqueue workers", lambda v: v == target.UA2F_WORKERS,
         "应为 %s" % target.UA2F_WORKERS))
    add(("ttl_tbl", "TTL 归一化表", lambda v: v == "yes", "表不存在"))
    add(("ntp_tbl", "NTP 重定向表", lambda v: v == "yes", "表不存在"))
    add(("offload", "flow offload", lambda v: v in ("", "0"),
         "必须关闭！开了 TTL/UA2F 全失效"))

    # ---- 内核调优（48 号脚本托管）----
    add(("rmem", "rmem_max", lambda v: v == "4194304", "应为 4194304"))
    add(("wmem", "wmem_max", lambda v: v == "4194304", "应为 4194304"))
    add(("syn_backlog", "tcp_max_syn_backlog", lambda v: v == "4096", "应为 4096"))
    add(("netdev_budget", "netdev_budget", lambda v: v == "600", "应为 600"))

    # ---- DNS / IPv6 ----
    if target.DNS_UPSTREAM:
        add(("dns_up", "DNS 上游含运营商 DNS",
             lambda v: v.isdigit() and int(v) >= 1,
             "缺 %s（重跑 43 别冲掉它）" % target.DNS_UPSTREAM))
    add(("dns_cache", "DNS 缓存", lambda v: v == target.DNS_CACHESIZE,
         "应为 %s" % target.DNS_CACHESIZE))
    if target.FORCE_DOMAIN:
        add(("napi_name", "域名覆盖 %s" % target.FORCE_DOMAIN,
             lambda v: v == target.FORCE_DOMAIN, "覆盖丢失（见 03 篇坑 21）"))
        add(("napi_ip", "覆盖 IP", lambda v: bool(v), "覆盖 IP 为空"))
    add(("rebind", "DNS 反重绑定", lambda v: v == "0", "应为 0（坑 13）"))
    add(("ra", "LAN IPv6", lambda v: v == "disabled", "应为 disabled"))
    add(("aaaa", "AAAA 过滤", lambda v: v == "1", "应为 1"))

    # ---- 时钟 / 无线 ----
    add(("tz", "时区", lambda v: v == "+0800", "应为 +0800（北京时间）"))
    add(("chan", "5G 信道", lambda v: v == target.WIFI_5G_CHANNEL,
         "应为 %s（固定非 DFS 信道，见 50 号脚本）" % target.WIFI_5G_CHANNEL))
    add(("rps_wan", "RPS(wan)", lambda v: v == "7", "应为 7"))
    add(("rps_wifi", "RPS(wifi)", lambda v: v == "6", "应为 6"))

    # ---- 认证 ----
    add(("auth_pid", "认证进程", lambda v: bool(v), "esurfingclient 未运行"))
    add(("auth_hb", "认证日志心跳", lambda v: bool(v), "日志里没有 esurfing 输出"))
    add(("bootdelay", "认证开机延迟", lambda v: v == "30", "boot() 里应为 sleep 30（见 30 号脚本）"))
    add(("cron_wd", "认证守护 cron",
         lambda v: v.isdigit() and int(v) >= 1, "crontab 缺 esurfingclient-watchdog（见 44 号脚本）"))

    # ---- 可观测 / 内存 ----
    add(("log_file", "日志落盘", lambda v: v == target.LOG_FILE, "未落盘（见 47 号脚本）"))
    add(("log_size", "日志轮转上限", lambda v: v == target.LOG_SIZE,
         "应为 %sKB" % target.LOG_SIZE))
    add(("boot_last", "启动历史", lambda v: "boot" in v, "无本次启动记录"))
    add(("zram", "zram 交换", lambda v: v == "1", "无 /dev/zram0"))
    return chk


def dwidth(text):
    """终端里 CJK 占两格，按显示宽度对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text, width):
    return text + " " * max(0, width - dwidth(text))


def as_int(value, default=0):
    """探针取不到值时（文件不存在/命令不存在）返回 default，别让体检脚本自己崩掉。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def fmt_bytes(n):
    n = as_int(n, -1)
    if n < 0:
        return "-"
    return "%.1fKB" % (n / 1024.0) if n < 1024 * 1024 else "%.1fMB" % (n / 1024.0 / 1024.0)


def client_ua_echo():
    """从本机（内网客户端）经路由器取明文 80 的 UA 回显。"""
    for url in ("http://httpbin.org/headers", "http://httpbin.org/get"):
        try:
            req = urllib.request.Request(url, headers={"Accept": "*/*"})
            body = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        except Exception as exc:  # 网络失败与「UA 不对」必须区分开
            return None, "回显失败：%s" % exc
        for line in body.splitlines():
            if "user-agent" in line.lower():
                return line.split(":", 1)[1].strip().strip('",'), None
    return None, "回显里没有 User-Agent 字段"


def tcp_probe(ip, port=443, timeout=4):
    started = time.time()
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        return True, int((time.time() - started) * 1000)
    except Exception:
        return False, int((time.time() - started) * 1000)
    finally:
        sock.close()


def https_ok(host):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    started = time.time()
    try:
        req = urllib.request.Request("https://%s/" % host, headers={"Accept": "*/*"})
        urllib.request.urlopen(req, timeout=12, context=ctx).read(256)
        return "HTTP 2xx", (time.time() - started) * 1000
    except urllib.error.HTTPError as exc:
        return "HTTP %d" % exc.code, (time.time() - started) * 1000
    except Exception as exc:
        return "失败：%s" % type(exc).__name__, (time.time() - started) * 1000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-client", action="store_true", help="跳过内网客户端侧验证")
    parser.add_argument("--probe-node", action="store_true", help="探测 ithome 的 5 个 CDN 节点")
    args = parser.parse_args()

    client = ssh_helper.connect()
    probe = (PROBE_SCRIPT
             .replace("__WAN__", target.WAN_IF)
             .replace("__LAN__", target.LAN_IF)
             .replace("__WIFI5__", target.WIFI_5G_IF)
             # 没配运营商 DNS 时用不可能出现的串，dns_up 恒为 0（该项也已跳过）
             .replace("__DNS_UP__", target.DNS_UPSTREAM or "__unset__"))
    rc, out, err = ssh_helper.run(client, probe, timeout=90)
    client.close()
    if rc != 0 and not out.strip():
        sys.exit("取状态失败：%s" % err.strip())

    state = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key.strip()] = value.strip()

    rows, failed = [], 0
    for key, label, ok, hint in build_checks():
        value = state.get(key, "")
        passed = ok(value)
        failed += 0 if passed else 1
        text = hint(value) if callable(hint) else hint
        rows.append((passed, label, value, "" if passed else text))

    width = max(dwidth(r[1]) for r in rows) + 2
    print("K2P 体检 @ %s   uptime=%ss   WAN=%s gw=%s" % (
        state.get("now", "?"), state.get("uptime", "?"),
        state.get("wan_ip", "?"), state.get("wan_gw", "?")))
    print("-" * 74)
    for passed, label, value, hint in rows:
        mark = "OK  " if passed else "FAIL"
        line = "%s %s %s" % (mark, pad(label, width), value or "(空)")
        if hint:
            line += "   <- " + hint
        print(line)

    print("-" * 74)
    print("日志：current=%s  old=%s（上限 %sKB）   启动历史：%s" % (
        fmt_bytes(state.get("msg_size")), fmt_bytes(state.get("msg_old_size")),
        state.get("log_size", "?"), state.get("boot_prev") or state.get("boot_last", "?")))
    print("NTP 重定向命中：%s 包    overlay 可用：%.1fMB   UA2F conntrack 错误：%s" % (
        state.get("ntp_hits", "?"), as_int(state.get("overlay_free")) / 1024.0,
        state.get("ua2f_err", "?")))
    print("上次认证日志：%s   本次启动以来守护脚本重启认证：%d 次" % (
        state.get("auth_hb") or "(无)", as_int(state.get("wd_restarts"))))
    # 去广告已下线：不再作为 FAIL 项，只作提示（恢复它时不该让体检变红）
    print("去广告清单：%s（该功能已下线，仅提示；见 07 篇）"
          % ("仍存在，如需彻底下线见 07 篇" if state.get("adblock") == "1" else "未启用"))

    if not args.no_client:
        print("-" * 74)
        ua, ua_err = client_ua_echo()
        if ua_err:
            print("WARN 客户端 UA 回显  %s" % ua_err)
        elif ua == target.UA2F_CUSTOM_UA:
            print("OK   客户端 UA 回显  %s（UA2F 生效）" % ua)
        else:
            print("FAIL 客户端 UA 回显  %s   <- 应为 %s（若在路由器上跑则结果无意义）"
                  % (ua, target.UA2F_CUSTOM_UA))
            failed += 1

        status, ms = https_ok("napi.ithome.com")
        if status.startswith("HTTP"):
            print("OK   napi.ithome.com  %s %.0fms（覆盖节点可达）" % (status, ms))
        else:
            print("FAIL napi.ithome.com  %s   <- 节点可能又被出口阻断，用 --probe-node 换 IP" % status)
            failed += 1

    if args.probe_node:
        print("-" * 74)
        print("CDN 节点 TCP/443 探测（与手机同一出口）")
        for name, ip in ITHOME_NODES:
            ok, ms = tcp_probe(ip)
            print("  %-4s %-16s %s %4dms" % ("OK" if ok else "FAIL", ip, name, ms))
        name, ip = ITHOME_BLOCKED
        ok, ms = tcp_probe(ip)
        print("  %-4s %-16s %s（%s）" % ("OK" if ok else "FAIL", ip, name,
                                         "已放行" if ok else "仍被阻断，符合预期"))
        if not ok:
            print("  -> 出口阻断仍在，napi 覆盖必须保留")
        current = state.get("napi_ip", "")
        print("  当前覆盖：%s" % current)

    print("-" * 74)
    print("结论：%d 项未通过（共 %d 项）" % (failed, len(rows)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
