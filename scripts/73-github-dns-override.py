#!/usr/bin/env python3
"""73 · 让 GitHub 可用 —— 纯 DNS 覆盖，零进程、零证书、零客户端配置。

为什么这条路能走通（2026-10-27 实测，Steam++ 关闭状态下）
-------------------------------------------------------
之前一直以为 `github.com` 是 IP 层封锁，实测发现是 **DNS 污染 + 部分 IP 被过滤**：

- `nslookup github.com` 会同时返回真实 IP（20.205.243.166）和一堆假 IP
  （`205.251.x.x` 是 AWS Route53 的地址、`198.51.44.x` 是异常段）。
  **而真 IP 里 20.205.243.166 恰好是连不上的那个**，客户端挑中它就超时。
- 逐个 IP 直连实测（同一个出口、同一台 PC）：

  | IP | 结果 |
  |---|---|
  | `140.82.114.3` | **200 / 2.9 秒**（最快）|
  | `20.27.177.113` | 200 / 7.4 秒 |
  | `4.237.22.38` | 200 / 7.1 秒 |
  | `20.205.243.166` | **超时**（DNS 默认给的就是它）|

换成可达 IP 后，`github.com/torvalds/linux` 直接 **200 / 330KB**，
静态资源（`github.githubassets.com`）和头像（`avatars`）也都正常。

为什么路由器自己 fetch 会失败、客户端却能成功
--------------------------------------------
路由器用 `uclient-fetch`（mbedtls）直连可达 IP 仍是 `rc=4`，但 PC 用同一个 IP 就是 200。
差异在路由器**自己的 TLS 栈**（mbedtls 与 GitHub 的握手参数不兼容），不是网络问题。

关键是：**路由器不需要自己访问 GitHub**。手机/电脑发起的是端到端 TLS，
路由器只做 NAT 转发，不参与握手。所以路由器唯一要做的就是把域名解析到可达 IP。

覆盖清单（每个域名只能给一个 IP）
--------------------------------
dnsmasq 的 `address=` **不轮询**，多写几个也只用第一个（实测连查三次都返回第一条）。
所以每条只保留一个实测可达的 IP；失效时改下面的常量重跑即可。

| 域名 | IP | 实测 |
|---|---|---|
| `github.com` | `140.82.114.3` | 200 / 2.9s |
| `github.githubassets.com` | `185.199.108.215` | 连接成功 128KB |
| `avatars.githubusercontent.com` | `185.199.108.133` | 200 |
| `user-images.githubusercontent.com` | `185.199.108.133` | 可达 |
| `raw.githubusercontent.com` | `185.199.108.133` | 6034 字节真实文件 |
| `objects.githubusercontent.com` | `185.199.108.133` | TLS 通 |
| `codeload.github.com` | `20.205.243.165` | tarball 下载正常 |

不做的部分
----------
- `git clone` 走 `github.com` 的 git 协议，需要 `github.com:443` 的 smart HTTP，
  覆盖 DNS 后可以尝试，但 `codeload` 下载 tarball 更稳（`/tar.gz/refs/heads/main`）。
- release 资源域名若失效，可用 `https://ghproxy.net/` 前缀（实测可用）。

用法
----
    export K2P_PASSWORD='...'
    python 73-github-dns-override.py          # 部署/更新（幂等）
    python 73-github-dns-override.py --status # 只看当前解析
    python 73-github-dns-override.py --remove # 撤销全部覆盖
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, backup  # noqa: E402

# 域名 → 单个可达 IP（dnsmasq 只用第一个，所以这里刻意每条一个）
GITHUB_DNS = [
    ("github.com", "140.82.114.3"),
    ("github.githubassets.com", "185.199.108.215"),
    ("avatars.githubusercontent.com", "185.199.108.133"),
    ("user-images.githubusercontent.com", "185.199.108.133"),
    ("raw.githubusercontent.com", "185.199.108.133"),
    ("objects.githubusercontent.com", "185.199.108.133"),
    ("codeload.github.com", "20.205.243.165"),
]

# 备用 IP：主 IP 失效时按顺序替换上面表里的值（实测过的候选）
SPARE_IPS = {
    "github.com": ["140.82.114.3", "20.27.177.113", "4.237.22.38", "140.82.113.3"],
    "raw.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "objects.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "avatars.githubusercontent.com": ["185.199.108.133", "185.199.110.133"],
    "github.githubassets.com": ["185.199.108.215", "185.199.111.215"],
}


def set_dns(client, name, ip):
    """幂等写入一条 dnsmasq 域名覆盖。"""
    rc, out, _ = run(client, r"""
