# 06 · CPU 与收包性能调优

前面几篇解决「能不能用、会不会被检测」，这一篇解决「好不好用」。

反检测方案（UA2F / TTL 归一化 / NTP 重定向）全部挂在 netfilter 上，而 netfilter 是转发路径上的「关卡」。
K2P 的 MT7621 只有 880MHz × 4，满速下载时任何一处浪费都会被放大成：CPU 飙升、softnet 丢包、整机卡顿、下载跑不满线路。

本文来自一次 Steam（5G Wi-Fi 客户端）满速下载实测，定位并修掉两个瓶颈：

| 瓶颈 | 根因 | 修复 |
|---|---|---|
| UA2F 用户态过载 | NFQUEUE 把下载流的纯 ACK 也送进用户态（约 3100 包/秒） | 队列前加两条 header-only 短包旁路（脚本 12） |
| 单核收包瓶颈 | packet steering 把 RPS 钉死在单核，且反复覆盖手工值 | rps-tuning.sh 多核分散 + 三层持久化（脚本 45） |

---

## 一、瓶颈一：UA2F 把纯 ACK 也送进了用户态

### 现象

满速下载时路由器 CPU 明显升高、内网页面卡顿，下载速率也上不去。

### 定位

```sh
# 看队列规则的计数（单位：包/字节）
nft list table inet ua2f | grep -E 'header-only|to 10010'

# 隔 10 秒再执行一次，对计数器做差即可得到包速率
```

调优前实测：队列规则约 **3100 包/秒** 恒定飞涨——几乎全是下载流的纯 ACK。

### 根因

postrouting 上的队列规则匹配 br-lan **全部原始方向 TCP**。而一条 HTTP 下载流里，99% 的包是**无负载的纯 ACK** —— 它们不可能包含 User-Agent，却每一包都要走一遍「内核 → 用户态 → 判决 → 放行」的 NFQUEUE 往返（含内核↔用户态拷贝与上下文切换）。880MHz 的 CPU 做这件事，直接被吃满。

### 解决

在入队规则**之前**插入两条「按长度旁路」规则（`scripts/12-patch-ua2f-ack-bypass.py` 会自动完成）：

```sh
# 在 /etc/init.d/ua2f 的 nft heredoc 里、$nfqueue_expr 队列行之前：
meta l4proto tcp ct direction original iifname "br-lan" ip length 1-80 counter return comment "!ua2f: bypass header-only ACK v4";
meta l4proto tcp ct direction original iifname "br-lan" ip6 length 1-104 counter return comment "!ua2f: bypass header-only ACK v6";
```

长度阈值怎么来的：

- **IPv4**：纯 ACK / 窗口更新 / 保活包通常为 20(IP) + 20(TCP) = 40B；TCP 头含选项最多 60B，
  即包总长最多 80B —— 阈值取 `80` 恰好含边界覆盖（`ip length` 匹配的是**包总长**）。
- **IPv6**：对应包总长最多 100B（40 基本头 + 60 TCP 头）；注意 `ip6 length` 匹配的是**负载长度**
  （不含 40 字节基本头），纯 ACK 的负载最多 60B，阈值取 `104` 留有充足余量。
- 真实 HTTP 请求（请求行 + Host + User-Agent + 其他头）远大于 80B，**不会被旁路**，UA 改写不受影响。

### 效果

同一下载场景中的计数器对比（同一时刻取样）：

```sh
meta ... ip length 1-80 ... return           counter packets 15227823 bytes 943423537   # 被旁路掉的短包
meta ... ip6 length 1-104 ... return         counter packets 0 bytes 0                  # 本环境 WAN 无 IPv6 上游
meta ... "br-lan" counter ... queue ... to 10010  counter packets 64431 bytes 53948061   # 真正进用户态
```

队列速率从约 3100 包/秒降到 **10~16 包/秒**；`sys` 时间下降约 **28%**；同线路下载速率提升 **10%~15%**。

### 回归验证（重要）

旁路后必须确认 UA 改写没有被误伤：

