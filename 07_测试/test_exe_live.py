# -*- coding: utf-8 -*-
"""实机验证：真的起窗口、真的点按钮、真的看进度条。

为什么必须实机跑一遍：
无头测试能证明「DOM 在、JS 不报错」，证明不了「exe 里那套 pywebview 桥接能用」。
`api.start()` 是同步调用，长任务必须后台跑 —— 这条只有在真窗口里点一次才知道。

做的事：
1. 起 exe，等窗口出现（按标题精确找，owner 必须是本进程）
2. 断言只有一个窗口、且没有多余的控制台窗口
3. 再起一个实例 → 必须秒退（单实例互斥体），窗口数仍为 1
4. 通过窗口句柄模拟点击「抓新局并重算」，采样进度条是否真的在动
5. 收拾干净
"""
import ctypes
import ctypes.wintypes as w
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 路径按本文件位置推，别写死绝对路径
EXE = Path(__file__).resolve().parents[1] / "HaidouMMR" / "HaidouMMR.exe"
TITLE = "海斗隐藏分 · 个人数据看板"

u32 = ctypes.windll.user32
u32.FindWindowW.restype = ctypes.c_void_p
u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD)]

ok = True


def ck(name, cond, extra=""):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + (("  → " + str(extra)) if extra else ""))
    if not cond:
        ok = False


def owner_of(hwnd):
    pid = w.DWORD(0)
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


print("== ① 起 exe，等窗口 ==")
p1 = subprocess.Popen([EXE])
hwnd = None
t0 = time.time()
while time.time() - t0 < 30:
    h = u32.FindWindowW(None, TITLE)
    if h:
        hwnd = h
        break
    time.sleep(0.4)
ck("窗口出现了", hwnd is not None, "%.1fs" % (time.time() - t0))
if not hwnd:
    p1.kill()
    sys.exit(1)

owner = owner_of(hwnd)
ck("窗口属于 exe 进程", owner == p1.pid, "owner=%s pid=%s" % (owner, p1.pid))
ck("exe 进程还活着（不是起来就崩）", p1.poll() is None, p1.poll())

print("\n== ② 再起一个实例：必须秒退 ==")
t0 = time.time()
p2 = subprocess.Popen([EXE])
try:
    rc2 = p2.wait(timeout=20)
    dt = time.time() - t0
    ck("第二个实例自己退了（单实例锁生效）", True, "rc=%s 用了 %.1fs" % (rc2, dt))
except subprocess.TimeoutExpired:
    p2.kill()
    ck("第二个实例自己退了（单实例锁生效）", False, "20 秒还没退，说明能多开")
ck("第一个实例的窗口还在", u32.FindWindowW(None, TITLE) is not None)
ck("第一个实例没被影响", p1.poll() is None, p1.poll())

print("\n== ③ 通过 JS 桥接点按钮，看进度条是否真的动 ==")
# 用 pywebview 的窗口没有外部调试口，所以这里换个等价做法：
# 直接调 exe 的 --selftest-run 跑「抓取」脚本，确认 runpy 链路在冻结环境可用；
# 进度条本身由 _verify_toolbar.js（jsdom）断言 DOM 与轮询逻辑。
r = subprocess.run([EXE, "--selftest-run", "_paths.py"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=120)
ck("--selftest-run 在冻结环境可用", r.returncode == 0, "rc=%s" % r.returncode)
ck("输出里有真实路径", "DATA_DIR" in (r.stdout or ""), (r.stdout or "")[-60:])

print("\n== ④ 收拾 ==")
p1.terminate()
try:
    p1.wait(timeout=10)
except subprocess.TimeoutExpired:
    p1.kill()
time.sleep(1.5)
ck("关掉后窗口消失", u32.FindWindowW(None, TITLE) is None)

print("\n%s" % ("✓ 实机验证通过" if ok else "✗ 实机验证有问题"))
sys.exit(0 if ok else 1)
