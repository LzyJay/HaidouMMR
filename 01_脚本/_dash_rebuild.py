# -*- coding: utf-8 -*-
"""海斗看板数据更新：重新聚合 + 替换 HTML 内嵌数据块（布局/JS 不动）。

数据流：
  hd_data.json (3990 局) + 海斗隐藏分_v2_本次估算.json + my_stats.json + 海斗roster.json + player_elo.json
  → 海斗dashboard_data.json
  → 注入 海斗隐藏分_数据看板.html

要点：
  ① 两个锚点：隐藏分锚点定 BT 全网 θ 的整体平移，表现分锚点定综合评分的位置；
     两个都是「人工给一个数」，任何来源的分数都可以，不依赖特定服务
  ② 综合分公式：滑窗基准（center 自适应最近 30 局均值）+ 等级 z-score 加权
  ③ 10 人明细加 mmr 字段；前端只显示 4 项：KDA / 伤害 / 承伤 / 隐藏分
"""
import json, re, sys, time as _t, statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import (DATA_DIR, BOARD, MIN_GAME_SECONDS, valid_game,  # noqa: E402
                    atomic_write_text)
# 表现分公式的**唯一真身**在 `_composite.py`（第九轮，2026-09-27 抽出来）：
# 以前权重/曲线写死在本文件里，别的脚本想复用只能再抄一份 ——
# 那正是铁律 45 说的「同一个值两份落点，迟早过期」。现在两边 import 同一个对象。
from _composite import (  # noqa: E402
    METRIC_KEYS, COMPOSITE_ANCHOR, COMPOSITE_WEIGHTS, COMPOSITE_CURVES,
    COMPOSITE_CLAMP, DOMINANCE_KEYS, DOMINANCE_PER,
    curve_score, metrics, composite_raw, window_mean_raw, window_mean_final,
    offset_for, clamp_score, finalize, max_raw, min_raw, roster_metrics,
    dominance_n1, dominance_bonus,
    pearson,          # 2026-10-03：原是本文件内一份 _pearson 副本，现统一到 _composite
)

# ---- 1) 聚合 ----
hd = json.load(open(DATA_DIR / "hd_data.json", encoding="utf-8"))
v2 = json.load(open(DATA_DIR / "海斗隐藏分_v2_本次估算.json", encoding="utf-8"))
cm = json.load(open(DATA_DIR / "champ_map.json", encoding="utf-8"))
ms = json.load(open(DATA_DIR / "my_stats.json", encoding="utf-8"))    # 76 局 stats 9 项指标
roster = json.load(open(DATA_DIR / "海斗roster.json", encoding="utf-8"))  # 76 局 ×10 人明细
pe = json.load(open(DATA_DIR / "player_elo.json", encoding="utf-8"))      # 560 个玩家 BT-fit mmr
pe_meta = pe.get("_meta", {}); pe.pop("_meta", None)
PE = pe

# 上次估算时间（给看板 KPI 打标记用）。
# 估算脚本不在 JSON 里写时间戳，而估算结果文件的 mtime 就是「这份分什么时候算出来的」，
# 语义对得上、也不用去动估算脚本。取不到就留空，看板那边会退回抓取时间。
try:
    _est_at = _t.strftime("%Y-%m-%d %H:%M",
                          _t.localtime((DATA_DIR / "海斗隐藏分_v2_本次估算.json").stat().st_mtime))
except OSError:
    _est_at = ""
# 日志里报的是 player_elo_meta.json 的 `method`（原来取的是不存在的 `formula`，
# 所以永远打印「公式 ?」——看着像 bug，其实只是字段名写错了）。
print(f"[load] hd {len(hd['games'])} 局 / roster {len(roster)} 局 ×10 人 / "
      f"mmr 表 {len(PE)} 人 / 评分方法 {pe_meta.get('method', '?')}")

me = hd["meta"]["puuid"]
my_ids = set(hd["my_game_ids"])
cm_name = lambda c: cm.get(str(c), str(c)) if not isinstance(c, int) else cm.get(str(c), str(c))

# ---- 玩家「名字#后缀」映射（2026-09-22 加） ----
# 为什么要它：`海斗roster.json` 里的 `n` 只有「名字」、没有 `#后缀`（stats 抓取时丢的），
# 而英雄联盟**允许重名** —— 看板上两个「玩家B」根本分不清谁是谁。
# `hd_data.json` 里每个玩家的 `name` 都是完整 Riot ID（实测 283220/283220 都带 '#'），
# 且主人这 159 局的 10 人必然都在网络里，所以**直接从它建映射回填**，
# 不必重抓 stats、也不必改 `_stats_crawl.py`（历史数据一样能补上后缀）。
PU_FULL = {}
for _g in hd["games"]:
    for _p in _g["players"]:
        _nm = _p.get("name")
        if _nm:
            PU_FULL[_p["puuid"]] = _nm


