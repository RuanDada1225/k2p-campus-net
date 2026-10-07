"""通用 SSH 封装。

连接信息全部从环境变量读取，仓库中不保存任何凭据：

    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    export K2P_LAN_SUBNET=192.168.1.0/24   # 可选
"""

import os
import sys

import paramiko

HOST = os.environ.get("K2P_HOST", "192.168.1.1")
USER = os.environ.get("K2P_USER", "root")
PASSWORD = os.environ.get("K2P_PASSWORD", "")
LAN_SUBNET = os.environ.get("K2P_LAN_SUBNET", "192.168.1.0/24")


def connect():
    if not PASSWORD:
        sys.exit("请先设置环境变量 K2P_PASSWORD")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
    )
    return client


def run(client, cmd, timeout=120):
    """执行命令，返回 (rc, stdout, stderr)。"""
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def upload(client, text, remote_path, timeout=60):
    """把文本内容写到路由器上的 remote_path。"""
    stdin, stdout, _ = client.exec_command("cat > %s" % remote_path, timeout=timeout)
    stdin.write(text)
    stdin.flush()
    stdin.channel.shutdown_write()
    return stdout.channel.recv_exit_status()


def step(client, label, cmd, timeout=120):
    """执行并打印一个带标题的步骤，返回 (rc, out, err)。"""
    rc, out, err = run(client, cmd, timeout=timeout)
    print("=== %s (rc=%d) ===" % (label, rc))
    print(out.strip())
    if err.strip():
        print("  STDERR:", err.strip())
    return rc, out, err