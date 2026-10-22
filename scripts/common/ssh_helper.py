"""通用 SSH 封装。

连接信息全部从环境变量读取，仓库中不保存任何凭据：

    export K2P_HOST=192.168.1.1
    export K2P_USER=root
    export K2P_PASSWORD='...'
    export K2P_LAN_SUBNET=192.168.1.0/24   # 可选
"""

import os
import sys
import time

import paramiko

import target

HOST = os.environ.get("K2P_HOST", "192.168.1.1")
USER = os.environ.get("K2P_USER", "root")
PASSWORD = os.environ.get("K2P_PASSWORD", "")
LAN_SUBNET = target.LAN_SUBNET   # 单一来源：common/target.py


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
    """把文本内容写到路由器上的 remote_path（非原子，覆盖会中途可见）。"""
    stdin, stdout, _ = client.exec_command("cat > %s" % remote_path, timeout=timeout)
    stdin.write(text)
    stdin.flush()
    stdin.channel.shutdown_write()
    return stdout.channel.recv_exit_status()


def upload_atomic(client, text, remote_path, mode="755", timeout=60):
    """原子写入：先写 <path>.new.<pid>，再 mv 覆盖。

    直接用 `cat >` 覆盖正在运行的 init 脚本时，若连接中断或磁盘写满，
    会留下一个被截断的脚本 —— 现象是「下次开机服务起不来」，且很难联想到是改脚本改坏的。
    同一文件系统内 mv 是 rename，原子替换，不会出现半截文件。

    注意：busybox 的 chmod 不支持 `--reference`（实测报 unrecognized option），
    所以权限由调用方显式给出，默认 755（init 脚本）。配置类文件请传 mode="644"。
    """
    tmp = "%s.new.%d" % (remote_path, os.getpid())
    if upload(client, text, tmp, timeout=timeout) != 0:
        run(client, "rm -f %s" % tmp)
        return 1
    rc, _, err = run(client, "chmod %s %s && mv -f %s %s"
                     % (mode, tmp, tmp, remote_path))
    if rc != 0:
        run(client, "rm -f %s" % tmp)
    return rc


def backup(client, path, timeout=30):
    """按 <path>.bak.<本地时间戳> 备份，返回备份路径；失败返回 None。

    统一命名，避免仓库里出现 `.orig` / `.ack.orig` / `.orig.<ts>` 三套写法。
    注意：时间戳取自**本机**时钟；路由器没有 RTC 时它的日期不可靠。
    """
    dest = "%s.bak.%s" % (path, time.strftime("%Y%m%d%H%M%S"))
    rc, _, _ = run(client, "cp -a %s %s 2>/dev/null || cp %s %s"
                   % (path, dest, path, dest), timeout=timeout)
    return dest if rc == 0 else None


def apply_steps(client, steps, timeout=120, stop_on_error=False):
    """依次执行 [(label, cmd), ...]，打印输出，返回失败步骤的 label 列表。

    stop_on_error=True 时遇到第一个失败就返回（适合有前后依赖的步骤）。
    """
    failed = []
    for label, cmd in steps:
        rc, out, err = run(client, cmd, timeout=timeout)
        print("=== %s (rc=%d) ===" % (label, rc))
        print(out.strip())
        if err.strip():
            print("  STDERR:", err.strip())
        if rc != 0:
            failed.append(label)
            if stop_on_error:
                break
    return failed


def step(client, label, cmd, timeout=120):
    """执行并打印一个带标题的步骤，返回 (rc, out, err)。"""
    rc, out, err = run(client, cmd, timeout=timeout)
    print("=== %s (rc=%d) ===" % (label, rc))
    print(out.strip())
    if err.strip():
        print("  STDERR:", err.strip())
    return rc, out, err