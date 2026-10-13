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
| 3 | `12-patch-ua2f-ack-bypass.py` | **性能**：UA2F 跳过纯 ACK 短包（下载时 CPU 大降） | 步骤 2 完成 |
| 4 | `20-setup-ttl-normalize.py` | TTL / HopLimit 归一化（独立 nft 表 + 开机脚本） | —— |
| 5 | `21-setup-ntp-redirect.py` | 强制内网 NTP 走路由器 | —— |
| 6 | `30-patch-esurfing-bootdelay.py` | 认证开机延迟 5s → 30s | 已装 esurfingclient |
| 7 | `40-tune-rmem.py` | 内核 socket 缓冲调优，消除 UA2F 报错 | 步骤 1 完成 |
| 8 | `41-set-timezone.sh` | 时区 + 国内 NTP 源（shell，路由器上执行） | —— |
| 9 | `42-fix-dns-rebind.py` | 关闭 dnsmasq 反重绑定保护，修复校内站点打不开 | —— |
| 10 | `43-optimize-dns-ipv6.py` | 关 LAN IPv6 + AAAA 过滤 + DNS 分流，修复「刷视频老是缓冲」 | 步骤 9 完成 |
| 11 | `44-setup-esurfing-watchdog.py` | 认证失败加速重试（守护脚本 + cron，约 1 分钟） | 已装 esurfingclient |
| 12 | `45-set-rps-masks.py` | **性能**：RPS 多核分散，反制 packet steering 单核钉死 | —— |
| 13 | `50-fix-5g-channel.sh` | 5G 固定到非 DFS 信道 149（shell） | —— |
| 14 | `60-install-extra-apps.sh` | 装 nlbwmon / watchcat 并调探测目标（shell） | 已配好软件源 |
| 15 | `70-set-static-ip.py` | 静态 DHCP 保留，给常用设备固定 IP | —— |
| 16 | `71-set-rdp-portforward.py` | RDP 端口转发（外网远程桌面连内网电脑） | 步骤 15 完成 |
| 17 | `80-install-zram.sh` | 安装 zram 压缩交换，防止内存盘耗尽导致重启（shell） | 软件源可用 |
| 18 | `90-verify-ua-length.py` | 批量验证 UA 改写覆盖范围（PC 上执行） | 步骤 1-2 完成 |
| 19 | `46-setup-dns-adblock.sh` | **可选**：DNS 层去广告（dnsmasq addn-hosts）+ 每周自动更新（shell） | 步骤 10 完成；固件自带 uclient-fetch |

> 带 `.sh` 的是**路由器端**脚本，用 `scp` 传上去或直接 `ssh root@路由器 'sh -s' < xxx.sh` 执行。
> 带 `.py` 的是**本机**脚本，通过 paramiko 远程执行。

## 每个脚本都会自检

所有脚本执行后都会打印关键状态（进程、nft 表、配置），请**确认输出符合预期**再进入下一步。

## 注意

- `11-patch-ua2f-lan-only.py` 会先备份 `/etc/init.d/ua2f` 到 `.orig`。
- `12-patch-ua2f-ack-bypass.py` 备份到 `/etc/init.d/ua2f.ack.orig`（不覆盖 11 号的 `.orig` 基线备份）。
- `40-tune-rmem.py` 会修改 `/etc/sysctl.conf` 并重启 ua2f。
- `43-optimize-dns-ipv6.py` 的校内域名 / 校园 DNS 默认是**示例占位**，执行前用
  `CAMPUS_DOMAINS` / `CAMPUS_DNS` 环境变量改成你学校的（多个域名用空格分隔）。
- `45-set-rps-masks.py` 会部署 `/usr/bin/rps-tuning.sh`、修改 `/etc/rc.local`，并给
  `/etc/init.d/packet_steering` 挂勾子（备份 `.orig`）以压住它的自动覆盖，
  详见 `docs/06-CPU与收包性能调优.md`。
- `46-setup-dns-adblock.sh` 会部署 `/usr/bin/dns-adblock-update.sh` 与
  `/etc/dns-blocklist.hosts`，并往 `/etc/crontabs/root` 追加一条每周更新任务；
  默认把清单裁剪到 6 万条（等间隔抽样），可设 `AB_MAX_LINES=0` 用全量。
  详见 `docs/07-DNS去广告.md`。
- `70-set-static-ip.py` / `71-set-rdp-portforward.py` 里的设备清单（名称/MAC/IP/端口）
  都是**示例占位**，执行前必须改成自己的；两者都是幂等的，可重复执行。
- **不要启用 flow offload**：被 offload 的流量绕过 mangle/postrouting，TTL 归一化与 UA2F 会失效。
- 这些脚本修改的是路由器**运行时 + 开机脚本**，确保重启后仍然生效。