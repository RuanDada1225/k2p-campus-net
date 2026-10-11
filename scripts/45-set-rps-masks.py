#!/usr/bin/env python3
"""45 · RPS 多核分散（反制 packet steering 的单核钉死）。

背景：OpenWrt 默认 network.globals.packet_steering=1，开机 S25 由
      /etc/init.d/packet_steering 运行 packet-steering.uc：给每个设备挑一个
      「最空闲核」写单核 rps_cpus（如 wan=4 → 只有 cpu2 收包）。
      后果：满速下载时单核打满、softnet 丢包 7%~11%。
      更糟的是它在 network / firewall reload 与 interface.* 事件后会自动重跑，
      手工写的 rps_cpus 会被覆盖。

解决（三层防线）：
  1. 部署 /usr/bin/rps-tuning.sh：
       eth0/wan/lan1-4 → 7（cpu0+1+2）
       phy*-ap*        → 6（cpu1+2；cpu0 扛硬件中断、cpu3 跑无线 napi/tx，避开）
  2. /etc/rc.local 开机调用一次；
  3. 在 /etc/init.d/packet_steering 的 reload_service 末尾挂钩子（备份 .orig）——
     它每次重跑，我们紧跟着覆盖回去。

注意：不要启用 flow offload —— 被 offload 的流量绕过 mangle/postrouting，
      TTL 归一化与 UA2F 改写都会失效（详见 docs/06-CPU与收包性能调优.md）。

实测：掩码生效后满速下载（14.33MB/s ≈ 115Mbps）全核 softnet 丢包 0，cpu2 不再饱和。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from ssh_helper import connect, run, upload  # noqa: E402

RPS_SH = "/usr/bin/rps-tuning.sh"
RC_LOCAL = "/etc/rc.local"
STEERING = "/etc/init.d/packet_steering"

RPS_SCRIPT = """#!/bin/sh
# rps-tuning.sh: spread RX packet processing across CPU cores.
# OpenWrt packet-steering assigns one CPU per device; this overrides it.
#   eth0/wan/lan1-4 : mask 7 = cpu0+cpu1+cpu2
#   wifi vifs       : mask 6 = cpu1+cpu2 (cpu0 carries all HW IRQs, cpu3 runs wifi tx/napi)
for i in eth0 wan lan1 lan2 lan3 lan4; do
\tf=/sys/class/net/$i/queues/rx-0/rps_cpus
\t[ -e "$f" ] && echo 7 > "$f"
done
for d in /sys/class/net/phy*-ap*; do
\tf=$d/queues/rx-0/rps_cpus
\t[ -e "$f" ] && echo 6 > "$f"
done
"""

RC_HOOK = ("# spread RX softirq across cores, overriding packet-steering's single-CPU choice\n"
           "/usr/bin/rps-tuning.sh\n")

STEERING_HOOK = ("\n\t# re-apply custom RPS masks after the stock balancer (eth=7 / wifi=6)"
                 "\n\tif [ -x /usr/bin/rps-tuning.sh ]; then"
                 "\n\t\t/usr/bin/rps-tuning.sh"
                 "\n\tfi")


def show_masks(client):
    cmd = ("for i in eth0 wan lan1 lan2 lan3 lan4; do "
           "f=/sys/class/net/$i/queues/rx-0/rps_cpus; [ -e $f ] && echo \"$i=$(cat $f)\"; done; "
           "for d in /sys/class/net/phy*-ap*; do "
           "[ -e $d ] && echo \"$(basename $d)=$(cat $d/queues/rx-0/rps_cpus)\"; done")
    return run(client, cmd)[1]


def main():
    client = connect()
    try:
        # 1) 部署 rps-tuning.sh
        rc = upload(client, RPS_SCRIPT, RPS_SH)
        run(client, "chmod 755 %s" % RPS_SH)
        print("RESULT=UPLOAD_RPS write_rc=%d" % rc)

        # 2) rc.local 开机调用
        rc, content, err = run(client, "cat %s" % RC_LOCAL)
        if rc != 0:
            print("读取 %s 失败 rc=%d err=%s" % (RC_LOCAL, rc, err))
            sys.exit(1)
        if "rps-tuning.sh" in content:
            print("RESULT=RC_LOCAL_ALREADY")
        else:
            run(client, "cp %s %s.orig" % (RC_LOCAL, RC_LOCAL))
            if "exit 0" in content:
                content = content.replace("exit 0", RC_HOOK + "\nexit 0", 1)
            else:
                content = content.rstrip("\n") + "\n\n" + RC_HOOK
            rc = upload(client, content, RC_LOCAL)
            print("RESULT=RC_LOCAL_PATCHED write_rc=%d" % rc)

        # 3) packet_steering 钩子
        steering_ok = True
        rc, content, err = run(client, "cat %s" % STEERING)
        if rc != 0:
            steering_ok = False
            print("未找到 %s，跳过挂钩（/etc/rc.local 的调用仍会生效）" % STEERING)
        elif "rps-tuning.sh" in content:
            print("RESULT=STEERING_ALREADY_HOOKED")
        else:
            idx = content.find("reload_service() {")
            close = content.find("\n}", idx) if idx != -1 else -1
            if close == -1:
                steering_ok = False
                print("PATTERN_NOT_FOUND：packet_steering 结构不同，")
                print("请手工在 reload_service 末尾追加 /usr/bin/rps-tuning.sh 调用")
            else:
                run(client, "cp %s %s.orig" % (STEERING, STEERING))
                content = content[:close] + STEERING_HOOK + content[close:]
                rc = upload(client, content, STEERING)
                run(client, "chmod 755 %s" % STEERING)
                print("RESULT=STEERING_HOOKED write_rc=%d" % rc)
                print("=== diff vs backup ===")
                print(run(client, "diff %s.orig %s" % (STEERING, STEERING))[1])

        # 4) 立即生效 + 持久性检验
        run(client, RPS_SH)
        print("=== masks now ===")
        print(show_masks(client))

        if steering_ok:
            print("=== force packet_steering rerun (persistence check) ===")
            run(client, "/etc/init.d/packet_steering reload")
            run(client, "sleep 1")
            print(show_masks(client))

        print("=== rc.local ===")
        print(run(client, "cat %s" % RC_LOCAL)[1])

        print("\n验证：两次打印的掩码应一致（eth0/wan/lan* = 7，phy*-ap* = 6）。")
        print("满速下载时执行 cat /proc/net/softnet_stat，第 2 列 dropped 不再增长即为通过。")
    finally:
        client.close()


if __name__ == "__main__":
    main()