def full_name(pu, fallback):
    """优先用带 `#后缀` 的完整 Riot ID；映射里查不到才退回原来的裸名字。"""
    return PU_FULL.get(pu) or fallback


# ---- mine（概要，来自 hd 缓存） ----
#
# 5 分钟以内的对局视为无效（挂机/重开），不进展板 —— 见 _paths.MIN_GAME_SECONDS。
# 口径必须和算分一致（_hd_crawl / _player_elo_build / 估算 都滤了），
# 否则看板上的局数和「计分段」对不上，用户会以为丢数据了。
n_short = 0
mine = []
_orphan_ids = []          # my_game_ids 里不含数据主人的局（换账号抓取时混入的其他号对局）
for g in hd["games"]:
    if g["gameId"] not in my_ids:
        continue
    if not valid_game(g):
        n_short += 1
        continue
    p = next((x for x in g["players"] if x["puuid"] == me), None)
    if p is None:
        # ⚠️ 防御（2026-09-28 实机崩溃修复）：my_game_ids 里这局根本没有数据主人，
        # 通常是上次用错账号抓取把别的号的对局写进来了。不能 StopIteration 把整个看板
        # 打崩 —— 跳过并明确告警，让看板照常出（只是少几局，而不是全崩）。
        _orphan_ids.append(g["gameId"])
        continue
    cid = str(p.get("champion"))
    mine.append({"gameId": g["gameId"], "ts": g["ts"], "dur": g.get("duration", 0),
                 "win": bool(p["win"]), "k": p["k"], "d": p["d"], "a": p["a"],
                 "champion": cm.get(cid, cid) or cid, "champId": cid})
mine.sort(key=lambda x: x["ts"])
if n_short:
    print(f"  ⚠ 已排除 {n_short} 局 <{MIN_GAME_SECONDS}s 的无效对局（挂机/重开）")
if _orphan_ids:
    print(f"  ⚠ {len(_orphan_ids)} 局 my_game_ids 里不含数据主人(me)，已跳过"
          f"（疑似换账号抓取混入的其他号对局；建议用正确账号重跑「抓新局并重算」）")
if not mine:
    print("✗ 一局有效对局都没有，看板无法生成。请先跑 _hd_crawl.py。")
    raise SystemExit(1)

def wl(rs): return sum(1 for r in rs if r["win"]), sum(1 for r in rs if not r["win"])
def wr(rs):
    w, l = wl(rs); return round(100 * w / max(len(rs), 1), 1), w, l
def kda(rs):
    ks = sum(r["k"] for r in rs); ds = sum(r["d"] for r in rs); aa = sum(r["a"] for r in rs)
    return {"k": ks, "d": ds, "a": aa, "kda": round((ks + aa) / max(ds, 1), 2)}

tot_wr, tot_w, tot_l = wr(mine)
seg = {}
for n in (10, 20, 30, 50):
    rs_n = mine[-n:]      # 总局数 < n 时就是全部局
    sw, sl = wl(rs_n)
    # ⚠️ 分母必须用 len(rs_n)（审查 M1）：总局数不足 n 时 mine[-n:] 取的是全部局，
    #    再除固定 n 会把「近 N 局胜率」稀释报低。当前数据 159 局不触发，换小数据主人会。
    seg[str(n)] = {"w": sw, "l": sl, "wr": round(100 * sw / max(len(rs_n), 1), 1)}

by_day = {}
for r in mine:
    day = _t.strftime("%m-%d", _t.localtime(r["ts"] / 1000))
    by_day.setdefault(day, []).append(r)
daily = [{"day": d, "w": wl(rs)[0], "l": wl(rs)[1], "kda": kda(rs)["kda"], "n": len(rs)}
         for d, rs in by_day.items()]

by_champ = {}
for r in mine: by_champ.setdefault(r["champion"], []).append(r)
champ_rows = []
for c, rs in sorted(by_champ.items(), key=lambda kv: -len(kv[1]))[:12]:
    sw, sl = wl(rs)
    champ_rows.append({"champion": c, "n": len(rs), "w": sw, "l": sl,
                       "wr": round(100 * sw / len(rs), 1), **kda(rs)})