name='__NAME__'
ip='__IP__'
i=0
while uci -q get dhcp.@domain[$i] >/dev/null 2>&1; do
    if [ "$(uci -q get dhcp.@domain[$i].name)" = "$name" ]; then
        uci -q delete dhcp.@domain[$i]; continue
    fi
    i=$((i+1))
done
uci add dhcp domain >/dev/null
uci set dhcp.@domain[-1].name="$name"
uci add_list dhcp.@domain[-1].ip="$ip"
echo "SET $name -> $ip"
""".replace("__NAME__", name).replace("__IP__", ip), timeout=60)
    print("   " + (out.strip() or "失败")[:100])
    return rc == 0


def del_dns(client, name):
    return run(client, r"""
name='__NAME__'
i=0
while uci -q get dhcp.@domain[$i] >/dev/null 2>&1; do
    if [ "$(uci -q get dhcp.@domain[$i].name)" = "$name" ]; then
        uci -q delete dhcp.@domain[$i]; continue
    fi
    i=$((i+1))
done
echo "DEL $name"
""".replace("__NAME__", name), timeout=60)[0] == 0


def show_status(client):
    names = [n for n, _ in GITHUB_DNS]
    cmd = "for d in %s; do\n" % " ".join(names)
    cmd += ("    ip=$(nslookup $d 127.0.0.1 2>/dev/null | awk '/^Address: [0-9]/{print $2; exit}')\n"
            "    printf '  %-42s -> %s\\n' \"$d\" \"${ip:-失败}\"\n"
            "done\n")
    rc, out, _ = run(client, cmd, timeout=90)
    print(out.rstrip())


def main():
    ap = argparse.ArgumentParser(description="GitHub DNS 覆盖（零证书方案）")
    ap.add_argument("--status", action="store_true", help="只查当前解析")
    ap.add_argument("--remove", action="store_true", help="撤销全部覆盖")
    args = ap.parse_args()

    client = connect()
    try:
        if args.status:
            print("=== 当前解析结果")
            show_status(client)
            return

        if args.remove:
            print("=== 撤销 GitHub DNS 覆盖")
            for name, _ in GITHUB_DNS:
                del_dns(client, name)
            run(client, "uci commit dhcp && /etc/init.d/dnsmasq restart")
            print("   已撤销，dnsmasq 已重启")
            return

        print("=== 改前解析")
        show_status(client)

        print("\n=== BACKUP=%s" % backup(client, "/etc/config/dhcp"))
        print("\n=== 写入覆盖")
        for name, ip in GITHUB_DNS:
            set_dns(client, name, ip)

        rc, out, err = run(client, "uci commit dhcp && /etc/init.d/dnsmasq restart && sleep 3 && echo RESTARTED")
        print("\n=== 应用：" + (out.strip() or err.strip())[:80])

        print("\n=== 验证（解析结果应为覆盖 IP）")
        bad = 0
        for name, ip in GITHUB_DNS:
            rc, out, _ = run(client,
                             "nslookup %s 127.0.0.1 2>/dev/null | "
                             "awk '/^Address: [0-9]/{print $2; exit}'" % name, timeout=30)
            got = out.strip()
            ok = (got == ip)
            print("   %-42s %-16s %s" % (name, got or "失败", "OK" if ok else "FAIL（期望 %s）" % ip))
            if not ok:
                bad = 1

        print("\n" + ("VERIFY=ok" if not bad else "VERIFY=fail"))
        print("""
=== 请在你的设备上确认（路由器自己 fetch 不了，是 mbedtls 的问题，不影响客户端）===
  PC/手机浏览器打开 https://github.com/
  命令行：curl -o NUL -w "%%{http_code}\\n" https://github.com/
  预期：200

=== 注意 ===
- dnsmasq 的 address 只返回第一个 IP，所以每条只配一个；失效时按下面的备用表改常量重跑：
%s
- 撤销：python 73-github-dns-override.py --remove
- 如果只是偶尔白屏（缺样式），多半是 githubassets 的 IP 变了，换备用值即可。
""" % "\n".join("  %-42s %s" % (k, " / ".join(v)) for k, v in SPARE_IPS.items()))

        if bad:
            sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