```sh
# 从内网设备访问 http://ua-check.stagoh.com/ ，应仍显示 Safari/537.
# 或跑 scripts/90-verify-ua-length.py 批量复测
```

> 教训：NFQUEUE 只适合「少量、需要深度处理」的包。大流量路径上用 NFQUEUE 时，
> 先用 nft 把「确定不需要处理」的包旁路掉，再入队。

---

## 二、瓶颈二：packet steering 把 RPS 钉死在单核

### 现象

修完瓶颈一后 CPU 降了，但下载丢包率仍有 **7%~11%**，总有一个核被吃满，速率上不到峰值。

### 定位

```sh
# 每行一个 CPU：第 1 列 processed、第 2 列 dropped（十六进制值）
cat /proc/net/softnet_stat

# 收包被分给了哪个核（4 = 只有 cpu2）
cat /sys/class/net/wan/queues/rx-0/rps_cpus
```

### 根因

`network.globals.packet_steering=1` 是 OpenWrt 的默认值。开机 S25 由 `/etc/init.d/packet_steering`
运行 `/usr/libexec/network/packet-steering.uc`：

- 给**每个**网络设备挑一个「最空闲核」，把**单核**掩码写进 `rps_cpus`（例：`4` = 只给 cpu2）；
- 并把内核收包线程钉核：`napi/mtk_eth-*` → cpu0，`napi/phy0-*` / `mt76-tx phy0` → cpu3。

于是 WAN 收包的全部 softirq 压在一个核上，单核打满后出现背压丢包。

更麻烦的是它会**自动重跑**：它注册了 `network` / `firewall` 的 reload trigger 和 `interface.*` 的
raw trigger —— network / firewall reload、拨号、DHCP 续约等事件都会触发它**覆盖手工写入的 rps_cpus**。
手工 `echo 7 > rps_cpus` 过几分钟就白改。

### 解决：三层防线

`scripts/45-set-rps-masks.py` 部署三样东西：

**1. `/usr/bin/rps-tuning.sh`** —— 真正干活的脚本：

```sh
#!/bin/sh
# rps-tuning.sh: spread RX packet processing across CPU cores.
# OpenWrt packet-steering assigns one CPU per device; this overrides it.
#   eth0/wan/lan1-4 : mask 7 = cpu0+cpu1+cpu2
#   wifi vifs       : mask 6 = cpu1+cpu2 (cpu0 carries all HW IRQs, cpu3 runs wifi tx/napi)
for i in eth0 wan lan1 lan2 lan3 lan4; do
	f=/sys/class/net/$i/queues/rx-0/rps_cpus
	[ -e "$f" ] && echo 7 > "$f"
done
for d in /sys/class/net/phy*-ap*; do
	f=$d/queues/rx-0/rps_cpus
	[ -e "$f" ] && echo 6 > "$f"
done
```

掩码怎么选：

- **eth0 / wan / lan1-4 → `7`（cpu0+1+2）**：把 RX softirq 摊到三个核；不含 cpu3 —— 那里是无线收发线程的主场。
- **Wi-Fi vif（`phy*-ap*`）→ `6`（cpu1+2）**：cpu0 扛着全部硬件中断，再塞无线收包会互相拖累，避开。

**2. `/etc/rc.local` 开机调用一次**：

```sh
# spread RX softirq across cores, overriding packet-steering's single-CPU choice
/usr/bin/rps-tuning.sh
```

**3. 在 `/etc/init.d/packet_steering` 的 `reload_service` 末尾挂钩子** —— 它每次重跑，紧跟着覆盖回去：

```sh
	# re-apply custom RPS masks after the stock balancer (eth=7 / wifi=6)
	if [ -x /usr/bin/rps-tuning.sh ]; then
		/usr/bin/rps-tuning.sh
	fi
```

### 验证

```sh
# 强制 packet steering 重跑（模拟它的自动覆盖），然后立刻检查
/etc/init.d/packet_steering reload
sleep 1
cat /sys/class/net/wan/queues/rx-0/rps_cpus       # 应仍为 7
cat /sys/class/net/phy*-ap*/queues/rx-0/rps_cpus  # 应仍为 6
```

