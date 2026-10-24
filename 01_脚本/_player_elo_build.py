# -*- coding: utf-8 -*-
"""给 hd_data.json 出现过的每个玩家算一个隐藏分刻度的实力分（v5 重写）。

v4 的坑（为什么重写）：
  - 独立 BT 拟合（K 26→3 衰减 + 每轮 1.2% 收缩）在稀疏网络（median 14 局）下
    把绝大多数玩家拉回基准 1500 → 467/560 人 mmr 全是同一个数（坍缩）。
  - my_theta 取自 v2 在线 Elo 坐标系，与独立拟合坐标系不一致，参照错位。

v5 方案：
  1) 全数据集（3990 局，含主人的局——对手评分与主人无关，无需排除）按时间序
     跑在线 Elo：新人前 10 局 K=32（快速定位），之后 K=16。
  2) 校准用「公平匹配原理」：海斗匹配会把 MMR 相近的 10 人凑一桌，
     所以主人本人参与局（当前 115 局）对手的最终分均值 ≈ 主人自己的 MMR(1928 锚点)。
     offset = 1928 - mean(对手分)。
  3) 刻度保持 Elo 自然刻度（400 分 / logit），不额外缩放。

落盘 player_elo.json: {puuid: {mmr, n_games}}（mmr 一位小数）
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import (DATA_DIR, MIN_GAME_SECONDS, valid_game, raise_if_cancelled,  # noqa: E402
                    atomic_write_text)
# 2026-10-03：原是本文件内一份 _pearson 副本（与 _dash_rebuild.py 的逐字相同），
# 现统一到 _composite.pearson。默认参数 min_n=2 / nd=4 与原实现行为一致。
from _composite import pearson  # noqa: E402

DATA = DATA_DIR / "hd_data.json"
EVAL = DATA_DIR / "海斗隐藏分_v2_本次估算.json"
OUT = DATA_DIR / "player_elo.json"
ELO_META = DATA_DIR / "player_elo_meta.json"      # 上一版的 offset 从这里复用
STATE = DATA_DIR / "_desktop_state.json"          # 桌面版记的锚点（按主人）

# 锚点 = 数据的**量表零点**：`mmr = 原始 Elo + offset`，而 `offset = 锚点分 - 主人对手最终分均值`。
# 默认 1708（一次截图锚定的那个数）。
# 历史：1928（2026-09-03 截图）→ 1541（2026-09-27 18:54 截图）→ 1708（2026-09-28 01:24 截图）。
# 2026-09-22 起改成可传参：换数据主人（--me）时，桌面版会把那个主人的锚点传进来；
# 新主人没有截图锚点 → **复用上一版 offset**（见下面 main() 里的说明）。
ANCHOR_DEFAULT = 1708.0
ANCHOR = ANCHOR_DEFAULT          # 兼容旧引用（会被 main() 里解析出的 anchor 覆盖）
K_PROVISIONAL = 32.0   # 前 10 局
K_STEADY = 16.0
N_PROVISIONAL = 10
ELO_BASE = 1500.0
ELO_SCALE = 400.0


def _prev_offset():
    """上一版 player_elo.json 用的 offset —— 换主人时沿用它，把量表保持在同一把尺子上。"""
    try:
        v = json.loads(ELO_META.read_text(encoding="utf-8")).get("offset")
        return float(v) if v is not None else None
    except Exception:
        return None


def resolve_anchor(explicit=None):
    """决定本次校准用哪个锚点分，返回 (anchor, 来源说明)。

    优先级：
      ① 命令行 `--anchor`（桌面版按「当前数据主人」从状态文件里取出来传进来）
      ② `02_数据\\_desktop_state.json` 里的锚点（只在它确实属于当前主人时才算）
      ③ 都没有 → 返回 None，让调用方走「复用上一版 offset」那条路
    """
    if explicit is not None:
        return float(explicit), "命令行 --anchor"
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return None, "状态文件读不到"
    hd_puuid = None
    try:
        hd_puuid = json.loads(DATA.read_text(encoding="utf-8"))["meta"]["puuid"]
    except Exception:
        pass
    # 平铺字段 = 「当前数据主人」的锚点；anchor_puuid 记录它属于谁。
    owner = st.get("anchor_puuid")
    if st.get("anchor_score") is not None and (owner is None or owner == hd_puuid):
        return float(st["anchor_score"]), "状态文件（当前数据主人）"
    # 换主人后，新主人自己的锚点存在 anchors[puuid] 里
    a = (st.get("anchors") or {}).get(hd_puuid or "")
    if a and a.get("anchor_score") is not None:
        return float(a["anchor_score"]), "状态文件（该主人自己的锚点）"
    return None, "该数据主人还没有截图锚点"


def _rankdata(xs):
    """平均秩（处理并列）"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def main():
    ap = argparse.ArgumentParser(description="全量在线 Elo + 公平匹配校准")
    ap.add_argument("--anchor", type=float, default=None,
                    help="校准用的锚点分（把某一局的实际分数当标定起点）。"
                         "不给就依次查状态文件 / 复用上一版 offset")
    ap.add_argument("--me", type=str, default=None,
                    help="把「数据主人」当成这个 puuid（默认=hd_data 的 meta.puuid）")
    args = ap.parse_args()

    t0 = time.time()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    me = args.me or data["meta"]["puuid"]
    games = data["games"]
    n_players = len({p["puuid"] for g in games for p in g["players"]})
    print(f"加载 {len(games)} 局 / {n_players} 玩家")

    # 按时间排序（在线 Elo 必须按时间序）
    games = sorted(games, key=lambda g: g.get("ts", 0))
    missing_ts = sum(1 for g in games if not g.get("ts"))
    if missing_ts:
        print(f"  ⚠ {missing_ts} 局无时间戳，按原顺序垫底")

    # 过滤畸形对局：一侧为空就没法算「队伍平均分」（除零）。
    #
    # 2026-09-21 实测：hd_data.json 里混进了 1 局 10 人 win 全为 false 的记录
    # （gameId 10000000004，624 秒的对局）。旧代码跑到这里直接
    # ZeroDivisionError 崩掉，而且它是第 3 步，后面 3 步全部中止 —— 用户看到的是
    # 「重建全量玩家评分失败」。这里跳过并明确报出来，绝不崩。
    valid, skipped = [], []
    for g in games:
        n = len(g.get("players") or [])
        w = sum(1 for p in g["players"] if p.get("win"))
        if n == 0 or w == 0 or w == n:
            skipped.append((g["gameId"], w, n))
        else:
            valid.append(g)
    if skipped:
        print(f"  ⚠ 跳过 {len(skipped)} 局畸形对局（胜负标记不可用，算不了 Elo）：")
        for gid, w, n in skipped[:5]:
            print(f"      gameId={gid}  win={w}/{n}")
        if len(skipped) > 5:
            print(f"      ... 另有 {len(skipped) - 5} 局")
    games = valid
    if not games:
        print("✗ 一局可用对局都没有，中止（不写盘，保留上一版 player_elo.json）")
        sys.exit(1)

    # 5 分钟以内的对局视为无效（挂机/重开），不能进 Elo —— 见 _paths.MIN_GAME_SECONDS。
    # _hd_crawl.py 已经不会收这种局了，这里再挡一次是为了防老数据。
    n_short = sum(1 for g in games if not valid_game(g))
    if n_short:
        games = [g for g in games if valid_game(g)]
        print(f"  ⚠ 跳过 {n_short} 局 <{MIN_GAME_SECONDS}s 的无效对局（挂机/重开，胜负无参考价值）")
        if not games:
            print("✗ 过滤后一局都不剩，中止（不写盘，保留上一版 player_elo.json）")
            sys.exit(1)

    rating = {}
    ngames = {}
    for gi, g in enumerate(games):
        # 每 200 局检查一次停止标志（这一步是纯 Python 循环，没有 I/O 可以打断，
        # 只能自己在安全点轮询）。200 局大约 1~2 秒，粒度够用。
        if gi % 200 == 0:
            raise_if_cancelled("重建全量评分")
        wins, loses = [], []
        for p in g["players"]:
            (wins if p["win"] else loses).append(p["puuid"])
            rating.setdefault(p["puuid"], ELO_BASE)
            ngames[p["puuid"]] = ngames.get(p["puuid"], 0) + 1
        # 队伍平均分
        ra = sum(rating[p] for p in wins) / len(wins)
        rb = sum(rating[p] for p in loses) / len(loses)
        # 期望胜率（logistic）
        ea = 1 / (1 + 10 ** ((rb - ra) / ELO_SCALE))
        # 每人各自 K（按个人局数）
        for p in wins:
            k = K_PROVISIONAL if ngames[p] <= N_PROVISIONAL else K_STEADY
            rating[p] += k * (1 - ea)
        for p in loses:
            k = K_PROVISIONAL if ngames[p] <= N_PROVISIONAL else K_STEADY
            rating[p] -= k * ea
        # 逐段打进度：桌面版进度条靠这行算百分比
        if (gi + 1) % 5000 == 0:
            print(f"  [Elo] {gi + 1}/{len(games)} 局", flush=True)

    # ---- 校准：主人 76 局对手最终分均值 ≈ 主人 MMR ----
    my_ids = set(data["my_game_ids"])
    if args.me and args.me != data["meta"]["puuid"]:
        # 临时把主人当成别人（调试/预演）：他的局直接从网络里现推，
        # 不依赖 my_game_ids（那份清单永远是 meta.puuid 的）
        my_ids = {g["gameId"] for g in games
                  if any(p["puuid"] == me for p in g["players"])}
    # 注意：my_ids 是 hd_data 里的原始清单，可能含已被上面滤掉的无效局。
    # 校准口径必须和 Elo 口径一致 —— 所以这里只认「过滤后还在 games 里」的局。
    my_valid = {g["gameId"] for g in games} & my_ids
    opp_ratings = []
    for g in games:
        if g["gameId"] not in my_ids:
            continue
        for p in g["players"]:
            if p["puuid"] != me and p["puuid"] in rating:
                opp_ratings.append(rating[p["puuid"]])
    if not opp_ratings:
        print("✗ 没有可用于校准的对手评分，中止（不写盘，保留上一版 player_elo.json）")
        sys.exit(1)
    opp_mean = sum(opp_ratings) / len(opp_ratings)

    # ---------- 校准：offset = 锚点分 - 主人对手最终分均值 ----------
    #
    # ⚠️ 换数据主人时**不能**拿「这个新主人的对手均值」去反算 offset ——
    #    那等于把整张玩家表的零点重新锚一次，算出来的结果必然是
    #    「新主人的对手均值恰好 = 锚点分」（循环论证），量表整体漂移，
    #    跟历史数据、跟看板上的参照曲线都没法比了。
    #    所以规则是：
    #      · 该主人**有**截图锚点（或命令行给了 --anchor）→ 按锚点校准（原逻辑）
    #      · 新主人**没有**锚点 → **复用上一版 offset**，让 mmr 继续待在同一把尺子上；
    #        新主人的分由估算脚本的 `--auto-anchor` 用同一个 offset 换算。
    anchor, why = resolve_anchor(args.anchor)
    if anchor is not None:
        anchor_used = anchor
        offset = anchor - opp_mean
        print(f"  主人 {len(my_valid)} 局对手最终分均值 = {opp_mean:.1f} → offset = {offset:+.1f}"
              f"   （锚点 {anchor:g}，来源：{why}）")
    else:
        prev = _prev_offset()
        if prev is not None:
            anchor_used = None
            offset = prev
            print(f"  ⚠ {why} → **复用上一版 offset {offset:+.1f}**，量表刻度保持不变")
            print(f"    （该主人的分由估算脚本 --auto-anchor 用同一个 offset 换算；"
                  f"拿到 TA 的分数截图后填一次就会自动改成精确锚定）")
        else:
            anchor_used = ANCHOR_DEFAULT
            offset = ANCHOR_DEFAULT - opp_mean
            print(f"  ⚠ {why}，也没有上一版 offset → 退回默认锚点 {ANCHOR_DEFAULT:g}")
            print(f"  主人 {len(my_valid)} 局对手最终分均值 = {opp_mean:.1f} → offset = {offset:+.1f}")

    # ---- 落盘 ----
    out = {"_meta": {
        "method": "online-elo-chronological",
        "k_provisional": K_PROVISIONAL, "k_steady": K_STEADY,
        "n_provisional": N_PROVISIONAL,
        "elo_scale": ELO_SCALE,
        "anchor": anchor_used,
        "offset": round(offset, 1),
        "offset_reused": anchor_used is None,
        "opp_mean_raw": round(opp_mean, 1),
        "calibration": ("主人 %d 局对手最终分均值 → %s（公平匹配原理）" % (len(my_valid), int(anchor_used))
                        if anchor_used is not None else
                        "该数据主人无截图锚点 → 复用上一版 offset %.1f，量表不漂移" % offset),
        "owner_puuid": me,
        "n_total": len(rating),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }}
    for pu, r in rating.items():
        out[pu] = {"mmr": round(r + offset, 1), "n_games": ngames[pu]}

    if not rating:
        print("✗ 一个玩家评分都没算出来，中止（不写盘，保留上一版 player_elo.json）")
        sys.exit(1)

    atomic_write_text(OUT, json.dumps(out, ensure_ascii=False))

    # 2026-09-27 补：`player_elo_meta.json` 必须跟着刷新。
    # 原来只有 `_stats_crawl.py`（第 4 步，**需要客户端在线**）会写它，于是
    # 「只重算不重抓」或「抓完又重算」时它会停在上一版的 anchor/offset 上 ——
    # 实测重锚到 1541 后它仍写着 anchor 1928 / offset 430.6（整整差 387 分），
    # 而 `海斗隐藏分估算_v2.py` 的**兜底路径**会读这个 offset：
    # 一旦主路径失效（无锚点、无上一版 offset），就会拿过期 offset 静默算错分。
    # 这里顺手把 `_meta` 落一份，语义与 `player_elo.json._meta` 完全一致。
    try:
        atomic_write_text(ELO_META,
                          json.dumps(out["_meta"], ensure_ascii=False, indent=1))
        print(f"  ✓ 同步 {ELO_META.name}（anchor={anchor_used} offset={offset:+.1f}）")
    except OSError as e:
        print(f"  ⚠ {ELO_META.name} 写入失败（不影响主结果，但看板/估算兜底会读到旧值）：{e}")

    # ---- 分布体检 ----
    mmrs = [v["mmr"] for k, v in out.items() if isinstance(v, dict) and "mmr" in v]
    import statistics
    print(f"  ✓ 输出 {len(mmrs)} 人 → {OUT.name} "
          f"({round(OUT.stat().st_size/1024,1)} KB)  耗时 {time.time()-t0:.1f}s")
    print(f"  全体 mmr: min={min(mmrs):.0f} max={max(mmrs):.0f} "
          f"mean={statistics.mean(mmrs):.0f} std={statistics.pstdev(mmrs):.0f}")
    # 主人对手的分布（这才是看板里显示的人群）
    opp_mmrs = [rating[p['puuid']] + offset for g in games if g['gameId'] in my_valid
                for p in g['players'] if p['puuid'] != me]
    print(f"  对手(按局) mmr: mean={statistics.mean(opp_mmrs):.0f} "
          f"std={statistics.pstdev(opp_mmrs):.0f} min={min(opp_mmrs):.0f} max={max(opp_mmrs):.0f}")
    # 常见值检查（不应再有 mass 坍缩）
    from collections import Counter
    c = Counter(round(v) for v in opp_mmrs)
    top = c.most_common(3)
    print(f"  对手常见值 top3: {top}  （单一值占比 {top[0][1]/len(opp_mmrs)*100:.1f}%，>30% 即坍缩）")
    # 主人对手中 n_games 分布
    opp_puids = {p["puuid"] for g in games if g["gameId"] in my_valid
                 for p in g["players"] if p["puuid"] != me}
    ns = [ngames[p] for p in opp_puids]
    print(f"  对手去重 {len(opp_puids)} 人，n_games: min={min(ns)} median={statistics.median(ns):.0f} max={max(ns)}")


if __name__ == "__main__":
    main()
