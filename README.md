# 斐讯 K2P 校园网多设备伪装实战（OpenWrt）

[![ci](https://github.com/RuanDada1225/k2p-campus-net/actions/workflows/ci.yml/badge.svg)](https://github.com/RuanDada1225/k2p-campus-net/actions/workflows/ci.yml)

> 把一台吃灰的斐讯 K2P 改造成「校园网单账号 · 多设备共用 · 不被多设备检测」的完整实战记录。
>
> 硬件：斐讯 K2P（MT7621A + MT7615DN，128MB RAM / 16MB Flash）
> 系统：OpenWrt 25.12.5
> 认证：天翼校园网 ESurfingClient
> 反检测：UA2F + TTL 归一化 + NTP 重定向
>
> 内容：8 篇文档 · 23 个可复用脚本 · 22 条实战踩坑（现象 → 定位 → 根因 → 解决）

---

## 背景

国内不少高校的校园网实行「一人一号」，部分运营商甚至做到了「一设备一号」——同一账号只允许一台设备在线，第二台设备一接入就会顶掉第一台，或者被强制重认证。

而宿舍里往往有一堆设备：笔记本、手机、平板、树莓派、智能音箱……逐个去认证既不现实，也容易触发检测。

本文记录的核心思路是：**让路由器用单一账号完成认证，并把内网所有设备的流量「洗」成同一个指纹**，从而骗过运营商的多设备检测设备。

---

## 整体方案

```
                校园网墙口
                    │
              ┌─────▼─────┐
              │  K2P WAN  │  ← ESurfingClient 在此完成 802.1X/Portal 认证
              └─────┬─────┘
                    │
        ┌───────────┴───────────┐
        │      OpenWrt 内核       │
        │  ┌─────────────────┐   │
        │  │ UA2F (NFQUEUE)  │   │  ① 统一 HTTP User-Agent
        │  ├─────────────────┤   │
        │  │ ttl_normalize   │   │  ② 统一 IPv4 TTL / IPv6 HopLimit = 64
        │  ├─────────────────┤   │
        │  │ ntp_redirect    │   │  ③ 强制内网 NTP 走路由器
        │  └─────────────────┘   │
        └───────────┬───────────┘
                    │ br-lan (192.168.1.1/24)
        ┌───────────┴───────────┐
        │  笔记本 / 手机 / 平板 …  │
        └───────────────────────┘
```

四个层次逐级递进：

| 层次 | 目标 | 手段 |
|---|---|---|
| 认证 | 让路由器替全网设备完成校园网认证 | ESurfingClient-CVersion |
| 应用层指纹 | 抹平 HTTP User-Agent 差异 | UA2F（NFQUEUE 改写） |
| 网络层指纹 | 抹平各设备 TTL / HopLimit 差异 | nftables `ttl_normalize` |
| 时间层指纹 | 抹平各设备时钟偏差 | nftables `ntp_redirect` + 路由器 NTP 服务端 |

---

## 快速开始

> 前提：路由器已刷 OpenWrt 并能 SSH 登录；校园网账号密码已知。

```bash
# 0. 准备：把 scripts/ 拷到本地，装好依赖（只有 paramiko）
pip install -r requirements.txt

# 1. 设置连接信息（不要写进仓库）
export K2P_HOST=192.168.1.1
export K2P_USER=root
export K2P_PASSWORD='你的路由器密码'

# 2. 按顺序执行（每个脚本都会自检并打印结果）
#    .py 在本机跑（通过 SSH 操作路由器）；.sh 是路由器端脚本，用 ssh 直接喂进去
python scripts/10-setup-ua2f.py              # 装 UA2F 并配置 UA 改写
python scripts/11-patch-ua2f-lan-only.py     # 让 UA2F 只处理 br-lan 流量（关键！）
python scripts/12-patch-ua2f-ack-bypass.py   # 满速下载不卡：UA2F 跳过纯 ACK 短包
python scripts/20-setup-ttl-normalize.py     # TTL / HopLimit 归一化
python scripts/21-setup-ntp-redirect.py      # 强制内网 NTP 走路由器
python scripts/30-patch-esurfing-bootdelay.py # 认证开机延迟 30s（避免 WAN 未就绪）
python scripts/40-tune-rmem.py               # 内核 socket 缓冲调优（消除 UA2F 报错）
ssh root@192.168.1.1 'sh -s' < scripts/41-set-timezone.sh      # 时区 + 国内 NTP 源
python scripts/42-fix-dns-rebind.py          # 关闭 DNS 反重绑定保护（修复校内站点打不开）
python scripts/43-optimize-dns-ipv6.py       # DNS 分流 + AAAA 过滤 + 关 LAN IPv6（修复刷视频缓冲）
python scripts/44-setup-esurfing-watchdog.py # 认证失败加速重试（约 1 分钟）
python scripts/44b-patch-watchdog-http-probe.py # watchdog 存活探测改 HTTP(S)（修 ping 假在线）
python scripts/45-set-rps-masks.py           # 满速下载不丢包：RPS 多核分散（关键）
ssh root@192.168.1.1 'sh -s' < scripts/47-persist-log.sh  # 日志落盘 + 启动历史（断电可查）
ssh root@192.168.1.1 'sh -s' < scripts/48-tune-sysctl.sh  # 五个内核调优项一次写全（幂等 + 回读校验）
ssh root@192.168.1.1 'sh -s' < scripts/50-fix-5g-channel.sh    # 5G 固定到非 DFS 信道 149
```

**随时体检**（在 PC 上执行，不要在路由器上做 UA 回显——理由见 04 篇 2.2）：

```bash
python scripts/91-health-check.py --probe-node
```

以上是「让路由器跑起来」。跑起来之后，按实际需求可选：

```bash
ssh root@192.168.1.1 'sh -s' < scripts/60-install-extra-apps.sh  # watchcat 断网自愈（nlbwmon 可选）
python scripts/70-set-static-ip.py           # 给常用设备固定 IP（改 DEVICES 后再执行）
python scripts/71-set-rdp-portforward.py     # 外网远程桌面连内网电脑（改 DEVICES 后再执行）
ssh root@192.168.1.1 'sh -s' < scripts/80-install-zram.sh   # zram 压缩交换（防内存盘耗尽重启）
ssh root@192.168.1.1 'sh -s' < scripts/46-setup-dns-adblock.sh  # DNS 层去广告（实机已下线，按需恢复）
```

详细的原理、每一步的验证方法、以及我踩过的坑，见下方文档；性能调优（满速下载不卡顿）另见 `docs/06-CPU与收包性能调优.md`；DNS 层去广告见 `docs/07-DNS去广告.md`（已下线）；持久日志与断电诊断见 `docs/08-持久日志与重启诊断.md`。

---

## 目录结构

```
k2p-campus-net/
├── README.md                     # 本文件：总览与快速开始
├── LICENSE                       # MIT
├── requirements.txt              # Python 依赖（只有 paramiko，锁了上界）
├── .github/workflows/ci.yml      # CI：compileall + dash -n + shellcheck
├── .gitignore                    # 忽略凭据 / 日志
├── .gitattributes                # 强制 LF 行尾
├── docs/
│   ├── 01-检测原理.md             # 运营商是怎么检测多设备的
│   ├── 02-部署实战.md             # 逐层部署的完整步骤与命令
│   ├── 03-踩坑与排错.md           # 实战中踩过的坑与解决办法
│   ├── 04-验证与运维.md           # 如何验证生效 + 日常运维
│   ├── 05-固定IP与远程访问.md      # 静态 DHCP 保留 + 端口转发（RDP）
│   ├── 06-CPU与收包性能调优.md     # UA2F 短包旁路 + RPS 多核分散（满速下载不卡）
│   ├── 07-DNS去广告.md            # dnsmasq addn-hosts 去广告 + 每周自动更新（已下线）
│   └── 08-持久日志与重启诊断.md    # 日志落盘 + 启动历史，断电也能事后取证
└── scripts/
    ├── README.md                 # 脚本清单与执行顺序
    ├── common/ssh_helper.py      # 通用 SSH 封装（凭据走环境变量）
    ├── common/target.py          # 设备差异点（网口名 / 网段 / radio），移植只改这里
    ├── 10-setup-ua2f.py
    ├── 11-patch-ua2f-lan-only.py
    ├── 12-patch-ua2f-ack-bypass.py
    ├── 20-setup-ttl-normalize.py
    ├── 21-setup-ntp-redirect.py
    ├── 30-patch-esurfing-bootdelay.py
    ├── 40-tune-rmem.py
    ├── 41-set-timezone.sh
    ├── 42-fix-dns-rebind.py
    ├── 43-optimize-dns-ipv6.py
    ├── 44-setup-esurfing-watchdog.py
    ├── 44b-patch-watchdog-http-probe.py  # 存活探测改 HTTP(S)：修「ping 通但不能上网」漏判
    ├── 45-set-rps-masks.py
    ├── 46-setup-dns-adblock.sh
    ├── 47-persist-log.sh
    ├── 48-tune-sysctl.sh         # 五个内核调优项一次写全（幂等 + 回读校验）
    ├── 50-fix-5g-channel.sh
    ├── 60-install-extra-apps.sh
    ├── 70-set-static-ip.py
    ├── 71-set-rdp-portforward.py
    ├── 80-install-zram.sh
    ├── 90-verify-ua-length.py
    └── 91-health-check.py        # 一键体检（在 PC 上跑，含客户端侧 UA 回显与节点探测）
```

---

## 关键结论速查（29 条）

> 跨文档汇总的结论清单，与 `docs/03-踩坑与排错.md` 里的 22 条踩坑并非一一对应。

| # | 结论 | 说明 |
|---|---|---|
| 1 | **UA3F 装不下，用 UA2F** | K2P overlay 仅 7.6MB，UA3F 要 17.6MB；UA2F 主程序仅 758KB |
| 2 | **UA2F 必须限制只处理 br-lan** | 否则会改写路由器自身的认证流量，导致「提取门户配置失败」 |
| 3 | **UA2F 是等长替换，且 `custom_ua` 有 11 字节截断** | 改写长度受原始 UA 长度与 11 字节上限双重约束；设长 UA 只生效前 11 字节，配置值≠真实值（见 03 坑 20） |
| 4 | **认证要延迟 30s 启动** | 开机 5s 时 WAN 还没就绪，认证失败会进入最长 30 分钟的退避 |
| 5 | **5G 要固定非 DFS 信道** | `auto` 会选到 DFS 信道 56，触发 60s CAC，重启后 70s 没网 |
| 6 | **rmem 要调大** | 默认 176KB（180224B）会导致 UA2F netlink socket 溢出报 `No buffer space available` |
| 7 | **独立 nft 表更稳** | `ttl_normalize` / `ntp_redirect` 用独立表，不会被 fw4 或 UA2F 重启清掉 |
| 8 | **apk 语言包版本被锁** | `/etc/apk/world` 里有版本哈希，`apk upgrade` 会跳过，要用 `apk add --upgrade` |
| 9 | **K2P 无线功率是驱动硬限制** | 2.4G/5G 都被驱动钳在 8dBm，改国家码无效，只能重编译驱动 |
| 10 | **日志默认为 tmpfs，但可以落盘** | `/var/log` 存内存、重启即清空；用 `system.log_file` 落到 `/etc/log/messages` + boot-log 记录启动历史，即可断电事后取证（见 08 篇） |
| 11 | **固定 IP 要「保留 + 收窄地址池」** | 保留地址必须落在动态池之外，否则会撞车 |
| 12 | **判断在线看 ARP，不看租约** | 租约到期前不会因设备断开而消失 |
| 13 | **远程访问优先用 VPN** | 直接暴露 3389 是常见入侵入口，能上 VPN 就别开转发 |
| 14 | **校内站点打不开，先查 DNS 反重绑定** | dnsmasq 默认丢弃「公网域名→私有 IP」的应答，会误伤校内系统 |
| 15 | **刷视频先卡几秒，先查 IPv6 假阳性** | LAN 广播 IPv6 而 WAN 无上游；关 RA/DHCPv6 + `filter_aaaa=1` + DNS 分流 |
| 16 | **莫名重启，先查 tmpfs 内存盘** | tmpfs 页不可回收，无 swap 时内核卡死被硬件看门狗复位；加 zram 泄洪 |
| 17 | **认证失败要等好几分钟** | 退避表硬编码 `{1,5,10,20,30}` 分钟；外部守护脚本 + cron 压到约 1 分钟 |
| 18 | **UA2F 要跳过纯 ACK 短包** | 下载流几乎全是无负载 ACK；`ip length 1-80` / `ip6 length 1-104` return 后队列 3100 → 10~16 包/秒 |
| 19 | **packet steering 会覆盖 RPS** | 它按「最空闲核」写单核 `rps_cpus`，且 network / firewall reload 后自动重跑；必须钩在它之后 |
| 20 | **DNS 上游按线路就近选** | 公共 DNS 不是万金油：实测运营商 DNS 10.8ms 快于阿里 15.0ms / 腾讯 17.5ms，CDN 解析也更近 |
| 21 | **DNS 去广告清单别贪大** | 10.9 万条 ≈13MB 内存 + 0.94MB overlay；清单按字母序排，裁剪要用等间隔抽样而不是 `head` 截断 |
| 22 | **WAF 拦 UA 认关键字** | EdgeOne 类 WAF 按 `safari` / `MicroMessenger` 关键字放行（不分大小写），残缺 `Mozilla/5.0` 会被拦 567；`custom_ua` 用 `Safari/537.`（11 字节内含 `Safari/`） |
| 23 | **ping 通 ≠ HTTPS 可达** | `ICMP 通 + TCP 443 断`是出口路径策略的典型指纹（校园网按 CDN 节点 IP 过滤）；排查要落到 TCP 层，并用第二出口（4G）做对照（03 坑 21） |
| 24 | **校园 DNS 冷解析比电信慢 20 倍** | 实测 237ms vs 12ms（校园需转发+审计，缓存命中才快）；校园 DNS 只适合校内域名按域名转发，不要做全局唯一上游（03 坑 22） |
| 25 | **日志要落盘 + 记录启动历史** | `system.log_file='/etc/log/messages'`（512KB≈24h）+ boot-log 用 `.clean_shutdown` 标记区分正常关机/异常断电（08 篇） |
| 26 | **UA 回显必须在客户端做，不能在路由器上做** | 队列规则带 `iifname "br-lan"`，路由器自身 output 流量不进 NFQUEUE；在路由器上回显只能看到 `uclient-fetch`，是假阴性（04 篇 2.2 有实测对照） |
| 27 | **判断重启原因，别拿文件 mtime 反推** | 无 RTC 设备开机早期的时钟是 `sysfixtime` 取 `/etc` 最新 mtime 造出来的，`/var/log` 又是 tmpfs（重启即重建）→ 据此推出的「几点断的电」是假的。只认 `boot-history` 的 `prev=` 与持久日志末条时间戳（08 篇 4.5） |
| 28 | **换 DNS 可能换到「能通的 CDN 节点」，学校 DNS 别提前排除** | 同一个域名：公共 DNS 一律给 `175.12.90.41`（校园出口只封它的 443、80 与 ICMP 都通），学校下发 DNS 给 `119.188.212.41`（可达）。「换 DNS 无效」只对公共 DNS 成立（03 篇坑 21/22） |
| 29 | **CDN 节点的 HTTP 404 不能用来判死活** | IT之家 所有节点访问 `/` 都回 404（App 用具体 API 路径），当时据此误判成「域名已下线」。可靠判据只有两条：TCP 443 能否建连、手机实测能否刷出内容（03 篇坑 21） |

---

## 适用性与移植

本方案的核心（UA2F + TTL 归一化 + NTP 重定向）**不限于 K2P**，任何能跑 OpenWrt 的路由器都适用。需要按实际情况调整的只有：

- 网口名（`wan` / `br-lan`）与 LAN 网段（`192.168.1.0/24`）
- 认证客户端（本文是天翼 `ESurfingClient`，其他学校可能是 `Dr.Com` / `校园网客户端` 等）
- overlay 空间（决定能装哪些插件）

---

## 免责声明

- 本项目仅供**个人学习与自有设备配置**使用。
- 校园网的使用请遵守你所在学校的网络管理规定与运营商服务协议，不要用于任何商业用途或牟利行为。
- 作者不对使用本方案导致的账号封禁、网络中断、设备损坏等任何后果负责。
- 仓库中所有账号、密码、学校标识均已脱敏，请勿填入真实凭据后提交。

## License

MIT