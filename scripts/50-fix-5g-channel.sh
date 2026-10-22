#!/bin/sh
# 50 · 5G 固定到非 DFS 信道（在路由器上执行）
#
# 问题：radio1.channel='auto' 时 ACS 可能选到 DFS 信道（如 56），
#       开机触发 60s CAC 雷达检测，导致重启后约 70s 没有 5G。
# 解决：固定到 CN 下的非 DFS 信道 149。
#
# 环境变量（移植时改这里，不用改脚本）
#   RADIO=radio1  VIF=phy1-ap0  VIF_2G=phy0-ap0  CHANNEL=149  WAIT=40
#
# 注意：不同设备/国家的可用信道不同，请先 iw phy phy1 info 确认。
#   CN 非 DFS：2.4G 用 1/6/11；5G 用 36/40/44/48 或 149~165
#   CN DFS  ：52~64；100~144 不可用
#
# 为什么不再固定 sleep 30：wifi reload 后信道就绪时间不固定，
# 固定等待既可能等到一半看着像「没生效」，也可能白等十几秒。
# 现在轮询到信道对上就退出，并在末尾断言（不符 → 退出码 1）。

RADIO="${RADIO:-radio1}"
VIF="${VIF:-phy1-ap0}"
VIF_2G="${VIF_2G:-phy0-ap0}"
CHANNEL="${CHANNEL:-149}"
WAIT="${WAIT:-40}"

cur() {
    iw dev "$1" info 2>/dev/null | sed -n 's/.*channel \([0-9]*\).*/\1/p'
}

echo "=== BEFORE ==="
echo "radio=$RADIO  vif=$VIF  目标信道=$CHANNEL"
echo "当前 uci: $(uci -q get "wireless.$RADIO.channel" || echo '(未设置)')"
iw dev "$VIF" info 2>&1 | grep -E 'channel|ssid'

echo "=== SET channel $CHANNEL ==="
uci set "wireless.$RADIO.channel=$CHANNEL"
uci commit wireless
echo "committed: $(uci -q get "wireless.$RADIO.channel")"

echo "=== RELOAD WIFI ==="
wifi reload

i=0
got=""
while [ "$i" -lt "$WAIT" ]; do
    got=$(cur "$VIF")
    [ "$got" = "$CHANNEL" ] && break
    i=$((i + 1))
    sleep 1
done
echo "等待 ${i}s 后 $VIF 信道=${got:-未知}"

echo "=== AFTER ==="
iw dev "$VIF" info 2>&1 | grep -E 'channel|ssid'
echo "--- 2.4G ($VIF_2G) ---"
iw dev "$VIF_2G" info 2>&1 | grep -E 'channel|ssid'

echo "=== DFS/CAC LOG ($CHANNEL 不应出现 CAC) ==="
logread | grep -iE 'DFS|CAC|ACS|AP-ENABLED' | tail -15

echo "=== 断言 ==="
if [ "$got" = "$CHANNEL" ]; then
    echo "VERIFY=ok（$VIF 已固定在信道 $CHANNEL）"
else
    echo "VERIFY=fail：$VIF 当前信道=${got:-未知}（期望 $CHANNEL）"
    echo "  排查：该信道是否被本机支持（iw phy phy1 info 里的 * 标记）、国家码是否限制；"
    echo "        也可改 CHANNEL=36 再试（36/40/44/48 同属非 DFS）。"
    exit 1
fi
