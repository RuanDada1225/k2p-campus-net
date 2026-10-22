#!/usr/bin/env python3
"""10 · 安装并配置 UA2F（NFQUEUE 模式）。

前提：ua2f 及其依赖已通过 apk 安装（见 docs/02-部署实战.md）。
本脚本只负责写配置并重启服务。

两种模式（2026-10 修订，修掉「重跑把 nfqueue_workers 打回 1」的坑）
------------------------------------------------------------------
- **配置文件已存在**：先备份，再只用 `uci set` 合并本脚本负责的键。
  `nfqueue_workers` 不在管辖范围内，保持现值不变（它属于 06 篇的性能调优，
  以前整份覆盖会让 4 静默回退成 1）。
- **配置文件不存在**：才写入完整模板（首次部署）。
- 写盘后**回读校验** `custom_ua`；不一致或任一步失败 → 退出码 1，且不重启服务。

环境变量
--------
    K2P_UA2F_CONF   配置文件路径，默认 /etc/config/ua2f
                    （测试时可指向 /tmp 下的副本，不碰现网、也不重启服务）
    UA2F_WORKERS    显式设置 nfqueue_workers（不设 = 保持现值）

注意：custom_ua 用 'Safari/537.'（11 字节）——它有 11 字节硬截断（设长了只生效前 11 字节），
且必须含 Safari/ 关键字以过 EdgeOne 类 WAF，详见 docs/01-检测原理.md 与 docs/03 坑 20。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

CUSTOM_UA = "Safari/537."
LISTEN_PORT = "10010"
FRESH_WORKERS = "4"          # 仅首次部署时写入；已存在则不动

# 配置文件不存在时写入的完整模板
TEMPLATE = """config ua2f 'enabled'
    option enabled '1'

config ua2f 'firewall'
    option handle_fw '1'
    option handle_tls '0'
    option handle_intranet '0'

config ua2f 'main'
    option mode 'NFQUEUE'
    option listen_port '%s'
    option nfqueue_workers '%s'
    option proxy_workers '0'
    option custom_ua '%s'
    option disable_connmark '0'
    option max_http_sessions '0'
    option session_ttl '300'
""" % (LISTEN_PORT, FRESH_WORKERS, CUSTOM_UA)

REMOTE_SCRIPT = r"""#!/bin/sh
set -e

CONF="__CONF__"
CONF_DIR=$(dirname "$CONF")
CONF_NAME=$(basename "$CONF")
UCI="uci -c $CONF_DIR"

if [ -f "$CONF" ]; then
    echo "MODE=merge  conf=$CONF"
    BAK="$CONF.bak.$(date +%s)"
    cp "$CONF" "$BAK"
    echo "BACKUP=$BAK"

    # 分段缺失则创建；已有则只改本脚本管辖的键
    $UCI get "$CONF_NAME.main"     >/dev/null 2>&1 || $UCI set "$CONF_NAME.main=$CONF_NAME"
    $UCI get "$CONF_NAME.firewall" >/dev/null 2>&1 || $UCI set "$CONF_NAME.firewall=$CONF_NAME"
    $UCI get "$CONF_NAME.enabled"  >/dev/null 2>&1 || $UCI set "$CONF_NAME.enabled=$CONF_NAME"

    $UCI set "$CONF_NAME.enabled.enabled=1"
    $UCI set "$CONF_NAME.firewall.handle_fw=1"
    $UCI set "$CONF_NAME.firewall.handle_tls=0"
    $UCI set "$CONF_NAME.firewall.handle_intranet=0"
    $UCI set "$CONF_NAME.main.mode=NFQUEUE"
    $UCI set "$CONF_NAME.main.listen_port=__PORT__"
    $UCI set "$CONF_NAME.main.custom_ua=__UA__"

    if [ -n "__WORKERS__" ]; then
        $UCI set "$CONF_NAME.main.nfqueue_workers=__WORKERS__"
        echo "WORKERS=set(__WORKERS__)"
    else
        echo "WORKERS=untouched($($UCI get "$CONF_NAME.main.nfqueue_workers" 2>/dev/null || echo unset))"
    fi
else
    echo "MODE=template  conf=$CONF"
    mkdir -p "$CONF_DIR"
    cat > "$CONF" <<'UA2F_TEMPLATE_EOF'
__TEMPLATE__UA2F_TEMPLATE_EOF
fi

$UCI commit "$CONF_NAME"
echo "COMMIT=ok"

# 回读校验：写没写进去以读出来的为准
GOT=$($UCI get "$CONF_NAME.main.custom_ua")
[ "$GOT" = "__UA__" ] || { echo "FAIL: custom_ua=$GOT (期望 __UA__)"; exit 1; }
echo "VERIFY=ok custom_ua=$GOT"

# handle_tls / handle_intranet 直接决定「会不会把认证流量也改坏」，必须一起验
for kv in handle_tls:0 handle_intranet:0; do
    k=${kv%%:*}; want=${kv##*:}
    got=$($UCI get "$CONF_NAME.firewall.$k")
    [ "$got" = "$want" ] || { echo "FAIL: firewall.$k=$got (期望 $want)"; exit 1; }
    echo "VERIFY=ok firewall.$k=$got"
done

echo "=== $CONF_NAME show ==="
$UCI show "$CONF_NAME"
"""

RESTART_SCRIPT = r"""#!/bin/sh
set -e
echo "=== restart ua2f ==="
/etc/init.d/ua2f enable
/etc/init.d/ua2f restart
sleep 3
echo "=== process ==="
pgrep -af ua2f
echo "=== nft queue rule ==="
nft list table inet ua2f 2>/dev/null | grep -E 'queue|dport 80'
echo "=== version ==="
ua2f --version 2>/dev/null | head -2 || true
"""


def main():
    conf = os.environ.get("K2P_UA2F_CONF", "/etc/config/ua2f")
    workers = os.environ.get("UA2F_WORKERS", "").strip()

    script = (REMOTE_SCRIPT
              .replace("__CONF__", conf)
              .replace("__PORT__", LISTEN_PORT)
              .replace("__UA__", CUSTOM_UA)
              .replace("__WORKERS__", workers)
              .replace("__TEMPLATE__", TEMPLATE))

    client = connect()
    try:
        upload(client, script, "/tmp/setup_ua2f_conf.sh")
        rc, out, err = run(client, "sh /tmp/setup_ua2f_conf.sh", timeout=120)
        print(out.rstrip())
        if err.strip():
            print("--- STDERR ---")
            print(err.rstrip())
        if rc != 0:
            print("\nFAIL：写配置失败（rc=%d），**未重启服务**，现网仍在用旧配置" % rc)
            sys.exit(1)

        # 指向测试副本时跳过重启，避免影响现网
        if conf != "/etc/config/ua2f":
            print("\n（K2P_UA2F_CONF 指向测试副本，已跳过重启，现网未受影响）")
            return

        upload(client, RESTART_SCRIPT, "/tmp/setup_ua2f_restart.sh")
        rc, out, err = run(client, "sh /tmp/setup_ua2f_restart.sh", timeout=120)
        print(out.rstrip())
        if err.strip():
            print("--- STDERR ---")
            print(err.rstrip())
        if rc != 0:
            print("\nFAIL：重启/自检失败（rc=%d）" % rc)
            sys.exit(1)

        print("\n提示：下一步务必执行 11-patch-ua2f-lan-only.py，")
        print("      否则 UA2F 会改写路由器自身的认证流量，导致校园网认证失败。")
    finally:
        client.close()


if __name__ == "__main__":
    main()
