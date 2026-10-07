#!/bin/sh
# 60 · 安装常用插件并调整 watchcat 探测目标（在路由器上执行）
#
# 前提：软件源已就绪。OpenWrt 25.12 的 apk feed 已配在
#       /etc/apk/repositories.d/distfeeds.list，不要手工创建 /etc/apk/repositories。
#
# 装完后：
#   nlbwmon  —— 流量统计（数据库默认在 tmpfs，重启清空）
#   watchcat —— 断网自动重启；探测目标改成国内可达地址

echo "=== 空间检查 ==="
df -h /overlay

echo "=== apk update ==="
apk update 2>&1 | tail -5

echo "=== 安装 nlbwmon ==="
apk add luci-app-nlbwmon 2>&1 | tail -5

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
ubus call service list '{"name":"nlbwmon"}' 2>/dev/null | grep -o '"running": *true'
ubus call service list '{"name":"watchcat"}' 2>/dev/null | grep -o '"running": *true'

echo "=== 剩余空间 ==="
df -h /overlay