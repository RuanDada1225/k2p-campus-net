#!/bin/sh
# 50 · 5G 固定到非 DFS 信道（在路由器上执行）
#
# 问题：radio1.channel='auto' 时 ACS 可能选到 DFS 信道（如 56），
#       开机触发 60s CAC 雷达检测，导致重启后约 70s 没有 5G。
# 解决：固定到 CN 下的非 DFS 信道 149。
#
# 注意：不同设备/国家的可用信道不同，请先 iw phy phy1 info 确认。
#   CN 非 DFS：2.4G 用 1/6/11；5G 用 36/40/44/48 或 149~165
#   CN DFS  ：52~64；100~144 不可用

echo "=== BEFORE ==="
uci get wireless.radio1.channel
iw dev phy1-ap0 info 2>&1 | grep -E 'channel|ssid'

echo "=== SET channel 149 ==="
uci set wireless.radio1.channel='149'
uci commit wireless
echo "committed: $(uci get wireless.radio1.channel)"

echo "=== RELOAD WIFI ==="
wifi reload
sleep 30

echo "=== AFTER ==="
iw dev phy1-ap0 info 2>&1 | grep -E 'channel|ssid'
echo "--- 2.4G ---"
iw dev phy0-ap0 info 2>&1 | grep -E 'channel|ssid'

echo "=== DFS/CAC LOG (149 不应出现 CAC) ==="
logread | grep -iE 'DFS|CAC|ACS|AP-ENABLED' | tail -15