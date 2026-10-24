# -*- coding: utf-8 -*-
"""在**临时副本**上端到端验证「换数据主人（--me）」，绝不碰真实数据。

验证链：
  ① `_hd_crawl.py --me <puuid> --seed --players 40` → meta 换成 TA、my_game_ids 全是 TA 的局、
     网络是并集（旧局不丢、原主人仍能找到自己的局）、BFS 让网络长大
  ② `海斗隐藏分估算_v2.py --data <副本> --auto-anchor` → 给出 TA 的隐藏分，
     anchor_source == "auto"、owner_puuid == TA、offset 复用「截图锚定」那套（不是 player_elo 的）
  ③ 反向：再 --me <原主人> 换回来，meta 恢复（证明可逆）
  ④ 真实数据一个字都没被改

需要游戏客户端在线（SGP 通道）。只读真实数据 + 只写临时目录。
"""
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
# 路径按本文件位置推，别写死绝对路径 —— 换盘/换目录就跑不起来了
# （与 test_progress.py / test_exe_live.py 同一惯例；2026-10-03 修，
#  原先这里是写死的 <包根>）
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "01_脚本"
DATA = ROOT / "02_数据"
sys.path.insert(0, str(SCRIPTS))

TMP = Path(tempfile.mkdtemp(prefix="hd_owner_test_"))
REAL = DATA / "hd_data.json"
src = json.loads(REAL.read_text(encoding="utf-8"))
meta0 = src["meta"]
me0, name0 = meta0["puuid"], meta0["summoner"]
n0, my0 = meta0["n_games"], len(src["my_game_ids"])
print("临时目录:", TMP)
print(f"真实 hd_data: {REAL.stat().st_size/1024/1024:.1f} MB")
print(f"原数据主人: {name0}  {me0[:8]}…  {n0} 局 / 我的 {my0} 局")

db = json.loads((DATA / "海斗dashboard_data.json").read_text(encoding="utf-8"))
vp = db.get("view_players") or []
tgt = vp[0]
tpu, tnm = tgt["pu"], tgt["n"]
print(f"目标新主人: {tnm}  {tpu[:8]}…（同场 {tgt['games']} 局）")

