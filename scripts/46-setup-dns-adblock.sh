#!/bin/sh
# 46 · DNS 层去广告（dnsmasq addn-hosts + 每周自动更新）
#
# 为什么不用 AdGuardHome / adblock-fast
# ---------------------------------------------------------------------------
# 硬件：斐讯 K2P（MT7621A 四核 880MHz / 128MB RAM / overlay 仅 7.6MB）
#   - AdGuardHome 的体积与内存都远超本机上限，装不下
#   - adblock-fast 要额外装 curl / ucode，且清单缓存在 /tmp：重启即失、
#     每次开机都得联网重下；本机 CPU 已在高位，不划算
#   - 本方案零额外依赖：uclient-fetch + ca-bundle 固件自带
#
# 关键实测（2026-10 · K2P 现网，均为本机实测而非估算）
#   - 上游 anti-AD 域名清单 109,446 条 / 2.9MB
#   - jffs2 压缩约 3 倍：2.9MB 的清单在 flash 上只占约 0.94MB
#   - dnsmasq 真实内存开销 ≈ 120 字节/条（10.9 万条 → VmRSS 约 13MB）
#   - reload 后重读全表在 880MHz 上要 5~10 秒，期间 DNS 不响应 → cron 放低峰
#   - jffs2 不会主动回收脏块，反复改写大文件会让可用空间长期偏低；
#     故默认把清单裁到 6 万条（等间隔抽样），并给替换加空间下限
#
# 前提
# ---------------------------------------------------------------------------
#   1) 已配好 DNS 分流（见 43-optimize-dns-ipv6.py），dnsmasq 自带版本即可
#   2) 本方案不依赖 dnsmasq 的 ipset/nftset 编译项（本机编译项为
#      no-ipset no-nftset，域名型屏蔽不受影响）
#   3) 这是 DNS 层屏蔽：对网页广告/统计埋点有效，对抖音/爱奇艺这类
#      「广告与内容同域」的 App 内广告基本无效
#
# 本脚本做四件事
# ---------------------------------------------------------------------------
#   a) 部署更新器 /usr/bin/dns-adblock-update.sh
#   b) 立即抓取一次清单 → /etc/dns-blocklist.hosts（hosts 格式 0.0.0.0 <域名>）
#   c) uci 挂上 addnhosts（init 会自动把它挂进 dnsmasq 的 ujail），reload
#   d) 加每周 cron，并自检（命中域名、对照域名、内存、空间）
#
# 可调环境变量（执行前 export，或在脚本内改默认值）
# ---------------------------------------------------------------------------
#   AB_SRC        清单源，默认 anti-AD 裸域名清单（国内直连，实测 4s 拉完）
#   AB_MAX_LINES  保留条数上限，默认 60000（等间隔抽样，覆盖均匀打折）；
#                 设 0 = 不裁剪（全量 10.9 万条：内存约 13MB、overlay 余量更紧）
#   AB_SCHED      cron 表达式，默认 `17 4 * * 0`（每周日 04:17 低峰）
#
# 用法
# ---------------------------------------------------------------------------
#   ssh root@192.168.1.1 'sh -s' < scripts/46-setup-dns-adblock.sh
#   撤销：见 docs/07-DNS去广告.md 末节

set -e

AB_SRC="${AB_SRC:-https://anti-ad.net/domains.txt}"
AB_MAX_LINES="${AB_MAX_LINES:-60000}"
AB_SCHED="${AB_SCHED:-17 4 * * 0}"
AB_DEST=/etc/dns-blocklist.hosts
AB_UPDATER=/usr/bin/dns-adblock-update.sh
AB_LOG=/tmp/dns-adblock.log

echo "=== 0. 基线：空间与内存 ==="
df -h /overlay | tail -1
free -m | head -2 | sed 's/^/  /'

echo
echo "=== a) 部署更新器 $AB_UPDATER ==="
cat > "$AB_UPDATER" <<'UPDATER_EOF'
#!/bin/sh
# dns-adblock-update.sh —— 抓取去广告清单，校验后原子替换，reload dnsmasq
#
# 设计要点（这是本方案能「自动更新又不炸」的核心）
#   1) 只接受裸域名格式，统一转成 hosts 格式 `0.0.0.0 <域名>`
#   2) 多重校验：条数下限 / 上限；任何一步不过 → 保留旧清单，绝不替换
#   3) 原子替换：先写临时文件再 mv，避免写到一半被打断留下半截清单
#   4) 主源失败自动切备用源；全部失败则退出，等下次 cron
#   5) 只 reload dnsmasq（SIGHUP 会重读 addn-hosts），不做整机重启
#
# 环境变量：AB_SRC（主源）、AB_FALLBACK（备用源）、AB_MAX_LINES（0=不裁剪）
set -u
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

