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
| 11b | `44b-patch-watchdog-http-probe.py` | **可靠性**：把 watchdog 的存活探测从 ping 改成 HTTP(S)，消除「假在线」漏判 | 步骤 11 完成 |
| 12 | `45-set-rps-masks.py` | **性能**：RPS 多核分散，反制 packet steering 单核钉死 | —— |
| 13 | `47-persist-log.sh` | **可观测**：日志落盘 + 启动历史，断电也能事后取证（shell） | —— |
| 14 | `48-tune-sysctl.sh` | **性能**：五个内核调优项一次写全并逐项回读校验（shell，幂等，先备份） | —— |
| 14b | `49-trim-idle-services.py` | **省资源**：关掉每分钟的 cron 播报、卸载统计面板（collectd 全家）、停用空转的 odhcpd、修无线的 `network` 绑定 | 步骤 10、13 完成 |
| 14d | `73-github-dns-override.py` | **可用性**：GitHub 全域名 DNS 覆盖（纯 dnsmasq，零证书零进程） | 见下方说明 |
| 15 | `50-fix-5g-channel.sh` | 5G 固定到非 DFS 信道 149（shell） | —— |
| 16 | `60-install-extra-apps.sh` | 装 watchcat（断网自愈）/ nlbwmon（可选）并调探测目标（shell） | 已配好软件源 |
| 17 | `70-set-static-ip.py` | 静态 DHCP 保留，给常用设备固定 IP | —— |
| 18 | `71-set-rdp-portforward.py` | RDP 端口转发（外网远程桌面连内网电脑） | 步骤 17 完成 |
| 19 | `80-install-zram.sh` | 安装 zram 压缩交换，防止内存盘耗尽导致重启（shell） | 软件源可用 |
| 20 | `90-verify-ua-length.py` | 批量验证 UA 改写覆盖范围（PC 上执行） | 步骤 1-2 完成 |
| 21 | `91-health-check.py` | **运维**：一键体检，31 项基线 + 客户端侧 UA 回显 + CDN 节点探测（PC 上执行，只读不改配置；期望值见 `common/target.py`） | 步骤 1-5 完成 |
| 22 | `46-setup-dns-adblock.sh` | **可选（实机已下线）**：DNS 层去广告 + 每周自动更新（shell） | 步骤 10 完成；固件自带 uclient-fetch |

> 带 `.sh` 的是**路由器端**脚本，用 `scp` 传上去或直接 `ssh root@路由器 'sh -s' < xxx.sh` 执行。
> 带 `.py` 的是**本机**脚本，通过 paramiko 远程执行。

## 每个脚本都会自检

所有脚本执行后都会打印关键状态（进程、nft 表、配置），请**确认输出符合预期**再进入下一步。

## 注意

- `11-patch-ua2f-lan-only.py` 会先备份 `/etc/init.d/ua2f` 到 `<path>.bak.<时间戳>`（`ssh_helper.backup()`）。
- `12-patch-ua2f-ack-bypass.py` 同样用 `.bak.<时间戳>`，不会覆盖 11 号的备份（时间戳不同）。
- `40-tune-rmem.py` 会修改 `/etc/sysctl.conf` 并重启 ua2f。
- `43-optimize-dns-ipv6.py` **幂等**：默认上游取 `common/target.py` 的 `DNS_UPSTREAM`
  （本线路就近运营商 DNS），现有的 `/校内域名/校园DNS` 分流规则原样保留；
  只有显式设置 `CAMPUS_DOMAINS` / `CAMPUS_DNS` 才重建分流规则（默认是示例占位）。
  `DNS_CACHESIZE`、`K2P_FORCE_DOMAIN`+`K2P_FORCE_IP`（按域名覆盖 CDN）也可覆盖。
  **不要退回「删光 server 只写公共 DNS」的老写法** —— 会把运营商 DNS 冲掉，见 03 篇坑 22。