实测：强制重跑后掩码保持不变。满速下载（14.33MB/s ≈ 115Mbps，线路峰值）时：

- `/proc/net/softnet_stat` 各核 dropped **全部为 0**（11 秒窗口共处理约 27.9 万包）；
- `NET_RX` 在三核间均衡分流（cpu0 16460 / cpu1 7848 / cpu2 10313 包/11s）；
- cpu2 不再饱和（100% → 84%），四核 81%~88%。

### phy 命名注记（mt7615 DBDC 的坑）

K2P 的 MT7615 是双频单芯片（DBDC）：`phy0` = 2.4G（ch11）、`phy1` = 5G（ch149）。
但无线收发的内核线程**只有一套、以 phy0 命名**（`napi/phy0-7` / `napi/phy0-8` / `mt76-tx phy0`），
两个频段共用、全部钉在 cpu3（可用 `taskset -p` 核对线程掩码 `psr=3`）。

所以 5G 客户端下载时，你看到的热线程是 `mt76-tx phy0`（cpu3，约 60%，无线发送主线程）与
`napi/mtk_eth-6`（cpu0，约 52%，WAN 收包主线程）——都是正常现象，别被线程名里的 "phy0" 误导。

---

## 三、四阶段实测对比

| 阶段 | 关键改动 | 下载速率 | softnet 丢包 | CPU 观察 |
|---|---|---|---|---|
| ① 之前 | UA2F 全量入队 + packet steering 单核 RPS | 达不到线路峰值，下载时整机卡 | —— | 多核高位 |
| ② + UA2F 短包旁路 | 队列前加 `ip length 1-80` / `ip6 length 1-104` return | 提升 10%~15% | —— | sys 下降约 28%；队列 3100 → 10~16 包/秒 |
| ③ + RPS 三核 | eth0/wan/lan1-4 = `7` | ≈14.3MB/s（约 115Mbps） | 从 7%~11% 降到 1% 以内 | 单核瓶颈解除 |
| ④ + Wi-Fi 掩码 | phy*-ap* = `6`（cpu1+2） | 14.33MB/s（保持峰值） | 全核 0 | cpu2 100% → 84%，四核 81%~88% |

> 数据来自同一线路的 Steam 满速下载实测（11 秒采样窗口）；丢包读自 `/proc/net/softnet_stat`。

结论：满速下载时四核 81%~88%，已接近 MT7621 880MHz 平台的整机上限；单核瓶颈与丢包全部解除，无需继续压榨。

---

## 四、持久性：重启与网络重载后仍然生效

验证要点（核心命令见上文「验证」小节）：

```sh
# 1. 强制 packet steering 重跑后，掩码是否仍然正确（最关键的检验）
/etc/init.d/packet_steering reload && sleep 1
cat /sys/class/net/wan/queues/rx-0/rps_cpus       # 7
cat /sys/class/net/phy*-ap*/queues/rx-0/rps_cpus  # 6

# 2. 检查三层防线就位
ls -l /usr/bin/rps-tuning.sh                       # 存在且可执行
grep rps-tuning /etc/rc.local                      # 有调用
grep -A2 rps-tuning /etc/init.d/packet_steering     # 有钩子
```

重启后按 `docs/04-验证与运维.md` 的重启持久性检查表逐项核对（已含 UA2F 短包旁路与 RPS 掩码两行）。

> 定式：凡是「会被 OpenWrt 动态服务改写」的内核参数（RPS、IRQ affinity 等），单独写一次必然不可靠。
> 要「部署脚本 + 开机调用 + 钩在改写者之后」三件套齐备。

---

## 五、已知限制与取舍

- **不要开 flow offload**：被 offload 的流量走 flowtable 快路径，绕过 mangle/postrouting ——
  TTL 归一化与 UA2F（NFQUEUE 队列就在 postrouting 上）都会失效，等于反检测白做。
  宁可少一点转发性能，也要保住反检测。
- **平台天花板**：115Mbps（14.33MB/s）已是本线路峰值；满速时四核 81%~88%。
  若换上更高带宽的线路，瓶颈会先回到 MT7621 本身。
