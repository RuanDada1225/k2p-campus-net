"""目标设备的差异点，集中在这里。

为什么要有这个文件
------------------
原先 `wan` / `br-lan` / `phy1-ap0` / `radio1` / `192.168.1.x` 散落在 8 个脚本里，
换一台路由器就得满仓库搜改，漏一个就是「静默不生效」。把它们收在一处，
移植时只改这里（或用同名环境变量覆盖）。

约定
----
- Python 脚本：`from target import WAN_IF, LAN_IF, ...`
- **已接入**：`20`（TTL 表模板）、`21`（NTP 表模板 + 网段）、`43`（DNS 默认上游）、
  `71`（LAN 网段校验）、`91`（体检探针 + 全部期望值）、
  `ssh_helper.LAN_SUBNET`（也从这里取，避免两处默认值打架）。
- **故意不接入**：`11` / `12` —— 它们是对上游 `ua2f` init 文本做**字面替换**，
  正则里的 `iifname "br-lan"` 是锚点，改了反而匹配不上。
- **尚未接入**：`50`（`radio1` / `phy1-ap0` 写在 shell 里，路由器启动时没有这些环境变量，
  移植时需要手改）；`45` 的 RPS 脚本里的 `eth0/lan1-4` 属端口名，同理。

注意
----
本文件只放「会随设备变化」的量。像 `/etc/log/messages` 这种项目自身的约定，
放在下面但一般不用改。
"""

import os

# ---- 网络接口 ----
WAN_IF = os.environ.get("K2P_WAN_IF", "wan")
LAN_IF = os.environ.get("K2P_LAN_IF", "br-lan")
WIFI_5G_IF = os.environ.get("K2P_WIFI_5G_IF", "phy1-ap0")
WIFI_2G_IF = os.environ.get("K2P_WIFI_2G_IF", "phy0-ap0")

# ---- 网段 ----
LAN_SUBNET = os.environ.get("K2P_LAN_SUBNET", "192.168.1.0/24")
LAN_PREFIX = os.environ.get("K2P_LAN_PREFIX", "192.168.1")

# ---- 无线 ----
RADIO_5G = os.environ.get("K2P_RADIO_5G", "radio1")
RADIO_2G = os.environ.get("K2P_RADIO_2G", "radio0")

# ---- 项目自身路径（一般不用改）----
UA2F_INIT = "/etc/init.d/ua2f"
STEERING_INIT = "/etc/init.d/packet_steering"
SYSCTL_CONF = "/etc/sysctl.conf"
LOG_FILE = "/etc/log/messages"
LOG_SIZE = os.environ.get("K2P_LOG_SIZE", "512")          # KB
BOOT_HISTORY = "/etc/log/boot-history.log"

# ---- 体检（91）的期望值 ----
# 以前这些魔数直接写在体检脚本里：改一个 custom_ua、换一条线路 DNS，体检就会红，
# 而红的原因跟「设备坏了」无关。放在这里（都可用同名环境变量覆盖），
# 让「设备差异」与「检查逻辑」分开。
UA2F_CUSTOM_UA = os.environ.get("K2P_UA2F_UA", "Safari/537.")
UA2F_WORKERS = os.environ.get("K2P_UA2F_WORKERS", "4")
DNS_CACHESIZE = os.environ.get("K2P_DNS_CACHESIZE", "4000")
WIFI_5G_CHANNEL = os.environ.get("K2P_5G_CHANNEL", "149")

# 本线路的就近运营商 DNS。43 用它做默认上游，91 用它判断「上游没被公共 DNS 顶掉」。
# 留空 = 体检跳过该项、43 沿用路由器上现有的上游。
DNS_UPSTREAM = os.environ.get("K2P_DNS_UPSTREAM", "202.96.128.86")

# 按域名强制的 CDN 覆盖（03 篇坑 21/22 的兜底手段）。
# FORCE_DOMAIN 留空 = 91 跳过该检查、43 不动现有覆盖。
#
# 2026-10-24 实测结论（当天早些时候曾误判为「域名已下线」，这里留档避免重犯）：
#   1) napi.ithome.com 一直存在，公共 DNS 解析到 175.12.90.41（移动 CDN 节点）；
#   2) 该 IP 只有 443 被校园网出口掐断（80 秒连、ICMP 通），于是 App 连不上、
#      而网页/小程序走 m/www/api 三个域名，全都正常；
#   3) 换「用谁的 DNS」会调度到不同节点：学校下发 DNS(172.16.2.1) 给出
#      119.188.212.41，这台从校园网完全可达，配置后 App 立即恢复；
#   4) 判断节点是否正常不能只看 404——正常节点访问 / 也回 404（App 用具体 API 路径），
#      只有「手机流量下实测能刷」才算通过。
FORCE_DOMAIN = os.environ.get("K2P_FORCE_DOMAIN", "napi.ithome.com")
FORCE_IP = os.environ.get("K2P_FORCE_IP", "119.188.212.41")
