#!/bin/sh
# 47-persist-log.sh —— 日志落盘 + 启动历史（断电可查）
#
# 在路由器上执行（也可 ssh root@192.168.1.1 'sh -s' < 47-persist-log.sh）
# 幂等：重复执行不会重复追加；修改前备份原文件。
#
# 做完两件事：
#   1. syslog 写入 /etc/log/messages，512KB 轮转（约 24 小时）
#   2. 每次开机向 /etc/log/boot-history.log 追加一行，记录 btime 与
#      上次关机性质（正常关机 / 异常断电），下次开机即可判断重启原因
#      守卫：只有 uptime <= 600s（真开机时刻）才写；手动重跑本脚本不会伪造记录
#
# 卸载：/etc/init.d/boot-log disable; uci delete system.@system[0].log_file; uci commit system

set -e

LOG_DIR=/etc/log
LOG_FILE="$LOG_DIR/messages"
HIST="$LOG_DIR/boot-history.log"
INIT=/etc/init.d/boot-log

[ "$(id -u)" = "0" ] || { echo "请用 root 执行"; exit 1; }

mkdir -p "$LOG_DIR"

# ---------- 1. syslog 落盘 ----------
echo "== 1/3 syslog 落盘 =="
cp /etc/config/system /etc/config/system.bak.$(date +%s) 2>/dev/null || true

uci set system.@system[0].log_file="$LOG_FILE"
uci set system.@system[0].log_size='512'
uci commit system
/etc/init.d/log restart
sleep 2

echo "log_file = $(uci get system.@system[0].log_file)"
echo "log_size = $(uci get system.@system[0].log_size) KB"
ls -l "$LOG_FILE" 2>/dev/null || echo "（下次写日志时创建）"

# ---------- 2. 启动历史脚本 ----------
echo ""
echo "== 2/3 启动历史 =="
if [ -f "$INIT" ]; then
    cp "$INIT" "$INIT.bak.$(date +%s)"
fi

cat > "$INIT" <<'EOF'
#!/bin/sh /etc/rc.common
START=99
STOP=01

# 只把「真开机」记进启动历史。
# 本脚本被手动重跑时 uptime 往往是几千秒，那时写进来的记录既不是启动、
# 又必然被标成「异常断电/复位」（此时不会有 .clean_shutdown 标记），
# 会直接污染 08 篇「断电取证」的唯一可信证据。
MAX_BOOT_UPTIME=600

start() {
    LOG=/etc/log/boot-history.log
    NOW=$(date '+%F %T')
    UP=$(cut -d. -f1 /proc/uptime)
    if [ "${UP:-999999}" -gt "$MAX_BOOT_UPTIME" ]; then
        echo "$NOW  skip   uptime=${UP}s  非开机时刻，未记入启动历史"
        return 0
    fi
    BTIME=$(awk '/btime/{print $2}' /proc/stat)
    if [ -f /etc/log/.clean_shutdown ]; then
        PREV="正常关机"
        rm -f /etc/log/.clean_shutdown
    else
        PREV="异常断电/复位"
    fi
    echo "$NOW  boot  btime=$BTIME  uptime=${UP}s  prev=$PREV" >> "$LOG"
    if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 200 ]; then
        tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
}

stop() {
    touch /etc/log/.clean_shutdown
}
EOF

chmod +x "$INIT"
"$INIT" enable
# 这里调 start 只是为了让「装完就能看到输出」；真正的 boot 行只会在下次开机的
# 前 600 秒内写入，重跑本脚本不会伪造一条「异常断电」。
"$INIT" start
echo "已启用 boot-log（S99）"

# ---------- 3. 验证 ----------
echo ""
echo "== 3/3 验证 =="
echo "--- 启动历史 ---"
cat "$HIST" 2>/dev/null || echo "（暂无记录）"
echo "--- 疑似「非开机时刻」写入的旧记录（uptime > 600s，属于历史遗留，可手工删）---"
grep -E 'uptime=[0-9]{4,}s' "$HIST" 2>/dev/null || echo "  （无）"
if [ ! -s "$HIST" ]; then
    echo "--- 说明：boot 行只在下一次「真实开机」时写入（uptime ≤ 600s 才记），"
    echo "    重启一次再回来看；本脚本不会为当前这次运行伪造记录。"
fi
echo "--- 日志文件 ---"
ls -l "$LOG_DIR"
echo "--- logd 进程 ---"
ps | grep '[l]ogd' || echo "logd 未运行"

echo ""
echo "完成。日常用法："
echo "  cat /etc/log/boot-history.log      # 看重启历史与上次关机性质"
echo "  tail -100 /etc/log/messages        # 看断电前最后日志"
