# -*- coding: utf-8 -*-
"""验证进度条 / 剩余时间 / 步骤编排的逻辑。

不打桩脚本，只测纯逻辑：标记解析、步骤表、百分比与 ETA 的单调性、
并发保护、锚点自动跟随的换算。

用法（在包内任何位置都能跑）：
    python 07_测试\\test_progress.py
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 路径按本文件位置推，别写死绝对路径 —— 换盘/换目录就跑不起来了
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "06_桌面版" / "app.py"
spec = importlib.util.spec_from_file_location("appmod", str(APP))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _check import check as ck, finish   # 2026-10-03 unified assert helper (was ck)
print("== ① 抓取进度标记解析（_mark_crawl）==")
st = {"players": 300}
ck("R1 开头 → 0.0", m._mark_crawl("[R1] 待扩展 300 人 (已抓列表 0) ...", st) == 0.0,
   m._mark_crawl("[R1] 待扩展 300 人 (已抓列表 0) ...", st))
ck("已抓 150/300 → 0.5", m._mark_crawl("[R2] 待扩展 150 人 (已抓列表 150) ...", st) == 0.5,
   m._mark_crawl("[R2] 待扩展 150 人 (已抓列表 150) ...", st))
ck("已抓 300/300 → 1.0", m._mark_crawl("[R3] 待扩展 0 人 (已抓列表 300) ...", st) == 1.0)
ck("超目标也封顶 1.0", m._mark_crawl("(已抓列表 999)", st) == 1.0)
ck("保存行 → 1.0", m._mark_crawl("✓ 保存 D:\\x\\hd_data.json: 1 局 / 2 玩家 / 主人 3 局", st) == 1.0)
ck("无关行 → None（不推进进度）", m._mark_crawl("  载入已有 21053 局, 继续扩展", st) is None)

print("== ② 估算进度标记解析（_mark_estimate）==")
# 权重取 2026-09-22 的**实测**值（整步 95.6s：数据加载 1.6s / BT 4.5s /
# Elo 0.01s / 谱分解 89.2s），不再按 (n-1)/4 等分 —— 等分时「刚进谱分解」
# 就报 50%，剩余时间被算成「只剩几十秒」（主人 2026-09-22 实机反馈）。
_W = m._EST_STAGE_W
ck("[1/4] → 实测权重", m._mark_estimate("[1/4] 全局 Bradley-Terry 拟合对手实力 ...", {}) == _W[1])
ck("[2/4] → 实测权重", m._mark_estimate("[2/4] 计算本人在线 Elo 轨迹 ...", {}) == _W[2])
ck("[3/4] → 实测权重", m._mark_estimate("[3/4] 谱分解交叉验证 ...", {}) == _W[3])
ck("[4/4] → 实测权重", m._mark_estimate("[4/4] 结果", {}) == _W[4])
ck("详细数据行 → 1.0", m._mark_estimate("  详细数据 -> D:\\x.json", {}) == 1.0)
ck("四个阶段严格递增（等值会被 _run_job 丢掉，进度就再也不动了）",
   _W[1] < _W[2] < _W[3] < _W[4] < 1.0, _W)
ck("谱分解那一段占大头（>80% 的工作落在 [3/4]→[4/4] 之间）",
   (_W[4] - _W[3]) > 0.8, _W[4] - _W[3])

print("== ②b 谱分解细粒度进度（2026-09-22 新增：原来卡在 9% 一分半）==")
# ⚠️ 段号与下面的「②b 补抓」重名了，但两段内容不冲突；改名要动历史注释，先留着。
# 实测（整步 100.1s）：谱1 幂迭代 6.04→98.50s = 92.4% 的时间，
# 而它以前只有 [3/4] 一个标记 → 进度条在这 92 秒里纹丝不动。
_SP = m._SPEC_SEG
ck("谱1 建超图 → 段起点",
   m._mark_estimate("  [谱1] 建超图 ...", {}) == _SP["谱1"][0])
ck("谱1 建图完成 → 迭代区间起点",
   m._mark_estimate("  [谱1] 建图完成: 248536 节点 / 34602 超边，开始 300 次幂迭代 ...", {})
   == _SP["谱1"][1])
ck("谱1 半程落在迭代区间内（线性插值）",
   _SP["谱1"][1] < m._mark_estimate("  [谱1] 150/300", {}) < _SP["谱1"][2],
   m._mark_estimate("  [谱1] 150/300", {}))
ck("谱1 迭代结束 → 段末",
   m._mark_estimate("  [谱1] 迭代结束 (300/300)", {}) == _SP["谱1"][3])
ck("谱2 起点 > 谱1 段末（区间不许重叠，否则第二段没进度）",
   _SP["谱2"][0] > _SP["谱1"][3], (_SP["谱1"][3], _SP["谱2"][0]))
ck("谱2 迭代结束 → 段末",
   m._mark_estimate("  [谱2] 迭代结束 (300/300)", {}) == _SP["谱2"][3])
ck("两段谱都夹在 [3/4] 与 [4/4] 之间",
   _W[3] < _SP["谱1"][0] and _SP["谱2"][3] < _W[4], (_W[3], _SP["谱2"][3], _W[4]))

# 真实输出序列必须**严格递增** —— 这正是 _run_job 的硬要求（等值/变小会被丢掉）
_seq = ["[1/4] x", "[2/4] x", "[3/4] x", "  [谱1] 建超图 ...",
        "  [谱1] 建图完成: 248536 节点 / 34602 超边，开始 300 次幂迭代 ..."]
_seq += ["  [谱1] %d/300" % k for k in (15, 30, 60, 150, 299, 300)]
_seq += ["  [谱1] 迭代结束 (300/300)", "  [谱2] 建超图 ...",
         "  [谱2] 建图完成: 3096 节点 / 169 超边，开始 300 次幂迭代 ..."]
_seq += ["  [谱2] %d/300" % k for k in (15, 150, 300)]
_seq += ["  [谱2] 迭代结束 (300/300)", "[4/4] 结果", "  详细数据 -> x.json"]
_vals = [m._mark_estimate(ln, {}) for ln in _seq]
ck("真实输出序列逐行都能解析", all(v is not None for v in _vals), _vals)
ck("真实输出序列严格递增（否则 _run_job 会丢标记）",
   all(a < b for a, b in zip(_vals, _vals[1:])), _vals)
ck("序列终点 = 1.0", _vals[-1] == 1.0, _vals[-1])

print("== ②b 补抓进度标记解析（_mark_backfill）==")
stb = {}
ck("载入 → 0.03", m._mark_backfill("载入 26151 局 / 主人 161 局", stb) == 0.03)
ck("待补抓对手 → 0.04 且记下人数",
   m._mark_backfill("待补抓对手: 137 人（网络内出现<5 局）", stb) == 0.04 and stb.get("cands") == 137,
   stb)
ck("无关行（最近 20 局…）→ None", m._mark_backfill("最近 20 局从 09-20 21:33 起", stb) is None)
f1 = m._mark_backfill("  [列表] 5/137 人", stb)
ck("[列表] 5/137 落在 0.05~0.40 之间", 0.05 < f1 < 0.40, f1)
ck("[列表] 137/137 → 0.40", m._mark_backfill("  [列表] 137/137 人", stb) == 0.40)
ck("列表汇总行 → 0.40",
   m._mark_backfill("列表 137 人 -> 新局 82 (243.1s)", stb) == 0.40)
f2 = m._mark_backfill("  [详情] 20/82 局", stb)
ck("[详情] 20/82 落在 0.42~0.92 之间", 0.42 < f2 < 0.92, f2)
ck("详情汇总行 → 0.95",
   m._mark_backfill("详情 +80 局, 累计 26300 局 / 主人 163 局", stb) == 0.95)
ck("保存行 → 1.0",
   m._mark_backfill("✓ 保存 D:\\x\\hd_data.json: 26300 局 / 1 玩家 / 主人 163 局", stb) == 1.0)
ck("无需补抓 → 0.9", m._mark_backfill("无需补抓", {}) == 0.9)

print("== ②c 评分重建进度标记（_mark_elo）==")
ste = {}
ck("加载 → 0.05", m._mark_elo("加载 26151 局 / 191512 玩家", ste) == 0.05)
f3 = m._mark_elo("  [Elo] 5000/26151 局", ste)
ck("[Elo] 5000/26151 落在 0.05~0.95 之间", 0.05 < f3 < 0.95, f3)
ck("[Elo] 26151/26151 → 0.95", m._mark_elo("  [Elo] 26151/26151 局", ste) == 0.95)
ck("✓ 输出 → 1.0", m._mark_elo("  ✓ 输出 191512 人 → _player_elo.json 3.1 MB", ste) == 1.0)
ck("offset 行 → None（不推进）",
   m._mark_elo("  主人 161 局对手最终分均值 = 1442.3 → offset = +485.7", ste) is None)

print("== ②d 本人明细进度标记（_mark_stats）==")
sts = {}
ck("待抓 → 0.02", m._mark_stats("待抓 161 局", sts) == 0.02)
f4 = m._mark_stats("  [Stats] 60/161 局", sts)
ck("[Stats] 60/161 落在 0.02~0.92 之间", 0.02 < f4 < 0.92, f4)
ck("[Stats] 161/161 → 0.92", m._mark_stats("  [Stats] 161/161 局", sts) == 0.92)
ck("✓ 输出（带两个空格缩进）→ 1.0",
   m._mark_stats("✓ 输出 161 局 → my_stats.json（含治疗占比）", sts) == 1.0)
ck("耗时行 → None", m._mark_stats("耗时 25.4s, 成功 161/161", sts) is None)

print("== ②e 看板重建进度标记（_mark_dash）==")
std = {}
ck("[load] → 0.3", m._mark_dash("[load] hd 26151 局 / roster 161 局 ×10 人", std) == 0.3)
ck("聚合完成 → 0.85", m._mark_dash("✓ 聚合完成: composite 30/10/20 = 1900/1910/1920", std) == 0.85)
ck("看板已更新 → 1.0", m._mark_dash("✓ 看板数据已更新: D:\\x.html  310.2 KB", std) == 1.0)
ck("当前 hidden 行 → None（在 1.0 之后，不倒退）",
   m._mark_dash("  当前 hidden 估算: 1928  /  composite 启发式: 1900", std) is None)

print("== ②e 真实输出序列：进度必须单调不减 ==")
SEQ = [
    ("_mark_backfill", 8, [
        "载入 26151 局 / 主人 161 局",
        "最近 20 局从 09-20 21:33 起",
        "待补抓对手: 137 人（网络内出现<5 局）",
        "  [列表] 5/137 人", "  [列表] 40/137 人", "  [列表] 137/137 人",
        "列表 137 人 -> 新局 82 (243.1s)",
        "  [详情] 20/82 局", "  [详情] 82/82 局",
        "详情 +80 局, 累计 26300 局 / 主人 163 局",
        "✓ 保存 D:\\x\\hd_data.json: 26300 局 / 1 玩家 / 主人 163 局",
    ]),
    ("_mark_elo", 5, [
        "加载 26151 局 / 191512 玩家",
        "  [Elo] 5000/26151 局", "  [Elo] 20000/26151 局", "  [Elo] 26151/26151 局",
        "  主人 161 局对手最终分均值 = 1442.3 → offset = +485.7",
        "  ✓ 输出 191512 人 → _player_elo.json 3.1 MB",
    ]),
    ("_mark_stats", 4, [
        "✓ LCU 已连接 @127.0.0.1:20468",
        "待抓 161 局",
        "  [Stats] 10/161 局", "  [Stats] 80/161 局", "  [Stats] 161/161 局",
        "耗时 25.4s, 成功 161/161",
        "✓ 输出 161 局 → my_stats.json（含治疗占比）",
    ]),
    ("_mark_dash", 3, [
        "[load] hd 26151 局 / roster 161 局 ×10 人",
        "✓ 聚合完成: composite 30/10/20 = 1900/1910/1920",
        "✓ 看板数据已更新: D:\\x.html  310.2 KB",
        "  当前 hidden 估算: 1928  /  composite 启发式: 1900",
    ]),
]
for fname, least, lines in SEQ:
    fn = getattr(m, fname)
    st = {"players": 300}
    seq = [fn(ln, st) for ln in lines]
    seen = [x for x in seq if x is not None]
    ck("%s：解析出 >= %d 个进度点" % (fname, least), len(seen) >= least, seen)
    ck("%s：单调不减" % fname, seen == sorted(seen), seen)
    ck("%s：最后一个进度点 = 1.0" % fname, abs(seen[-1] - 1.0) < 1e-9, seen[-1])

print("== ③ 步骤编排（_stages_for）==")
argv = ["--anchor", "1928", "--anchor-games-ago", "3"]
ORDER = ["抓取最新对局", "补抓新对手", "重建全量玩家评分",
         "抓本人对局明细", "估算隐藏分", "重建看板"]

upd = m._stages_for("update", 300, argv)
ck("update 有 6 步", len(upd) == 6, [s["name"] for s in upd])
ck("update 顺序正确", [s["name"] for s in upd] == ORDER, [s["name"] for s in upd])
ck("六步全都挂了真实进度标记（不再纯靠估时）",
   all(s["marker"] is not None for s in upd),
   [(s["name"], s["marker"] is not None) for s in upd])
ck("update 只抓客户端窗口（--players 0），不跑 BFS",
   upd[0]["argv"] == ["--seed", "--players", "0"], upd[0]["argv"])
ck("update 预估耗时远小于 crawl",
   upd[0]["secs"] < m._stages_for("crawl", 300, argv)[0]["secs"],
   "%s vs %s" % (upd[0]["secs"], m._stages_for("crawl", 300, argv)[0]["secs"]))
ck("明细步在估算之前（看板的 9 项指标靠它）",
   [s["name"] for s in upd].index("抓本人对局明细")
   < [s["name"] for s in upd].index("估算隐藏分"),
   [s["name"] for s in upd])
ck("明细步是致命的（缺 stats 会让看板崩）",
   upd[3]["fatal"] is True and upd[3]["script"] == "_stats_crawl.py",
   (upd[3]["fatal"], upd[3]["script"]))

crawl = m._stages_for("crawl", 300, argv)
ck("crawl 有 6 步且顺序一致", [s["name"] for s in crawl] == ORDER)
ck("crawl 带 --players 300（完整 BFS）",
   crawl[0]["argv"] == ["--seed", "--players", "300"], crawl[0]["argv"])
ck("估算步带上锚点参数", crawl[4]["argv"] == argv, crawl[4]["argv"])
ck("抓取失败是致命的（fatal）", crawl[0]["fatal"] is True)
ck("补抓失败不致命（只是没补上）", crawl[1]["fatal"] is False)

# 估时自校准：上次实测耗时应该被采纳
hint = {"补抓新对手": 61.5, "重建全量玩家评分": 0.4, "估算隐藏分": 99999}
cal = m._stages_for("update", 300, argv, secs_hint=hint)
_by = {s["name"]: s["secs"] for s in cal}
ck("采纳上次实测耗时", _by["补抓新对手"] == 61.5, _by)
ck("太小的值被夹掉（0.4 秒不可信）", _by["重建全量玩家评分"] == m.SEC_ELO, _by)
ck("太大的值被夹掉（99999 秒不可信）", _by["估算隐藏分"] == m.SEC_ESTIMATE, _by)
ck("没给 hint 的步骤保持默认", _by["重建看板"] == m.SEC_DASH, _by)
ck("hint 是垃圾也不崩",
   len(m._stages_for("update", 300, argv, secs_hint={"x": None, "y": "abc"})) == 6)

ck("recalc 只 2 步（不抓数据）",
   [s["name"] for s in m._stages_for("recalc", 300, argv)] == ["估算隐藏分", "重建看板"])
ck("rebuild 只 1 步", len(m._stages_for("rebuild", 300, argv)) == 1)
ck("未知类型 → 空表", m._stages_for("nonsense", 300, argv) == [])

print("== ③b 抓取进度标记：只抓窗口时没有 BFS 行 ==")
st0 = {"players": 1}
ck("种子完成 → 0.5", m._mark_crawl("  种子局详情: 21094 局", st0) == 0.5,
   m._mark_crawl("  种子局详情: 21094 局", st0))
ck("保存行 → 1.0", m._mark_crawl("✓ 保存 D:\\x: 1 局 / 2 玩家 / 主人 3 局", st0) == 1.0)

print("== ④ 参数校验与锚点自动跟随（_anchor_argv）==")
none_st = {}
ck("空锚点 → 不带参数", m._anchor_argv("", 0, none_st) == [])
ck("锚点非数字 → None", m._anchor_argv("abc", 0, none_st) is None)
ck("局数非整数 → None", m._anchor_argv("1928", "x", none_st) is None)
ck("没有已记录的锚点局 → 退回按局数",
   m._anchor_argv("1928", 3, none_st) ==
   ["--anchor", "1928.0", "--anchor-games-ago", "3"],
   m._anchor_argv("1928", 3, none_st))
ck("负数局数夹到 0",
   m._anchor_argv("1928", -5, none_st) ==
   ["--anchor", "1928.0", "--anchor-games-ago", "0"],
   m._anchor_argv("1928", -5, none_st))

same = {"anchor_game_id": 10000000001, "anchor_score": 1928.0}
ck("分数没变 → 带 gameId（自动跟随，用户不用填）",
   m._anchor_argv("1928", 3, same) ==
   ["--anchor", "1928.0", "--anchor-game-id", "10000000001"],
   m._anchor_argv("1928", 3, same))
ck("分数只差 0.4 → 仍算同一张截图",
   "--anchor-game-id" in m._anchor_argv("1928.4", 3, same),
   m._anchor_argv("1928.4", 3, same))
ck("分数变了（换了新截图）→ 按用户填的局数",
   m._anchor_argv("1950", 0, same) ==
   ["--anchor", "1950.0", "--anchor-games-ago", "0"],
   m._anchor_argv("1950", 0, same))
ck("有 gameId 但没记分数 → 不冒险，退回局数",
   m._anchor_argv("1928", 2, {"anchor_game_id": 5}) ==
   ["--anchor", "1928.0", "--anchor-games-ago", "2"],
   m._anchor_argv("1928", 2, {"anchor_game_id": 5}))

print("== ⑤ 百分比单调、且封顶 100% ==")
now = time.time()
plan = [{"name": "A", "secs": 100}, {"name": "B", "secs": 100}]
m._job_set(running=True, finished=False, ok=None, error=None,
           started=now - 50, plan=plan, step=1, step_total=2, step_name="A",
           step_frac=0.0, step_secs=100, step_started=now - 50,
           step_marked=False, step_has_marker=True, log=[], steps=[], used_ago=0)
p1 = m._job_progress()
m._job_set(step_frac=0.5, step_marked=True)
p2 = m._job_progress()
m._job_set(step=2, step_frac=0.0, step_marked=False, step_started=now)
p3 = m._job_progress()
m._job_set(step_frac=1.0, step_marked=True)
p4 = m._job_progress()
fracs = [p1["frac"], p2["frac"], p3["frac"], p4["frac"]]
ck("百分比递增（不回退）", fracs == sorted(fracs), fracs)
ck("全部落在 0~1", all(0.0 <= f <= 1.0 for f in fracs), fracs)
ck("最后一步跑完 → 100%", p4["frac"] == 1.0, p4["frac"])
ck("有标记的步骤、标记没到 → 不许拿估时抢跑（停 0）", p1["frac"] == 0.0, p1["frac"])

print("== ⑤a 回归：估时抢跑会把进度条拽回去 ==")
# 2026-09-21 实测：第 1 步改走 SGP 后单次请求 2.5s、估时 3.8s，
# 估时在「种子局详情」（标记值 0.5）到达前先冲到了 0.9，
# 于是总进度从 0.0167 **倒退**到 0.0093。
m._job_set(running=True, finished=False, started=now - 4,
           plan=[{"name": "抓取最新对局", "secs": 3.8}, {"name": "B", "secs": 200}],
           step=1, step_total=2, step_name="抓取最新对局",
           step_frac=0.0, step_secs=3.8, step_started=now - 4,
           step_marked=False, step_has_marker=True)
pre = m._job_progress()
ck("估时已跑满（4s/3.8s）也不许抢跑，仍然是 0", pre["frac"] == 0.0, pre["frac"])
m._job_set(step_frac=0.5, step_marked=True)      # 「种子局详情」
post = m._job_progress()
ck("标记 0.5 一到 → 只增不减（不回退）", post["frac"] >= pre["frac"],
   "%s -> %s" % (pre["frac"], post["frac"]))
ck("且真的动了", post["frac"] > 0, post["frac"])

print("== ⑤b 有真实标记时，估时不许盖过标记 ==")
# 回归：曾经写成 cur = max(标记, min(0.9, 已用/估时))。
# 补抓实测 9.9s ~ 464s，估时一旦取小值，那项立刻冲到 0.9，
# 于是「脚本说才 5%」被演成「快 90% 了」—— 正好和封顶 0.9 的初衷相反。
m._job_set(step=1, step_frac=0.05, step_marked=True,
           step_secs=10, step_started=now - 500, started=now - 500,
           plan=[{"name": "A", "secs": 10}, {"name": "B", "secs": 100}])
p = m._job_progress()
ck("脚本说 5% 就是 5%（估时已严重超时也不许抬高）",
   abs(p["frac"] - (10 * 0.05) / 110) < 1e-4, p["frac"])
ck("超时也不许假报 90%", p["frac"] < 0.1, p["frac"])

# 反过来：**没有标记解析器**的步骤才允许按估时推进（并封顶 0.9）
m._job_set(step_frac=0.0, step_marked=False, step_has_marker=False,
           step_secs=10, step_started=now - 500)
p = m._job_progress()
ck("无标记解析器 + 严重超时 → 封顶在 0.9 对应的总进度",
   abs(p["frac"] - (10 * 0.9) / 110) < 1e-4, p["frac"])

print("== ⑥ 剩余时间合理 ==")
m._job_set(step=1, step_frac=0.5, step_started=now - 50, started=now - 50)
p = m._job_progress()
ck("有 ETA", p["eta"] is not None, p["eta"])
ck("ETA 非负", p["eta"] >= 0, p["eta"])
ck("ETA 是数字", isinstance(p["eta"], (int, float)), type(p["eta"]))
ck("已用时间 > 0", p["elapsed"] > 0, p["elapsed"])

# 同样进度、更久用时 → ETA 应更大
m._job_set(step=1, step_frac=0.5, step_started=now - 200, started=now - 200)
q = m._job_progress()
ck("用时翻倍 → 剩余时间也变大（按速率外推）", q["eta"] > p["eta"],
   "%s vs %s" % (q["eta"], p["eta"]))

print("== ⑥b 平台期：剩余时间要跟着真实剩余 1:1 往下走 ==")
# 场景（2026-09-22 主人实机反馈的那次）：估算步刚进 [3/4]，
# 接下来是 89 秒的谱分解，中间**一个标记都没有**。
# 旧公式 eta = elapsed/frac*(1-frac) 在这段时间里只以 (1-frac) 的速率慢慢爬，
# 于是整体 89% 时显示「剩余约 20 秒」，实际还要 90 秒。
# 新口径：当前步剩余 = 计划剩余 − 已落后计划的量 → 等待多久就减多少。
plan_est = [{"name": "估算隐藏分", "secs": 107.4}]
m._job_set(running=True, finished=False, ok=None, error=None,
           plan=plan_est, step=1, step_total=1, step_name="估算隐藏分",
           step_frac=m._EST_STAGE_W[3], step_marked=True, step_has_marker=True,
           step_secs=107.4, log=[], steps=[], used_ago=0)
etas = {}
for _dt, _tag in ((6.0, "刚进谱分解"), (45.0, "等了 45 秒"), (88.0, "等了 88 秒")):
    m._job_set(step_started=now - _dt, started=now - _dt)
    etas[_tag] = m._job_progress()["eta"]
e1, e2, e3 = etas["刚进谱分解"], etas["等了 45 秒"], etas["等了 88 秒"]
# 期望的下降量按公式算，**不写死具体数字** —— 权重会随实测更新（0.09→0.057 就踩过），
# 而这里要守的不变量是「平台期按 1:1 递减」这件事本身。
# 落后量 lag(t) = max(0, t − cur×估时)；从 6s 等到 45s，ETA 应正好降 lag(45)−lag(6)。
_lag = lambda t: max(0.0, t - m._EST_STAGE_W[3] * 107.4)   # noqa: E731
_expect_drop = _lag(45.0) - _lag(6.0)
ck("刚进谱分解就报 ~98 秒（不再假报「只剩几十秒」）", e1 > 60, e1)
ck("平台期里 ETA 持续下降（不冻结）", e2 < e1 and e3 < e2,
   "%s → %s → %s" % (e1, e2, e3))
ck("下降幅度 ≈ 真实等待量（1:1，不是 12%）",
   abs((e1 - e2) - _expect_drop) < 2.0, "%s vs 期望 %s" % (e1 - e2, _expect_drop))
ck("等到快结束时 ETA 已经很小（不是还在报 90 秒）", e3 < 30, e3)
ck("全程不假报 0 秒", min(e1, e2, e3) > 0, (e1, e2, e3))

print("== ⑥c 整步严重超时：不许归零，按超出量保守爬升 ==")
# 估时 10s 的步骤跑了 60s（补抓实测 9.9s ~ 464s 就是这种量级差）——
# 这时「计划剩余」是负的，绝不能钳成 0 然后一直显示「马上就好」。
m._job_set(plan=[{"name": "A", "secs": 10.0}], step=1, step_total=1,
           step_name="A", step_frac=0.0, step_marked=False,
           step_has_marker=True, step_secs=10.0,
           step_started=now - 60, started=now - 60)
p = m._job_progress()
ck("超时 6 倍也不报 0 秒", p["eta"] > 0, p["eta"])
ck("按超出量的一半爬升（保守但不假）", p["eta"] >= 20.0, p["eta"])

print("== ⑥d 剩余时间要包含后面步骤的估时 ==")
m._job_set(plan=[{"name": "A", "secs": 10.0}, {"name": "B", "secs": 50.0}],
           step=1, step_total=2, step_name="A", step_frac=0.5, step_marked=True,
           step_has_marker=True, step_secs=10.0,
           step_started=now - 5, started=now - 5)
p = m._job_progress()
ck("当前步剩 5s + 后续步 50s → ETA > 50", p["eta"] > 50.0, p["eta"])

print("== ⑦ 完成态 ==")
m._job_set(running=False, finished=True, ok=True, step_frac=1.0)
p = m._job_progress()
ck("完成时 100%", p["frac"] == 1.0, p["frac"])
ck("完成时 ETA = 0", p["eta"] == 0, p["eta"])
ck("running=False / finished=True", p["running"] is False and p["finished"] is True)

print("== ⑧ 空闲态（还没跑过任何任务）==")
m._job_set(running=False, finished=False, started=0.0, plan=[], step=0,
           step_total=0, step_name="", step_frac=0.0, step_secs=0.0,
           step_started=0.0, log=[], steps=[], ok=None, error=None)
p = m._job_progress()
ck("空闲时 frac=0", p["frac"] == 0.0, p["frac"])
ck("空闲时 eta=None", p["eta"] is None, p["eta"])
ck("空闲时不报错", p["running"] is False and p["finished"] is False)

print("== ⑨ 并发保护：已有任务时拒绝再启动 ==")
m._job_set(running=True, finished=False, plan=plan, step=1, step_total=2,
           step_started=now, started=now, step_frac=0.0, step_secs=100,
           log=[], steps=[], ok=None, error=None)
r = m._start_job("rebuild", None, 0, 300)
ck("第二个任务被拒", r["ok"] is False, r)
ck("拒绝原因说清楚了", "已经有一个任务在跑" in (r.get("msg") or ""), r.get("msg"))
m._job_set(running=False, finished=False, plan=[], step=0, step_total=0)

print("== ⑭ 抓取前预检查游戏客户端（2026-09-22 新增）==")
# 客户端没开时原来的表现是「没有全部成功」+ 一堆看不懂的报错，
# 用户根本不知道要先去登录游戏（主人原话：「游戏没登录就写明先登录再点抓取」）。
# 这里把探测打桩成「没开」，只验拒绝逻辑本身 —— 不打桩就会真的去探测/起任务。
_ready_old = m._lcu_ready
m._lcu_ready = lambda: False
try:
    r = m._start_job("update", None, 0, 300)
finally:
    m._lcu_ready = _ready_old
ck("客户端没开 → 拒绝启动", r.get("ok") is False, r)
ck("带 need_login 标记（前端据此弹提示层）", r.get("need_login") is True, r)
ck("文案写明「请先登录游戏」", "登录游戏" in (r.get("msg") or ""), r.get("msg"))
ck("拒绝时没留下运行中的任务", m._JOB["running"] is False, m._JOB["running"])
ck("抓取类任务的第一步确实是 _hd_crawl.py（所以要预检查）",
   any(s["script"] == "_hd_crawl.py" for s in m._stages_for("update", 0, [])))
ck("recalc 不碰客户端（只有估算 + 看板）",
   all(s["script"] not in ("_hd_crawl.py", "_stats_crawl.py")
       for s in m._stages_for("recalc", 0, [])))

print("== ⑮ 数据主人存档：一个账号一份 / 秒切 / 软删（2026-09-22 新增）==")
# ⚠️ 全程在**临时目录**上跑：把 DATA_DIR / OWNER_SAVE_DIR / ELO_BASE_JSON / ROOT
#    都指过去，跑完复原。绝不碰真实的 02_数据\ 与 _stale\。
_SAVE_ROOT = Path(tempfile.mkdtemp(prefix="hd_saves_test_"))
_D = _SAVE_ROOT / "02_数据"
_D.mkdir(parents=True, exist_ok=True)
_old_paths = (m.DATA_DIR, m.OWNER_SAVE_DIR, m.ELO_BASE_JSON, m.ROOT)
m.DATA_DIR = _D
m.OWNER_SAVE_DIR = _D / "_owners"
m.ELO_BASE_JSON = m.OWNER_SAVE_DIR / "_player_elo_base.json"
m.ROOT = _SAVE_ROOT
try:
    (_D / "hd_data.json").write_text(json.dumps({
        "meta": {"puuid": "PU-A", "summoner": "A#1", "n_my_games": 2, "n_games": 3},
        "my_game_ids": [1, 2],
        "games": [{"gameId": i, "players": [{"puuid": "PU-A"}, {"puuid": "PU-B"}]}
                  for i in (1, 2, 3)],
    }, ensure_ascii=False), encoding="utf-8")
    for _fn in m.OWNER_SAVE_FILES:
        (_D / _fn).write_text('{"x": 1}', encoding="utf-8")
    # offset 是从 player_elo_meta.json 读的（_stats_crawl.py 落的盘），必须给真值
    (_D / "player_elo_meta.json").write_text(
        json.dumps({"offset": 100.0, "owner_puuid": "PU-A"}), encoding="utf-8")
    (_D / "player_elo.json").write_text(json.dumps({
        "_meta": {"offset": 100.0},
        "PU-B": {"mmr": 1500.0, "n_games": 7},
        "PU-C": {"mmr": 1600.5, "n_games": 3},
    }), encoding="utf-8")

    _r = m._archive_owner("PU-A", "A#1")
    ck("存档成功且带名字", _r.get("ok") and _r["saved"]["name"] == "A#1", _r)
    ck("存档里记了 elo offset（方案 D 的关键字段）",
       _r["saved"].get("elo_offset") == 100.0, _r["saved"].get("elo_offset"))
    ck("共享底表已生成（全量表只存这一份）", m.ELO_BASE_JSON.is_file())
    m._archive_owner("PU-A", "A#1")
    ck("一个账号只留一份（重复存档=覆盖，不堆积）",
       len([x for x in m.OWNER_SAVE_DIR.iterdir() if x.is_dir()]) == 1,
       [x.name for x in m.OWNER_SAVE_DIR.iterdir()])
    ck("列表读得到，且标出「当前生效」",
       m._list_owner_saves()["saves"][0]["puuid"] == "PU-A" and
       m._list_owner_saves()["saves"][0]["current"] is True)

    m._materialize_elo("PU-A", 130.0)
    _pe = json.loads((_D / "player_elo.json").read_text(encoding="utf-8"))
    ck("物化评分表：按 offset 差值整体平移（1500 → 1530）",
       _pe["PU-B"]["mmr"] == 1530.0, _pe["PU-B"])
    ck("物化评分表：一位小数照搬（1600.5 → 1630.5）", _pe["PU-C"]["mmr"] == 1630.5, _pe["PU-C"])
    ck("物化评分表：n_games 保留", _pe["PU-B"]["n_games"] == 7, _pe["PU-B"])

    _mr = m._set_owner_meta("PU-B", "B#2")
    ck("meta 能切到另一个人", _mr.get("ok"), _mr)
    _hd = json.loads((_D / "hd_data.json").read_text(encoding="utf-8"))
    ck("顶层 my_game_ids 换成 TA 的（_stats_crawl 读这份）",
       _hd["my_game_ids"] == [1, 2, 3], _hd["my_game_ids"])
    ck("meta.my_game_ids 也要写（估算脚本读这份）",
       _hd["meta"]["my_game_ids"] == [1, 2, 3], _hd["meta"].get("my_game_ids"))
    ck("记下上一个主人（网络是并集，可换回）",
       _hd["meta"].get("prev_puuid") == "PU-A", _hd["meta"].get("prev_puuid"))
    ck("查无此人 → 拒绝切（不写坏 meta）",
       m._set_owner_meta("PU-ZZZ", "x").get("ok") is False)

    _dr = m._drop_owner_save("PU-A")
    ck("删除是**软删**：挪进 _stale 而不是硬删", _dr.get("ok") and (_SAVE_ROOT / "_stale").is_dir(), _dr)
    ck("存档目录里已经没有它了", not (m.OWNER_SAVE_DIR / "PU-A").exists())
finally:
    m.DATA_DIR, m.OWNER_SAVE_DIR, m.ELO_BASE_JSON, m.ROOT = _old_paths

print("== ⑯ meta 缓存：命中要快、文件一改要失效（2026-09-22）==")
# 只验**缓存语义**，不断言具体耗时（机器快慢不同）。
# 真实 hd_data.json 46 MB，解析一次约 1~2 秒 —— 这就是「管理存档读列表要 2 秒」的根因。
m._META_CACHE.update({"mtime": None, "size": None, "meta": {}})
_v1 = m._hd_meta()
ck("第一次读得到 meta", isinstance(_v1, dict) and bool(_v1), _v1)
ck("缓存记录了 mtime/size（判定失效用）",
   m._META_CACHE["mtime"] is not None and m._META_CACHE["size"] is not None)
ck("缓存命中：再次调用内容一致", m._hd_meta() == _v1)
m._META_CACHE["mtime"] = -1          # 伪造「文件被改过了」
_v3 = m._hd_meta()
ck("mtime 变了 → 重新读，不返回旧的（防脏读）", _v3 == _v1, _v3)
ck("重读后刷新了缓存 key", m._META_CACHE["mtime"] != -1)
ck("_current_owner_puuid() 走快路径，结果与 hd_data 的 meta 一致",
   m._current_owner_puuid() == (_v1.get("puuid") or "").strip(),
   (m._current_owner_puuid(), _v1.get("puuid")))

print("== ⑩ _LineTee 按行回调 ==")
got = []
tee = m._LineTee(got.append)
tee.write("第一行\n第二")
tee.write("行\n\n第三行无换行")
ck("完整行都回调了", got == ["第一行", "第二行", ""], got)
tee.write("\n")
ck("补上换行后回调第三行", got[-1] == "第三行无换行", got)
ck("全文都留着", "第一行" in tee.buf.getvalue() and "第三行无换行" in tee.buf.getvalue())
ck("\\r 被吃掉（进度条式输出不会带乱码）",
   (lambda t: (t.write("a\r\n"), t.buf.getvalue())[1])(m._LineTee(None)).startswith("a"),
   "")

print("== ⑪ 锚点记忆：按 gameId，不是数局数 ==")
# ⚠️ 必须把 state 文件指到临时目录 —— 否则会往真实的 _desktop_state.json
# 写进假数据，下次真跑时锚点被带偏，算出来的分是错的。
# 测试污染真实状态是最阴的一类 bug。
# （json / pathlib / tempfile 已在文件顶部导入）
_REAL_STATE = m._STATE_FILE
# 真实 state 文件本来就该存在（桌面版跑成功后自己会写）。
# 所以断言的是「测试没改动它」，而不是「它不存在」——
# 早先写成 not exists() 是错的，一旦手工播种过就会误报。
_REAL_STATE_SNAP = _REAL_STATE.read_bytes() if _REAL_STATE.exists() else None
m._STATE_FILE = pathlib.Path(tempfile.gettempdir()) / "_test_desktop_state.json"
for _f in (m._STATE_FILE, m._state_bak()):
    try:
        _f.unlink()
    except OSError:
        pass

ck("没有 state 文件时 _load_state 返回空 dict", m._load_state() == {}, m._load_state())
m._save_state(anchor_game_id=123)
ck("存得进、读得出", m._load_state().get("anchor_game_id") == 123, m._load_state())
m._save_state(anchor_score=1928.0)
ck("是增量更新，不覆盖已有的键",
   m._load_state().get("anchor_game_id") == 123 and
   m._load_state().get("anchor_score") == 1928.0, m._load_state())
ck("原子写：没有留下 .tmp 残file",
   not m._STATE_FILE.with_name(m._STATE_FILE.name + ".tmp").exists())
ck("留了上一代备份", m._state_bak().exists(), m._state_bak())

# 主文件被写坏（0 字节 / 垃圾）时，要能退回备份 ——
# 锚点是唯一没法自动获取的信息，丢了会静默算错分。
_good = m._load_state()
m._STATE_FILE.write_text("", encoding="utf-8")
ck("主文件 0 字节 → 退回备份", m._load_state().get("anchor_game_id") is not None,
   m._load_state())
m._STATE_FILE.write_text("{ 这不是 json", encoding="utf-8")
ck("主文件是垃圾 → 退回备份", isinstance(m._load_state(), dict), m._load_state())
m._save_state(note="x")          # 写回正常内容
ck("写回后主文件可读", m._load_state().get("note") == "x", m._load_state())

# _remember_anchor：从估算结果里把 gameId 捡出来
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / m.ESTIMATE_NAME).write_text(json.dumps({
    "anchor": {"anchor_score": 1930.0, "anchor_games_ago": 41,
               "anchor_game_id": 999, "anchor_game_time": "09-06 11:21"},
}, ensure_ascii=False), encoding="utf-8")
_real_dir = m.DATA_DIR
m.DATA_DIR = tmp
m._remember_anchor()
m.DATA_DIR = _real_dir
st = m._load_state()
ck("_remember_anchor 记下了 gameId", st.get("anchor_game_id") == 999, st)
ck("_remember_anchor 记下了分数", st.get("anchor_score") == 1930.0, st)
ck("_remember_anchor 记下了实际局数", st.get("anchor_games_ago") == 41, st)

# 估算结果里没有 gameId（老版本产物）→ 不该写脏 state
(tmp / m.ESTIMATE_NAME).write_text(json.dumps({
    "anchor": {"anchor_score": 1930.0, "anchor_games_ago": 0},
}, ensure_ascii=False), encoding="utf-8")
before = dict(st)
m.DATA_DIR = tmp
m._remember_anchor()
m.DATA_DIR = _real_dir
ck("老产物没有 gameId → state 保持不变", m._load_state() == before, m._load_state())

# 估算结果文件不存在 → 不该崩
m.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
m._remember_anchor()
m.DATA_DIR = _real_dir
ck("估算结果缺失时不抛异常", True)

m._STATE_FILE = _REAL_STATE
try:
    (pathlib.Path(tempfile.gettempdir()) / "_test_desktop_state.json").unlink()
except OSError:
    pass

_now_snap = _REAL_STATE.read_bytes() if _REAL_STATE.exists() else None
ck("真实 state 文件没被测试改动", _now_snap == _REAL_STATE_SNAP,
   "%s（测试前 %s 字节，测试后 %s 字节）" % (
       _REAL_STATE,
       "无" if _REAL_STATE_SNAP is None else len(_REAL_STATE_SNAP),
       "无" if _now_snap is None else len(_now_snap)))

# ⚠️ 备份也要一并守（2026-09-22 补）。
# 原来只断言了主文件没被改动 —— 而实测发现**备份曾经被单独搞成 0 字节**
# 而主文件完好，于是这条断言照样绿、问题溜了两轮没人发现。
# 教训：守卫一个文件对时，**两个都要快照**，别只盯主文件。
_REAL_BAK = m._state_bak()
_REAL_BAK_SNAP = _REAL_BAK.read_bytes() if _REAL_BAK.exists() else None
_now_bak = _REAL_BAK.read_bytes() if _REAL_BAK.exists() else None
ck("真实 state 备份没被测试改动", _now_bak == _REAL_BAK_SNAP,
   "%s（测试前 %s 字节，测试后 %s 字节）" % (
       _REAL_BAK,
       "无" if _REAL_BAK_SNAP is None else len(_REAL_BAK_SNAP),
       "无" if _now_bak is None else len(_now_bak)))
if _REAL_BAK.exists():
    ck("真实 state 备份不是 0 字节空壳（0 字节＝锚点兜底失效）",
       _REAL_BAK.stat().st_size > 0, "%d 字节" % _REAL_BAK.stat().st_size)

print("== ⑫ 任务编排（_run_job）：用假脚本验流程本身 ==")
# 为什么值得单独测：真脚本跑一轮要十几分钟，而流程本身的 bug
# （步骤顺序、致命步中止、锚点记忆触发条件）根本不需要真网络就能抓出来。
# 这一节就是为了钉住「update 也会记锚点」——它曾经被写成 kind in ("crawl","recalc")，
# 把最常用的「抓新局并重算」漏掉了。
_calls = []


def _fake_run(name, argv, on_line=None):
    _calls.append((name, [str(a) for a in argv]))
    lines = {
        "_hd_crawl.py": ["[R1] 待扩展 300 人 (已抓列表 0) ...",
                         "  种子局详情: 1 局",
                         "✓ 保存 D:\\x\\hd_data.json: 1 局 / 2 玩家 / 主人 3 局"],
        "_hd_backfill.py": ["载入 1 局 / 主人 1 局", "待补抓对手: 4 人",
                            "  [列表] 2/4 人", "列表 4 人 -> 新局 3 (1.0s)",
                            "  [详情] 3/3 局", "详情 +3 局, 累计 4 局 / 主人 2 局",
                            "✓ 保存 D:\\x\\hd_data.json: 4 局 / 5 玩家 / 主人 2 局"],
        "_player_elo_build.py": ["加载 4 局 / 5 玩家", "  [Elo] 4/4 局",
                                 "  ✓ 输出 5 人 → _player_elo.json 1 KB"],
        "_stats_crawl.py": ["待抓 4 局", "  [Stats] 2/4 局", "  [Stats] 4/4 局",
                            "耗时 1.0s, 成功 4/4",
                            "✓ 输出 4 局 → my_stats.json（含治疗占比）"],
        "海斗隐藏分估算_v2.py": ["[1/4] 全局 BT 拟合 ...", "[2/4] 在线 Elo 轨迹 ...",
                                "[3/4] 谱分解 ...", "[4/4] 汇总 ...",
                                "  详细数据 -> D:\\x\\est.json"],
        "_dash_rebuild.py": ["[load] hd 4 局", "✓ 聚合完成: composite 1/2/3",
                             "✓ 看板数据已更新: D:\\x.html  1 KB"],
    }.get(name, [])
    for ln in lines:
        if on_line:
            on_line(ln)
    return 0, "fake " + name


_real_run_script = m._run_script
_real_load_state = m._load_state
_real_remember = m._remember_anchor
_real_save_state = m._save_state
# 这一段会真的调 _save_state（记实测耗时），必须把状态文件重定向到临时目录，
# 否则会把真实的 _desktop_state.json 写脏 —— 之前就踩过这个坑。
_real_state_file = m._STATE_FILE
_tmp_state = pathlib.Path(tempfile.mkdtemp()) / "_desktop_state.json"
m._STATE_FILE = _tmp_state

# ⚠️⚠️ 只改 `_STATE_FILE` **不够**（2026-09-22 实测被咬）。
#
# `_run_job` 跑在**后台 daemon 线程**里，所以存在这个竞态：
#   · `_run_job` 收尾时是「先宣布任务结束、再 `_save_state`」的（现已改成反序，但
#     测试不该依赖实现顺序）；
#   · 于是 `while m._job_progress()["running"]: sleep()` 一退出，主线程立刻往下走、
#     恢复真实 `_STATE_FILE`，而那次 `_save_state` 可能才刚读到内容、还没写；
#   · 结果：本该落在临时目录的备份**落到了真实 `02_数据\`**；更糟的是测试最后
#     `sys.exit()` 会**强杀 daemon 线程**，于是常停在「已写 `.bak.tmp`、未 `os.replace`」
#     的中间态 —— 实测在真实数据目录留下过 `_desktop_state.bak.json.tmp`。
#
# 所以这里不只改模块变量，而是**把写入目标钉死**：整个调用期间 `_STATE_FILE`
# 都指向临时目录，谁先谁后都不会漏出去。
def _pinned_save_state(**kw):
    real, m._STATE_FILE = m._STATE_FILE, _tmp_state
    try:
        return _real_save_state(**kw)
    finally:
        m._STATE_FILE = real


m._save_state = _pinned_save_state

# ⚠️ 任务日志也要重定向（2026-09-22 补）。
# `_run_job` 收尾时会 `_write_job_log()` 写 `02_数据\_last_job.log` ——
# 不重定向的话，跑一次测试就往真实数据目录里丢一个文件，
# 下面那条「真实数据目录不许多出新文件」的断言就会**第一次跑失败、第二次跑通过**
# （因为第二次基线里已经有它了）—— 典型的"有状态假绿/假红"，比不测还坏。
_real_job_log = m._JOB_LOG_FILE
m._JOB_LOG_FILE = pathlib.Path(tempfile.mkdtemp()) / "_last_job.log"

# ⚠️ 自动建档（2026-09-22 新增）同样要重定向：假流水线跑完会调 `_archive_owner()`，
# 那是**真写真拷**的（还会把 16.8 MB 的评分表复制一份）—— 不钉死的话，
# 真实的 `02_数据\` 里会多出一个 `_owners\`，最后那条「不许多出新文件」的断言就红。
# 顺带把 `_hd_meta` 打桩，免得为了一个 meta 去解析 46 MB 的 hd_data。
# ⚠️ 客户端预检查（2026-09-22 新增）也会拦住假任务：没开游戏时 `_start_job("update")`
#    会直接返回 need_login —— 行为是对的，但测试环境没有客户端，必须打桩放行。
_real_lcu_ready = m._lcu_ready
m._lcu_ready = lambda: True

_real_save_paths = (m.OWNER_SAVE_DIR, m.ELO_BASE_JSON, m._hd_meta)
_tmp_owners = pathlib.Path(tempfile.mkdtemp(prefix="hd_owners_test_"))
m.OWNER_SAVE_DIR = _tmp_owners
m.ELO_BASE_JSON = _tmp_owners / "_player_elo_base.json"
m._hd_meta = lambda: {"puuid": "PU-TEST", "summoner": "T#1",
                      "n_my_games": 1, "n_games": 4}

# 顺便留个基线：这一段跑完，真实数据目录里**不许多出任何文件**。
_REAL_DATA_DIR = m.DATA_DIR
_REAL_DIR_BEFORE = sorted(p.name for p in _REAL_DATA_DIR.iterdir()) \
    if _REAL_DATA_DIR.is_dir() else []
# 基线得真的是"真实数据目录"，否则后面那条断言会变成永远为真的空断言
ck("⑫ 段的基线目录确实是真实数据目录（含 hd_data.json）",
   (_REAL_DATA_DIR / "hd_data.json").is_file(), _REAL_DATA_DIR)

_remembered = []
m._run_script = _fake_run
m._load_state = lambda: {}          # 不读真实 state，测出来的结果才可复现
m._remember_anchor = lambda: _remembered.append(1)

r = m._start_job("update", 1928, 0, 300)
ck("假脚本任务启动成功", r.get("ok") is True, r)
_t0 = time.time()
while m._job_progress()["running"] and time.time() - _t0 < 30:
    time.sleep(0.05)
p = m._job_progress()
ck("假脚本跑完 → finished 且 ok", p["finished"] and p["ok"], (p["finished"], p["ok"]))
ck("六步全跑到了", len(p["steps"]) == 6, [s["name"] for s in p["steps"]])
ck("结束时 100%", abs(p["frac"] - 1.0) < 1e-9, p["frac"])
ck("锚点参数传给了估算步",
   _calls[4][1] == ["--anchor", "1928.0", "--anchor-games-ago", "0"], _calls[4][1])
ck("update 成功也会记锚点（回归：曾漏掉 update）",
   len(_remembered) == 1, _remembered)

# 实测耗时落盘 → 下次估时更准
_saved = json.loads(_tmp_state.read_text(encoding="utf-8")) if _tmp_state.exists() else {}
ck("实测耗时被记进 state（step_secs）",
   isinstance(_saved.get("step_secs"), dict) and len(_saved["step_secs"]) == 6,
   _saved.get("step_secs"))
ck("六步的耗时都记了",
   all(n in _saved.get("step_secs", {}) for n in
       ["抓取最新对局", "补抓新对手", "重建全量玩家评分",
        "抓本人对局明细", "估算隐藏分", "重建看板"]),
   _saved.get("step_secs"))

# 致命步失败 → 立刻中止，后面的步骤不跑
_calls.clear()
_remembered.clear()


def _fake_run_fail(name, argv, on_line=None):
    _calls.append((name, [str(a) for a in argv]))
    return 1, "boom"


m._run_script = _fake_run_fail
r = m._start_job("update", 1928, 0, 300)
ck("第二次任务能启动（锁没被上一次占住）", r.get("ok") is True, r)
_t0 = time.time()
while m._job_progress()["running"] and time.time() - _t0 < 30:
    time.sleep(0.05)
p = m._job_progress()
ck("致命步失败 → ok=False", p["finished"] and p["ok"] is False, p["ok"])
ck("致命步失败 → 只跑了 1 步就中止", len(p["steps"]) == 1, [s["name"] for s in p["steps"]])
ck("失败时不记锚点", _remembered == [], _remembered)
ck("失败有可读的错误信息", "失败" in " ".join(p["log"]), p["log"][-2:])

# ---- ⑬ 「停止」按钮：协作式取消 ----
print("\n== ⑬ 「■ 停止」按钮：标志文件 → 脚本安全退出 ==")
# 用户 2026-09-21 要求「抓取数据面板上增加停止按钮，让我可以中途停止」。
# 机制是**协作式**的：界面写一个 _cancel.flag，脚本在主循环安全点检查它、
# 抛 SystemExit(3) 退出。为什么不直接杀线程见 _paths.py 的注释
# （runpy 同进程跑，异步抛异常会落在 json.dump 中间 → 留半个文件）。
#
# 这一节不打桩真脚本，只钉编排层 + 机制本身：
#   ① 退出码 3 的约定、两边 CANCEL_FILE 同名（不同名 = 停止永远不生效）
#   ② 没任务时点停止 → 明确拒绝，不静默成功
#   ③ 跑着的时候点停止 → 标志落盘、stopping=True、幂等
#   ④ 脚本以 rc=3 退出 → stopped=True / ok=False / 后面步骤不再开 / 耗时不入库
#   ⑤ 新任务启动会清掉残留标志（否则新任务一启动就自杀）

# ① 约定与同名
_CF = m.CANCEL_FILE
_tmp_cf = pathlib.Path(tempfile.mkdtemp()) / "_cancel.flag"
m.CANCEL_FILE = _tmp_cf
ck("退出码 3 被定义为「用户主动停止」", m.RC_STOPPED == 3, m.RC_STOPPED)
ck("app.py 的停止标志文件名是 _cancel.flag", _CF.name == "_cancel.flag", _CF.name)
_pspec = importlib.util.spec_from_file_location("_paths_probe",
                                                str(ROOT / "01_脚本" / "_paths.py"))
_pmod = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(_pmod)
ck("_paths.CANCEL_FILE 与 app.py 的 CANCEL_FILE 同名（不同名 = 停止永远不生效）",
   _pmod.CANCEL_FILE.name == _CF.name, (_pmod.CANCEL_FILE.name, _CF.name))
ck("_paths.CANCEL_FILE 和 app.py 指向同一个目录",
   _pmod.CANCEL_FILE.parent == _CF.parent, (_pmod.CANCEL_FILE.parent, _CF.parent))

# 机制本身：cancel_requested / raise_if_cancelled / clear_cancel
_pmod.CANCEL_FILE = _tmp_cf
if _tmp_cf.exists():
    _tmp_cf.unlink()
ck("没有标志时 cancel_requested() = False", _pmod.cancel_requested() is False)
_tmp_cf.write_text("x", encoding="utf-8")
ck("写了标志后 cancel_requested() = True", _pmod.cancel_requested() is True)
try:
    _pmod.raise_if_cancelled("单测")
    ck("raise_if_cancelled 命中标志时抛 SystemExit", False, "没抛，停止会失效")
except SystemExit as _e:
    ck("raise_if_cancelled 抛的是 SystemExit(3)", _e.code == 3, _e.code)
_pmod.clear_cancel()
ck("clear_cancel 清掉标志", _pmod.cancel_requested() is False)

# 五个长跑脚本都必须真的有安全点 —— 漏一个，那个脚本的停止就是摆设。
# 这条断言的价值：以后新加长跑脚本时，忘了调 raise_if_cancelled 会直接红。
for _f in ("_hd_crawl.py", "_hd_backfill.py", "_player_elo_build.py",
           "_stats_crawl.py", "海斗隐藏分估算_v2.py"):
    _src = (ROOT / "01_脚本" / _f).read_text(encoding="utf-8")
    ck("%s 里有安全点（raise_if_cancelled）" % _f, "raise_if_cancelled" in _src)

# ② 没任务时点停止
_api = m.Api()
_st = _api.stop()
ck("没有任务在跑时点停止 → 明确返回失败（不静默成功）",
   _st.get("ok") is False and bool(_st.get("msg")), _st)

# ③④ 跑着的时候点停止
_gate = threading.Event()


def _fake_run_stop(name, argv, on_line=None):
    _calls.append(name)
    if len(_calls) == 1:
        _gate.wait(10)          # 卡住，模拟「这一小步还在跑，还没到安全点」
        return m.RC_STOPPED, "user stopped"
    return 0, "fake " + name


m._run_script = _fake_run_stop
m._load_state = lambda: {}
_calls.clear()
_remembered.clear()
if _tmp_cf.exists():
    _tmp_cf.unlink()
r = m._start_job("update", 1928, 0, 300)
ck("停止场景：任务启动成功", r.get("ok") is True, r)
_t0 = time.time()
while len(_calls) < 1 and time.time() - _t0 < 10:
    time.sleep(0.02)
ck("第一段已经在跑（假脚本被调用）", len(_calls) == 1, _calls)
_st = _api.stop()
ck("跑着的时候点停止 → ok", _st.get("ok") is True, _st)
ck("停止标志文件已落盘（脚本轮询的就是它）", _tmp_cf.exists(), _tmp_cf)
ck("进度快照里 stopping=True（界面显示「停止中 …」）",
   m._job_progress()["stopping"] is True, m._job_progress().get("stopping"))
ck("重复点停止 → 幂等（仍返回 ok，不会重复写）", _api.stop().get("ok") is True)
_gate.set()
_t0 = time.time()
while m._job_progress()["running"] and time.time() - _t0 < 30:
    time.sleep(0.02)
p = m._job_progress()
ck("脚本以 rc=3 退出 → stopped=True", p["stopped"] is True, p.get("stopped"))
ck("停止不算失败，但 ok=False（界面据 stopped 换文案）", p["ok"] is False, p.get("ok"))
ck("停止后后面的步骤不再开（只跑了 1 段）",
   len(p["steps"]) == 1, [s["name"] for s in p["steps"]])
ck("结束时 stopping 复位成 False", p["stopping"] is False, p.get("stopping"))
ck("停止时不记锚点", _remembered == [], _remembered)
_saved2 = (json.loads(_tmp_state.read_text(encoding="utf-8"))
           if _tmp_state.exists() else {})
ck("被停止的步骤耗时**不**记进 state（半截耗时会把下次估时算得离谱地短）",
   "抓取最新对局" not in (_saved2.get("step_secs") or {}),
   (_saved2.get("step_secs") or {}).get("抓取最新对局"))
ck("停止后日志里有可读的说明", any("停止" in x for x in p["log"]), p["log"][-3:])

# ⑤ 新任务启动必须清掉残留标志
_tmp_cf.write_text("stale", encoding="utf-8")
_calls.clear()
m._run_script = _fake_run
r = m._start_job("update", 1928, 0, 300)
ck("新任务启动会清掉上一次的停止标志（否则新任务一启动就自杀）",
   not _tmp_cf.exists(), _tmp_cf)
_t0 = time.time()
while m._job_progress()["running"] and time.time() - _t0 < 30:
    time.sleep(0.02)
ck("清掉标志后新任务能正常跑完（没被上一次的停止带偏）",
   m._job_progress()["finished"] and m._job_progress()["ok"] is True,
   m._job_progress().get("stopped"))

m._run_script = _real_run_script
m._load_state = _real_load_state
m._remember_anchor = _real_remember
m._save_state = _real_save_state
m._STATE_FILE = _real_state_file
m._JOB_LOG_FILE = _real_job_log
m.OWNER_SAVE_DIR, m.ELO_BASE_JSON, m._hd_meta = _real_save_paths
m._lcu_ready = _real_lcu_ready
m.CANCEL_FILE = _CF
if _tmp_cf.exists():
    _tmp_cf.unlink()
ck("真实 state 文件依然没被改动（⑫ 段也没写脏）",
   (_REAL_STATE.read_bytes() if _REAL_STATE.exists() else None) == _REAL_STATE_SNAP,
   _REAL_STATE)

# ⚠️ 真实数据目录里**不许多出任何文件或临时件**（2026-09-22 补，回归"⑫ 段写穿到真实目录"）。
# 起因：`_run_job` 在 daemon 线程里收尾时，主线程可能已经恢复真实 `_STATE_FILE`，
# 于是本该落在临时目录的备份写穿到真实 `02_数据\`，留下 `_desktop_state.bak.json.tmp`。
# 光看 state 文件本身看不出来（主文件没变），必须**比对整个目录的清单**。
if _REAL_DATA_DIR.is_dir():
    _REAL_DIR_AFTER = sorted(p.name for p in _REAL_DATA_DIR.iterdir())
    _new = [n for n in _REAL_DIR_AFTER if n not in _REAL_DIR_BEFORE]
    ck("真实数据目录没被测试写入新文件（含 .tmp 残留）", not _new, _new)

finish()
