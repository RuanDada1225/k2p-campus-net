#!/usr/bin/env python3
"""49 · 给常驻服务做减法：cron 日志降噪 + 关掉没人用的 collectd / odhcpd + 修无线的 network 绑定。

现象
----
1) `/etc/log/messages` 涨得比预期快：512KB 只够 ~24 小时（见 `docs/08`）。
   实测最近 300 行里有 **144 行**是 crond 的作业播报：

       cron.err crond[1798]: USER root pid 32752 cmd /usr/bin/esurfingclient-watchdog.sh

   watchdog 每分钟一轮，于是每分钟必写一条；整个 ring buffer 里 **1397/4169 行**
   （≈1/3）都是它，把 512KB 的落盘预算白白吃掉三分之一。
2) 内存被两个「装了但没在用」的常驻进程白占（K2P 只有 116MB）：
   - `collectd`（RSS ~3.6MB）在给 luci-app-statistics 采数据，但没人看面板；
     它的数据目录 `/tmp/rrd` 还在 **tmpfs（内存盘）** 里，又吃掉 472KB。
   - `odhcpd`（RSS ~1.5MB）本该负责 RA / DHCPv6 / NDP，可 `43` 号脚本早已把
     `dhcp.lan.ra/dhcpv6/ndp` 全部设为 `disabled`，它现在是一个纯空转进程。
3) 无线的 `network` 绑定是 `'lan wan wan6'`，而 **`wan6` 在运行期并不存在**
   （`ifstatus wan6` → `Interface wan6 not found`），`wan` 也没把无线拖进 `br-wan`
   （`brctl show` 里 `br-wan` 是空的，两路 AP 实际都在 `br-lan`）。
   配置与实际不符，每次 `wifi reload` / `ifup` 都会白跑一遍找不到的接口。

本脚本做四件事（每项都先查后改，已是目标态则打印 ALREADY 跳过）
----
a) `system.@system[0].cronloglevel` → `9`，让 busybox crond 彻底不写 syslog。
   **这个值的语义与直觉相反，是逐级实测出来的（2026-10-24，BusyBox v1.37.0）**：

   | cronloglevel | 每分钟一轮的 watchdog 写几条日志 |
   |---|---|
   | 9 | **0 条**（连 `crond started` 都不写） |
   | 8 | 1 条（`USER root pid … cmd …`） |
   | 7 / 5 / 4 / 3 / 2 / 1 | 1 条起，且**越低越啰嗦**：0 级会多出 `line …` / `job: 0 …` / `child running …` |
   | 0 | 14 条/轮（调试级全开） |

   即：作业播报无法单独关掉，只能全关（9）或全开。实测 7 → 9 后 ring buffer 里
   1/3 的行（1397/4169）不再产生。**代价：cron 自己的报错也不写了**
   （例如 crontab 语法错、命令执行失败），所以末尾断言里额外查了 crontab 条目还在、
   watchdog 最近确有运行痕迹。**不靠猜**：改完重启 cron，按新 crond 的 PID 数
   `USER ` 行，70 秒内一条没有才算成功。`CRON_LOGLEVEL` 可覆盖目标值。
b) **整包卸载** LuCI「统计」菜单这条链：`luci-app-statistics` + `luci-i18n-statistics-zh-cn`
   + `collectd` 及全部 `collectd-mod-*` + `rrdtool1`（固件是 OpenWrt 25.x，用 `apk`）。
   `apk del` 会连孤儿依赖（`librrd1` / `libltdl7`）一起清掉，比逐个停用干净，
   也把 overlay 里那些文件真正还回去（这套组件装在 overlay，不是 squashfs）。
   顺手 `rm -rf /tmp/rrd`（472KB **内存盘**）、清 LuCI 菜单缓存并重启 `rpcd` / `uhttpd`，
   否则菜单项要等缓存过期才消失。要恢复只能重装：`apk add luci-app-statistics`。
c) `/etc/init.d/odhcpd stop + disable`。动手前先断言「没有任何接口还在当 RA/DHCPv6/NDP
   服务端」，只要有一条不是 `disabled` 就整体跳过 —— 关掉别人正在用的 DHCPv6 会直接断网。
d) 把每个 `wifi-iface` 的 `network` 列表里去掉 `wan` / `wan6`（其它网络名原样保留），
   改完 `wifi reload` 生效。

为什么不动 `network.lan.ipv6`
----
`43` 号已经把 LAN 侧的 RA / DHCPv6 / NDP 关了，剩下 `br-lan` 上那个 ULA
（`fdb1:5639:c323::1/60`）只在路由器本地存在，不发给任何客户端，也不占什么内存。
要抹掉它得 `uci set network.lan.ipv6=0` + `/etc/init.d/network reload`，
而 network reload 会重建 `br-lan`、把全家网络抖一下 —— 收益与代价不成比例，故不做。

幂等与安全性
----
- 改前备份 `/etc/config/{system,luci_statistics,wireless}` 到 `<path>.bak.<时间戳>`；
- 四项各自独立判断，重复执行只打印 `ALREADY`，不会再重启任何服务；
- d) 是唯一会断一下的操作：`wifi reload` 用**后台进程**发起（`sleep 2` 后执行），
  脚本主动断开再重连，因此**即使你正用 WiFi 连着路由器也不会把脚本跑挂**；
- 末尾逐项断言（配置值 / 进程消失 / rc.d 链接消失 / 无线仍回到 br-lan / 连通性），
  任一不符退出码 1。

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 49-trim-idle-services.py

    # 可选
    export CRON_LOGLEVEL=4     # 直接指定目标级别，跳过逐级试探
    export SKIP_WIFI=1         # 只做 a/b/c，不动无线（不想断网时用）
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup  # noqa: E402

# busybox crond 的 -l 语义（实测）：9 = 完全静音，8 = 每条作业一行，
# 7 及以下反而逐级变啰嗦（0 会把调试级全打开）。故候选只有「静音」这一个目标值；
# 压不住时（例如固件行为不同）脚本会保留上一个值并明确报告。
CRON_LEVEL_CANDIDATES = [int(x) for x in os.environ.get("CRON_LOGLEVEL", "9").split()]
CRON_SETTLE = 70            # 秒：watchdog 每分钟一轮，等一轮才能判定
SKIP_WIFI = os.environ.get("SKIP_WIFI", "") == "1"

# LuCI「统计」菜单背后的整条链：UI + i18n + collectd 本体 + 全部采集插件 + rrdtool。
# 固件用 apk（OpenWrt 25.x / apk-tools 3），`apk del` 会连孤儿依赖
# （librrd1 / libltdl7 等）一起清掉，不需要手工点名。
STATS_PKGS = (
    "luci-app-statistics luci-i18n-statistics-zh-cn collectd "
    "collectd-mod-cpu collectd-mod-interface collectd-mod-iwinfo "
    "collectd-mod-load collectd-mod-memory collectd-mod-network "
    "collectd-mod-rrdtool rrdtool1"
)
STATS_PKG_RE = "^(collectd|luci-app-statistics|luci-i18n-statistics|rrdtool)"
SKIP_WIFI = os.environ.get("SKIP_WIFI", "") == "1"
WIFI_RECONNECT_TIMEOUT = 120
WIFI_AP_TIMEOUT = 60

# 统计一组进程的 RSS 之和（KB）。调用时传带方括号的模式，如 '[c]ollectd'
RSS_FN = """
rss() {
    _sum=0
    for _p in $(ps | grep "$1" | awk '{print $1}'); do
        _v=$(grep VmRSS /proc/$_p/status 2>/dev/null | awk '{print $2}')
        [ -n "$_v" ] && _sum=$((_sum + _v))
    done
    echo $_sum
}
"""


def step(client, label, cmd, timeout=120):
    """执行并打印一个带标题的步骤，返回 (rc, stdout, stderr)。"""
    rc, out, err = run(client, cmd, timeout=timeout)
    print("== %s (rc=%d)" % (label, rc))
    for line in out.rstrip().splitlines():
        print("   " + line)
    if err.strip():
        print("   STDERR: " + err.strip().replace("\n", "\n   "))
    return rc, out, err


def mem_available(client):
    """MemAvailable（KB），取不到返回 None。"""
    _, out, _ = run(client, "awk '/MemAvailable/{print $2}' /proc/meminfo")
    try:
        return int(out.strip())
    except ValueError:
        return None


def service_state(client, name):
    """返回 (是否开机自启, 进程数, RSS/KB)。"""
    _, en, _ = run(client, "ls /etc/rc.d/ 2>/dev/null | grep -c '^S.*%s$'" % name)
    _, n, _ = run(client, "ps | grep -c '[%s]%s'" % (name[0], name[1:]))
    _, rss, _ = run(client, RSS_FN + "rss '[%s]%s'" % (name[0], name[1:]))
    try:
        return en.strip() not in ("", "0"), int(n.strip() or 0), int(rss.strip() or 0)
    except ValueError:
        return False, 0, 0


def trim_cron(client):
    """a) cronloglevel 降噪。返回最终生效的级别（一个都没压住则返回 None）。"""
    print("\n--- a) cron 日志降噪 ---")
    _, cur, _ = run(client, "uci -q get system.@system[0].cronloglevel")
    cur = cur.strip()
    _, cnt, _ = run(client, "logread | grep -c 'USER root pid'")
    print("   当前 cronloglevel=%s；ring buffer 内已有 %s 条作业播报"
          % (cur or "(未设置)", cnt.strip()))

    done = None
    for level in CRON_LEVEL_CANDIDATES:
        if cur == str(level):
            print("   RESULT=cronloglevel=%d ALREADY（不再重启 cron 复测）" % level)
            return level
        print("   BACKUP=%s" % backup(client, "/etc/config/system"))
        rc, _, err = run(client,
                         "uci set system.@system[0].cronloglevel='%d' && uci commit system"
                         % level)
        if rc != 0:
            print("   uci 写入失败（%s），跳过 %d" % (err.strip(), level))
            continue
        rc, _, _ = run(client, "/etc/init.d/cron restart")
        if rc != 0:
            print("   重启 cron 失败，跳过 %d" % level)
            continue
        _, pid, _ = run(client, "ps | grep '[c]rond' | awk '{print $1}' | head -1")
        pid = pid.strip()
        print("   cronloglevel=%d 已生效（crond pid=%s），等 %ds 看它还播不播报…"
              % (level, pid, CRON_SETTLE))
        n = _wait_cron_job_lines(client, pid, CRON_SETTLE)
        if n == 0:
            print("   级别 %d：%ds 内 0 条作业播报 → 采用" % (level, CRON_SETTLE))
            done = level
            break
        print("   级别 %d：仍播报 %d 条，试下一个候选" % (level, n))

    if done is None:
        print("   RESULT=cron_still_noisy：目标级别没能压住，保留当前值")
        print("   （该固件的 crond 可能没实现 -l 9 的静音语义，别为了静音去关作业）")
    else:
        print("   RESULT=cronloglevel=%d（cron 从此不再写 syslog）" % done)
    return done


def _wait_cron_job_lines(client, pid, wait):
    """等新 crond 至少经历一轮作业，数它用这个 PID 播报了几条作业行。"""
    slept = 0
    while slept < wait:
        time.sleep(20)
        slept += 20
        run(client, "true")                       # 保持 SSH 通道活跃
    _, out, _ = run(client, "logread | grep 'crond\\[%s\\]' | grep -c 'USER '" % pid)
    try:
        return int(out.strip())
    except ValueError:
        return -1


def uninstall_statistics(client):
    """b) 卸载统计面板（luci-app-statistics + collectd 全家）并清 LuCI 缓存。"""
    print("\n--- b) 卸载统计面板（LuCI「统计」菜单）---")
    _, n_pkg, _ = run(client, "apk info 2>/dev/null | grep -cE '%s'" % STATS_PKG_RE)
    try:
        n_pkg = int(n_pkg.strip())
    except ValueError:
        n_pkg = -1
    _, alive, rss = service_state(client, "collectd")
    _, menu, _ = run(client, "ls /usr/share/luci/menu.d/ 2>/dev/null | grep -c statistics")
    print("   残留包 %d 个，collectd 进程 %d 个（RSS %dKB），LuCI 菜单项 %s 个"
          % (n_pkg, alive, rss, menu.strip()))
    if n_pkg == 0 and alive == 0 and menu.strip() == "0":
        print("   RESULT=statistics ALREADY（包 / 进程 / 菜单项均不在了）")
        return False

    # 组件是从 squashfs 之外的 overlay 装的（`/overlay/upper/usr/lib/collectd` 存在），
    # 所以卸载能真正省下 overlay 空间，而不是只留一个 whiteout。
    _, df0, _ = run(client, "df -k /overlay | awk 'NR==2{print $4}'")
    for cfg in ("/etc/config/luci_statistics", "/etc/config/collectd"):
        rc, _, _ = run(client, "[ -f %s ] && echo yes" % cfg)
        if rc == 0:
            print("   BACKUP=%s" % backup(client, cfg))

    step(client, "先停掉 collectd（避免文件被占用）",
         "killall collectd 2>/dev/null; /etc/init.d/collectd stop 2>/dev/null; "
         "/etc/init.d/collectd disable 2>/dev/null; rm -rf /tmp/rrd; echo '  已停'")
    rc, out, err = run(client, "apk del %s 2>&1 | tail -5" % STATS_PKGS, timeout=180)
    print("== apk del (rc=%d)" % rc)
    for line in out.rstrip().splitlines():
        print("   " + line)
    if rc != 0:
        print("   RESULT=statistics uninstall FAILED：%s" % err.strip()[:200])
        return False
    step(client, "清 LuCI 菜单 / ACL 缓存",
         "rm -rf /tmp/luci-indexcache* /tmp/luci-modulecache /tmp/luci-*cache*; "
         "/etc/init.d/rpcd restart 2>&1; /etc/init.d/uhttpd restart 2>&1 | tail -2; "
         "echo '  已清'")
    _, df1, _ = run(client, "df -k /overlay | awk 'NR==2{print $4}'")
    try:
        freed = int(df1.strip()) - int(df0.strip())
    except ValueError:
        freed = 0
    print("   RESULT=statistics uninstalled（overlay 可用 %sKB → %sKB，+%dKB）"
          % (df0.strip(), df1.strip(), freed))
    return True


def trim_odhcpd(client):
    """c) 停用空转的 odhcpd。返回是否发生了变更。"""
    print("\n--- c) 停用 odhcpd ---")
    # 注意：uci 的布尔值是带引号的 `'disabled'`，所以只能 `grep -v disabled`，
    # 写成 `grep -v '=disabled'` 一条都过滤不掉（实测踩过）。
    _, serving, _ = run(
        client,
        "uci show dhcp 2>/dev/null | grep -E '\\.(ra|dhcpv6|ndp)=' | grep -v 'disabled'")
    if serving.strip():
        for line in serving.rstrip().splitlines():
            print("   " + line)
        print("   RESULT=odhcpd SKIP：上面这些接口还在用它当服务端，关掉会断网")
        return False

    is_enabled, alive, rss = service_state(client, "odhcpd")
    if not is_enabled and alive == 0:
        print("   RESULT=odhcpd ALREADY（已停用）")
        return False

    print("   开机自启=%s，进程数=%d，RSS=%dKB；所有接口的 ra/dhcpv6/ndp 均 disabled"
          % (is_enabled, alive, rss))
    step(client, "stop + disable odhcpd",
         "/etc/init.d/odhcpd stop 2>&1; /etc/init.d/odhcpd disable 2>&1")
    _, _, after = service_state(client, "odhcpd")
    print("   RESULT=odhcpd disabled（RSS %dKB → %dKB）" % (rss, after))
    return True


def fix_wifi_network(client):
    """d) 去掉 wifi-iface 的 network 列表里的 wan/wan6。返回是否有变更。"""
    print("\n--- d) 无线 network 绑定 ---")
    _, before, _ = run(client, "uci show wireless | grep -E '\\.network='")
    print("   改前：")
    for line in before.rstrip().splitlines():
        print("     " + line)

    # 重建列表：保留除 wan/wan6 之外的所有网络名；一个不剩则回落到 lan
    _, out, _ = run(client, r"""
