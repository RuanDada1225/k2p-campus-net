#!/usr/bin/env python3
"""44b · 把 watchdog 的存活探测从 ping 改成 HTTP(S)（修复「假在线」漏判）。

现象
----
2026-10-23 22:38–23:06，认证会话失效后网络实际断掉约 28 分钟，
44 号的 watchdog 一次都没动手，直到 23:06 才重启认证。

根因
----
44 号的判断是 `ping -c 2 -W 2 223.5.5.5`。但「认证掉了、认证接口又不响应」
这种半死状态下，**IP 层仍然能通 ICMP**，ping 得到的是假在线：

    22:40:23  [DialerClient.c:104] 需要认证        ← 认证会话已失效
    22:40:29  [NetClientCurl.c:331] CURLcode: 28   ← auth.cgi 连上但 5 秒零字节
    22:40:39  watchcat: Could not reach 223.5.5.5 for 60
    22:40–23:05  watchdog 每分钟跑一次，全部 exit 0（ping 通 → 认为没问题）
    23:06:13  watchdog 终于判断不通 → 重启认证 → 23:06:14 一次成功

`/tmp/esurfing-watchdog.stamp` 里始终只有 23:06 那次的时间戳，就是它 26 分钟没动手的证据。
这与 docs/03 坑 23「ping 通 ≠ HTTPS 可达」是同一类现象。

解决
----
把探测换成真正能代表「能上网」的 HTTP(S) 请求，三选一成功即视为在线：

1. `https://www.baidu.com` —— HTTPS 不会被门户劫持页冒充，被劫持时 TLS 握手失败
2. `http://connect.rom.miui.com/generate_204` —— 要求响应体为空（被劫持时是 HTML）
3. `https://www.qq.com` —— 第二个 HTTPS 目标，防单点

三个都不通才重启认证，避免误判。路由器上没有 curl，用固件自带的 `/bin/uclient-fetch`；
万一它不存在，退回原来的 ICMP 判断，不会把 watchdog 弄瘫。

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 44b-patch-watchdog-http-probe.py

    # 先在副本上演练（不碰现网）
    export K2P_WATCHDOG_SH=/tmp/wd.test
    python 44b-patch-watchdog-http-probe.py

说明
----
- 前置：已执行 44-setup-esurfing-watchdog.py。
- 幂等：脚本里出现 `probe_online` 即判定已打过补丁，直接跳过。
- 备份：改前写 `<path>.bak.<ts>`（与本仓库其他 patch 脚本一致）。
- 写入用 upload_atomic（先写 .new，再 mv），避免半截脚本导致 watchdog 起不来。
- 校验：`sh -n` 语法 + 关键标记计数 + 旧 ping 判断已消失，任一不符退出码 1。
- 不重启任何服务：watchdog 由 cron 每分钟调用，改完下一次触发即生效。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup, upload_atomic  # noqa: E402

WD = os.environ.get("K2P_WATCHDOG_SH", "/usr/bin/esurfingclient-watchdog.sh")
IS_TEST = WD != "/usr/bin/esurfingclient-watchdog.sh"

MARKER = "probe_online"

OLD_BLOCK = """# 外网可达 -> 一切正常
if ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 || ping -c 2 -W 2 119.29.29.29 >/dev/null 2>&1; then
    exit 0