# `score_after` 只有「带锚点」跑估算时才会写（见 海斗隐藏分估算_v2.py 的 --anchor）。
# 不带锚点跑，行里只有原始 elo_after —— 两者差一个 offset，混着用会让轨迹和头部分数
# 落在不同刻度上。所以这里明确报错说清楚，而不是甩一个 KeyError 出来。
# （2026-09-21 实测：手动跑估算时漏了 --anchor，看板重建就死在这一行。）
_missing = [r for r in v2["games"] if "score_after" not in r]
if _missing:
    print(f"✗ 估算结果里 {len(_missing)}/{len(v2['games'])} 局缺 score_after（锚定分）。")
    print("  说明这次估算没带 --anchor 跑。请用桌面版按钮重跑，或手动加锚点：")
    print("      海斗隐藏分估算_v2.py --anchor <分数> --anchor-game-id <锚点局id>")
    raise SystemExit(1)

# 轨迹同样排除无效对局。估算脚本自己已经滤过了，但 `海斗隐藏分_v2_本次估算.json`
# 可能是旧版本跑出来的（那时候还没这个过滤），所以按 gameId 再挡一次。
_short_ids = {g["gameId"] for g in hd["games"] if not valid_game(g)}
_n_traj_short = sum(1 for r in v2["games"] if r["gameId"] in _short_ids)
if _n_traj_short:
    print(f"  ⚠ 轨迹里排除 {_n_traj_short} 局 <{MIN_GAME_SECONDS}s 的无效对局"
          f"（估算结果是旧版跑的就地过滤；重跑一次估算会更干净）")

traj = [{"time": r["time"], "ts": r["ts"], "win": r["win"], "champion": r["champion"],
         "k": r["k"], "d": r["d"], "a": r["a"], "opp_avg": r["opp_avg"],
         "opp_known": r["opp_known"], "score_after": r["score_after"],
         "elo_after": r["elo_after"]} for r in v2["games"]
        if r["gameId"] not in _short_ids]
if not traj:
    print("✗ 轨迹一局都不剩（全被判为无效对局？），中止（不写盘）")
    raise SystemExit(1)
score_by_id = {r["gameId"]: r["score_after"] for r in v2["games"]}
opp_by_id = {r["gameId"]: r["opp_avg"] for r in v2["games"]}
known_by_id = {r["gameId"]: r["opp_known"] for r in v2["games"]}
for r in mine:
    r["scored"] = r["gameId"] in score_by_id
    r["score"] = score_by_id.get(r["gameId"])
    r["opp_avg"] = opp_by_id.get(r["gameId"])
    r["opp_known"] = known_by_id.get(r["gameId"])
    if r["dur"]:
        m, s = divmod(int(r["dur"]), 60); r["dur_str"] = f"{m}:{s:02d}"
    else:
        r["dur_str"] = ""

d0 = _t.strftime("%m-%d", _t.localtime(mine[0]["ts"] / 1000))
d1 = _t.strftime("%m-%d", _t.localtime(mine[-1]["ts"] / 1000))

# ---- 1.5) 9 项指标均值（含治疗占比） ----
ms_by_gid = {m["gid"]: m for m in ms}

# 只有抓过 stats 的局才有 9 项指标。新抓回来的局如果还没跑 _stats_crawl.py，
# 这里就是缺数据 —— 直接 ms_by_gid[gid] 会 KeyError 把整个看板打崩
# （2026-09-21 实测：抓到 29046 局后，看板重建 rc=1 就死在这一行）。
# 统一在这里过滤一次，并明确警告，后面一律用 ms_rows。
ms_rows = [r for r in mine if r["gameId"] in ms_by_gid]
_n_missing = len(mine) - len(ms_rows)
if _n_missing:
    print(f"  ⚠ {_n_missing}/{len(mine)} 局缺 stats（先跑 _stats_crawl.py 补齐），"
          f"本次只用有数据的 {len(ms_rows)} 局")
if not ms_rows:
    print("✗ 一局 stats 都没有，看板无法计算。请先跑 _stats_crawl.py。")
    raise SystemExit(1)
_mine_pos = {r["gameId"]: i for i, r in enumerate(mine)}

# ===== 表现分公式已抽到 `_composite.py`（第九轮，2026-09-27） =====
#   `metrics()` / `COMPOSITE_ANCHOR` / `COMPOSITE_WEIGHTS` / `COMPOSITE_CURVES` /
#   `curve_score()` / `composite_raw()` / `window_mean_raw()` / `finalize()` 全部搬到那里，
#   本文件只 import（见顶部 import 块）。原因：诊断/复核脚本
#   没法 import 本文件（本文件 import 即执行），若各自抄一份常量就是铁律 45 说的
#   「同一个值两份落点，迟早过期」。
#   v4 → v6 的历史沿革与口径说明见 `_composite.py` 的模块 docstring。
#
#   ⚠️ 第九轮（2026-09-28）重标定：原来 8 条曲线最高分加权和只有 **93.5**，
#      这套公式**永远打不到 100 分**，而满分是可能出现的。
#      已修：末控制点得分全部抬到 100 + `COMPOSITE_CLAMP` 溢出后夹紧。
#      同时新增「局内统治力加成」（见 `_composite.DOMINANCE_KEYS`）：
#      本局在 6 项里拿到全场 10 人第 1 的项数 × 1 分。
#      ⚠️ 加成**必须计入下面的 offset 标定**，否则锚点会漂 +0.73 分。