changed=0
for s in $(uci show wireless | sed -n 's/^wireless\.\([^.]*\)\.network=.*/\1/p'); do
    cur=$(uci -q get wireless.$s.network)
    new=""
    for n in $cur; do
        case "$n" in wan|wan6) ;; *) new="$new $n" ;; esac
    done
    new="${new# }"
    [ -z "$new" ] && new="lan"
    if [ "$new" != "$cur" ]; then
        uci -q delete wireless.$s.network
        for n in $new; do uci add_list wireless.$s.network="$n"; done
        echo "  $s: '$cur' -> '$new'"
        changed=1
    fi
done
echo "CHANGED=$changed"
""")
    relevant = [l for l in out.rstrip().splitlines() if not l.startswith("CHANGED=")]
    changed = out.rstrip().endswith("CHANGED=1")
    for line in relevant:
        print(line)
    if not changed:
        print("   RESULT=wireless ALREADY（network 里已无 wan/wan6）")
        return False

    print("   BACKUP=%s" % backup(client, "/etc/config/wireless"))
    rc, _, err = run(client, "uci commit wireless")
    if rc != 0:
        print("   uci commit 失败：%s" % err.strip())
        return False
    print("   RESULT=wireless committed（下一步 wifi reload 生效，会断网几秒）")
    return True


def wifi_reload_and_reconnect(client):
    """后台发起 wifi reload，主动断开再重连，避免 WiFi 侧连接把脚本跑挂。"""
    print("\n--- 应用无线配置 ---")
    run(client, "sh -c 'sleep 2; wifi reload' >/dev/null 2>&1 </dev/null &")
    print("   已在后台发起 wifi reload，2 秒后断开重连…")
    time.sleep(4)
    client.close()

    t0 = time.time()
    while time.time() - t0 < WIFI_RECONNECT_TIMEOUT:
        try:
            new = connect()
            print("   已重连（耗时 %ds）" % int(time.time() - t0))
            return new
        except Exception as exc:                  # noqa: BLE001
            print("   重连失败（%s），5s 后重试" % type(exc).__name__)
            time.sleep(5)
    return None


def wait_wifi_ap(client, timeout=WIFI_AP_TIMEOUT):
    """等两路 AP 重新挂回 br-lan（wifi reload 后约 10s 才起）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, out, _ = run(client, "ls /sys/class/net/br-lan/brif/ 2>/dev/null")
        if "phy0-ap0" in out and "phy1-ap0" in out:
            print("   两路 AP 已回到 br-lan（%ds）" % int(time.time() - t0))
            return True
        time.sleep(5)
    return False


