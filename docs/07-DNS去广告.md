# 07 · DNS 层去广告

> **状态说明（2026-10）**：本功能已在实机**下线**。原因是体感收益有限——
> 主流 App 的广告多为同域或硬编码 IP，DNS 层拦不到（见下方边界说明）。
> 本文与 `scripts/46-setup-dns-adblock.sh` 保留，作为方案与踩坑参考，随时可按需恢复。
> 下线操作：删除 `dhcp.@dnsmasq[0].addnhosts` 引用、移除更新 cron、删除清单与脚本即可。

目标：**不加装任何额外软件包**，让路由器把内网所有设备的网页广告、统计埋点、
第三方广告 SDK 域名在 DNS 层直接掐掉。

> **先说边界**（避免期望落空）：DNS 层去广告只对**独立域名**的广告有效。
> 抖音 / 爱奇艺 / 腾讯视频这类 App 的**站内广告与正片同域名**，或走 App 自带的
> HTTPDNS + 硬编码 IP，DNS 层无从下手。设备侧开了 DoH（Chrome / iOS 私有 DNS）
> 也会直接绕过路由器。因此本方案能明显净化**网页浏览**，
> 但**不要指望它清掉 App 开屏广告**。

---

## 一、为什么不装 AdGuardHome / adblock-fast

| 方案 | 结论 | 原因（本机实测） |
|---|---|---|
| AdGuardHome | 不可行 | 体积与常驻内存远超本机上限（overlay 只有 7.6MB） |
| adblock-fast | 不划算 | 要额外装 curl / ucode；清单缓存在 `/tmp`，重启即失，每次开机都得联网重下 |
| 第三方过滤型 DNS | 不选 | 要放弃「就近运营商 DNS」的延迟优势（见 03 篇坑 14 实测） |
| **dnsmasq 原生 addn-hosts** | **采用** | 零额外依赖；清单落盘在 overlay，重启立即生效、断网也有屏蔽 |

能这么做的关键前提：**固件自带 `uclient-fetch` + `ca-bundle`**，本身就能 HTTPS 下载，
所以不需要为了拉清单去装 `curl`。

---

## 二、原理

dnsmasq 支持 `addn-hosts=<文件>`：文件里每行 `IP 域名` 就是一条静态解析记录。
把广告域名统一写成 `0.0.0.0 域名`，这些域名就被解析到 `0.0.0.0`，客户端连接立即失败。

挂载只需一行 uci：

```sh
uci add_list dhcp.@dnsmasq[0].addnhosts='/etc/dns-blocklist.hosts'
uci commit dhcp
```

**两个容易踩空的点**（读 `/etc/init.d/dnsmasq` 源码确认）：

1. init 里的 `append_addnhosts()` 会顺带调用 `append_extramount()`，把该文件**自动加进
   ujail 的挂载白名单**。dnsmasq 跑在 ujail 沙箱里，**不挂载就读不到文件**——用
   `addnhosts` 选项可以免去手工处理。
2. `/etc/init.d/dnsmasq reload` 的实现是「重新生成配置 + 给 dnsmasq 发 SIGHUP」，
   而 dnsmasq 收到 SIGHUP 会**重读 addn-hosts 文件**。所以清单更新后只要 `reload`，
   不需要整机重启 dnsmasq。

---

## 三、实测数据（本机，2026-10）

上游清单：**anti-AD**（`https://anti-ad.net/domains.txt`，裸域名、一行一个，国内直连实测 4 秒拉完）。

| 项目 | 实测值 |
|---|---|
| 上游条数 | 109,446 条 / 2.9MB（纯文本） |
| overlay 实际占用 | 约 **0.94MB**（jffs2 压缩约 3 倍：写入前后 `df` 差实测） |
| dnsmasq 内存 | 10.9 万条 → **VmRSS ≈ 13MB**（≈120 字节/条），本机可用内存约 38MB |
| reload 后重读全表 | 880MHz CPU 上约 **5~10 秒**，这期间 DNS 不响应 |
| 本方案默认档 | 6 万条（等间隔抽样后实际 54,723 条 / 1.49MB） |

> 实测方法与坑都记在 03 篇坑 19。

---

## 四、部署

```sh
ssh root@192.168.1.1 'sh -s' < scripts/46-setup-dns-adblock.sh
```

脚本做四件事：部署更新器 `/usr/bin/dns-adblock-update.sh` → 首次抓取清单到
`/etc/dns-blocklist.hosts` → uci 挂 `addnhosts` 并 reload → 加每周 cron 并自检。

