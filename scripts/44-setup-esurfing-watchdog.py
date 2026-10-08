#!/usr/bin/env python3
"""44 · 认证重试加速守护：认证失败后不必干等好几分钟。

现象
----
偶发认证失败（或校园网侧把会话踢掉）后，要等好几分钟才恢复上网。

根因
----
ESurfingClient 内置的重试退避表是**硬编码**的 `{1, 5, 10, 20, 30}` 分钟，最多 5 次，
改配置改不动。所以一次失败，可能要 5 分钟后才轮到下一次重试。

解决
----
部署一个外部守护脚本，用 cron **每分钟**检查一次：

- 认证进程在、且外网可达 → 什么都不做
- 认证进程在、但外网不通 → 重启认证服务
  （重启会顺带触发一次补偿登出，有助于清掉服务端卡住的在线会话）
- 加了 50 秒限流，避免反复重启形成风暴

这样把最坏恢复时间从「分钟级」压到「约 1 分钟」。

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 44-setup-esurfing-watchdog.py

说明
----
守护脚本落在路由器 `/usr/bin/esurfingclient-watchdog.sh`，cron 条目写在
`/etc/crontabs/root`（幂等，重复执行不会重复添加）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run  # noqa: E402

WATCHDOG = r"""#!/bin/sh
# esurfingclient 认证重试加速守护脚本
# 作用: 外网不通(认证未成功)时重启认证服务, 把重试间隔压到约 1 分钟
# 同时重启会触发一次补偿登出, 有助于清掉服务端卡住的在线会话

LOG=/tmp/esurfing-watchdog.log
STAMP=/tmp/esurfing-watchdog.stamp

# 开机 2 分钟内不干预, 避免打断启动流程(程序自身有 30 秒延迟认证)
up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
[ -n "$up" ] && [ "$up" -lt 120 ] && exit 0

# 认证进程不在 -> 不处理
ps w 2>/dev/null | grep -q '[e]surfingclient --role auth' || exit 0

# 外网可达 -> 一切正常
if ping -c 2 -W 2 223.5.5.5 >/dev/null 2>&1 || ping -c 2 -W 2 119.29.29.29 >/dev/null 2>&1; then
    exit 0
fi

# 外网不通 -> 限流(至少间隔 50 秒)后重启认证服务
now=$(date +%s)
last=0
[ -f "$STAMP" ] && last=$(cat "$STAMP" 2>/dev/null)
case "$last" in ''|*[!0-9]*) last=0;; esac
[ $((now - last)) -lt 50 ] && exit 0
echo "$now" > "$STAMP"

/etc/init.d/esurfingclient restart >/dev/null 2>&1
echo "$(date '+%F %T') 外网不通, 已重启认证服务以加速重试" >> "$LOG"
"""

CMD_TEMPLATE = r"""
echo '=== 1. cron 是否可用 ==='
ls -l /etc/init.d/cron 2>/dev/null || echo '(无 cron init 脚本)'
command -v crond || echo '(无 crond)'

echo
echo '=== 2. 写入守护脚本 ==='
cat > /usr/bin/esurfingclient-watchdog.sh <<'WDEOF'
__WATCHDOG__WDEOF
chmod +x /usr/bin/esurfingclient-watchdog.sh
ls -l /usr/bin/esurfingclient-watchdog.sh

echo
echo '=== 3. 添加 cron 条目（幂等）==='
CRON=/etc/crontabs/root
touch $CRON
grep -qF 'esurfingclient-watchdog.sh' $CRON || \
    echo '* * * * * /usr/bin/esurfingclient-watchdog.sh' >> $CRON
echo '--- crontab ---'
cat $CRON

echo
echo '=== 4. 启用并启动 cron ==='
/etc/init.d/cron enable 2>&1
/etc/init.d/cron restart 2>&1 || /etc/init.d/cron start 2>&1
ps w 2>/dev/null | grep '[c]rond' || echo '(crond 未运行)'

echo
echo '=== 5. 手动跑一次（当前有网，应无动作）==='
/usr/bin/esurfingclient-watchdog.sh; echo "rc=$?"
cat /tmp/esurfing-watchdog.log 2>/dev/null || echo '(无日志 = 未触发重启, 正常)'
"""


def main():
    client = connect()
    try:
        cmd = CMD_TEMPLATE.replace("__WATCHDOG__", WATCHDOG)
        rc, out, err = run(client, cmd, timeout=120)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n完成。之后认证失败最多约 1 分钟就会自动重试。")
    finally:
        client.close()


if __name__ == "__main__":
    main()