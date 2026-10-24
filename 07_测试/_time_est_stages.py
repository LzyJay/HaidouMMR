# -*- coding: utf-8 -*-
"""一次性测量工具：给估算脚本的每一行输出打上「距启动多少秒」的时间戳。

用途：量出 `海斗隐藏分估算_v2.py` 四个阶段 [1/4]~[4/4] 的真实耗时占比，
用来给 `06_桌面版/app.py` 的 `_mark_estimate` 定标记权重（原来是等分的
(n-1)/4，和真实耗时差很远，进度条与剩余时间因此失真）。

不参与常规断言，量完即可得到权重；结果写在同目录 `_est_stage_times.txt`。
用法：
    python 07_测试\\_time_est_stages.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "01_脚本" / "海斗隐藏分估算_v2.py"
TXT = Path(__file__).resolve().parent / "_est_stage_times.txt"

# 与桌面版「抓新局并重算」里第 5 步完全相同的参数（见 _desktop_state.json）
ARGV = ["--anchor", "1928.0", "--anchor-game-id", "10000000003"]


def main() -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    t0 = time.time()
    cmd = [sys.executable, str(SCRIPT)] + ARGV
    print("cmd:", " ".join(cmd))
    p = subprocess.Popen(
        cmd, cwd=str(SCRIPT.parent), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    assert p.stdout is not None
    with TXT.open("w", encoding="utf-8") as fh:
        for line in p.stdout:
            dt = time.time() - t0
            rec = "[%7.2f] %s" % (dt, line.rstrip())
            fh.write(rec + "\n")
            fh.flush()
            # 只把阶段行回显到控制台，避免刷屏
            s = line.strip()
            if s.startswith("[") and "/4]" in s[:8] or "详细数据 ->" in s:
                print(rec)
        p.wait()
    print("总耗时 %.1fs，完整日志 -> %s" % (time.time() - t0, TXT))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