可调环境变量（`export` 后执行，或改脚本内默认值）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AB_SRC` | anti-AD `domains.txt` | 清单源，主源失败会自动切备用源 |
| `AB_MAX_LINES` | `60000` | 保留条数上限；**设 `0` = 不裁剪**（全量 10.9 万条，内存约 13MB、overlay 余量更紧） |
| `AB_SCHED` | `17 4 * * 0` | cron 表达式（每周日 04:17 低峰） |

**为什么默认裁剪到 6 万条**：内存与 overlay 余量都要留够。裁剪用的是**等间隔抽样**
（`awk -v s=step -v o=off 'NR % s == o'`，`off` 按周轮换：`off = (epoch/604800) % step`），
让覆盖率在整张表上均匀下降，且长期看会轮流扫过全表。**不能用 `head` 截断**——
anti-AD 的清单是**按字母序**排的，`head` 会把 m~z 段的广告域名（umeng / tanx 这类）
整段丢光。固定用 `NR % step == 1` 也不行：那样同一批域名永远留在表里、另一批永远进不来。

---

## 五、验证

```sh
# 1. 清单已落盘、条数与体积
wc -l /etc/dns-blocklist.hosts
ls -la /etc/dns-blocklist.hosts

# 2. uci 挂上了，且生成配置里有 addn-hosts
uci show dhcp.@dnsmasq[0] | grep addnhosts
grep -h 'addn-hosts' /var/etc/dnsmasq.conf.*

# 3. dnsmasq 日志确认读入（条数应等于上一步 wc -l）
logread | grep -E 'dns-blocklist'

# 4. 命中清单的域名应返回 0.0.0.0
nslookup doubleclick.net 127.0.0.1

# 5. 对照：正常域名应返回真实 IP
nslookup www.qq.com 127.0.0.1

# 6. 资源占用
df -h /overlay
free | head -2
pgrep -f '/usr/sbin/dnsmasq' | while read p; do grep VmRSS /proc/$p/status; done
```

**通过标准**：第 4 步返回 `Address: 0.0.0.0`，第 5 步返回真实 IP，overlay 余量 ≥ 1.5MB。

> 提示：`pgrep -x dnsmasq` 会匹配到 **ujail 外壳进程**（它的 comm 也叫 dnsmasq，
> RSS 只有 1MB 左右），那是假象；真正干活的是 `/usr/sbin/dnsmasq`。

---

## 六、自动更新与失效保护

`/usr/bin/dns-adblock-update.sh` 的流程（每一步都为「宁可不去广告，也不能搞坏路由器」）：

1. `uclient-fetch` 拉取主源，失败自动切备用源；全失败则退出、**保留旧清单**；
2. 归一化：去 CR、丢注释/空行/非法行、统一补 `0.0.0.0 ` 前缀、`sort -u` 去重；
3. **条数下限**校验（默认 1000 条，防止拿到一个被截断的半截文件）；
4. 按 `AB_MAX_LINES` 做**等间隔抽样**裁剪；
5. **内容没变就直接跳过**（不写盘、不 reload，省一次 flash 写入和 10 秒 DNS 抖动）；
6. **空间守卫**：`/overlay` 可用 < 800KB 就放弃本次更新；
7. **原子替换**：先写 `.new` 再 `mv`（同文件系统内 rename 是原子的），
   任何一步失败都保留旧清单；
8. `/etc/init.d/dnsmasq reload`。

cron 每周执行一次，日志两处可看：

```sh
logread | grep dns-adblock        # 走 syslog
cat /tmp/dns-adblock.log          # 独立日志（tmpfs，重启即清空）
```

---

## 七、撤销

```sh
# 1. 去掉 uci 里的挂载（dnsmasq reload 后立即停止屏蔽）
uci -q delete dhcp.@dnsmasq[0].addnhosts
uci commit dhcp
/etc/init.d/dnsmasq reload

# 2. 删 cron 与更新器
grep -v 'dns-adblock-update.sh' /etc/crontabs/root > /tmp/cron.new
mv /tmp/cron.new /etc/crontabs/root
rm -f /usr/bin/dns-adblock-update.sh

# 3. 删清单，回收空间
rm -f /etc/dns-blocklist.hosts
df -h /overlay
```

---

## 八、已知限制

- **对 App 内广告基本无效**：广告与内容同域名，或走 HTTPDNS，DNS 层拦不到。
- **DoH 会绕过**：客户端启用 DNS-over-HTTPS 后不再问路由器，本方案对它无效；
  要封堵需另加 nft 规则重定向 53/853 端口（本包未做）。
- **每次更新有短暂 DNS 中断**：reload 后重读全表要 5~10 秒，期间查询会超时，
  所以 cron 放在每周日凌晨低峰；若被中断，稍等即恢复。
- **jffs2 不主动回收脏块**：反复改写清单文件会让 overlay 可用空间长期偏低，
  因此默认裁剪到 6 万条并设空间下限；真要全量，请接受余量变紧这一代价。
- **可能误杀**：清单越大越容易波及正常服务，anti-AD 相对克制，但仍建议先观察一周。
- **不是全量上游**：默认 6 万条是等间隔抽样，属「覆盖均匀打折」，不是完整清单。
