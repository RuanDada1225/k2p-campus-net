# scripts

配套脚本清单。所有 Python 脚本通过**环境变量**读取连接信息，仓库中不含任何凭据。

## 依赖

```bash
pip install paramiko
```

## 连接信息

```bash
export K2P_HOST=192.168.1.1
export K2P_USER=root
export K2P_PASSWORD='你的路由器密码'
# 可选：LAN 网段（用于 ntp_redirect 规则）
export K2P_LAN_SUBNET=192.168.1.0/24
```

## 执行顺序

| 顺序 | 脚本 | 作用 | 前置条件 |
|---|---|---|---|
| 1 | `10-setup-ua2f.py` | 安装并配置 UA2F（NFQUEUE 模式） | 已装 ua2f 包 |
| 2 | `11-patch-ua2f-lan-only.py` | **关键**：让 UA2F 只处理 br-lan 流量 | 步骤 1 完成 |
| 3 | `20-setup-ttl-normalize.py` | TTL / HopLimit 归一化（独立 nft 表 + 开机脚本） | —— |
| 4 | `21-setup-ntp-redirect.py` | 强制内网 NTP 走路由器 | —— |
| 5 | `30-patch-esurfing-bootdelay.py` | 认证开机延迟 5s → 30s | 已装 esurfingclient |
| 6 | `40-tune-rmem.py` | 内核 socket 缓冲调优，消除 UA2F 报错 | 步骤 1 完成 |
| 7 | `41-set-timezone.sh` | 时区 + 国内 NTP 源（shell，路由器上执行） | —— |
| 8 | `42-fix-dns-rebind.py` | 关闭 dnsmasq 反重绑定保护，修复校内站点打不开 | —— |
| 9 | `43-optimize-dns-ipv6.py` | 关 LAN IPv6 + AAAA 过滤 + DNS 分流，修复「刷视频老是缓冲」 | 步骤 8 完成 |
| 10 | `44-setup-esurfing-watchdog.py` | 认证失败加速重试（守护脚本 + cron，约 1 分钟） | 已装 esurfingclient |
| 11 | `50-fix-5g-channel.sh` | 5G 固定到非 DFS 信道 149（shell） | —— |
| 12 | `60-install-extra-apps.sh` | 装 nlbwmon / watchcat 并调探测目标（shell） | 已配好软件源 |
| 13 | `70-set-static-ip.py` | 静态 DHCP 保留，给常用设备固定 IP | —— |
| 14 | `71-set-rdp-portforward.py` | RDP 端口转发（外网远程桌面连内网电脑） | 步骤 13 完成 |
| 15 | `80-install-zram.sh` | 安装 zram 压缩交换，防止内存盘耗尽导致重启（shell） | 软件源可用 |
| 16 | `90-verify-ua-length.py` | 批量验证 UA 改写覆盖范围（PC 上执行） | 步骤 1-2 完成 |

> 带 `.sh` 的是**路由器端**脚本，用 `scp` 传上去或直接 `ssh root@路由器 'sh -s' < xxx.sh` 执行。
> 带 `.py` 的是**本机**脚本，通过 paramiko 远程执行。

## 每个脚本都会自检

所有脚本执行后都会打印关键状态（进程、nft 表、配置），请**确认输出符合预期**再进入下一步。

## 注意

- `11-patch-ua2f-lan-only.py` 会先备份 `/etc/init.d/ua2f` 到 `.orig`。
- `40-tune-rmem.py` 会修改 `/etc/sysctl.conf` 并重启 ua2f。
- `43-optimize-dns-ipv6.py` 的校内域名 / 校园 DNS 默认是**示例占位**，执行前用
  `CAMPUS_DOMAINS` / `CAMPUS_DNS` 环境变量改成你学校的（多个域名用空格分隔）。
- `70-set-static-ip.py` / `71-set-rdp-portforward.py` 里的设备清单（名称/MAC/IP/端口）
  都是**示例占位**，执行前必须改成自己的；两者都是幂等的，可重复执行。
- 这些脚本修改的是路由器**运行时 + 开机脚本**，确保重启后仍然生效。