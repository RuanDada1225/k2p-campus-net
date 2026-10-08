#!/bin/sh
# 80 · 安装并启用 zram 压缩交换（在路由器上执行）
#
# 背景
# ----
# K2P 只有 128MB 内存，而 /var/log、/tmp、nlbwmon 数据库都放在 tmpfs（内存盘）里。
# tmpfs 页（shmem）**不属于任何可被 OOM killer 杀掉的进程**，在没有 swap 时也无法换出：
# 一旦内存盘把内存吃满，内核会陷入回收活锁，最终被**硬件看门狗复位** —— 表现为「莫名其妙重启」。
#
# 加一个 zram 压缩交换后，这些页可以被压缩后换出，内存压力有地方泄洪，卡死/复位随之消失。
#
# 前提
# ----
# 软件源可用（OpenWrt 25.12 的 apk feed 已配在 /etc/apk/repositories.d/distfeeds.list）。
# overlay 需约 100KB 空间。
#
# 用法
# ----
#   scp 80-install-zram.sh root@192.168.1.1:/tmp/
#   ssh root@192.168.1.1 'sh /tmp/80-install-zram.sh'
#
#   # 或
#   ssh root@192.168.1.1 'sh -s' < 80-install-zram.sh

set -e

echo "=== 安装前空间 ==="
df -h /overlay | tail -1

echo
echo "=== 搜索 zram 相关包 ==="
apk search zram 2>/dev/null | sed 's/^/  /'

echo
echo "=== 安装 kmod-zram + zram-swap ==="
apk add kmod-zram zram-swap 2>&1 | tail -10

echo
echo "=== 启用并启动 zram 服务 ==="
/etc/init.d/zram enable 2>&1
/etc/init.d/zram start 2>&1
sleep 2

echo
echo "=== 验证 ==="
echo -n "  内核模块: "; lsmod 2>/dev/null | grep -q zram && echo "已加载 ✅" || echo "未加载 ❌"
echo -n "  压缩算法: "; cat /sys/block/zram0/comp_algorithm 2>/dev/null | grep -o '\[.*\]' || echo "(无)"
echo "  --- 交换分区 ---"
cat /proc/swaps
echo "  --- 内存 ---"
free -m
echo "  --- 开机自启 ---"
ls -l /etc/rc.d/ 2>/dev/null | grep zram || echo "  (未设置开机自启!)"

echo
echo "=== 安装后空间 ==="
df -h /overlay | tail -1

echo
echo "提示：zram 默认按内存一半创建交换分区。若要调整大小，改的是 uci 里的"
echo "      system.@system[0].zram_size_mb（单位 MiB），改完 /etc/init.d/zram restart。"
echo "      注意：本包没有 /etc/config/zram，别去找那个文件。"