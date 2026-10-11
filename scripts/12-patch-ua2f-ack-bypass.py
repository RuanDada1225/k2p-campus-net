#!/usr/bin/env python3
"""12 · 让 UA2F 跳过「纯 ACK 短包」（性能关键）。

问题：NFQUEUE 规则匹配 br-lan 全部原始方向 TCP。大流量下载（如 Steam 走
      80 端口）时，99% 的包是无负载的纯 ACK —— 它们不可能包含 User-Agent，
      但每包都要走一遍「内核 → 用户态 → 判决」的 NFQUEUE 往返。
      实测满速下载时队列约 3100 包/秒，CPU sys 被吃满、下载变卡。

解决：在入队规则之前插入两条「按长度旁路」规则：
        IPv4：ip length 1-80    （纯 ACK ≤60B，取总长阈值并留余量）
        IPv6：ip6 length 1-104  （注意 ip6 length 是负载长度，同为留余量的阈值）
      真实 HTTP 请求（请求行 + Host + User-Agent）远大于该长度，不会被旁路。

实测效果：队列 3100 → 10~16 包/秒，sys 下降约 28%，同线路下载 +10%~15%。

前置：必须已执行 11-patch-ua2f-lan-only.py（本脚本锚定带 iifname "br-lan"
      的队列行）。备份写入 /etc/init.d/ua2f.ack.orig，不覆盖 11 号的 .orig 基线。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

INIT = "/etc/init.d/ua2f"
BACKUP = INIT + ".ack.orig"

QUEUE_LINE = 'meta l4proto tcp ct direction original iifname "br-lan" counter $nfqueue_expr;'
QUEUE_LINE_NO_LAN = 'meta l4proto tcp ct direction original counter $nfqueue_expr;'
BYPASS_V4 = ('meta l4proto tcp ct direction original iifname "br-lan" '
             'ip length 1-80 counter return comment "!ua2f: bypass header-only ACK v4";')
BYPASS_V6 = ('meta l4proto tcp ct direction original iifname "br-lan" '
             'ip6 length 1-104 counter return comment "!ua2f: bypass header-only ACK v6";')


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
            run(client, "cp %s %s" % (INIT, BACKUP))
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if QUEUE_LINE in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    lines[i:i] = [indent + BYPASS_V4, indent + BYPASS_V6]
                    break
            rc = upload(client, "\n".join(lines), INIT)
            print("RESULT=PATCHED write_rc=%d" % rc)
        elif QUEUE_LINE_NO_LAN in content:
            print('请先执行 11-patch-ua2f-lan-only.py（队列规则还没有 iifname "br-lan"）')
            sys.exit(2)
        else:
            print("PATTERN_NOT_FOUND：UA2F 版本可能不同，请手工检查 $nfqueue_expr 队列行")
            sys.exit(2)

        print("=== diff vs backup ===")
        print(run(client, "diff %s %s" % (BACKUP, INIT))[1])

        print("=== restart ua2f ===")
        print(run(client, "/etc/init.d/ua2f restart", timeout=120)[1])
        print(run(client, "sleep 3; pgrep -af ua2f")[1])

        print("=== postrouting chain ===")
        print(run(client, "nft list table inet ua2f | sed -n '/chain postrouting/,$p'")[1])

        print("\n验证：")
        print("1. 上面两条 bypass header-only 规则应出现在 queue 行之前；")
        print("2. 满速下载几十秒后，bypass 计数应远大于 queue 计数（队列降到十几包/秒）；")
        print("3. 回归验证 UA 改写仍生效（内网访问 http://ua-check.stagoh.com/）。")
    finally:
        client.close()


if __name__ == "__main__":
    main()