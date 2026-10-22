"""目标设备的差异点，集中在这里。

为什么要有这个文件
------------------
原先 `wan` / `br-lan` / `phy1-ap0` / `radio1` / `192.168.1.x` 散落在 8 个脚本里，
换一台路由器就得满仓库搜改，漏一个就是「静默不生效」。把它们收在一处，
移植时只改这里（或用同名环境变量覆盖）。

约定
----
- Python 脚本：`from target import WAN_IF, LAN_IF, ...`
- **已接入**：`20`（TTL 表模板）、`21`（NTP 表模板 + 网段）、`91`（体检探针）、
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
BOOT_HISTORY = "/etc/log/boot-history.log"
