#!/usr/bin/env python3
"""90 · 批量验证 UA2F 改写后的 UA 长度（在本机 PC 上执行）。

原理：UA2F 是等长替换，改写结果的长度 = 原始 UA 的长度。
      用不同长度的 UA 访问 UA 回显站点，观察改写结果。

前提：
  1. 本机已接入该路由器（走 K2P 上网）；
  2. UA2F 已启用。

站点：http://ua-check.stagoh.com/
"""

import re
import urllib.request

URL = "http://ua-check.stagoh.com/"

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

PAT = re.compile(r'id="php-user-agent".*?<p>(.*?)</p>', re.S)


def main():
    print("custom_ua 越短覆盖越广。以下为各长度原始 UA 的改写结果：\n")
    for ua in UAS:
        req = urllib.request.Request(URL, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read().decode("utf-8", "replace")
            m = PAT.search(body)
            got = m.group(1) if m else "<parse-fail>"
        except Exception as exc:
            got = "ERR:%s" % exc
        flag = "OK " if got == "Mozilla" else "!! "
        print("%sorig_len=%3d  ->  result_len=%3d  result=%r"
              % (flag, len(ua), len(got), got))

    print("\n判定：常见 UA（>=7 字符）应全部变成 'Mozilla'；")
    print("      短于 custom_ua 长度的原始 UA 会残留，但现实中罕见。")


if __name__ == "__main__":
    main()