# ---- 局内统治力加成所需的「全场 10 人指标表」 ----
#
# `dominance_n1()` 要比「全场 10 人」（唯一一处跨队口径），所以这里先把每局
# 10 人的 8 项指标算出来缓存住 —— 一份数据供三处用：
#   ① offset 标定（主人近 30 局的逐局加成）
#   ② allseries 每局的 comp 与 n1
#   ③ roster 明细里每个人的 comp 与 n1
# 只对**有效对局**建表，与看板其余部分口径一致。
# ⚠️ 循环变量别用 `_t` —— 那是 `import time as _t` 的别名（历史踩坑）。
_lobby = {}        # gid(str) -> [(pu, metrics), ...]，每局 10 人
_lob_by_pu = {}    # gid(str) -> {pu: metrics}
_n1 = {}           # (gid(str), pu) -> 拿到全场第一的项数
for _g in roster:
    if not valid_game(_g):
        continue
    _rows = []
    for _team in _g["teams"]:
        for _p in _team["players"]:
            if not _p.get("pu"):
                continue
            _rows.append((_p["pu"],
                          roster_metrics(_team["players"], _p, _team.get("kills") or 0)))
    _gid = str(_g["gid"])
    _lobby[_gid] = _rows
    _lob_by_pu[_gid] = {_pu: _m for _pu, _m in _rows}
    for _pu, _m in _rows:
        _n1[(_gid, _pu)] = dominance_n1([m for _, m in _rows], _m)


def _bonus(gid, pu):
    """(局, 玩家) → 统治力加成分。查不到（无效局 / 无 pu）返回 0。"""
    return dominance_bonus(_n1.get((str(gid), pu), 0))


print(f"  ✓ 全场 10 人指标表 {len(_lobby)} 局；统治力加成 "
      f"{len(DOMINANCE_KEYS)} 项 × {DOMINANCE_PER} 分/项")

# ---- 锚点平移 + 逐局序列 ----
#
# 全部局逐局序列（allseries）：轨迹图和「表现分基础指标」都要能自己调时间范围，
# 所以把**每一局**的 9 项指标 + 单局综合分都下发，前端按窗口自己切片重算均值。
# 以前只下发最近 30 局（series），范围选择器无从谈起。
W = 30
win_rows = ms_rows[-W:]                       # 取「最近 30 局里有 stats 的」
win_ms = [ms_by_gid[r["gameId"]] for r in win_rows]

# 平移量 = 锚点 −（近 30 局**逐局原始分的算术平均** + 近 30 局**加成均值**）。
#
# v6 改动：以前是 `锚点 − composite_raw(win_ms)`（先求窗口均值再过多行曲线），
# 那样算出来的「窗口均值」与「逐局评分求平均」不是一回事 —— 看板上「综合评分」
# 和逐局「对局评分」列自相矛盾。改成逐局口径后两者一致。
# 改成逐局口径后 `mean(近30局逐局 comp) == COMPOSITE_ANCHOR` 精确成立。
#
# 第九轮补充：锚点是**关于最终分**的外部真值，而最终分 = raw + offset + 加成，
# 所以 offset 必须把加成的均值吸收掉（否则实测漂 +0.73 分，看板「综合评分」
# 就和锚点对不上）。
_mean_raw = window_mean_raw(win_ms)      # = 逐局原始分的算术平均（`_composite.py`）
_mean_bonus = sum(_bonus(r["gameId"], me) for r in win_rows) / max(len(win_rows), 1)
COMP_OFFSET = offset_for(win_ms, COMPOSITE_ANCHOR, _mean_bonus)   # 只平移位置，不压动态
print(f"  offset = {COMP_OFFSET:+.3f}（锚点 {COMPOSITE_ANCHOR} − raw均值 {_mean_raw:.3f}"
      f" − 加成均值 {_mean_bonus:.3f}）")


