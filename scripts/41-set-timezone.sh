#!/bin/sh
# 41 · 时区与 NTP 源（在路由器上执行）
#
#   scp 41-set-timezone.sh root@192.168.1.1:/tmp/ && ssh root@192.168.1.1 'sh /tmp/41-set-timezone.sh'
#   或： ssh root@192.168.1.1 'sh -s' < 41-set-timezone.sh
#
# 说明：校园网下 ntp.tencent.com 无法解析，改用 time1.cloud.tencent.com。

echo '=== BEFORE ==='
uci show system | grep -E 'zonename|timezone'
uci show system.ntp

echo
echo '=== SET TIMEZONE ==='
uci set system.@system[0].zonename='Asia/Shanghai'
uci set system.@system[0].timezone='CST-8'

echo '=== SET NTP SERVERS ==='
uci -q delete system.ntp.server
uci add_list system.ntp.server='ntp.aliyun.com'
uci add_list system.ntp.server='ntp1.aliyun.com'
uci add_list system.ntp.server='time1.cloud.tencent.com'
uci add_list system.ntp.server='ntp.ntsc.ac.cn'
uci add_list system.ntp.server='cn.pool.ntp.org'
uci set system.ntp.enabled='1'
# 注意：这里保持 enable_server=0；NTP 服务端模式由 21-setup-ntp-redirect.py 开启
uci commit system

echo
echo '=== AFTER ==='
uci show system | grep -E 'zonename|timezone'
uci show system.ntp

echo
echo '=== ENABLE + START NTPD ==='
/etc/init.d/sysntpd enable
/etc/init.d/sysntpd restart
sleep 4

echo '=== APPLY TZ ==='
/etc/init.d/system reload 2>/dev/null || /etc/init.d/system restart
sleep 3

echo
echo '=== VERIFY ==='
echo -n 'date      : '; date
echo -n 'date -u   : '; date -u
echo -n '/tmp/TZ   : '; cat /tmp/TZ 2>/dev/null
echo -n 'localtime : '; ls -la /etc/localtime 2>&1
echo '=== NTPD PROC ==='
pgrep -af 'ntpd' | head -5
echo '=== NTP SYNC NOW ==='
ntpd -n -q -p ntp.aliyun.com 2>&1 | head -3
echo -n 'final date: '; date