#!/usr/bin/env python3
"""40 · 内核 socket 缓冲调优，消除 UA2F 的 conntrack 报错。

问题：默认 net.core.rmem_max 只有 176KB（180224 字节），UA2F 的 netlink socket
      接收缓冲溢出，反复报：
        Conntrack catch error: No buffer space available
      （该报错只影响事件通知，不影响数据面包改写，但会刷屏）

解决：把 rmem_max / rmem_default 调到 4MB 并写入 /etc/sysctl.conf 持久化，
      然后重启 ua2f 让其重建 socket。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run  # noqa: E402

MARK = "# ua2f netlink buffer tuning"
LINES = [
    MARK,
    "net.core.rmem_max = 4194304",
    "net.core.rmem_default = 4194304",
]


def main():
    client = connect()
    try:
        # 1. 去重：删掉可能已存在的旧块
        run(client, "sed -i '/%s/,$d' /etc/sysctl.conf" % MARK.replace(" ", "\\ "))

        # 2. 追加新块
        payload = "\\n".join(LINES) + "\\n"
        rc, out, err = run(client, "printf '%s' >> /etc/sysctl.conf" % payload)
        print("append rc=%d err=%s" % (rc, err.strip()))

        # 3. 生效
        rc, out, err = run(client, "sysctl -p /etc/sysctl.conf")
        print("--- sysctl -p ---")
        print(out.strip())
        if err.strip():
            print("ERR:", err.strip())

        # 4. 验证
        rc, out, err = run(client,
                           "sysctl net.core.rmem_max net.core.rmem_default; "
                           "echo '--- file tail ---'; tail -5 /etc/sysctl.conf")
        print("--- verify ---")
        print(out.strip())

        # 5. 重启 ua2f 让其重建 socket
        rc, out, err = run(client, "/etc/init.d/ua2f restart; sleep 2; pgrep -af ua2f")
        print("--- ua2f restart ---")
        print(out.strip())
    finally:
        client.close()


if __name__ == "__main__":
    main()