# 原始 10 人明细按 gid → {puuid: 玩家} 索引。
# 用途有两个：
#   ① allseries 要补 dmg / gold / dur —— my_stats.json 只存占比，没有原始量，
#      而「分均伤害 / 分均经济」必须用原始量 ÷ 时长；
#   ② 前端「切视角后整页跟着变」要用任意玩家的原始值重算占比，
#      分母取他自己那一队的 5 人。
# 用 raw roster（而不是 roster_by_gid）是为了不被下面的字段裁剪影响。
# ⚠️ 循环变量别用 `_t` —— 那是 `import time as _t` 的别名，覆盖掉之后
#    _series_row 里的 _t.strftime 会炸成 "dict has no attribute strftime"。
_roster_raw = {}
for _g in roster:
    if not valid_game(_g):
        continue
    _d = {}
    for _team in _g["teams"]:
        for _p in _team["players"]:
            if _p.get("pu"):
                _d[_p["pu"]] = _p
    _roster_raw[str(_g["gid"])] = _d


def _series_row(r):
    mrow = ms_by_gid[r["gameId"]]
    rp = _roster_raw.get(str(r["gameId"]), {}).get(me) or {}
    return {
        "seq": _mine_pos[r["gameId"]] + 1, "ts": r["ts"], "win": r["win"],
        "champ": r["champion"],
        "date": _t.strftime("%m-%d", _t.localtime(r["ts"] / 1000)),
        # k/d/a 也下发：前端算窗口 KDA 要用「逐局 (K+A)/D 再求均值」，
        # 直接拿 allseries 里已四舍五入到 2 位的 kda 再平均会和后端差 0.01。
        "k": mrow["k"], "d": mrow["d"], "a": mrow["a"],
        "kda": round((mrow["k"] + mrow["a"]) / max(mrow["d"], 1), 2),
        "dmg_share": mrow["dmg_share"], "taken_share": mrow["taken_share"],
        "gold_share": mrow["gold_share"], "heal_share": mrow["heal_share"],
        "dmg_conv": mrow["dmg_conv"], "kill_part": mrow["kill_part"],
        # 原始量 + 时长：给「分均伤害 / 分均经济 / 场均死亡」三项新指标用。
        # 取不到就 0（老数据没有 raw roster 时那几项显示 0，不会崩）。
        "dmg": rp.get("dmg", 0), "gold": rp.get("gold", 0),
        "dur": r.get("dur") or 0,
        "comp": round(finalize(composite_raw([mrow]), COMP_OFFSET,
                               _bonus(r["gameId"], me)), 1),
        # 本局拿到全场第一的项数（统治力加成的依据）。必须下发 ——
        # 前端 `compositeOf()` 按窗口自己重算时要复现加成，只给 comp 的话
        # 切窗口后的「综合评分」会和头部对不上。
        "n1": _n1.get((str(r["gameId"]), me), 0),
        "score": r.get("score"),
    }


allseries = [_series_row(r) for r in ms_rows]     # 全部有 stats 的局（旧 → 新）
series = allseries[-W:]                            # 兼容：series 仍=最近 30 局

# 1.5 9 项指标均值给前端（一律走 ms_rows：只统计有 stats 的局）
m30 = metrics(win_ms)
m10 = metrics([ms_by_gid[r["gameId"]] for r in ms_rows[-10:]])
m20prev = metrics([ms_by_gid[r["gameId"]] for r in ms_rows[-30:-10]])

# 综合分三档（逐局评分求平均；c30 由 COMP_OFFSET 的定义恒等于锚点）
def _win_comp(rows):
    """窗口综合分 = 窗口内**逐局单局评分**的算术平均（逐局平移 + 加成 + 夹紧）。

    这就是「平均本局评分」口径（v6 起）。
    和 `composite_raw(多行)` 的区别：这里先逐局过曲线再平均，不是先平均再过曲线。

    第九轮：改用 `window_mean_final()` —— **逐局先夹紧再平均**。
    夹紧是非线性操作，「先平均再夹紧」与「逐局夹紧再平均」不等价，
    统一采用「每局先出一个分，再对分求平均」。
    """
    if not rows:
        return None
    return round(window_mean_final(rows, COMP_OFFSET,
                                   [_bonus(r["gid"], me) for r in rows]), 1)


c30_raw = round(COMPOSITE_ANCHOR, 1)
c10_raw = _win_comp([ms_by_gid[r["gameId"]] for r in ms_rows[-10:]])
c20_raw = _win_comp([ms_by_gid[r["gameId"]] for r in ms_rows[-30:-10]])
comp_raw30 = round(_mean_raw, 1)

# roster → 按 gid 索引 + 英雄名 + mmr 字段 + 每人当局「对局评分」
MMR_MIN_N = 5   # 样本 <5 局的玩家不给点估计（只打了一两局，任何分数都是编的）


