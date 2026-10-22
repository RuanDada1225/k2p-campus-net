#!/usr/bin/env python3
"""12 · 让 UA2F 跳过「纯 ACK 短包」（性能关键）。

问题：NFQUEUE 规则匹配 br-lan 全部原始方向 TCP。大流量下载（如 Steam 走
      80 端口）时，99% 的包是无负载的纯 ACK —— 它们不可能包含 User-Agent，
      但每包都要走一遍「内核 → 用户态 → 判决」的 NFQUEUE 往返。
      实测满速下载时队列约 3100 包/秒，CPU sys 被吃满、下载变卡。

解决：在入队规则之前插入两条「按长度旁路」规则：
        IPv4：ip length 1-80    （纯 ACK 总长最多 80B = IP 20B + TCP 头 60B，阈值含边界覆盖）
        IPv6：ip6 length 1-104  （注意 ip6 length 是负载长度，纯 ACK 负载最多 60B，阈值有余量）
      真实 HTTP 请求（请求行 + Host + User-Agent）远大于该长度，不会被旁路。

实测效果：队列 3100 → 10~16 包/秒，sys 下降约 28%，同线路下载 +10%~15%。

前置：必须已执行 11-patch-ua2f-lan-only.py（本脚本锚定带 iifname "br-lan"
      的队列行）。备份写入 `<path>.bak.<ts>`，不覆盖 11 号的备份。

校验不依赖 `diff`（busybox 没有这个命令），改看：`sh -n` 语法 + 两条 bypass 规则
是否都在 queue 行**之前**。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup, upload_atomic  # noqa: E402

INIT = os.environ.get("K2P_UA2F_INIT", "/etc/init.d/ua2f")   # 测试时可指向副本
IS_TEST = INIT != "/etc/init.d/ua2f"

QUEUE_LINE = 'meta l4proto tcp ct direction original iifname "br-lan" counter $nfqueue_expr;'
QUEUE_LINE_NO_LAN = 'meta l4proto tcp ct direction original counter $nfqueue_expr;'
BYPASS_V4 = ('meta l4proto tcp ct direction original iifname "br-lan" '
             'ip length 1-80 counter return comment "!ua2f: bypass header-only ACK v4";')
BYPASS_V6 = ('meta l4proto tcp ct direction original iifname "br-lan" '
             'ip6 length 1-104 counter return comment "!ua2f: bypass header-only ACK v6";')

ORDER_CHECK = (
    'h=$(grep -n header-only {f} | tail -1 | cut -d: -f1); '
    # 队列规则行用 "iifname ... nfqueue_expr" 定位；"queue num" 只出现在变量声明处
    'q=$(grep -n "iifname.*nfqueue_expr" {f} | head -1 | cut -d: -f1); '
    'if [ -n "$h" ] && [ -n "$q" ] && [ "$h" -lt "$q" ]; then echo ORDER_OK; '
    'else echo "ORDER_BAD h=$h q=$q"; fi'
).format(f=INIT)


def main():
    client = connect()
    try:
        rc, content, err = run(client, "cat %s" % INIT)
        if rc != 0 or "queue num" not in content:
            print("读取 %s 失败 rc=%d err=%s" % (INIT, rc, err))
            sys.exit(1)

        if BYPASS_V4 in content:
            print("RESULT=ALREADY_PATCHED")
        elif QUEUE_LINE in content:
            # 同上：只处理唯一一条队列行，出现多条就停下来问人
            n_q = content.count(QUEUE_LINE)
            if n_q != 1:
                print("AMBIGUOUS：队列行出现 %d 次（期望 1 次），已中止。" % n_q)
                print("请手工检查 %s 的 NFQUEUE 规则。" % INIT)
                sys.exit(2)

            bak = backup(client, INIT)
            print("BACKUP=%s" % bak)
            if not bak:
                print("FAIL：备份失败，未做任何修改")
                sys.exit(1)
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if QUEUE_LINE in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    lines[i:i] = [indent + BYPASS_V4, indent + BYPASS_V6]
                    break
            rc = upload_atomic(client, "\n".join(lines), INIT)
            print("RESULT=PATCHED write_rc=%d" % rc)
            if rc != 0:
                sys.exit(1)
        elif QUEUE_LINE_NO_LAN in content:
            print('前置不满足：队列规则还没有 iifname "br-lan"。')
            print('请先执行 scripts/11-patch-ua2f-lan-only.py')
            sys.exit(2)
        else:
            print("PATTERN_NOT_FOUND：UA2F 版本可能不同，请手工检查 $nfqueue_expr 队列行")
            sys.exit(2)

        # ---- 校验（不用 diff）----
        rc, out, _ = run(client, "sh -n %s && echo SYNTAX_OK" % INIT)
        syntax_ok = "SYNTAX_OK" in out
        print("语法检查: %s" % ("OK" if syntax_ok else "FAIL"))

        rc, out, _ = run(client, "grep -c header-only %s" % INIT)
        n = out.strip()
        count_ok = n.isdigit() and int(n) == 2
        print("header-only 旁路规则: %s 条（期望 2）" % n)

        rc, out, _ = run(client, ORDER_CHECK)
        order_ok = "ORDER_OK" in out
        print("顺序检查（旁路必须在 queue 之前）: %s" % out.strip())

        if not (syntax_ok and count_ok and order_ok):
            print("\nFAIL：校验未通过，请检查上面的输出")
            sys.exit(1)

        if IS_TEST:
            print("（K2P_UA2F_INIT 指向测试副本，已跳过重启，现网未受影响）")
            return

        print("\n=== restart ua2f ===")
        print(run(client, "/etc/init.d/ua2f restart", timeout=120)[1].strip())
        rc, out, _ = run(client, "sleep 3; pgrep -af ua2f")
        print(out.strip())
        if not out.strip():
            print("FAIL：重启后没有 ua2f 进程")
            sys.exit(1)

        print("\n=== postrouting chain ===")
        print(run(client, "nft list table inet ua2f | sed -n '/chain postrouting/,$p'")[1].strip())

        print("\n验证：")
        print("1. 上面两条 bypass header-only 规则应出现在 queue 行之前；")
        print("2. 满速下载几十秒后，bypass 计数应远大于 queue 计数（队列降到十几包/秒）；")
        print("3. 回归验证 UA 改写仍生效（内网客户端访问 http://ua-check.stagoh.com/）。")
    finally:
        client.close()


if __name__ == "__main__":
    main()