- `45-set-rps-masks.py` 会部署 `/usr/bin/rps-tuning.sh`、修改 `/etc/rc.local`，并给
  `/etc/init.d/packet_steering` 挂勾子（备份 `.bak.<时间戳>`）以压住它的自动覆盖，
  详见 `docs/06-CPU与收包性能调优.md`。
- `46-setup-dns-adblock.sh` 会部署 `/usr/bin/dns-adblock-update.sh` 与
  `/etc/dns-blocklist.hosts`，并往 `/etc/crontabs/root` 追加一条每周更新任务；
  默认把清单裁剪到 6 万条（等间隔抽样），可设 `AB_MAX_LINES=0` 用全量。
  详见 `docs/07-DNS去广告.md`。**该功能已在实机下线**（体感收益有限），脚本保留供按需恢复。
- `47-persist-log.sh` 把 syslog 落到 `/etc/log/messages`（`log_size` 512KB，约 24 小时），
  并部署 `/etc/init.d/boot-log`（S99）记录每次启动与上次关机性质（正常关机 / 异常断电）。
  会备份 `/etc/config/system` 与已存在的 boot-log 到 `<path>.bak.<时间戳>`；幂等。
  `start()` 带 uptime 守卫：只有 `uptime ≤ 600s`（真开机时刻）才写记录，重跑本脚本不会
  伪造一条「异常断电」。详见 `docs/08-持久日志与重启诊断.md`。
- `60-install-extra-apps.sh` 里的 nlbwmon 是可选组件，本机已卸载：它的统计默认写在
  tmpfs（重启清零），如需长期统计应先把数据库目录改到 overlay 并缩短 commit 间隔。
- `50-fix-5g-channel.sh` 的 `RADIO` / `VIF` / `VIF_2G` / `CHANNEL` 可用环境变量覆盖
  （默认 `radio1` / `phy1-ap0` / `phy0-ap0` / `149`）；它现在轮询到信道生效才算完，
  末尾断言不符即退出码 1，不再靠固定 `sleep 30` 赌。
- `70-set-static-ip.py` / `71-set-rdp-portforward.py` 里的设备清单（名称/MAC/IP/端口）
  都是**示例占位**，执行前必须改成自己的；两者都是幂等的，可重复执行。
  动手前先在 Python 侧做配置检查（保留地址是否落在动态池、WAN 端口是否重复、
  目标是否在 LAN 网段、名字是否含空格），执行末尾逐项断言，任一不符退出码 1。
- **不要启用 flow offload**：被 offload 的流量绕过 mangle/postrouting，TTL 归一化与 UA2F 会失效。
- `44b-patch-watchdog-http-probe.py` 把 44 号 watchdog 里的 `ping` 判断换成 `uclient-fetch` 的 HTTP(S) 探测
  （2 个 HTTPS 目标 + 1 个 204 空响应体探针，三选一成功才算在线；路由器上没装 curl）。
  原因是**认证掉线后 IP 层仍能通 ICMP**：2026-10-23 22:38 实际断网、22:40 起 ping 一直成功，
  watchdog 因此 26 分钟没动作（`/tmp/esurfing-watchdog.stamp` 全程未更新），直到 23:06 才恢复。
  幂等；改前备份 `<path>.bak.<ts>`；`K2P_WATCHDOG_SH=/tmp/x` 可先在副本上演练，不碰现网。
- **实机另做过一批手工加固，尚未脚本化**（2026-10-24，换机/重刷后需手工补）：
  `/etc/sysctl.conf` 追加 `net.ipv4.conf.{all,default,wan,br-lan}.rp_filter=2`、
  `kernel.kptr_restrict=1`、`net.ipv4.conf.{all,default,wan,br-lan}.send_redirects=0`。
  选**宽松** RPF 而非严格，是因为本机有 UA2F 的 NFQUEUE 与多张独立 nft 表，
  严格模式（1）有误杀风险；实测改后 `Ip: InDiscards` 仍为 0，未丢任何正常流量。
- `system.conloglevel` 已由 8 降为 4。它走 `/etc/init.d/system` 的 `dmesg -n`，
  **只影响内核消息是否写串口（ttyS0）**，不改变 `/etc/log/messages` 的落盘量。