dst = TMP / "hd_data.json"
shutil.copyfile(REAL, dst)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _check import check as ck, finish   # 2026-10-03 unified assert helper (was ck)
def load_mod(fname, alias):
    spec = importlib.util.spec_from_file_location(alias, str(SCRIPTS / fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_crawl(puuid, name, players):
    """跑一次 _hd_crawl.main()，OUT 打到副本上。"""
    mod = load_mod("_hd_crawl.py", "hc_%d" % (int(time.time() * 1000) % 10**8))
    mod.OUT = dst
    old_argv = sys.argv
    sys.argv = ["_hd_crawl.py", "--seed", "--players", str(players), "--games", "10"]
    if puuid:
        sys.argv += ["--me", puuid]
    if name:
        sys.argv += ["--me-name", name]
    buf = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(buf):
            mod.main()
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        sys.argv = old_argv
    return rc, buf.getvalue()


def run_estimate():
    """在副本上跑估算（输出也写副本）。"""
    mod = load_mod("海斗隐藏分估算_v2.py", "est_%d" % (int(time.time() * 1000) % 10**8))
    mod.DATA_DIR = TMP
    # 刻意**不打桩** ESTIMATE_FILE / STATE_FILE / ELO_META_FILE：
    # auto_offset() 就该按真实情况去读「上一次截图锚定的 estimating 结果 / 桌面版状态」，
    # 打桩反而会把 offset 来源测错（第一次跑就踩到了这个坑）。
    old_argv = sys.argv
    sys.argv = ["est", "--data", str(dst), "--auto-anchor", "--show", "3"]
    buf = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(buf):
            mod.main()
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        sys.argv = old_argv
    return rc, buf.getvalue()


print("\n=== ① 换到新主人（--me + BFS 40 人）===")
t0 = time.time()
rc, out = run_crawl(tpu, tnm, 40)
print("  crawl rc=%s  耗时 %.1fs" % (rc, time.time() - t0))
for ln in out.strip().splitlines()[-7:]:
    print("    |", ln)
ck("crawl 退出码 = 0", rc == 0, rc)

d1 = json.loads(dst.read_text(encoding="utf-8"))
m1 = d1["meta"]
games1 = {g["gameId"]: g for g in d1["games"]}
my1 = set(d1["my_game_ids"])
ck("meta.puuid 换成了新主人", m1["puuid"] == tpu, m1["puuid"][:12])
ck("meta.summoner 是新主人的完整 Riot ID", m1["summoner"] == tnm, m1["summoner"])
ck("meta 记了 prev_puuid（换回去用）", m1.get("prev_puuid") == me0, m1.get("prev_puuid"))
ck("meta 记了 owner_switched_at", bool(m1.get("owner_switched_at")))
ck("my_game_ids 非空", len(my1) > 0, len(my1))
ck("my_game_ids 每一局都真的含新主人",
   all(any(p["puuid"] == tpu for p in games1[g]["players"]) for g in my1))
ck("网络是并集：旧局一局没丢", len(games1) >= n0, f"{n0} → {len(games1)}")
ck("BFS 让网络长大（比原来多抓了局）", len(games1) > n0, f"{n0} → {len(games1)}")
ck("原主人仍能找到自己的局（可换回）",
   any(any(p["puuid"] == me0 for p in g["players"]) for g in games1.values()))
ck("n_my_games 与 my_game_ids 一致", m1["n_my_games"] == len(my1),
   f"{m1['n_my_games']} vs {len(my1)}")

print("\n=== ② 用 --auto-anchor 给 TA 算隐藏分（TA 没有截图）===")
rc, out = run_estimate()
print("  estimate rc=%s" % rc)
for ln in out.strip().splitlines():
    if any(k in ln for k in ("自动锚定", "兜底", "当前隐藏分估算", "95% 置信半宽",
                             "其中本人参与", "对手", "趋势:", "详细数据", "✗", "⚠")):
        print("    |", ln)
ck("estimate 跑通", rc == 0, rc)
est_file = TMP / "海斗隐藏分_v2_本次估算.json"
ck("估算结果写到了副本（真实文件没被覆盖）", est_file.is_file(), est_file)
e = json.loads(est_file.read_text(encoding="utf-8"))
ck("落盘 owner_puuid = 新主人", e.get("owner_puuid") == tpu, e.get("owner_puuid"))
ck("anchor_source = auto（没截图走自动给分）",
   (e.get("anchor") or {}).get("anchor_source") == "auto",
   (e.get("anchor") or {}).get("anchor_source"))
sc = e.get("estimate")
ck("给出的隐藏分落在合理区间（1200~3000）", sc is not None and 1200 <= sc <= 3000, sc)
ck("raw_elo + offset == estimate",
   abs((sc - e["raw_elo"]) - e["anchor"]["offset"]) < 0.2,
   f"{sc} - {e['raw_elo']} vs offset {e['anchor']['offset']}")
rows = e.get("games") or []
ck("TA 的 Elo 轨迹有足够多局（>=10）", len(rows) >= 10, len(rows))
print("    → TA 的隐藏分 = %.1f（raw_elo %.1f, offset %+.1f, 轨迹 %d 局）"
      % (sc, e["raw_elo"], e["anchor"]["offset"], len(rows)))

print("\n=== ③ 反向：换回原主人（证明可逆）===")
rc2, _ = run_crawl(me0, name0, 0)
d2 = json.loads(dst.read_text(encoding="utf-8"))
m2 = d2["meta"]
games2 = {g["gameId"]: g for g in d2["games"]}
my2 = set(d2["my_game_ids"])
ck("换回后 meta.puuid = 原主人", m2["puuid"] == me0, m2["puuid"][:12])
ck("换回后 meta.summoner = 原主人名", m2["summoner"] == name0, m2["summoner"])
ck("换回后 my_game_ids 全是原主人的局",
   all(any(p["puuid"] == me0 for p in games2[g]["players"]) for g in my2))
ck("换回后局数继续不减（并集）", len(games2) >= len(games1),
   f"{len(games1)} → {len(games2)}")
print("    原主人局数 %d → 换成 TA %d 局 → 换回 %d 局" % (my0, len(my1), len(my2)))

print("\n=== ④ 真实数据一个字都没被改 ===")
now = json.loads(REAL.read_text(encoding="utf-8"))
ck("真实 hd_data 的 meta.puuid 仍是原主人", now["meta"]["puuid"] == me0, now["meta"]["puuid"][:12])
ck("真实数据里没有 prev_puuid（说明没被这次测试动过）", not now["meta"].get("prev_puuid"))
real_est = json.loads((DATA / "海斗隐藏分_v2_本次估算.json").read_text(encoding="utf-8"))
ck("真实估算文件的 owner_puuid 没被改成 TA", real_est.get("owner_puuid") != tpu)
ck("真实数据局数没变", now["meta"]["n_games"] == n0, f"{n0} → {now['meta']['n_games']}")

finish(footers=["临时目录（可删）: %s" % TMP])
