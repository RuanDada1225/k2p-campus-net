#!/usr/bin/env python3
"""11 · 让 UA2F 只处理 br-lan 入向流量（关键步骤）。

问题：UA2F 默认会处理所有转发 TCP 流量，会把路由器自身发起的
      认证 HTTP 请求（如校园网门户配置获取）也改写掉，导致认证失败。

解决：在 /etc/init.d/ua2f 的 NFQUEUE 规则里加上 iifname "br-lan"，
      只处理内网客户端流量。

本脚本会先备份到 /etc/init.d/ua2f.orig。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

INIT = "/etc/init.d/ua2f"
OLD = "meta l4proto tcp ct direction original counter $nfqueue_expr;"
NEW = 'meta l4proto tcp ct direction original iifname "br-lan" counter $nfqueue_expr;'


def main():
    client = connect()
    try:
        rc, content, err = run(client, "cat %s" % INIT)
        if rc != 0 or "queue num" not in content:
            print("读取 %s 失败 rc=%d err=%s" % (INIT, rc, err))
            sys.exit(1)

        if NEW in content:
            print("RESULT=ALREADY_PATCHED")
        elif OLD in content:
            run(client, "cp %s %s.orig" % (INIT, INIT))
            content = content.replace(OLD, NEW, 1)
            rc = upload(client, content, INIT)
            print("RESULT=PATCHED write_rc=%d" % rc)
        else:
            print("PATTERN_NOT_FOUND：UA2F 版本可能不同，请手工检查 NFQUEUE 规则")
            sys.exit(2)

        print("=== diff vs backup ===")
        print(run(client, "diff %s.orig %s" % (INIT, INIT))[1])

        print("=== restart ua2f ===")
        print(run(client, "/etc/init.d/ua2f restart", timeout=120)[1])
        print(run(client, "sleep 3; pgrep -af ua2f")[1])

        print("=== postrouting chain ===")
        print(run(client, "nft list table inet ua2f | sed -n '/chain postrouting/,$p'")[1])

        print("\n验证：确认输出里 NFQUEUE 规则带有 iifname \"br-lan\"。")
    finally:
        client.close()


if __name__ == "__main__":
    main()