- **无线侧仍有一个热点线程**：`mt76-tx phy0`（cpu3，下载时约 60%）是无线发送主线程，属正常现象。
- **RPS 是软件分流**：不是硬件多队列（RSS）。它把 softirq 摊到多核，收益在「下载 + 多设备」这类
  混合负载下最明显；单个大流仍可能集中在单核处理。
- **IPv6 的长度语义**：`ip6 length` 匹配 IPv6 **负载长度**（不含 40 字节基本头），与 v4 的
  `ip length`（总长）不同。本环境 WAN 无 IPv6 上游，v6 旁路规则计数为 0，属防御性配置。

---

## 六、队列与缓冲补调（第二梯队，防「单点排队」）

前三节解决的是「已经观察到的瓶颈」（ACK 洪水、RPS 单核）。本节是针对**潜在单点队列**的补调，
四项全部低风险、可随时回退，且都做了持久化。

### 6.1 参数与理由

| 参数 | 默认/原值 | 调整为 | 作用 | 为什么调 |
|---|---|---|---|---|
| `net.ipv4.tcp_max_syn_backlog` | 128 | **4096** | TCP 半连接队列（SYN 排队） | 128 在多设备同时建连（P2P、测速、宿舍全员上线）时容易溢出丢 SYN |
| dnsmasq `cachesize` | 1000 | **4000** | DNS 缓存条数 | 多设备 + 大量域名时缓存淘汰频繁，重复查询变多 |
| `net.core.wmem_max` | 176KB | **4MB** | socket **发送**缓冲上限 | 之前只调了收方向（`rmem_max` 4MB），收发不对称，上传/视频通话受限 |
| `net.core.netdev_budget` | 300 | **600** | 单次软中断可处理的包数 | 满速下载时收包预算偏紧，翻倍让 softirq 更从容 |

> 另一项 `ua2f.main.nfqueue_workers` 曾从 `1` 调到 `4`（对应 4 个队列 / 4 核）。
> 这个改动**没有解决当时的酷安图片问题**（真凶见 `03` 坑 20），但 4 workers 本身无害，
> 高并发下更从容，故保留。

### 6.2 落地方式

```sh
# 内核参数：写 sysctl 并立即生效
cat >> /etc/sysctl.conf <<'EOF'
net.ipv4.tcp_max_syn_backlog=4096
net.core.wmem_max=4194304
net.core.netdev_budget=600
EOF
sysctl -p /etc/sysctl.conf

# dnsmasq 缓存
uci set dhcp.@dnsmasq[0].cachesize='4000'
uci commit dhcp
/etc/init.d/dnsmasq restart

# UA2F workers（可选）
uci set ua2f.main.nfqueue_workers='4'
uci commit ua2f
/etc/init.d/ua2f restart
```

### 6.3 验证

```sh
sysctl net.ipv4.tcp_max_syn_backlog net.core.wmem_max net.core.netdev_budget
uci get dhcp.@dnsmasq[0].cachesize        # 4000
nslookup www.baidu.com 127.0.0.1         # 解析正常
ping -c 2 223.5.5.5                      # 连通正常
```

持久性：sysctl 三项写在 `/etc/sysctl.conf`，重启由 `/etc/init.d/sysctl` 加载；
dnsmasq 与 UA2F 均走 UCI commit，重启不丢。

### 6.4 取舍说明

- 这些都是「队列/缓冲上限」，调大只影响极端并发时的表现，**不改变稳态吞吐**；
  实测三态（UA2F 开/关、长短 UA）吞吐差异都在噪声内，说明调参不是「提速」，而是「防掉队」。
- `wmem_max` 调大后单连接内存占用上限提高，128MB 内存机器上仍安全（实际按需分配，不会预占）。
- 带宽监控（nlbwmon）已从本机卸载：它把统计写在 tmpfs（重启清零），且对日常使用价值有限。
  若需要长期流量统计，优先考虑「数据库目录落 overlay + 缩短 commit 间隔」的改法，而不是默认配置。