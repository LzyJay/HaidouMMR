# -*- coding: utf-8 -*-
"""端到端：用**真实脚本**跑一遍 app.py 的任务机制，看进度条是不是真的在动。

不 mock `_run_script` —— 打桩只能证明「我调用了它」，证明不了
「真实脚本的输出能被 _mark_crawl / _mark_estimate 解析出来」。
而后者才是进度条唯一可能出错的地方（脚本改了措辞，标记就废了）。

同时把每步实际耗时打出来，用来校准 app.py 里的 SEC_* 估时常量。
"""
import importlib.util
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 路径按本文件位置推，别写死绝对路径
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "06_桌面版" / "app.py"
KIND = sys.argv[1] if len(sys.argv) > 1 else "update"

spec = importlib.util.spec_from_file_location("appmod", str(APP))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

api = m.Api()

print("== 起点：确认没有残留任务 ==")
p = api.progress()
assert not p["running"], p
print("   running=False ✓")

print("\n== 启动任务 kind=%s ==" % KIND)
# ⚠️ 锚点参数必须跟当前真值走（2026-09-28 第十轮：1708）。
#    原来写死 1928（第六轮旧锚点）—— 那会把 `_desktop_state.json` 的 anchor_score
#    改回 1928、`traj_offset` 跟着被重写，等于**静默毁掉重锚结果**。
#    换锚点后记得同步这里（见铁律 31 / 铁律 56）。
r = api.start(KIND, 1708, 0, 300)
print("   start() 返回: %s" % r)
assert r["ok"], r
print("   ✓ 立刻返回（没有阻塞）—— 这是进度条能做出来的前提")

# 先看看锚点参数是怎么被翻译成命令行的
argv = m._anchor_argv(1708, 0, m._load_state())
print("   锚点参数 -> %s" % argv)

print("\n== 轮询进度（每 0.5 秒）==")
t0 = time.time()
last_step = 0
last_frac = -1.0
fracs = []
step_names = []
step_times = {}
# 审查 M13：轮询必须有总超时 —— 同包其它轮询都有 30s 上限，唯独这条真跑
# 几十分钟的反而没有；任务 hang（LCU 未登录卡重试等）时测试会永久挂死。
POLL_MAX = 3600.0      # 完整 crawl 十几分钟到半小时量级，1 小时是安全上限
timed_out = False
while True:
    p = api.progress()
    frac = p["frac"]
    if p["step"] != last_step:
        last_step = p["step"]
        step_names.append(p["step_name"])
        step_times[p["step_name"]] = time.time() - t0
        print("   [%6.1fs] 第 %d/%d 步  %s" %
              (time.time() - t0, p["step"], p["step_total"], p["step_name"]))
    # 进度不许回退
    if frac < last_frac - 1e-9:
        print("   ✗ 进度回退了！ %s -> %s" % (last_frac, frac))
        sys.exit(1)
    last_frac = frac
    fracs.append(frac)
    eta = p["eta"]
    print("        frac=%.3f  eta=%s  已用=%.0fs" %
          (frac, ("%.0fs" % eta) if eta is not None else "None", p["elapsed"]))
    if not p["running"]:
        break
    if time.time() - t0 > POLL_MAX:
        print("   ✗ 轮询超过 %.0fs 仍未结束 —— 任务疑似 hang，请求停止后判失败" % POLL_MAX)
        try:
            api.stop()
        except Exception as e:
            print("   （stop 也失败了：%s）" % e)
        timed_out = True
        break
    time.sleep(0.5)

p = api.progress()
total = time.time() - t0
print("\n== 结果 ==")
print("   完成=%s  ok=%s  总用时=%.1fs" % (p["finished"], p["ok"], total))
for s in p["steps"]:
    print("      %-16s rc=%s  %ss" % (s["name"], s["rc"], s["secs"]))
if p.get("error"):
    print("   错误:\n%s" % p["error"])
print("   进度采样 %d 次，最小 %.3f 最大 %.3f" % (len(fracs), min(fracs), max(fracs)))
print("   走到过的步骤: %s" % step_names)

print("\n== 校准用：各步实际耗时 ==")
for s in p["steps"]:
    print("   SEC_%-14s = %s" % (s["name"][:12], s["secs"]))

# 断言
ok = True
if timed_out:
    print("   ✗ 轮询超时（%ds）—— 任务 hang，整条判失败" % POLL_MAX)
    ok = False
if not (p["finished"] and p["ok"]):
    print("   ✗ 任务没有全部成功")
    ok = False
if fracs != sorted(fracs):
    print("   ✗ 进度有回退")
    ok = False
if max(fracs) < 0.999:
    print("   ✗ 结束时没到 100%%（max=%.3f）" % max(fracs))
    ok = False
if len(step_names) < p["step_total"]:
    print("   ✗ 只走到了 %d 步，应该有 %d 步" % (len(step_names), p["step_total"]))
    ok = False
if all(f == 0.0 for f in fracs):
    # 审查 M13：原条件 any(...) and max<0.999 与「没有全部成功」高度重叠，
    # 且文案说「一直停在 0」语义应是 all —— 中途合法出现过 0 采样不该触发。
    print("   ✗ 进度全程停在 0（标记一个都没被解析出来？）")
    ok = False

print("\n%s" % ("✓ 端到端通过" if ok else "✗ 端到端有问题"))
sys.exit(0 if ok else 1)
