# -*- coding: utf-8 -*-
"""`test_anchor_v6.py` 与 `test_snapshot.py` 共用的加载 + 辅助函数（唯一真身）。

为什么要抽出来
--------------
2026-09-28 第十轮收尾：原来 43 条断言全塞在 `test_anchor_v6.py` 里，其中
**16 条会随数据浮动**（抓一局新对局就红），27 条是**恒定口径**（公式/算法/真值，永远该绿）。
2026-09-28 跑 `test_job_e2e.py update`（抓到 4 局新对局）时那 16 条一起红，
一眼看去像「13 条全炸了」，其实**没有一条是 bug**。

所以拆成两个文件：
  · `test_anchor_v6.py` —— **口径断言**，恒定。抓新局后仍然全绿。
  · `test_snapshot.py`  —— **快照断言**，随数据浮动。红了先看是不是数据刷新了。

两个文件共用本模块的数据加载与复算函数 —— 口径只写一份，
免得「改了公式只改一处」这种事再发生（见铁律：公式抽成唯一真身）。

用法：
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _v6_common import *
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DASH = os.path.join(ROOT, "02_数据", "海斗dashboard_data.json")

# 断言计数器已抽成唯一真身（2026-10-03）：本模块只做 re-export，
# 这样 `from _v6_common import *`（test_anchor_v6.py）与 test_snapshot.py 都无需改动。
# ⚠️ 本模块**不可**被其它测试文件当公共 helper import —— 下方 `:51-55` 有顶层副作用
#    （找不到 dashboard 就 sys.exit(2)、导入即加载 0.8 MB JSON）。
#    纯断言需求请用 `_check.py`。
sys.path.insert(0, HERE)
from _check import Counter, check, counts, exit_code, finish  # noqa: F401,E402


if not os.path.exists(DASH):
    print("[X] 找不到 " + DASH + "，先跑一次 _dash_rebuild.py")
    sys.exit(2)

D = json.load(io.open(DASH, encoding="utf-8"))
F = D["anchors"]["composite_formula"]
W = F["weights"]
CV = F["curves"]
OFF = float(F["offset_exact"])
ANC_C = float(D["anchors"]["composite_anchor"]["value"])
ANC_H = float(D["anchors"]["hidden_anchor"]["value"])
ME = D["me_puuid"]
A = D["allseries"]
MINE = D["mine"]
ROSTER = D["roster"]

L30 = A[-30:]


# ---------- 参照曲线（照抄后端 _curve_score / 前端 curveScore 的分段线性插值） ----------
def curve(pts, v):
    if v <= pts[0][0]:
        return pts[0][1]
    if v >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        if x1 <= v <= x2:
            return y2 if x2 <= x1 else y1 + (y2 - y1) * (v - x1) / (x2 - x1)
    return pts[-1][1]


def comp_raw(m):
    return sum(curve(CV[k], float(m[k] or 0)) * W[k] for k in W if k in CV) / 100.0


# 第九轮（2026-09-28）：最终分 = 平移 + **局内统治力加成** → 再按夹紧区间截断。
# 加成的依据是「本局在 dominance.keys 里拿到全场 10 人第 1 的项数」，所以下面
# 必须**独立复算全场 10 人**的排名（不能只看本人那一队）—— 这正是本文件的意义。
CLAMP = F.get("clamp") or [0.0, 100.0]
DOM_KEYS = (F.get("dominance") or {}).get("keys") or []
DOM_PER = float((F.get("dominance") or {}).get("per") or 0.0)


def metrics_from_roster(team_players, p, team_kills):
    """从 roster 原始数值重算某人在某局的 8 项指标（口径照抄 _stats_crawl.py）"""
    td = sum(x["dmg"] for x in team_players) or 1
    tt = sum(x["taken"] for x in team_players) or 1
    tg = sum(x["gold"] for x in team_players) or 1
    th = sum(x["heal"] for x in team_players) or 1
    return {
        "dmg_share": round(p["dmg"] / td * 100.0, 1),
        "taken_share": round(p["taken"] / tt * 100.0, 1),
        "gold_share": round(p["gold"] / tg * 100.0, 1),
        "heal_share": round(p["heal"] / th * 100.0, 1),
        "dmg_conv": round((p["dmg"] / td) / ((p["gold"] / tg) or 1), 2),
        "kill_part": round(min((p["k"] + p["a"]) / max(team_kills, 1) * 100.0, 100.0), 1),
        "kda": round((p["k"] + p["a"]) / max(p["d"], 1), 2),
        "win_rate": 100.0 if p["win"] else 0.0,
    }


def dom_n1(rg, pu):
    """独立复算：本局我在 DOM_KEYS 里有几项是全场 10 人第 1（并列也算）。"""
    rows = []
    for t in rg["teams"]:
        for p in t["players"]:
            if p.get("pu"):
                rows.append((p["pu"], metrics_from_roster(t["players"], p, t.get("kills") or 0)))
    mine = next(m for u, m in rows if u == pu)
    return sum(1 for k in DOM_KEYS if mine[k] >= max(m[k] for _, m in rows))


def comp_of(m, n1=0):
    v = comp_raw(m) + OFF + DOM_PER * n1
    return round(min(max(v, float(CLAMP[0])), float(CLAMP[1])), 1)


def my_rows_from_roster():
    """本人每一局的 (8 项指标, 统治力第 1 项数)（按 mine 的时间序 = allseries 的顺序）"""
    out = []
    for g in MINE:
        rg = ROSTER.get(str(g["gameId"]))
        if not rg:
            out.append(None)
            continue
        hit = None
        for t in rg["teams"]:
            for p in t["players"]:
                if p.get("pu") == ME:
                    hit = (metrics_from_roster(t["players"], p, t.get("kills") or 0),
                           dom_n1(rg, ME))
        out.append(hit)
    return out


def my_rows_cached():
    """my_rows_from_roster() 很慢（每局都要复算全场 10 人），两个文件都要用，缓存一份。"""
    global _ROWS
    try:
        return _ROWS
    except NameError:
        _ROWS = my_rows_from_roster()
        return _ROWS
