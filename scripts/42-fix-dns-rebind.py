#!/usr/bin/env python3
"""42 · 关闭 dnsmasq 反重绑定保护：修复校内站点（教务系统等）打不开。

现象
----
外网一切正常，但学校教务系统 / 内网站点打不开；换成手机热点就正常。

根因
----
dnsmasq 默认开启**反重绑定保护**（`rebind_protection=1`），会丢弃
「公网域名解析到私有 IP」的 DNS 应答 —— 这是防 DNS rebinding 攻击的正常机制。
但很多学校的校内系统就是这么解析的（校园网内部用私有地址互访），于是被误伤。

本脚本做的就是把该保护关掉。

用法
----
    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    python 42-fix-dns-rebind.py

安全提示
--------
关掉保护后，内网 DNS 不再拦截「公网域名 → 私有 IP」的应答，理论上增加了
DNS rebinding 风险。如果只想放行校内域名，更稳妥的做法是改用白名单：

    uci add_list dhcp.@dnsmasq[0].rebind_domain='example.edu.cn'

（把 example.edu.cn 换成你学校的域名后缀，多个域名就 add_list 多次。）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run  # noqa: E402

# 用来验证的校内域名（示例占位，换成你学校的）
PROBE_DOMAIN = "jwxt.example.edu.cn"

CMD = r"""
echo '=== BEFORE: rebind_protection ==='
uci get dhcp.@dnsmasq[0].rebind_protection 2>/dev/null || echo '(未显式设置，默认 1)'

echo
echo '=== BEFORE: dnsmasq 启动参数 ==='
ps w | grep '[d]nsmasq' | grep -o 'stop-dns-rebind' || echo '(无 --stop-dns-rebind)'

echo
echo '=== 应用修改 ==='
uci set dhcp.@dnsmasq[0].rebind_protection='0'
uci commit dhcp
echo "rebind_protection = $(uci get dhcp.@dnsmasq[0].rebind_protection)"

echo
echo '=== 重启 dnsmasq ==='
/etc/init.d/dnsmasq restart
sleep 4

echo
echo '=== AFTER: 进程参数 ==='
if ps w | grep '[d]nsmasq' | grep -q 'stop-dns-rebind'; then
    echo '⚠️  仍带 --stop-dns-rebind（未生效）'
else
    echo '✅ 已无 --stop-dns-rebind'
fi

echo
echo '=== dnsmasq 运行状态 ==='
pgrep -f dnsmasq >/dev/null && echo '✅ 运行中' || echo '❌ 未运行'

echo
echo '=== 解析校内域名（应能拿到应答） ==='
nslookup __PROBE_DOMAIN__ 127.0.0.1 2>&1 | tail -6

echo
echo '=== 解析公网域名（确认 DNS 整体正常） ==='
nslookup www.baidu.com 127.0.0.1 2>&1 | tail -4
"""


def main():
    client = connect()
    try:
        rc, out, err = run(client, CMD.replace("__PROBE_DOMAIN__", PROBE_DOMAIN), timeout=120)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())
        if rc != 0:
            sys.exit("执行失败 (rc=%d)" % rc)
        print("\n提示：若只想放行校内域名而非全局关闭，见脚本头部注释的 rebind_domain 白名单写法。")
    finally:
        client.close()


if __name__ == "__main__":
    main()