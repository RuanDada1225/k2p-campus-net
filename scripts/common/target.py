"""目标设备的差异点，集中在这里。

为什么要有这个文件
------------------
原先 `wan` / `br-lan` / `phy1-ap0` / `radio1` / `192.168.1.x` 散落在 8 个脚本里，
换一台路由器就得满仓库搜改，漏一个就是「静默不生效」。把它们收在一处，
移植时只改这里（或用同名环境变量覆盖）。

约定
----
- Python 脚本：`from target import WAN_IF, LAN_IF, ...`
- Shell 脚本：读同名环境变量，例如 `${K2P_WAN_IF:-wan}`。
  两边的默认值必须保持一致 —— 改这里时记得同步 shell 里的默认值。

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