SRC="${AB_SRC:-https://anti-ad.net/domains.txt}"
FALLBACK="${AB_FALLBACK:-https://cdn.jsdelivr.net/gh/privacy-protection-tools/anti-AD@master/anti-ad-domains.txt}"
DEST="${AB_DEST:-/etc/dns-blocklist.hosts}"
MAX_LINES="${AB_MAX_LINES:-0}"
MIN_LINES="${AB_MIN_LINES:-1000}"
LOG="${AB_LOG:-/tmp/dns-adblock.log}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
    logger -t dns-adblock "$*" 2>/dev/null || true
}

RAW=/tmp/adb-raw.$$
NEW=/tmp/adb-new.$$
trap 'rm -f "$RAW" "$NEW" "$NEW.cap"' EXIT INT TERM

fetch() {
    # uclient-fetch 支持 https（固件自带 libustream-mbedtls + ca-bundle）
    if command -v timeout >/dev/null 2>&1; then
        timeout 180 uclient-fetch -O "$RAW" "$1" >/dev/null 2>&1
    else
        uclient-fetch -O "$RAW" "$1" >/dev/null 2>&1
    fi
    [ -s "$RAW" ]
}

ok=0
for url in "$SRC" "$FALLBACK"; do
    [ -n "$url" ] || continue
    if fetch "$url"; then
        ok=1
        log "fetched: $url"
        break
    fi
    log "fetch failed: $url"
done
[ "$ok" = 1 ] || { log "ERROR: all sources failed, keep old list"; exit 1; }

# 归一化：去 CR、丢弃注释/空行/非法行、补 0.0.0.0 前缀、去重排序
sed -e 's/\r$//' "$RAW" \
    | awk '/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/ { print "0.0.0.0 " $0 }' \
    | sort -u > "$NEW"

n=$(wc -l < "$NEW")
if [ "$n" -lt "$MIN_LINES" ]; then
    log "ERROR: only $n entries (< $MIN_LINES), keep old list"
    exit 1
fi
if [ "$MAX_LINES" -gt 0 ] && [ "$n" -gt "$MAX_LINES" ]; then
    # 注意：清单是按字母序排的。用 head 截断会把 m~z 段的广告域名整段丢光
    # （umeng / tanx 这些全在后面），所以改成「等间隔抽样」，让覆盖率在
    # 整张表上均匀下降，而不是砍掉尾巴。
    step=$(( (n + MAX_LINES - 1) / MAX_LINES ))
    awk -v s="$step" 'NR % s == 1' "$NEW" > "$NEW.cap" && mv -f "$NEW.cap" "$NEW"
    n=$(wc -l < "$NEW")
    log "sampled down to $n entries (every ${step}th line)"
fi

# 内容没变就不写盘、不 reload（省一次 flash 写入和 10 秒 DNS 抖动）
if [ -f "$DEST" ] && cmp -s "$NEW" "$DEST"; then
    log "no change ($n entries), skip install"
    exit 0
fi

# 空间保护：下限只需盖住「新清单压缩后的大小」（6 万条约 0.52MB）。
# 注意 jffs2 不会主动回收脏块：阈值设高会形成「空间不足→跳过更新→空间
# 永不回收」的死锁；真正的兜底是写失败即中止、保留旧清单。
avail=$(df -k /overlay 2>/dev/null | awk 'NR==2{print $4}')
if [ "${avail:-0}" -lt 800 ]; then
    log "ERROR: overlay free ${avail}KB < 800KB, keep old list"
    exit 1
fi

# 原子替换：临时文件 → mv（同一文件系统内 mv 是 rename，原子）
if ! cp "$NEW" "$DEST.new" || ! chmod 0644 "$DEST.new" || ! mv -f "$DEST.new" "$DEST"; then
    log "ERROR: install to $DEST failed, keep old list"
    exit 1
fi
sync
log "installed $n entries -> $DEST"

# 安装阶段由 46 号脚本自己控制 reload 时机，避免重复触发（每次重读 10.9 万条
# 在这颗 880MHz CPU 上要 5~10 秒，期间 DNS 不响应）
if [ "${AB_NO_RELOAD:-0}" = 1 ]; then
    log "skip reload (AB_NO_RELOAD=1)"
    exit 0
fi

if /etc/init.d/dnsmasq reload >/dev/null 2>&1; then
    log "dnsmasq reloaded"
else
    log "WARN: dnsmasq reload failed (will apply on next restart)"
fi
exit 0
UPDATER_EOF
chmod 0755 "$AB_UPDATER"
echo "  已写入并赋可执行权限"

echo
echo "=== b) 首次抓取清单 ==="
# AB_NO_RELOAD=1：此时 dnsmasq 还没引用该文件，先不 reload，留给步骤 c 统一触发
if AB_SRC="$AB_SRC" AB_MAX_LINES="$AB_MAX_LINES" AB_LOG="$AB_LOG" AB_NO_RELOAD=1 "$AB_UPDATER"; then
    echo "  OK"