def _roster_player_comp(m, bonus=0.0):
    """把一个玩家这一局的 8 项指标 → 单局综合分（含统治力加成）。

    指标本身由 `_composite.roster_metrics()` 提前算好（分母 = 我方 5 人、占比 1 位小数、
    转化 2 位小数），本函数只负责「过曲线 + 平移 + 加成 + 夹紧」。
    所有复核代码用的是**同一个 `roster_metrics()`**，
    所以「标定用的指标」和「看板显示的指标」不可能漂移。

    ⚠️ 加成必须传：否则明细里的「对局评分」会和 allseries 里那一局的 comp 对不上
    （「我」那一行的断言钉住了两者必须相等）。
    """
    if m is None:
        return None
    return round(finalize(composite_raw([m]), COMP_OFFSET, bonus), 1)


roster_by_gid = {}
for g in roster:
    if not valid_game(g):        # 无效对局（<5 分钟）不进明细，见 _paths.MIN_GAME_SECONDS
        continue
    teams = []
    for t in g["teams"]:
        players = []
        for p in t["players"]:
            pu = p.get("pu")
            if pu == me:
                # 本人 = **当前估算值**（不是锚点值）。
                # v6 改动：原来写死 1928（锚点），但本人早就从锚点漂到 2029.5 了，
                # 明细里「·我」那一行会和轨迹图末端对不上，而且「同局位置」
                # 会拿一个过期的分数去和别人的当前分数比。
                mmr, mmr_n = v2["estimate"], len(mine)
            elif pu and pu in PE and PE[pu].get("n_games", 0) >= MMR_MIN_N:
                mmr, mmr_n = PE[pu]["mmr"], PE[pu]["n_games"]
            else:
                mmr, mmr_n = None, 0
            players.append({
                # `n` 一律给「名字#后缀」的完整 Riot ID（见上面的 PU_FULL 说明）——
                # 全站显示都读这一个字段，所以加后缀**前端一行都不用改**。
                "n": full_name(pu, p["n"]), "pu": pu,
                "cn": cm_name(p["c"]) if p.get("c") is not None else "?",
                "win": p["win"],
                "k": p["k"], "d": p["d"], "a": p["a"],
                "kda": round((p["k"] + p["a"]) / max(p["d"], 1), 2),
                "dmg": p["dmg"], "taken": p["taken"],
                # gold / heal 也要下发：前端「切视角」时要用**任意玩家**的
                # 原始量重算经济占比 / 治疗占比 / 分均经济。只下发 dmg/taken
                # 的话，换个视角过去这几项就只能显示 0。
                "gold": p.get("gold", 0), "heal": p.get("heal", 0),
                "comp": _roster_player_comp(_lob_by_pu.get(str(g["gid"]), {}).get(pu),
                                            _bonus(g["gid"], pu)),
                # 统治力加成依据：本局拿到全场第一的项数（前端切视角时复现加成要用）
                "n1": _n1.get((str(g["gid"]), pu), 0),
                "mmr": mmr,
                "mmr_n": mmr_n,
            })
        teams.append({"t": t["t"], "win": t["win"], "kills": t["kills"], "players": players})
    roster_by_gid[str(g["gid"])] = {"dur": g["dur"], "ts": g["ts"], "teams": teams}

# ---- 视角切换：所有出现在明细里的玩家（按同场局数降序） ----
# 看板默认以数据主人为视角（「·我」标记 / 对手质量排除对象 / 弹层的联手-交手）。
# 这里把候选名单一次性算好下发，前端下拉直接读，不用在 JS 里再滚 1560 次。
#
# ⚠️ 只保留**同场 ≥5 局**的（VIEW_MIN_GAMES）。用户 2026-09-21 明确要求：
#    「保留同场大于等于 5 局的备选项，小于的删掉」。
#    原因：1172 个候选里绝大多数只同场过 1 局，塞进下拉等于没法用；
#    而且同场 1~4 局的人，轨迹/指标样本太小，切过去看基本是噪声。
#    想查只同场过一两局的人，走下拉旁边的「自己输 ID」。
VIEW_MIN_GAMES = 5
_view = {}
for _rg in roster_by_gid.values():
    for _t in _rg["teams"]:
        for _p in _t["players"]:
            _pu = _p.get("pu")
            if not _pu or _pu == me:
                continue
            _e = _view.setdefault(_pu, {"pu": _pu, "games": 0, "names": {}})
            _e["games"] += 1
            _e["names"][_p["n"]] = _e["names"].get(_p["n"], 0) + 1
