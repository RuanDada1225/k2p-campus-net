#!/usr/bin/env python3
"""90 · 批量验证 UA2F 改写后的 UA 长度（在本机 PC 上执行）。

原理：UA2F 是等长替换，改写只在原始 UA 的长度内进行，
      结果长度 = min(原始 UA 长度, custom_ua 长度)。
      用不同长度的 UA 访问 UA 回显站点，观察改写结果。

期望值从哪来（2026-10 修订，修掉「期望值写死」的坑）
--------------------------------------------------
1. 环境变量 `UA2F_EXPECT`（最高优先级）
2. 能连上路由器时，读 `uci get ua2f.main.custom_ua`（推荐，永不与配置脱节）
3. 都拿不到时退回 'Safari/537.'

回显站点也做了冗余：ua-check.stagoh.com 优先，失败自动切 httpbin.org/headers。
以前只依赖单一站点，站点挂掉/改版会让整张表变红，看起来像「UA2F 坏了」。

退出码：全部符合预期 0；有不符合 1；一条都没成功回显 2。

前提：本机已接入该路由器（走 K2P 上网），UA2F 已启用。
"""

import json
import os
import re
import sys
import urllib.request

DEFAULT_EXPECT = "Safari/537."
STAGOH_URL = "http://ua-check.stagoh.com/"
HTTPBIN_URL = "http://httpbin.org/headers"
PAT = re.compile(r'id="php-user-agent".*?<p>(.*?)</p>', re.S)

UAS = [
    "a",
    "okhttp",
    "curl/8.5.0",
    "Mozilla/5.0",
    "okhttp/3.12.0",
    "Dalvik/2.1.0 (Linux; U; Android 13)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 " + "X" * 190,
]


def detect_expect():
    """优先级：环境变量 > 路由器 uci > 默认值。"""
    env = os.environ.get("UA2F_EXPECT", "").strip()
    if env:
        return env, "环境变量 UA2F_EXPECT"

    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
        from ssh_helper import connect, run  # 延迟导入，没装 paramiko 也能跑
        client = connect()
        try:
            rc, out, _ = run(client, "uci -q get ua2f.main.custom_ua")
        finally:
            client.close()
        if rc == 0 and out.strip():
            return out.strip(), "路由器 uci"
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print("（读取 uci 失败：%s；改用默认值）" % type(exc).__name__)

    return DEFAULT_EXPECT, "默认值"


def probe_stagoh(ua):
    req = urllib.request.Request(STAGOH_URL, headers={"User-Agent": ua})
    body = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
    m = PAT.search(body)
    return m.group(1) if m else None


def probe_httpbin(ua):
    req = urllib.request.Request(HTTPBIN_URL, headers={"User-Agent": ua})
    body = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
    data = json.loads(body)
    return data.get("headers", {}).get("User-Agent")


ENDPOINTS = [("ua-check.stagoh.com", probe_stagoh), ("httpbin.org/headers", probe_httpbin)]


def echo_ua(ua, preferred):
    """按 preferred 顺序尝试回显站点，返回 (结果, 站点名)。全失败则 (None, None)。"""
    order = [e for e in ENDPOINTS if e[0] == preferred] + [e for e in ENDPOINTS if e[0] != preferred]
    for name, fn in order:
        try:
            got = fn(ua)
            if got:
                return got, name
        except Exception:  # noqa: BLE001
            continue
    return None, None


def main():
    expect, source = detect_expect()
    print("期望值：%r（来源：%s，长度 %d）\n" % (expect, source, len(expect)))

    preferred, ok, warn, bad, total = ENDPOINTS[0][0], 0, 0, 0, 0
    for ua in UAS:
        got, via = echo_ua(ua, preferred)
        total += 1
        if got is None:
            print("!! orig_len=%3d  ->  回显失败（两个站点都不可用）" % len(ua))
            bad += 1
            continue
        preferred = via  # 记住能用的那个，后面少走弯路
        if got == expect or (len(ua) >= len(expect) and got.startswith(expect)):
            state = "OK "
            ok += 1
        elif got == expect[:len(ua)]:
            state = "WARN"      # 原始 UA 比期望值短 → 只写进了期望值的前 N 字节，属预期
            warn += 1
        else:
            state = "!!  "
            bad += 1
        print("%s orig_len=%3d  ->  result_len=%3d  result=%r  [%s]"
              % (state, len(ua), len(got), got, via))

    print("\n判定：常见 UA（>=%d 字符）应全部变成 %r；" % (len(expect), expect))
    print("      短于 custom_ua 长度的原始 UA 会残留（等长替换的必然结果），标 WARN 不算失败。")
    print("小计：%d 通过 / %d 残串（预期） / %d 异常，共 %d 条" % (ok, warn, bad, total))

    if ok == 0:
        print("\n一条都没成功回显：先确认本机走的是 K2P（curl http://httpbin.org/headers 应回 Safari/537.）")
        return 2
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