def verify(client, cron_level, odhcpd_touched, wifi_touched):
    """末尾逐项断言，返回 rc（非 0 表示有 FAIL）。"""
    print("\n=== 验证 ===")
    cron_assert = ('[ "$(uci -q get system.@system[0].cronloglevel)" = "%d" ] '
                   '|| { say "FAIL: cronloglevel 不是 %d"; bad=1; }'
                   % (cron_level, cron_level)) if cron_level else \
                  '[ "$(uci -q get system.@system[0].cronloglevel)" != "8" ] ' \
                  '|| { say "FAIL: cronloglevel 仍是 8"; bad=1; }'
    odhcpd_block = """
if [ "__ODHCPD__" = "1" ]; then
    [ "$(ls /etc/rc.d/ 2>/dev/null | grep -c odhcpd)" = "0" ] || { say "FAIL: odhcpd 仍开机自启"; bad=1; }
    [ "$(ps | grep -c '[o]dhcpd')" = "0" ] || { say "FAIL: odhcpd 进程还在"; bad=1; }
fi
""".replace("__ODHCPD__", "1" if odhcpd_touched else "0")
    wifi_block = """
if [ "__WIFI__" = "1" ]; then
    ls /sys/class/net/br-lan/brif/ 2>/dev/null | grep -q 'phy0-ap0' \\
        || { say "FAIL: AP 没回到 br-lan"; bad=1; }
fi
""".replace("__WIFI__", "1" if wifi_touched else "0")

    rc, out, _ = run(client, r"""
bad=0
say() { echo "   $*"; }

say "--- 配置 ---"
say "cronloglevel=$(uci -q get system.@system[0].cronloglevel)"
uci show wireless | grep -E '\.network=' | sed 's/^/   /'

say "--- 进程 / 开机自启 ---"
say "collectd: $(ps | grep -c '[c]ollectd') 个进程，$(apk info 2>/dev/null | grep -cE '__PKG_RE__') 个残留包"
say "odhcpd  : $(ps | grep -c '[o]dhcpd') 个进程，$(ls /etc/rc.d/ 2>/dev/null | grep -c odhcpd) 个 rc.d 链接"
say "dnsmasq : $(ps | grep -c '[d]nsmasq') 个进程"
say "ua2f    : $(ps | grep -c '[u]a2f') 个进程"

say "--- 内存 / overlay ---"
free -m | sed -n '2p' | sed 's/^/   /'
df -h /overlay | sed -n '2p' | sed 's/^/   /'

say "--- 网桥 ---"
say "br-lan 端口: $(ls /sys/class/net/br-lan/brif/ 2>/dev/null | tr '\n' ' ')"
iwinfo phy0-ap0 info 2>/dev/null | sed -n '1p;3p' | sed 's/^/   /'

say "--- 连通性 ---"
ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 && say "外网: 通" || say "外网: 不通（仅提示，不计入断言）"
nslookup www.qq.com 127.0.0.1 >/dev/null 2>&1 && say "DNS : 正常" || say "DNS : 异常（仅提示）"

say "--- watchdog（cron 静音后靠它确认作业还在跑）---"
say "crontab 条目: $(grep -c esurfingclient-watchdog /etc/crontabs/root 2>/dev/null) 条"
ls -l /tmp/esurfing-watchdog.stamp 2>/dev/null | sed 's/^/   /' || say "(无 stamp 文件，改看 /tmp/esurfing-watchdog.log)"

say "--- 新日志的最后几行（应不再有每分钟的作业播报）---"
logread | tail -4 | sed 's/^/   /'

say "--- 断言 ---"
[ "$(grep -c esurfingclient-watchdog /etc/crontabs/root 2>/dev/null)" -ge 1 ] \
    || { say "FAIL: crontab 里没有 watchdog 条目"; bad=1; }
__CRON_ASSERT__
[ "$(apk info 2>/dev/null | grep -cE '__PKG_RE__')" = "0" ] || { say "FAIL: 统计面板的包还有残留"; bad=1; }
[ "$(ps | grep -c '[c]ollectd')" = "0" ] || { say "FAIL: collectd 进程还在"; bad=1; }
[ "$(ls /usr/share/luci/menu.d/ 2>/dev/null | grep -c statistics)" = "0" ] || { say "FAIL: LuCI 菜单项 luci-app-statistics.json 还在"; bad=1; }
[ "$(ls /usr/share/rpcd/acl.d/ 2>/dev/null | grep -c statistics)" = "0" ] || { say "FAIL: rpcd ACL 里还留着 statistics"; bad=1; }
__ODHCPD_BLOCK__
uci show wireless | grep -E '\.network=' | grep -qE "(^|[ '])wan6?([ ']|$)" \
    && { say "FAIL: 无线 network 里还有 wan/wan6"; bad=1; }
__WIFI_BLOCK__
[ "$(ps | grep -c '[d]nsmasq')" -ge 1 ] || { say "FAIL: dnsmasq 没在跑"; bad=1; }
[ "$bad" = 0 ] && echo "VERIFY=ok" || { echo "VERIFY=fail"; exit 1; }
"""
                   .replace("__PKG_RE__", STATS_PKG_RE)
                   .replace("__CRON_ASSERT__", cron_assert)
                   .replace("__ODHCPD_BLOCK__", odhcpd_block)
                   .replace("__WIFI_BLOCK__", wifi_block))
    for line in out.rstrip().splitlines():
        print(line)
    return rc