view_players = [
    {"pu": e["pu"], "n": max(e["names"], key=e["names"].get), "games": e["games"]}
    for e in sorted(_view.values(), key=lambda x: (-x["games"], x["pu"]))
    if e["games"] >= VIEW_MIN_GAMES
]
print(f"  ✓ 视角候选 {len(view_players)} 人（同场 ≥{VIEW_MIN_GAMES} 局；"
      f"全量同场过的共 {len(_view)} 人，不足 {VIEW_MIN_GAMES} 局的 {len(_view) - len(view_players)} 人未列入下拉）")

# ============== 锚点口径 ==============
# 只记录「本次估算用了什么锚点」。第三方对照库、分位曲线、段位阶梯
# 都不在仓库里、也不参与计算 —— 锚点由使用者自己提供（截图上的一个分数）。
print(f"[anchors] 隐藏分锚点 {v2['anchor']['anchor_score']} / 表现分锚点 {COMPOSITE_ANCHOR}")

out = {
    "summoner": hd["meta"]["summoner"], "region": "国服 (WeGame)",
    # 数据主人（视角切换要用 puuid 判定「是不是我」，比按名字匹配稳）
    # ⚠️ `me_name` 也带 `#后缀`（原来这里 `.split("#")[0]` 把后缀砍掉了）：
    # 它得和 roster 里的 `n`（现在同样带后缀）对得上，
    # 否则前端 `isView()` 在「没有 pu 的老快照」上按名字兜底匹配会失配。
    "me_puuid": me, "me_name": full_name(me, hd["meta"]["summoner"]),
    "queue": "海克斯大乱斗 (queue 2400)", "crawled_at": hd["meta"]["crawled_at"],
    "scope": {"n_games": hd["meta"]["n_games"], "n_players": hd["meta"]["n_players"],
              "n_my": len(mine), "n_hist": hd["meta"]["n_hist_crawled"],
              "from": d0, "to": d1},
    "stats": {"total": {"n": len(mine), "w": tot_w, "l": tot_l, "wr": tot_wr, **kda(mine)},
              "segments": seg, "last20_kda": kda(mine[-20:])},
    "estimate": {"current": v2["estimate"], "ci": v2["ci_half_95"], "raw_elo": v2["raw_elo"],
                 "anchor_score": v2["anchor"]["anchor_score"],
                 "anchor_time": v2["anchor"]["anchor_game_time"],
                 "anchor_elo": v2["anchor"]["anchor_elo"], "offset": v2["anchor"]["offset"],
                 "delta": round(v2["estimate"] - v2["anchor"]["anchor_score"], 1),
                 # 上次估算时间：直接取估算结果文件的 mtime。
                 # 估算脚本自己不在 JSON 里写时间戳，而 mtime 就是「这份估算什么时候
                 # 算出来的」，语义完全对得上，也省得去改估算脚本。
                 # 读不到就退回抓取时间（至少不是空）。
                 "computed_at": _est_at or hd["meta"]["crawled_at"],
                 "crawled_at": hd["meta"]["crawled_at"]},
    "model": {k: v2["model"][k] for k in
              ("type", "bt_epochs", "bt_k0", "bt_k1", "bt_shrink", "k_factor",
               "min_games_theta", "n_opp_with_theta", "n_opp_total", "prior")},
    "spectral": v2["spectral"], "spectral_deg5": v2["spectral_deg5"],

    # ============== 锚点与评分公式（下发给看板） ==============
    "anchors": {
        # 隐藏分锚点（v2 估算已用 → 单点校准）
        "hidden_anchor": {
            "value": v2["anchor"]["anchor_score"],
            "source": "本次估算的人工锚点：把某一局的实际分数当作标定起点",
            "used_for": "BT 全网 θ + 在线 Elo 终值的整体平移（fit_bt → raw_elo + offset）",
            "mapped_via": "v2 估算 raw_elo + offset → estimate",
        },
        # 表现评分锚点
        "composite_anchor": {
            "value": COMPOSITE_ANCHOR,
            "source": "本次标定的人工锚点：近 30 局的平均表现评分",
            "used_for": "综合评分公式的位置校准（单点平移：逐局分 + offset，动态全保留）",
        },
        # 我方综合分启发式的权 + 公式（v6：绝对锚定曲线 + 逐局平均）
        "composite_formula": {
            "type": "绝对锚定参照曲线加权 + 逐局平均 + %g 锚点平移（v6；v5 的窗口均值口径已废弃）"
                    % COMPOSITE_ANCHOR,
            "weights": COMPOSITE_WEIGHTS,
            "curves": COMPOSITE_CURVES,
            "anchor_value": COMPOSITE_ANCHOR,
            "offset": round(COMP_OFFSET, 1),
            # 前端算任意窗口的综合分要用的**未取整**平移量。
            # 只下发 round(offset,1) 的话，前端和这里的尾数会差最多 0.05，
            # 正好能跨过 .x5 的取整边界 → 同一窗口两边差 0.1 分（实测 64.8 vs 64.7）。
            "offset_exact": COMP_OFFSET,
            # 近 30 局**逐局原始分的平均**（v6 起；以前是「窗口均值过曲线」的原始分）
            "raw_last30": comp_raw30,
            "calibration": "锚点只平移位置不压动态：mean(近30局逐局 raw) + offset = %g；"
                           "窗口综合分 = 逐局评分的算术平均"
                           % COMPOSITE_ANCHOR,
            # 溢出后夹紧区间。原始分是无上限的，最终夹到 0~100；
            # 而 8 条曲线最高只能加权出 93.5 —— 永远打不到 100。
            # null = 不夹紧（v6 行为）。前端 `compositeRawOf/compositeOf` 会读它。
            "clamp": list(COMPOSITE_CLAMP) if COMPOSITE_CLAMP else None,
            "raw_max": round(max_raw(), 1),
            "raw_min": round(min_raw(), 1),
            # ---- 局内统治力加成（第九轮，2026-09-28）----
            # 本局在 `keys` 里拿到**全场 10 人第 1** 的项数 × per 分，
            # 加在「平移之后、夹紧之前」。前端 `compositeOf()` 靠每行的 `n1` 字段复现。
            "dominance": {
                "keys": list(DOMINANCE_KEYS),
                "per": DOMINANCE_PER,
                "basis": "全场 10 人（全公式唯一一处跨队口径）",
                "why": "评分 ≥85 的点「6 项里最好排名」全部 = 1，"
                       "而 55~70 分的点只有 1/10 满足 ⇒ 高分里含「单项碾压」成分",
            },
        },
    },

    "traj": traj, "mine": mine, "daily": daily, "champions": champ_rows,
    "metrics": {"last30": m30, "last10": m10, "prev20": m20prev,
                "composite": c30_raw, "composite10": c10_raw, "composite20": c20_raw},
    "series": series,               # 最近 30 局（_verify_dash.js 依赖这个字段）
    "allseries": allseries,         # 全部有 stats 的局 —— 指标/趋势面板按窗口自己切片
    "view_players": view_players,   # 视角切换下拉的候选名单（同场 ≥VIEW_MIN_GAMES 局）
    "view_min_games": VIEW_MIN_GAMES,   # 前端据此说明「为什么下拉里没有同场 1 局的人」
    "roster": roster_by_gid,
}
atomic_write_text(DATA_DIR / "海斗dashboard_data.json", json.dumps(out, ensure_ascii=False))
print(f"✓ 聚合完成: composite 30/10/20 = {c30_raw}/{c10_raw}/{c20_raw} "
      f"(raw30={comp_raw30}, offset={COMP_OFFSET:+.1f}, 锚点 {COMPOSITE_ANCHOR})")
