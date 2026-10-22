#!/bin/sh
# 48-tune-sysctl.sh —— 内核调优一次性落地（幂等、可回滚）
#
# 用法（在路由器上执行）：
#   ssh root@192.168.1.1 'sh -s' < scripts/48-tune-sysctl.sh
#
# 为什么需要它
# ------------
# 40-tune-rmem.py 只写 rmem 两项；tcp_max_syn_backlog / wmem_max / netdev_budget
# 原来只存在于 docs/06 的手工命令里 —— 重装或换机时按脚本顺序走会漏掉这三项，
# 而 docs/04 的检查表和 91 体检都要求它们（4096 / 4MB / 600）。
#
# 设计
# ----
#   1) 按「键名」逐条去重，只动下面这 5 个键，不碰文件里其它内容
#   2) 改前备份 /etc/sysctl.conf
#   3) 写入后逐项回读校验，任一不符 → 退出码 1
#   4) 顺带清掉 40 号脚本留下的旧标记注释，避免两套块并存
#
# 环境变量
# --------
#   SYSCTL_CONF   默认 /etc/sysctl.conf；指向别处时只校验文件内容、不执行 sysctl -p

set -u

CONF="${SYSCTL_CONF:-/etc/sysctl.conf}"
MARK="# k2p kernel tuning - managed by scripts/48-tune-sysctl.sh"

PARAMS='net.core.rmem_max=4194304
net.core.rmem_default=4194304
net.ipv4.tcp_max_syn_backlog=4096
net.core.wmem_max=4194304
net.core.netdev_budget=600'

[ "$(id -u)" = 0 ] || { echo "请用 root 执行"; exit 1; }
[ -f "$CONF" ] || : > "$CONF"

esc() { printf '%s' "$1" | sed 's/\./\\./g'; }

echo "=== BEFORE ==="
echo "$PARAMS" | while IFS='=' read -r k v; do
    printf '  %-32s %s\n' "$k" "$(sysctl -n "$k" 2>/dev/null || echo '?')"
done

BAK="$CONF.bak.$(date +%s)"
cp "$CONF" "$BAK"
echo "备份: $BAK"

echo "=== 去重并写入 ==="
echo "$PARAMS" | while IFS='=' read -r k v; do
    sed -i "/^$(esc "$k")[[:space:]]*=/d" "$CONF"
done
# 清掉 40 号留下的旧标记（它的内容已并入本脚本管理）
sed -i "/^# ua2f netlink buffer tuning\$/d" "$CONF"
sed -i "/^# k2p kernel tuning/d" "$CONF"

{
    echo "$MARK"
    echo "$PARAMS"
} >> "$CONF"
echo "已写入 $CONF"

echo "=== 生效 ==="
if [ "$CONF" = "/etc/sysctl.conf" ]; then
    if ! sysctl -p "$CONF" >/dev/null 2>&1; then
        echo "FAIL: sysctl -p 失败，内核参数未生效"
        exit 1
    fi
    echo "sysctl -p 完成"
else
    echo "（测试路径，跳过 sysctl -p）"
fi

echo "=== 校验 ==="
# 注意：while 放在管道里会进子 shell，变量传不出来，所以把结果收集成字符串再判断
report=$(echo "$PARAMS" | while IFS='=' read -r k v; do
    if [ "$CONF" = "/etc/sysctl.conf" ]; then
        got=$(sysctl -n "$k" 2>/dev/null || echo '?')
    else
        got=$(sed -n "s/^$(esc "$k")[[:space:]]*=[[:space:]]*//p" "$CONF" | tail -1)
    fi
    if [ "$got" = "$v" ]; then
        printf 'OK    %-32s %s\n' "$k" "$got"
    else
        printf 'FAIL  %-32s %s (期望 %s)\n' "$k" "$got" "$v"
    fi
done)

echo "$report" | sed 's/^/  /'
if echo "$report" | grep -q '^FAIL'; then
    echo
    echo "FAIL: 有参数未生效（内核可能不支持该键，见 sysctl -p 的报错）"
    exit 1
fi

echo
echo "全部通过。回滚：cp '$BAK' '$CONF' && sysctl -p '$CONF'"