def main():
    client = connect()
    try:
        print("=== 改前基线 ===")
        step(client, "当前状态", RSS_FN + r"""
echo "collectd RSS(KB): $(rss '[c]ollectd')"
echo "odhcpd  RSS(KB): $(rss '[o]dhcpd')"
echo "cronloglevel: $(uci -q get system.@system[0].cronloglevel)"
free -m | sed -n '2p'
""")
        mem0 = mem_available(client)

        cron_level = trim_cron(client)
        uninstall_statistics(client)
        odhcpd_touched = trim_odhcpd(client)

        wifi_touched = False
        if SKIP_WIFI:
            print("\n--- d) 跳过（SKIP_WIFI=1）---")
        else:
            wifi_touched = fix_wifi_network(client)
            if wifi_touched:
                new = wifi_reload_and_reconnect(client)
                if new is None:
                    print("RESULT=wifi_reconnect_failed：wifi reload 后 %ds 内没连回来，"
                          "请手动 ssh 上去执行 `wifi reload` 检查" % WIFI_RECONNECT_TIMEOUT)
                    return 1
                client = new
                if not wait_wifi_ap(client):
                    print("   警告：%ds 内没看到 AP 回到 br-lan，请检查 `iwinfo`"
                          % WIFI_AP_TIMEOUT)

        rc_final = verify(client, cron_level, odhcpd_touched, wifi_touched)
        mem1 = mem_available(client)
        if mem0 and mem1:
            print("\n=== 内存 ===")
            print("MemAvailable: %dKB → %dKB（+%dKB）" % (mem0, mem1, mem1 - mem0))
        print("\n提示：统计面板已整包卸载（含 collectd / rrdtool）。要恢复只能重装："
              "`apk add luci-app-statistics`（`docs/04` §4.8），"
              "它是唯一需要联网取包的步骤。")
        if rc_final != 0:
            sys.exit("验证未通过，请按上面 FAIL 行排查（配置已备份为 .bak.<时间戳>）")
    finally:
        try:
            client.close()
        except Exception:                         # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