- **`system.cronloglevel` 的语义与直觉相反**（2026-10-24 在 BusyBox v1.37.0 上逐级实测）：
  `9` = 完全静音（连 `crond started` 都不写）、`8` = 每条作业一行、
  `7 及以下越低越啰嗦`（`0` 会把 `file root:` / `line …` / `job: 0 …` / `child running …`
  全打开，实测一轮作业写 14 行）。**作业播报无法单独关掉，只能全关或全开**；
  原先设备上是 `7`，watchdog 每分钟一轮就写一条，占掉 ring buffer 里 1397/4169 行（≈1/3）。
  现由 `49-trim-idle-services.py` 设为 `9`。代价是 **cron 自己的报错也不再写 syslog**
  （crontab 语法错、命令执行失败），所以怀疑 watchdog 停了要去查
  `/etc/crontabs/root` 与 `/tmp/esurfing-watchdog.log`，而不是看日志。
  已实测确认：静音后作业**照常执行**（临时打点任务按时写文件），只是不再播报。
- `49-trim-idle-services.py` 另外做了三件事：
  1. **整包卸载 LuCI「统计」面板**（`luci-app-statistics` + `luci-i18n-statistics-zh-cn`
     + `collectd` + 全部 `collectd-mod-*` + `rrdtool1`，固件是 OpenWrt 25.x，用 `apk del`，
     连孤儿依赖 `librrd1` / `libltdl7` 一起清）。看着好看但没人看，白占 RSS ~2MB
     和内存盘里 472KB 的 `/tmp/rrd`；卸载后 overlay 可用空间 +460KB。
     **它是唯一需要联网取包才能恢复的改动**：`apk add luci-app-statistics`（见 `docs/04` §4.8）。
     只卸载不删 `/etc/config/luci_statistics`（apk 默认保留配置），旧设置已另存 `.bak.<ts>`。
  2. 停用空转的 `odhcpd`（`43` 号已把 `dhcp.lan.ra/dhcpv6/ndp` 全设 `disabled`）。
  3. 把无线的 `network 'lan wan wan6'` 收敛为 `'lan'` —— `wan6` 在运行期根本不存在
     （`ifstatus wan6` → `Interface wan6 not found`），实测两路 AP 一直只挂在 `br-lan`。
  合计释放约 2.7MB 内存（MemAvailable 28.7MB → 31.4MB）+ 460KB overlay。
- **Steam 社区这条路已评估并放弃**（2026-10-25 撤除，脚本与服务均已删除；
  日常改为在 PC 上直接用 Steam++）。留档是为了避免以后重复研究：
  - 三种病因实测分清：`community.steamstatic.com` / `avatars.steamstatic.com` 只是
    DNS 指到不可达的 Fastly 节点（换 Akamai 节点即可，**免证书**）；
    `steamcommunity.com` 是 **SNI 阻断**（同一 IP 换 `SNI=www.qq.com` 立刻握手成功，
    带 `Host: steamcommunity.com` 就返回真页面）；`github.com` / `discord.com` 是
    **IP 层封锁**，换 SNI 无效，怎么都救不了。
  - 实测排除：学校不给 IPv6（`wan6` 启用后 `odhcp6c` 跑起来但拿不到全局地址）；
    拆 TCP 段 / 加延迟 / 乱序 / 逐字节 / 拆 TLS record **全部被 RST**，
    说明这条线路的 DPI 做了完整流重组，「不改 SNI 绕过」一律无效。
  - 日后若重做，只剩两条路：路由器自建 MITM（stunnel + 自签 CA，**每台客户端装证书**），
    或买一台境外 VPS 做透明代理（不用证书但要钱）。
