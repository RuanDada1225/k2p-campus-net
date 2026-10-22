#!/usr/bin/env python3
"""30 · 给认证客户端加开机延迟（5s -> 30s）。

问题：/etc/init.d/esurfingclient 的 boot() 默认 sleep 5，
      开机 5 秒时 WAN 还没就绪，认证必然失败并进入硬编码退避表
      `{1, 5, 10, 20, 30}` 分钟（最坏要等 30 分钟），
      导致重启后要好几分钟才有网。

解决：把 boot() 里的 sleep 5 改成 sleep 30。

如需适配其他认证客户端，修改 CLIENT_INIT 即可。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup  # noqa: E402

CLIENT_INIT = os.environ.get("K2P_AUTH_INIT", "/etc/init.d/esurfingclient")
SED = "sed -i '/^boot() {/,/^}/ s/sleep 5/sleep 30/' " + CLIENT_INIT
CHECK = "sed -n '/^boot() {/,/^}/p' " + CLIENT_INIT


def main():
    client = connect()
    try:
        rc, out, err = run(client, CHECK)
        before = out.strip()
        print("--- BEFORE ---")
        print(before or "(未找到 boot() 函数，请确认路径：%s)" % CLIENT_INIT)
        if not before:
            sys.exit(2)

        # 已经是 sleep 30 就别再动（幂等 + 少一次写盘）
        if "sleep 30" in before and "sleep 5" not in before:
            print("RESULT=ALREADY_PATCHED")
            return

        bak = backup(client, CLIENT_INIT)
        print("BACKUP=%s" % bak)
        if not bak:
            print("FAIL：备份失败，未做任何修改")
            sys.exit(1)

        rc, out, err = run(client, SED)
        print("SED_RC=%d %s" % (rc, err.strip()))

        rc, out, err = run(client, CHECK)
        after = out.strip()
        print("--- AFTER ---")
        print(after)

        # 断言：真的改成了 30。以前只打印前后对照让人肉眼比对，看漏就等于没改。
        if "sleep 30" not in after or "sleep 5" in after:
            print("\nFAIL：sleep 5 -> 30 没有生效（sed 模式可能没匹配上）")
            print("回滚：cp '%s' '%s'" % (bak, CLIENT_INIT))
            sys.exit(1)
        print("\nVERIFY=ok  boot() 已改为 sleep 30")

        print("\n验证：重启路由器后 logread | grep -i esurfing，应一次认证成功、无退避。")
    finally:
        client.close()


if __name__ == "__main__":
    main()