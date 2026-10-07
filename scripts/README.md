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
| 8 | `50-fix-5g-channel.sh` | 5G 固定到非 DFS 信道 149（shell） | —— |
| 9 | `60-install-extra-apps.sh` | 装 nlbwmon / watchcat 并调探测目标（shell） | 已配好软件源 |
| 10 | `90-verify-ua-length.py` | 批量验证 UA 改写覆盖范围（PC 上执行） | 步骤 1-2 完成 |

> 带 `.sh` 的是**路由器端**脚本，用 `scp` 传上去或直接 `ssh root@路由器 'sh -s' < xxx.sh` 执行。
> 带 `.py` 的是**本机**脚本，通过 paramiko 远程执行。

## 每个脚本都会自检

所有脚本执行后都会打印关键状态（进程、nft 表、配置），请**确认输出符合预期**再进入下一步。

## 注意

- `11-patch-ua2f-lan-only.py` 会先备份 `/etc/init.d/ua2f` 到 `.orig`。
- `40-tune-rmem.py` 会修改 `/etc/sysctl.conf` 并重启 ua2f。
- 这些脚本修改的是路由器**运行时 + 开机脚本**，确保重启后仍然生效。