- **GitHub 不是「IP 层封锁」，是「DNS 污染 + 部分 IP 被过滤」**（2026-10-27 实测，
  关闭 Steam++ 后从同一个出口验证）：
  - `nslookup github.com` 会同时返回真实 IP 和假 IP —— `205.251.x.x` 是 AWS Route53
    的地址、`198.51.44.x` 是异常段，而**真 IP 里 `20.205.243.166` 恰好连不上**，
    客户端挑中它就超时（表现就是「偶尔能开、多数超时」）。
  - 逐个 IP 直连：`140.82.114.3` **200 / 2.9s**（最快）、`20.27.177.113` 200 / 7.4s、
    `4.237.22.38` 200 / 7.1s、`20.205.243.166` **超时**。换成可达 IP 后
    `github.com/torvalds/linux` 直接 **200 / 330KB**，头像 282KB、raw 6034 字节均正常。
  - **路由器自己 `uclient-fetch` 仍然失败（rc=4），但这是 mbedtls 与 GitHub 握手不兼容，
    不影响客户端** —— 手机/电脑是端到端 TLS，路由器只做 NAT，不参与握手。
    所以路由器唯一要做的就是**把域名解析到可达 IP**。
  - **`dnsmasq` 的 `address=` 不轮询**：一条里写多个 IP，实测连查三次都只返回第一个。
    所以每条只配一个可达 IP，失效时按 `73` 号脚本里的 `SPARE_IPS` 表改常量重跑。
  - **两个必踩的坑**：① 覆盖后客户端仍走 IPv6 导致超时，是 **Windows DNS 缓存里残留的
    旧 AAAA**，`ipconfig /flushdns` 后立刻正常（`filter_aaaa=1` 已在 `43` 号设过，
    DNS 层不返回 AAAA）；② `raw.githubusercontent.com` 的 4 个 IP 可达性**会动态变化**
    （一小时前 108/110 通、109 不通，再测变成 109 通、108/110 不通），
    这就是「GitHub 时好时坏」的真正原因，不是线路故障。
- `napi.ithome.com -> 183.131.62.41` 的域名覆盖已删除（该 API 域名服务端下线，
  详见 `common/target.py` 注释）；`91` 相应两项检查因 `FORCE_DOMAIN` 默认值清空而自动跳过。
- `/etc/config/esurfingclient.1.logout` 是 JSON 而非 uci 格式，放在 `/etc/config/` 下会让
  每条 `uci` 命令在 stderr 吐一句 `Parse error (invalid command) at line 1`，属已知噪音，可忽略。
- **同一个域名，换「用谁的 DNS 解析」会落到不同 CDN 节点**（2026-10-24 实测，比坑 21 更具体）：
  `napi.ithome.com` 走公共 DNS（202.96.128.86 / 223.5.5.5 / 114 / 8.8.8.8）一致得到
  `175.12.90.41`（移动节点），该 IP **只有 443 被校园网出口掐断**（80 秒连、ICMP 通）；
  走学校下发 DNS（`172.16.2.1`）得到 `119.188.212.41`，从校园网完全可达，配置后 App 立刻恢复。
  所以「公共 DNS 比学校 DNS 好」只在 CDN 就近性上成立——一旦节点被出口过滤，就要换学校 DNS
  的调度结果再试。排查动作：`nslookup <域名> 202.96.128.86` 与 `nslookup <域名> 172.16.2.1` 对比。
- **判断 CDN 节点「是否正常」不能只看 HTTP 404**：IT之家 的正常节点访问 `/` 同样回 404
  （App 用的是具体 API 路径），当时据此误判成「域名已下线」。可靠判据只有两条：
  TCP 443 能否建连；以及手机流量下实测能否刷出内容。
- 实机另加了一条兜底 NAT（改道），因为 iOS 的 DNS 缓存极顽固，重启 App 也不刷新：
  `dest 175.12.90.41:443 → 119.188.212.41:443`，已落成 uci redirect `napi-ithome-fix`
  （`src=lan / src_dip=175.12.90.41 / src_dport=443 / dest=wan / dest_ip / dest_port=443`）。
  有了它，设备连哪个 IP 都会被半路纠正；`fw4 reload` 与重启均保留。
  节点 IP 若变，用学校 DNS 重查后同时改 `target.py` 与这条 redirect。
- 这些脚本修改的是路由器**运行时 + 开机脚本**，确保重启后仍然生效。