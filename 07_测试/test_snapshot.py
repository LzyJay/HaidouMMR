# -*- coding: utf-8 -*-
"""**快照断言** —— 随数据浮动的那一半（2026-09-28 第十轮收尾从 test_anchor_v6.py 拆出）。

⚠️ 这里每一条都会随数据刷新而变，红了**先别急着当成 bug**：
   跑了 `test_job_e2e.py update`、或桌面版点了「抓新局并重算」之后，
   最近 30 局的窗口会滑动、同场过的人会变多、名次会重排、锚点局也不再是末局 ——
   这些数字全都会变，属于**预期变化**。

2026-09-28 实测（跑 e2e 抓到 4 局新对局后）：16 条里 **13 条一起红**，
逐条核过**没有一条是 bug**：
   estimate 1708 → 1678.8（锚点局不再是末局，这是**正确**的）
   同场人数 1533 → 1569｜本区 54/85 → 35/52｜mmr 模式 50/100 → 74/100
   comp 模式 39/100 → 44/100｜逐局名次序列全变｜本地复算范围 878~2682 → 1129~2682

想让这里保持全绿，就要**别刷新数据**；真刷新了就按新数据更新这些期望值。
恒定不变的那部分在 `test_anchor_v6.py`（口径断言，抓新局后仍然全绿）。

2026-09-28 第十轮崩溃修复后重新刷新数据（roster 202→208、网络 277881→278549 人、
estimate 1708→—），下面期望值已按这次正确数据 re-baseline。
其中 ③ 隐藏分落锚改为「±100 容忍带 + delta 口径自洽」—— 因为 estimate 只在
锚点局=末局那一刻等于锚点，抓新局后必然漂移，硬等 ==锚点 会每次刷新都红（误报）。

跑法：python 07_测试\\test_snapshot.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _v6_common import *  # noqa: E402,F403  数据加载 + 复算函数（唯一真身）

print("快照断言（随数据浮动）—— 当前数据：allseries %d 局 / roster %d 局"
      % (len(A), len(ROSTER)))
print()

# ---------------------------------------------------------------- ① 近 30 局窗口 ----
# 这两个数是对齐已有的**显示值**，窗口滑动就会变。
print("== 近 30 局窗口：KDA / 伤害转化（对齐聚合口径）==")
kS = sum(r["k"] + r["a"] for r in L30)
dS = sum(r["d"] for r in L30)
kda_agg = round(kS / max(dS, 1), 2)
conv_agg = round(sum(r["dmg_share"] for r in L30) / max(sum(r["gold_share"] for r in L30), 1e-9), 2)
kda_old = round(sum((r["k"] + r["a"]) / max(r["d"], 1) for r in L30) / 30, 2)
conv_old = round(sum(r["dmg_conv"] for r in L30) / 30, 2)
check("近 30 局 KDA（聚合口径 Σ(K+A)/ΣD）== 3.33（对齐值，2026-09-28 刷新后）",
      kda_agg == 3.33, "%.2f（旧逐局口径 %.2f）" % (kda_agg, kda_old))
check("近 30 局伤害转化（均值伤占÷均值经占）== 0.98（对齐值，2026-09-28 刷新后）",
      conv_agg == 0.98, "%.2f（旧逐局口径 %.2f）" % (conv_agg, conv_old))

# ------------------------------------------------------------------ ② 局数 ----
print("\n== 本人局数 ==")
rows = my_rows_cached()
miss = [i for i, r in enumerate(rows) if r is None]
check("本人 %d 局在 roster 里都找得到" % len(rows), not miss, "缺 %d 局" % len(miss))

# -------------------------------------------------------------- ③ 隐藏分落锚 ----
# ⚠️ 只有「锚点局 == 最新一局」时 estimate 才等于锚点。抓了新局之后锚点局变成
#    「N 局前」，estimate 就会自然偏离 —— 那是对的，不是 bug。
#    所以这里**不再硬等 == 锚点**（那会每次刷新都红），改成「±100 容忍带 +
#    delta 口径自洽 + 轨迹末点对齐 estimate」，照样能抓出量级错误（如 1700→200 这种）。
print("\n== 隐藏分：当前估算是否落在锚点附近（抓新局后锚点局不再是末局，estimate 会自然偏离）==")
E = D["estimate"]
check("estimate.current 落在锚点 ±100 内（抓新局后自然漂移，非 bug）",
      abs(E["current"] - ANC_H) <= 100, E["current"])
check("estimate.delta == current − 锚点（口径自洽）",
      abs(E["delta"] - (E["current"] - ANC_H)) < 0.2,
      "delta=%s current-anchor=%.1f" % (E["delta"], E["current"] - ANC_H))
check("轨迹末点 score_after 与 estimate 一致（±1）",
      abs(D["traj"][-1]["score_after"] - E["current"]) < 1.0,
      D["traj"][-1]["score_after"])

# ------------------------------------------------------ ④ 模块 A：本区排名百分比 ----
print("\n== 模块 A：本区排名百分比（本人 vs 同场过的所有人）==")
by = {}
for gid, rg in ROSTER.items():
    for t in rg["teams"]:
        for p in t["players"]:
            if p.get("pu") and p.get("comp") is not None:
                by.setdefault(p["pu"], []).append(p["comp"])
avg = {pu: sum(v) / len(v) for pu, v in by.items()}
vals = sorted(avg.values())
mineA = avg[ME]
below = sum(1 for v in vals if v < mineA - 1e-9)
pctA = round(100 * (len(vals) - below) / len(vals))
check("同场过的人数 == 1587（2026-09-28 刷新后）", len(vals) == 1587, len(vals))
check("本人 %d 局平均 comp == 64.3990 附近" % len(rows), abs(mineA - 64.3990) < 0.1,
      "%.4f" % mineA)
check("自算分位 == 前 55%（2026-09-28 刷新后）", pctA == 55, "前 %d%%" % pctA)

finish()
