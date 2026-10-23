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
# 2026-10-24：默认值已清空。原值 napi.ithome.com -> 183.131.62.41 属于失效配置：
#   1) 该 API 域名在服务端下线——112.67.255.199 与 183.131.62.41 两台 IT之家 服务器
#      对 Host: napi.ithome.com 一律回 404，只有 api.ithome.com 才有 200；
#   2) 真实 CDN 节点 175.12.90.41 的 443 被校园网按 CDN 段屏蔽（80 秒连、443 超时），
#      所以「指回真实解析」也救不了，覆盖本身已无收益；
#   3) 实机上的该条覆盖已删除，重跑 43 不应再种回去。
FORCE_DOMAIN = os.environ.get("K2P_FORCE_DOMAIN", "")
FORCE_IP = os.environ.get("K2P_FORCE_IP", "")
