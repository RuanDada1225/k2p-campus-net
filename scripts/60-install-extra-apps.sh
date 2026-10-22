#!/bin/sh
# 60 · 安装常用插件并调整 watchcat 探测目标（在路由器上执行）
#
# 前提：软件源已就绪。OpenWrt 25.12 的 apk feed 已配在
#       /etc/apk/repositories.d/distfeeds.list，不要手工创建 /etc/apk/repositories。
#
# 装完后：
#   nlbwmon  —— 流量统计（数据库默认在 tmpfs，重启清空；本项目实机已卸载，按需安装）
#   watchcat —— 断网自动重启；探测目标改成国内可达地址

echo "=== 空间检查 ==="
df -h /overlay

echo "=== apk update ==="
apk update 2>&1 | tail -5

echo "=== 安装 nlbwmon（可选，默认跳过）==="
# 本项目实机已卸载 nlbwmon：它的数据库在 tmpfs，重启清零，价值有限（见 docs/04 4.5）。
# 以前这里是无条件安装，等于「修个 watchcat 顺手把卸掉的包又装回来」。
if [ "${INSTALL_NLBWMON:-0}" = "1" ]; then
    apk add luci-app-nlbwmon 2>&1 | tail -5
else
    echo "  跳过（需要时：INSTALL_NLBWMON=1 sh 60-install-extra-apps.sh）"
fi

echo "=== 安装 watchcat ==="
apk add luci-app-watchcat 2>&1 | tail -5

echo "=== 修改 watchcat 探测目标 -> 223.5.5.5 ==="
uci set watchcat.@watchcat[0].pinghosts='223.5.5.5'
uci commit watchcat && echo UCI_OK
uci show watchcat

echo "=== 重启 watchcat ==="
/etc/init.d/watchcat restart 2>&1
sleep 3
pgrep -af watchcat.sh

echo "=== 服务状态 ==="
if [ "${INSTALL_NLBWMON:-0}" = "1" ]; then
    ubus call service list '{"name":"nlbwmon"}' 2>/dev/null | grep -o '"running": *true' || echo "  nlbwmon: 未运行"
fi
ubus call service list '{"name":"watchcat"}' 2>/dev/null | grep -o '"running": *true' || echo "  watchcat: 未运行"

echo "=== 剩余空间 ==="
df -h /overlay