else
    echo "  ⚠ 首次抓取失败（网络/认证未就绪？）—— 已跳过，稍后 cron 会自动重试"
fi
if [ -f "$AB_DEST" ]; then
    echo "  清单: $(wc -l < "$AB_DEST") 条 / $(wc -c < "$AB_DEST") 字节"
    head -1 "$AB_DEST" | sed 's/^/  样例: /'
else
    echo "  ⚠ $AB_DEST 尚不存在"
fi

echo
echo "=== c) uci 挂载 addnhosts 并 reload dnsmasq ==="
# 先清掉旧值再重加，保证幂等；选项不存在时 uci 会返回非 0，故忽略
uci -q delete dhcp.@dnsmasq[0].addnhosts 2>/dev/null || true
uci add_list dhcp.@dnsmasq[0].addnhosts="$AB_DEST"
uci commit dhcp
# reload = 重新生成配置 + 给 dnsmasq 发 SIGHUP（会重读 addn-hosts）
if /etc/init.d/dnsmasq reload >/dev/null 2>&1; then
    echo "  reload 完成"
else
    echo "  ⚠ reload 返回非 0，改用 restart"
    /etc/init.d/dnsmasq restart >/dev/null 2>&1 || true
fi
echo "  --- uci ---"
uci show dhcp.@dnsmasq[0] | grep addnhosts | sed 's/^/  /' || echo "  (无)"
echo "  --- 生成配置里的 addn-hosts（证明已进 ujail 挂载）---"
grep -h 'addn-hosts' /var/etc/dnsmasq.conf.* 2>/dev/null | sed 's/^/  /' || echo "  (无)"

echo
echo "=== d) 每周定时更新 ==="
grep -v 'dns-adblock-update.sh' /etc/crontabs/root > /tmp/.cron.new 2>/dev/null || true
echo "$AB_SCHED AB_SRC='$AB_SRC' AB_MAX_LINES=$AB_MAX_LINES $AB_UPDATER >/dev/null 2>&1" >> /tmp/.cron.new
mv /tmp/.cron.new /etc/crontabs/root
/etc/init.d/cron reload >/dev/null 2>&1 || /etc/init.d/cron restart >/dev/null 2>&1 || true
echo "  --- crontab ---"
sed 's/^/  /' /etc/crontabs/root

echo
echo "=== 验证 ==="
# 关键：reload 后 dnsmasq 要重新读入 10.9 万条记录，880MHz 上约 5~10 秒，
# 这期间查询会超时——必须先等它就绪，否则会把「正在加载」误判成「失败」。
i=0
while [ $i -lt 40 ]; do
    if nslookup www.qq.com 127.0.0.1 >/dev/null 2>&1; then
        break
    fi
    i=$((i+1))
    sleep 1
done
echo "  等待 dnsmasq 就绪：约 ${i}s"
testdom=$(awk 'NR==1{print $2}' "$AB_DEST" 2>/dev/null)
echo "  --- 命中清单的域名，应返回 0.0.0.0：$testdom ---"
if [ -n "$testdom" ]; then
    nslookup "$testdom" 127.0.0.1 2>&1 | sed -n '/^Name:/,/^Address:/p' | sed 's/^/  /'
fi
echo "  --- 对照：正常域名应返回真实 IP ---"
nslookup www.qq.com 127.0.0.1 2>&1 | sed -n '/^Name:/,/^Address:/p' | sed 's/^/  /'
echo "  --- dnsmasq 内存占用 ---"
# 注意：pgrep -x dnsmasq 会匹配到 ujail 外壳进程（它的 comm 也叫 dnsmasq），
# 那个 RSS 只有 1MB 左右，是假象；真正干活的是 /usr/sbin/dnsmasq
for p in $(pgrep -f '/usr/sbin/dnsmasq' 2>/dev/null); do
    grep -E 'VmRSS' /proc/$p/status | sed 's/^/  /'
done
echo "  --- 系统可用内存 ---"
free | head -2 | sed 's/^/  /'
echo "  --- 空间占用 ---"
df -h /overlay | tail -1 | sed 's/^/  /'

echo
echo "完成。自动更新日志：$AB_LOG（tmpfs，重启即清空）；同时写 syslog，可用 logread 看"
echo "注意：每次更新后 dnsmasq 要重读全表，DNS 会短暂（约 5~10 秒）不响应；"
echo "      cron 已安排在每周日 04:17 低峰执行"
echo "提示：DNS 层屏蔽对 App 内广告无效（广告与内容同域）；设备开 DoH 会绕过本机 DNS"
echo "默认清单 6 万条（等间隔抽样，覆盖在整张表上均匀下降）；"
echo "要全量覆盖可重跑并设 AB_MAX_LINES=0（10.9 万条，内存约 13MB、overlay 余量更紧）"