fi"""

OLD_IF = ("if ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 || "
          "ping -c 2 -W 2 119.29.29.29 >/dev/null 2>&1; then")

NEW_BLOCK = """# 外网可达 -> 一切正常
# 用 HTTP(S) 探测而不是 ping：认证掉线但 IP 层没封 ICMP 时，ping 通是假象
# （2026-10-23 实测：22:40 起 ping 一直通，实际断网 28 分钟才被发现）
probe_online() {
    if command -v uclient-fetch >/dev/null 2>&1; then
        # HTTPS 不会被门户劫持页冒充：被劫持时 TLS 握手失败，返回非 0
        uclient-fetch -q -O /dev/null -T 6 https://www.baidu.com >/dev/null 2>&1 && return 0
        # 204 探测要求响应体为空：门户劫持会塞一段 HTML，这里判为失败
        body=$(uclient-fetch -q -O - -T 6 http://connect.rom.miui.com/generate_204 2>/dev/null)
        rc=$?
        [ "$rc" -eq 0 ] && [ -z "$body" ] && return 0
        # 第二个 HTTPS 目标，防单点判死
        uclient-fetch -q -O /dev/null -T 6 https://www.qq.com >/dev/null 2>&1 && return 0
    fi
    # 兜底：设备上没有 uclient-fetch 时退回 ICMP，避免 watchdog 整体失效
    ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1
}

if probe_online; then
    exit 0
fi"""

SELFTEST = r"""
echo '--- 探测目标自测（当前有网，期望 HTTPS_OK / 204_EMPTY 至少一个出现）'
uclient-fetch -q -O /dev/null -T 6 https://www.baidu.com >/dev/null 2>&1 && echo HTTPS_OK || echo HTTPS_FAIL
body=$(uclient-fetch -q -O - -T 6 http://connect.rom.miui.com/generate_204 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$body" ]; then echo 204_EMPTY; else echo "204_FAIL rc=$rc len=${#body}"; fi
uclient-fetch -q -O /dev/null -T 6 https://www.qq.com >/dev/null 2>&1 && echo QQ_OK || echo QQ_FAIL
"""


def main():
    client = connect()
    try:
        rc, content, err = run(client, "cat %s" % WD)
        # 注意：脚本里进程匹配写的是 '[e]surfingclient'，带方括号，不能用它做特征
        if rc != 0 or "esurfing-watchdog.log" not in content:
            print("读取 %s 失败 rc=%d err=%s" % (WD, rc, err))
            print("前置不满足：请先执行 scripts/44-setup-esurfing-watchdog.py")
            sys.exit(2)

        content = content.replace("\r\n", "\n")

        if MARKER in content:
            print("RESULT=ALREADY_PATCHED")
        elif OLD_BLOCK in content:
            bak = backup(client, WD)
            print("BACKUP=%s" % bak)
            if not bak:
                print("FAIL：备份失败，未做任何修改")
                sys.exit(1)
            new = content.replace(OLD_BLOCK, NEW_BLOCK)
            wrc = upload_atomic(client, new, WD, mode="755")
            print("RESULT=PATCHED write_rc=%d" % wrc)
            if wrc != 0:
                sys.exit(1)
        elif OLD_IF in content:
            # 注释行被改过的版本：只换 if 行，并把函数插到它前面
            bak = backup(client, WD)
            print("BACKUP=%s" % bak)
            if not bak:
                print("FAIL：备份失败，未做任何修改")
                sys.exit(1)
            lines = content.split("\n")
            out = []
            for line in lines:
                if OLD_IF in line:
                    out.append(NEW_BLOCK)
                else:
                    out.append(line)
            wrc = upload_atomic(client, "\n".join(out), WD, mode="755")
            print("RESULT=PATCHED_LINE write_rc=%d" % wrc)
            if wrc != 0:
                sys.exit(1)
        else:
            print("PATTERN_NOT_FOUND：没找到原来的 ping 判断，脚本可能已被改过")
            print("请手工检查 %s 里 '外网可达' 那一段" % WD)
            sys.exit(2)

        # ---- 校验 ----
        rc, out, _ = run(client, "sh -n %s && echo SYNTAX_OK" % WD)
        syntax_ok = "SYNTAX_OK" in out
        print("语法检查: %s" % ("OK" if syntax_ok else "FAIL"))

        rc, out, _ = run(client, "grep -c %s %s" % (MARKER, WD))
        n_marker = out.strip()
        print("probe_online 出现次数: %s（期望 2：定义 1 + 调用 1）" % n_marker)

        rc, out, _ = run(client, "grep -c uclient-fetch %s" % WD)
        n_fetch = out.strip()
        print("uclient-fetch 出现次数: %s（期望 >= 3）" % n_fetch)

        rc, out, _ = run(client, 'grep -c "ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 ||" %s' % WD)
        n_old = out.strip()
        print("旧 ping 判断残留: %s（期望 0）" % n_old)

        ok = (syntax_ok
              and n_marker.isdigit() and int(n_marker) == 2
              and n_fetch.isdigit() and int(n_fetch) >= 3
              and n_old.strip() == "0")

        print()
        print("=== 探测目标自测 ===")
        print(run(client, SELFTEST, timeout=60)[1].strip())

        print()
        print("--- 改后的关键片段 ---")
        print(run(client, "sed -n '/外网可达/,/^fi$/p' %s" % WD)[1].strip())

        if not ok:
            print("\nFAIL：校验未通过，请检查上面的输出；可用备份回滚：")
            print("  cp <path>.bak.<ts> %s" % WD)
            sys.exit(1)

        if IS_TEST:
            print("\n（K2P_WATCHDOG_SH 指向副本，现网未受影响）")
            return

        print("\n完成。watchdog 由 cron 每分钟调用一次，下一次触发即按新逻辑判断。")
        print("下次再遇到「认证掉了但 ping 通」，应在约 1 分钟内自动重启认证。")

    finally:
        client.close()


if __name__ == "__main__":
    main()