print(f"  series 窗口 {len(series)} 局 / allseries {len(allseries)} 局 / "
      f"roster {len(roster_by_gid)} 局 / 视角候选 {len(view_players)} 人")

# ---- 2) 注入 HTML ----
html_path = str(BOARD / "海斗隐藏分_数据看板.html")
# newline=""：universal-newlines 会把 CRLF→LF 读进来、写出时不再转回去，
# 等于把整份 900KB 看板的换行符改写一遍（diff 全文件噪音，审查 H5 附带项）
html = open(html_path, encoding="utf-8", newline="").read()
payload = json.dumps(out, ensure_ascii=False).replace("</", "<\\/")
pat = re.compile(r'(<script id="dash-data" type="application/json">)(.*?)(</script>)', re.S)
assert pat.search(html), "HTML 里找不到 dash-data 数据块"
html = pat.sub(lambda m: m.group(1) + payload + m.group(3), html, count=1)
# 原子写（审查 H5）：看板 HTML 是唯一产物，写一半崩了整个工具打不开
atomic_write_text(html_path, html)
# ⚠️ 别用 `len(html) / 1024` —— 那是**字符数**。中文按 UTF-8 占 3 字节，
#    会报小 ~9%（实测打印 805.7「KB」，真实是 900,151 字节 / 879.1 KB）。
_nbytes = len(open(html_path, "rb").read())
print(f"✓ 看板数据已更新: {html_path}  {_nbytes / 1024:.1f} KB（{_nbytes} 字节）")
print(f"  当前 hidden 估算: {round(out['estimate']['current'])}  /  composite 启发式: {c30_raw}")
