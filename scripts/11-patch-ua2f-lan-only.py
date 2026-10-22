#!/usr/bin/env python3
"""11 · 让 UA2F 只处理 br-lan 入向流量（关键步骤）。

问题：UA2F 默认会处理所有转发 TCP 流量，会把路由器自身发起的
      认证 HTTP 请求（如校园网门户配置获取）也改写掉，导致认证失败。

解决：在 /etc/init.d/ua2f 的 NFQUEUE 规则里加上 iifname "br-lan"，
      只处理内网客户端流量。

行为
----
- 改前按 `<path>.bak.<ts>` 备份（统一命名，见 scripts/README.md）
- 用 upload_atomic 原子替换，避免中断留下半截 init 脚本
- 校验不依赖 `diff`（busybox 没有这个命令）：改看 `sh -n` 语法 + 队列行是否带 iifname
- 失败 → 退出码 1

前置：已安装 ua2f 包（见 docs/02）。本脚本只改 init 脚本，不动 uci 配置。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup, upload_atomic  # noqa: E402

INIT = os.environ.get("K2P_UA2F_INIT", "/etc/init.d/ua2f")   # 测试时可指向副本
IS_TEST = INIT != "/etc/init.d/ua2f"
OLD = "meta l4proto tcp ct direction original counter $nfqueue_expr;"
NEW = 'meta l4proto tcp ct direction original iifname "br-lan" counter $nfqueue_expr;'


def main():
    client = connect()
    try:
        # 前置检查：ua2f 得先装上，否则改的是一个不存在的文件
        rc, out, _ = run(client, "command -v ua2f || echo NO_UA2F")
        if "NO_UA2F" in out:
            print("前置不满足：未安装 ua2f。先执行 scripts/10-setup-ua2f.py 与 docs/02 的 apk 安装步骤。")
            sys.exit(2)

        rc, content, err = run(client, "cat %s" % INIT)
        if rc != 0 or "queue num" not in content:
            print("读取 %s 失败 rc=%d err=%s" % (INIT, rc, err))
            sys.exit(1)

        if NEW in content:
            print("RESULT=ALREADY_PATCHED")
        elif OLD in content:
            bak = backup(client, INIT)
            print("BACKUP=%s" % bak)
            if not bak:
                print("FAIL：备份失败，未做任何修改")
                sys.exit(1)
            content = content.replace(OLD, NEW, 1)
            rc = upload_atomic(client, content, INIT)
            print("RESULT=PATCHED write_rc=%d" % rc)
            if rc != 0:
                sys.exit(1)
        else:
            print("PATTERN_NOT_FOUND：UA2F 版本可能不同，请手工检查 NFQUEUE 规则")
            sys.exit(2)

        # ---- 校验（不用 diff）----
        rc, out, _ = run(client, "sh -n %s && echo SYNTAX_OK" % INIT)
        syntax_ok = "SYNTAX_OK" in out
        print("语法检查: %s" % ("OK" if syntax_ok else "FAIL"))

        # 队列规则行形如：meta l4proto tcp ct direction original iifname "br-lan" counter $nfqueue_expr;
        # 注意规则行里是变量 $nfqueue_expr，字面 "queue num" 只出现在变量赋值处，别拿它当锚点
        rc, out, _ = run(client, "grep -c 'iifname.*nfqueue_expr' %s" % INIT)
        n = out.strip()
        lan_ok = n.isdigit() and int(n) >= 1
        print("队列行带 iifname \"br-lan\": %s 行（期望 ≥1）" % n)

        print("\n=== 队列规则 ===")
        print(run(client, "grep -n 'nfqueue_expr' %s | grep iifname" % INIT)[1].strip())

        if not (syntax_ok and lan_ok):
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
        print("\n验证：上面的 NFQUEUE 规则应带 iifname \"br-lan\"。")
    finally:
        client.close()


if __name__ == "__main__":
    main()
