#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海克斯大乱斗(queue 2400 / KIWI) 隐藏分估算 v2 —— 纯海斗数据版

与 v1 的根本区别:
  v1 用「峡谷排位段位」代理对手的海斗实力 -> 两套完全不同的匹配池, 代理失效
  v2 用「对手真实的海斗对局」估计其实力 -> 全部数据来自 queue 2400 本身

算法:
  1) 滚雪球 BFS 从 LCU 抓局部对局网络(每局完整 10 人 + 胜负), 见 _hd_crawl.py
  2) 全局 Bradley-Terry 团队模型: 用所有「不含主人」的对局拟合每个玩家的海斗能力 θ
     —— 关键: 排除主人参与的对局, 使对手能力估计完全独立于主人的表现, 避免循环论证
  3) 主人的在线 Elo 轨迹: 按时间顺序, 以对手 θ 均值为基准做 Elo 更新
  4) 锚定: 用一张截图上的分数(如 1928)校准到隐藏分刻度
  5) 交叉验证: 复刻官方论文的谱分解(超图共现 -> Θ 算子 -> 第二特征向量), 给出强弱轴位置

用法:
  python 海斗隐藏分估算_v2.py --anchor 1928 --anchor-games-ago 5
  python 海斗隐藏分估算_v2.py                      # 不给锚点, 只出相对能力
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import (SCRIPTS, DATA_DIR, MIN_GAME_SECONDS, valid_game,  # noqa: E402
                    raise_if_cancelled, atomic_write_text)

DATA = DATA_DIR / "hd_data.json"
CHAMP_MAP_FILE = DATA_DIR / "champ_map.json"
ESTIMATE_FILE = DATA_DIR / "海斗隐藏分_v2_本次估算.json"
ELO_META_FILE = DATA_DIR / "player_elo_meta.json"
STATE_FILE = DATA_DIR / "_desktop_state.json"


def auto_offset():
    """把「估算轨迹的原始 Elo」换算到**隐藏分刻度**要加的那个 offset。

    什么时候需要它：数据主人不是自己（没有分数截图），
    又想让分数直接是隐藏分刻度 —— 用户 2026-09-22 定的规则是
    「先按数据库自动给分，之后有截图再覆盖」。

    为什么不能拿 `player_elo_meta` 的 offset 直接用：那是**另一个估计器**
    （全量在线 Elo，K=32/16）的刻度；本脚本的轨迹是 K=22、且从
    「对手覆盖率达标」那一段起算 —— 两者系统偏差实测差 **46 分**
    （历史 479.9 vs 433.5 差 46；2026-09-28 第十轮 136.8 vs 210.5 差 73.7）。
    所以优先复用它自己那套 offset：

      ① 桌面版状态里记的 `traj_offset`（明确来自截图锚定，最可信）
      ② 上一次估算结果里的 `anchor.offset`（前提：那次是截图锚定，不是 auto）
      ③ 兜底才用 player_elo_meta 的 offset（会差几十点，打印警告）

    ⚠️ ① 和 ② 是**同一个量**，不是两个来源：state 的 `traj_offset` 就是 app.py
    在上一轮截图锚定时从 `anchor.offset` 抄过去的。所以重锚之后如果没走桌面版
    跑一遍，① 会停在旧值 —— 第十轮真踩了：state 里还是第九轮的 −17.8（锚点 1541 /
    anchor_elo 1558.8），而当前估算已经是 136.8（锚点 1708 / anchor_elo 1571.2），
    差 **154.6 分**。真正**另一把尺子**的是 ③ 的 player_elo offset（本轮 +210.5）。
    """
    try:
        v = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("traj_offset")
        if v is not None:
            return float(v)
    except Exception:
        pass
    try:
        a = (json.loads(ESTIMATE_FILE.read_text(encoding="utf-8")).get("anchor") or {})
        if a.get("offset") is not None and a.get("anchor_source") != "auto":
            return float(a["offset"])
    except Exception:
        pass
    try:
        v = json.loads(ELO_META_FILE.read_text(encoding="utf-8")).get("offset")
        if v is not None:
            print("  ⚠ 只能用 player_elo 的 offset 兜底（估计器口径不同，可能差几十点）")
            return float(v)
    except Exception:
        pass
    return None

# Elo 刻度: 差 400 分 -> 胜率 10:1
ELO_SCALE = 400.0
ELO_BASE = 1500.0
K_FACTOR = 22.0          # 主人在线 Elo 的学习率
# --- 全局 BT / 团队 Elo 拟合 ---
BT_EPOCHS = 40          # 遍历轮数
BT_K0 = 26.0            # 初始学习率(Elo 刻度)
BT_K1 = 3.0             # 末轮学习率
BT_SHRINK = 0.012       # 每轮向基准收缩的强度(正则, 抑制小样本发散)
MIN_GAMES_FOR_THETA = 3  # 至少出现多少局才单独估 θ


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def elo_expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / ELO_SCALE))


def bt_p(team_a: list[float], team_b: list[float]) -> float:
    """队 A 胜队 B 的概率(Elo 刻度的团队 BT)"""
    da = sum(team_a) / len(team_a)
    db = sum(team_b) / len(team_b)
    return 1.0 / (1.0 + 10 ** ((db - da) / ELO_SCALE))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="海克斯大乱斗隐藏分估算 v2 (纯海斗数据)")
    ap.add_argument("--anchor", type=float, default=None,
                    help="截图上的「预估海斗隐藏分」, 例 1928")
    ap.add_argument("--anchor-games-ago", type=int, default=0,
                    help="截图的分数是几局之前的? 0=就是现在")
    ap.add_argument("--anchor-game-id", type=int, default=None,
                    help="锚点对应那一局的 gameId。给了它就**不用数几局前** —— "
                         "脚本自己在本轨迹里定位那一局。重抓数据后轨迹变长也不会跑偏，"
                         "所以桌面版会记住这个值自动带上。找不到时才退回 --anchor-games-ago")
    ap.add_argument("--data", type=str, default=str(DATA))
    # ---------- 数据主人可切换（2026-09-22） ----------
    ap.add_argument("--me", type=str, default=None,
                    help="把「本人」当成这个 puuid（默认=hd_data 的 meta.puuid）。"
                         "换主人后 meta.puuid 本身就变了，这个参数主要给调试/预演用")
    # ---------- 没有截图时的自动锚定 ----------
    ap.add_argument("--auto-anchor", action="store_true",
                    help="没有分数截图时，复用「截图锚定留下的 offset」把轨迹换算成"
                         "隐藏分刻度（数据主人换成别人时用；有截图就用 --anchor 覆盖）")
    ap.add_argument("--min-cov", type=float, default=7.0,
                    help="Elo 轨迹起点要求: 每局对手实力可标定比例 >= 该值(0~9), 默认7")
    ap.add_argument("--recrawl", action="store_true", help="重新抓取数据")
    ap.add_argument("--players", type=int, default=260)
    ap.add_argument("--show", type=int, default=14, help="显示最近多少局明细")
    args = ap.parse_args()

    if args.recrawl or not Path(args.data).exists():
        print("→ 抓取数据中 ...")
        import subprocess
        # 同 _lcu_creds2.hidden_kwargs()：别让子进程弹控制台窗口
        # （从 GUI 子系统的 exe 里起 console 程序时 Windows 会新开一个黑窗口）。
        r = subprocess.run([sys.executable, str(SCRIPTS / "_hd_crawl.py"),
                            "--players", str(args.players)],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        print(r.stdout[-1500:])

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    me = args.me or data["meta"]["puuid"]
    games = data["games"]
    my_ids = set(data.get("my_game_ids") or [])
    # 主人被临时换成别人（--me）：那份清单永远是 meta.puuid 的，改成现推
    if args.me and args.me != data["meta"]["puuid"]:
        my_ids = {g["gameId"] for g in games
                  if any(p["puuid"] == me for p in g["players"])}
    # 兜底: 没记录 my_game_ids 就自己判
    if not my_ids:
        my_ids = {g["gameId"] for g in games if any(p["puuid"] == me for p in g["players"])}

    champ_map = {}
    if CHAMP_MAP_FILE.exists():
        champ_map = json.loads(CHAMP_MAP_FILE.read_text(encoding="utf-8"))

    def cname(cid):
        if isinstance(cid, int):
            cid = str(cid)
        return champ_map.get(cid, str(cid))

    games.sort(key=lambda g: g["ts"])

    # 5 分钟以内的对局视为无效（挂机/重开），不能进 BT 拟合、更不能进 Elo 轨迹 ——
    # 见 _paths.MIN_GAME_SECONDS。_hd_crawl.py 已经不会收，这里再挡一次防老数据。
    n_short = sum(1 for g in games if not valid_game(g))
    if n_short:
        games = [g for g in games if valid_game(g)]
        my_ids = {g["gameId"] for g in games} & my_ids
        print(f"  跳过 {n_short} 局 <{MIN_GAME_SECONDS}s 的无效对局（挂机/重开）")
        if not games:
            print("✗ 过滤后一局都不剩，中止")
            return

    my_games = [g for g in games if g["gameId"] in my_ids]
    other_games = [g for g in games if g["gameId"] not in my_ids]

    print("=" * 74)
    print(f"  召唤师: {data['meta']['summoner']}")
    print(f"  数据: {len(games)} 场海克斯大乱斗 / {len({p['puuid'] for g in games for p in g['players']})} 名玩家")
    print(f"  其中本人参与 {len(my_games)} 场, 用于标定对手实力的独立对局 {len(other_games)} 场")
    print("=" * 74)

    if not my_games:
        print("✗ 没有找到主人参与的海斗对局")
        return

    # ---------- 1) 全局 BT 拟合(排除主人参与的对局) ----------
    print("\n[1/4] 全局 Bradley-Terry 拟合对手实力 (纯海斗数据, 排除本人对局) ...")
    theta = fit_bt(other_games, exclude={me})
    vals = list(theta.values())
    print(f"  拟合玩家 {len(theta)} 名, θ 均值 {statistics.mean(vals):.0f}, "
          f"标准差 {statistics.pstdev(vals):.0f}")
    prior = statistics.median(vals) if vals else ELO_BASE

    # 参与拟合的玩家里, 出现在主人对局中的比例
    opp_all = {p["puuid"] for g in my_games for p in g["players"] if p["puuid"] != me}
    covered = sum(1 for pu in opp_all if pu in theta)
    print(f"  本人对局中的对手 {len(opp_all)} 名, 其中有独立 θ 的 {covered} 名 "
          f"({100*covered/max(len(opp_all),1):.0f}%)")

    # ---------- 2) 主人在线 Elo 轨迹 ----------
    print("\n[2/4] 计算本人在线 Elo 轨迹 ...")
    # 只从"对手实力覆盖率 >= min_cov 的连续段"起算(更早的对手大多已不活跃, θ 缺失,
    # 会污染 Elo 起点; 从首个覆盖率合格的局开始)
    def cov_of(g):
        opps = [p for p in g["players"] if p["puuid"] != me]
        return sum(1 for p in opps if p["puuid"] in theta), len(opps)

    rows = []
    elo = prior
    started = False
    cov_hist = []
    for g in my_games:
        mine = next((p for p in g["players"] if p["puuid"] == me), None)
        if mine is None:
            continue
        opps = [p for p in g["players"] if p["puuid"] != me]
        opp_th = [theta.get(p["puuid"], prior) for p in opps]
        opp_avg = sum(opp_th) / len(opp_th)
        cov_n, cov_d = cov_of(g)
        cov_hist.append(cov_n / max(cov_d, 1))
        if not started and cov_n < args.min_cov:
            continue            # 前面低覆盖率的局跳过, 不参与 Elo 轨迹
        started = True
        win = mine["win"]
        S = 1.0 if win else 0.0
        before = elo
        elo = before + K_FACTOR * (S - elo_expected(before, opp_avg))
        rows.append({
            "gameId": g["gameId"],
            "ts": g["ts"],
            "time": time.strftime("%m-%d %H:%M", time.localtime(g["ts"] / 1000)) if g["ts"] else "?",
            "win": win,
            "champion": cname(mine.get("champion")),
            "k": mine.get("k", 0), "d": mine.get("d", 0), "a": mine.get("a", 0),
            "opp_avg": round(opp_avg, 1),
            "opp_known": cov_n, "opp_total": cov_d,
            "elo_before": round(before, 1),
            "elo_after": round(elo, 1),
        })

    if not rows:
        print("✗ 无有效对局(覆盖率都不达标?)")
        return

    # ---------- 3) 锚定 ----------
    final_elo = rows[-1]["elo_after"]
    anchor_info = None

    # 没有截图（换了数据主人）→ `--auto-anchor` 自动给分。
    # 等价于「把锚点定在轨迹最后一局、分数取 elo + 复用的 offset」，
    # 结果和走 `--anchor` 那条路完全一致（offset = anchor_score - anchor_elo）。
    anchor_score = args.anchor
    anchor_source = "screenshot" if args.anchor is not None else None
    if anchor_score is None and args.auto_anchor:
        _off = auto_offset()
        if _off is None:
            print("  ⚠ --auto-anchor 找不到可复用的 offset（没有截图锚定的估算结果，"
                  "也没有 player_elo_meta）→ 只能给未校准的 Elo 刻度")
        else:
            anchor_score = round(final_elo + _off, 1)
            anchor_source = "auto"
            print(f"  ⚙ 自动锚定（无截图）：复用 offset {_off:+.1f}，"
                  f"轨迹末端 Elo {final_elo:.0f} → 隐藏分 {anchor_score:.0f}")

    if anchor_score is not None:
        idx = None
        # 优先按 gameId 定位锚点那一局。
        # 为什么：`--anchor-games-ago` 是相对**本轨迹**数局数的，而轨迹只包含
        # 对手覆盖率达标的那部分对局 —— 重抓数据后轨迹会变长，光凭「几局前」
        # 会指到别的局上去。给了 gameId 就能精确找到同一局，重抓多少次都不跑偏。
        # （自动锚定时不走这条：它本来就锚在最后一局）
        if args.anchor_game_id is not None and anchor_source != "auto":
            for i, r in enumerate(rows):
                if r["gameId"] == args.anchor_game_id:
                    idx = i
                    break
            if idx is None:
                print(f"  ⚠ 锚点局 {args.anchor_game_id} 不在本次轨迹里"
                      f"（对手覆盖率不足或数据变了），"
                      f"退回按「{args.anchor_games_ago} 局前」定位")
        if idx is None:
            idx = len(rows) - 1 - (max(0, args.anchor_games_ago) if anchor_source != "auto" else 0)
            idx = max(0, min(idx, len(rows) - 1))
        ago_eff = len(rows) - 1 - idx
        anchor_elo = rows[idx]["elo_after"]
        offset = anchor_score - anchor_elo
        for r in rows:
            r["score_before"] = round(r["elo_before"] + offset, 1)
            r["score_after"] = round(r["elo_after"] + offset, 1)
        final_score = rows[-1]["score_after"]
        anchor_info = {
            "anchor_score": anchor_score,
            "anchor_source": anchor_source,
            # 实际用到的局数（按 gameId 定位时它才是准的）
            "anchor_games_ago": ago_eff,
            "anchor_games_ago_arg": args.anchor_games_ago,
            "anchor_game_id": rows[idx]["gameId"],
            "anchor_game_time": rows[idx]["time"],
            "anchor_elo": round(anchor_elo, 1),
            "offset": round(offset, 1),
        }
    else:
        final_score = final_elo

    # 置信区间: 锚定后"变化量"的误差来自锚点局之后的 m 局 Elo 随机游走
    #   每局方差 ≈ K²×Var(S) ≈ K²×0.25 (S~Bernoulli, E≈0.5), 累计 σ ≈ K·√m/2
    #   95% 半宽 ≈ 1.96 × K·√m/2 ≈ K·√m
    ANCHORED = anchor_score is not None
    m_after_anchor = max(len(rows) - 1 - (idx if ANCHORED else len(rows) // 2), 1)
    ci = K_FACTOR * math.sqrt(m_after_anchor) if ANCHORED else 0.0

    # ---------- 4) 谱分解交叉验证 ----------
    print("\n[3/4] 谱分解交叉验证 (复刻官方: 超图共现 -> Θ 算子 -> 第二特征向量) ...")
    spec = spectral_axis(games, me, theta=theta, label="谱1")
    spec5 = spectral_axis(games, me, theta=theta, min_deg=5, label="谱2")

    # ---------- 输出 ----------
    print("\n[4/4] 结果")
    print("=" * 74)
    if ANCHORED:
        _src = "自动（无截图，复用 offset）" if anchor_info.get("anchor_source") == "auto" else "截图"
        print(f"  当前隐藏分估算:   {round(final_score)}")
        print(f"  锚点[{_src}]({anchor_info['anchor_game_time']}, 锚点后 {anchor_info['anchor_games_ago']} 局): "
              f"{anchor_info['anchor_score']}   变化 {round(final_score - anchor_info['anchor_score'], 1):+}")
        print(f"  95% 置信半宽:     ±{round(ci)}  (锚点后 {m_after_anchor} 局的 Elo 游走误差)")
    else:
        print(f"  当前海斗能力(Elo 刻度, 未校准): {round(final_score)}")
        print(f"  ⚠ 建议加 --anchor <截图分数> 以换算成隐藏分刻度"
              f"（或加 --auto-anchor 用数据库自动给分）")
    print("=" * 74)

    wins = sum(1 for r in rows if r["win"])
    print(f"\n  近期战绩: {wins}胜 {len(rows)-wins}负  (胜率 {100*wins/len(rows):.0f}%)")
    avg_opp = statistics.mean([r["opp_avg"] for r in rows])
    print(f"  对手平均实力: {avg_opp:.0f}  (本人当前 {final_elo:.0f})")

    print(f"\n  最近 {min(args.show, len(rows))} 局明细:")
    hdr = (f"{'时间':<12}{'英雄':<7}{'结果':<6}{'K/D/A':<12}"
           f"{'对手实力':<10}{'估分 前→后'}")
    print("  " + hdr)
    print("  " + "-" * (len(hdr) + 6))
    for r in rows[-args.show:]:
        m = "胜" if r["win"] else "负"
        kda = f"{r['k']}/{r['d']}/{r['a']}"
        key = "score" if ANCHORED else "elo"
        print(f"  {r['time']:<12}{str(r['champion'])[:5]:<7}{m:<6}{kda:<12}"
              f"{r['opp_avg']:<10.0f}{r[key+'_before']:>7.0f} → {r[key+'_after']:<7.0f}")

    # 趋势
    if len(rows) >= 8:
        half = len(rows) // 2
        k = 'score_after' if ANCHORED else 'elo_after'
        later = statistics.mean([r[k] for r in rows[-half:]])
        earlier = statistics.mean([r[k] for r in rows[:half]])
        arrow = "↑ 上行" if later > earlier else ("↓ 下行" if later < earlier else "→ 持平")
        print(f"\n  趋势: 后{half}局均值 {later:.0f}  vs  前{half}局 {earlier:.0f}   "
              f"Δ {later-earlier:+.1f}  {arrow}")
        ko = 'opp_avg'
        lo = statistics.mean([r[ko] for r in rows[-half:]])
        eo = statistics.mean([r[ko] for r in rows[:half]])
        print(f"  对手实力: 后{half}局 {lo:.0f}  vs  前{half}局 {eo:.0f}   Δ {lo-eo:+.0f}")

    if spec:
        print(f"\n  谱分解交叉验证 (复刻官方超图共现方法):")
        print(f"    局部网络 {spec['n_nodes']} 节点 / {spec['n_edges']} 条对局超边, "
              f"迭代 {spec['iters']} 次")
        if spec5 and spec5["n_nodes"] != spec["n_nodes"]:
            print(f"    仅看高频玩家(deg≥5, {spec5['n_nodes']} 人): "
                  f"本人分位 {spec5['pct']:.0f}%")
        print(f"    ⚠ 雪球采样的局部网络以本人为中心且严重稀疏(中位度=1), "
              f"谱轴分位有 hub 偏差, 仅作弱交叉参考")

    # 落盘
    out = DATA_DIR / "海斗隐藏分_v2_本次估算.json"
    atomic_write_text(out, json.dumps({
        "meta": data["meta"],
        # 这份估算到底是算谁的 —— 换数据主人后必须能一眼看出来
        # （app.py 会拿它和 hd_data 的 meta.puuid 对一下，不一致就提示重算）
        "owner_puuid": me,
        "owner_name": data["meta"].get("summoner") if me == data["meta"].get("puuid") else None,
        "model": {
            "type": "Bradley-Terry(team, Elo scale) on queue2400 + online Elo",
            "elo_scale": ELO_SCALE, "k_factor": K_FACTOR,
            "bt_epochs": BT_EPOCHS, "bt_k0": BT_K0, "bt_k1": BT_K1,
            "bt_shrink": BT_SHRINK, "min_games_theta": MIN_GAMES_FOR_THETA,
            "n_opp_with_theta": covered, "n_opp_total": len(opp_all),
            "prior": round(prior, 1),
        },
        "anchor": anchor_info,
        "estimate": round(final_score, 1),
        "ci_half_95": round(ci, 1),
        "raw_elo": round(final_elo, 1),
        "spectral": spec,
        "spectral_deg5": spec5,
        "games": rows,
    }, ensure_ascii=False, indent=1))
    print(f"\n  详细数据 -> {out}")


# --------------------------------------------------------------------------
def fit_bt(games: list[dict], exclude: set[str] | None = None,
           epochs: int = BT_EPOCHS) -> dict[str, float]:
    """全局 Bradley-Terry / 团队 Elo 拟合, 输出每个玩家的 Elo 刻度能力 θ。

    实现方式: 多轮遍历所有对局做团队 Elo 更新, 学习率从 K0 线性衰减到 K1,
    每轮结束后按 BT_SHRINK 向基准收缩(等价于高斯先验 / L2 正则),
    抑制出场少的玩家 θ 发散。比朴素 SGD 稳定得多。

    exclude 里的玩家不参与拟合(即使出现在对局中也跳过), 用于把"本人"排除,
    使对手实力估计完全独立于本人的表现, 避免循环论证。
    """
    exclude = exclude or set()
    cnt: dict[str, int] = defaultdict(int)
    obs = []
    for g in games:
        win_t, lose_t = [], []
        for p in g["players"]:
            if p["puuid"] in exclude:
                continue
            (win_t if p["win"] else lose_t).append(p["puuid"])
        if len(win_t) < 3 or len(lose_t) < 3:
            continue
        for pu in win_t + lose_t:
            cnt[pu] += 1
        obs.append((win_t, lose_t))

    theta = {pu: ELO_BASE for pu in cnt if cnt[pu] >= MIN_GAMES_FOR_THETA}
    if len(obs) < 30 or len(theta) < 20:
        return {}

    random.seed(7)
    order = list(range(len(obs)))
    for ep in range(epochs):
        # 每个 epoch 开头检查停止标志。BT 拟合是这一步最耗时的部分（实测 112s），
        # 40 个 epoch 摊下来一个约 3 秒，作为「中途停止」的响应粒度够了。
        raise_if_cancelled("BT 拟合")
        # 学习率线性衰减 + 每轮向基准收缩(正则)
        K = BT_K0 + (BT_K1 - BT_K0) * (ep / max(epochs - 1, 1))
        random.shuffle(order)
        for i in order:
            win_t, lose_t = obs[i]
            ws = [theta[pu] for pu in win_t if pu in theta]
            ls = [theta[pu] for pu in lose_t if pu in theta]
            if not ws or not ls:
                continue
            p = bt_p(ws, ls)          # 队 A(胜方)的期望胜率
            d = K * (1.0 - p)
            for pu in win_t:
                if pu in theta:
                    theta[pu] += d
            for pu in lose_t:
                if pu in theta:
                    theta[pu] -= d
        # 正则: 向基准收缩, 再重新居中固定刻度
        for pu in theta:
            theta[pu] = ELO_BASE + (theta[pu] - ELO_BASE) * (1.0 - BT_SHRINK)
        m = sum(theta.values()) / len(theta)
        for pu in theta:
            theta[pu] += ELO_BASE - m
    return theta


# --------------------------------------------------------------------------
def spectral_axis(games: list[dict], me: str, iters: int = 300,
                  theta: dict[str, float] | None = None,
                  min_deg: int = 1, label: str = "谱") -> dict | None:
    """复刻官方论文: 超图共现 -> Θ = D^-1/2 H De^-1 H^T D^-1/2 -> 第二特征向量。

    注意: 这里的网络是围绕本人滚雪球采样得到的, 不是全服无偏样本,
    因此分位会受 hub 效应污染(采样中心节点被高估)。
    min_deg>1 时只保留出场 >= min_deg 局的玩家, 缓解稀疏噪声。
    """
    # 建超图
    # 进度输出：这一段（建图 + 300 次幂迭代）实测占估算步 93%（89.2s/95.6s），
    # 没有标记的话桌面版进度条会在 9% 卡住一分多钟（2026-09-22 主人反馈）。
    # 前缀用 label 区分：这个函数被调两次（全网 min_deg=1 / 高频 min_deg=5），
    # 桌面版的进度「只增不减」，同前缀会让第二段彻底没有进度。
    print(f"  [{label}] 建超图 ...", flush=True)
    nodes: dict[str, int] = {}
    edges = []
    for g in games:
        ps = [p["puuid"] for p in g["players"]]
        if len(ps) < 10:
            continue
        members = []
        for pu in ps:
            if pu not in nodes:
                nodes[pu] = len(nodes)
            members.append(nodes[pu])
        edges.append(members)
    n_nodes, n_edges = len(nodes), len(edges)
    print(f"  [{label}] 建图完成: {n_nodes} 节点 / {n_edges} 超边，开始 {iters} 次幂迭代 ...",
          flush=True)

    deg = [0] * n_nodes
    for e in edges:
        for i in e:
            deg[i] += 1
    if me not in nodes:
        return None

    # 只保留 deg >= min_deg 的节点
    if min_deg > 1:
        keep = [i for i in range(n_nodes) if deg[i] >= min_deg]
        remap = {old: new for new, old in enumerate(keep)}
        if nodes[me] not in remap:
            return None
        new_edges = []
        for e in edges:
            ne = [remap[i] for i in e if i in remap]
            if len(ne) >= 6:
                new_edges.append(ne)
        edges = new_edges
        n_nodes = len(keep)
        n_edges = len(edges)
        nodes = {pu: remap[old] for pu, old in list(nodes.items()) if old in remap}
        # 重算度
        deg = [0] * n_nodes
        for e in edges:
            for i in e:
                deg[i] += 1
        if len(edges) < 20 or n_nodes < 20:
            return None

    # Θ = D^-1/2 H De^-1 H^T D^-1/2 是行随机矩阵的对称化形式:
    #   最大特征值 1 的特征向量为 φ1 = D^1/2 · 1 (即 sqrt(deg)),
    #   第二特征向量(强弱轴)须与 φ1 正交 —— 幂迭代每步按标准内积去掉 φ1 分量
    dsqrt = [math.sqrt(d) if d else 0.0 for d in deg]
    phi1_norm2 = sum(d * d for d in dsqrt)   # φ1·φ1 = Σ deg

    # 随机初始化必须用**同一个** Random 实例取一列数 ——
    # 原写法 `random.Random(11).uniform(...)` 在列表推导里每次循环都新建
    # seed=11 的实例，所有分量恒等（常数向量），根本不是随机初始化（审查 M2）。
    _rng = random.Random(11)
    v = [_rng.uniform(-1, 1) for _ in range(n_nodes)]
    it = 0
    _step = max(1, iters // 20)      # 每 ~5% 打一行，够平滑又不会把日志刷爆
    for it in range(iters):
        # u = D^{-1/2} v
        u = [v[i] / dsqrt[i] if dsqrt[i] else 0.0 for i in range(n_nodes)]
        # w = De^{-1} H^T u   (每条超边求和 / 边大小)
        w = [sum(u[i] for i in e) / len(e) for e in edges]
        # y = H w
        y = [0.0] * n_nodes
        for ei, e in enumerate(edges):
            wv = w[ei]
            for i in e:
                y[i] += wv
        # nv = D^{-1/2} y
        nv = [y[i] / dsqrt[i] if dsqrt[i] else 0.0 for i in range(n_nodes)]
        # 正交化: 去掉 φ1 = sqrt(deg) 分量 (与最大特征向量正交 => 收敛到第二特征向量)
        if phi1_norm2 > 0:
            proj = sum(nv[i] * dsqrt[i] for i in range(n_nodes)) / phi1_norm2
            nv = [nv[i] - proj * dsqrt[i] for i in range(n_nodes)]
        norm = math.sqrt(sum(x * x for x in nv))
        if norm < 1e-12:
            break
        nv = [x / norm for x in nv]
        diff = sum(abs(nv[i] - v[i]) for i in range(n_nodes)) / n_nodes
        v = nv
        if (it + 1) % _step == 0:
            print(f"  [{label}] {it + 1}/{iters}", flush=True)
        if diff < 1e-8:
            break
    # 收尾标记：提前收敛时也要把「这一段做完了」告诉外面，
    # 否则进度会停在最后一个周期点（比如 120/300）不动。
    print(f"  [{label}] 迭代结束 ({it + 1}/{iters})", flush=True)

    # 定符号: 若有 BT 拟合的 θ, 用「轴 vs θ」的秩相关定方向(轴越大 = 越强)
    sign = 1.0
    if theta and len(theta) > 20:
        pairs = [(v[nodes[pu]], theta[pu]) for pu in theta if pu in nodes]
        if len(pairs) > 20:
            rv = [p[0] for p in pairs]
            rt = [p[1] for p in pairs]
            # Spearman 秩相关
            def rank(xs):
                o = sorted(range(len(xs)), key=lambda k: xs[k])
                r = [0.0] * len(xs)
                for pos, k in enumerate(o):
                    r[k] = pos
                return r
            rv_r, rt_r = rank(rv), rank(rt)
            mr = (len(pairs) - 1) / 2
            cov = sum((a - mr) * (b - mr) for a, b in zip(rv_r, rt_r))
            den = sum((a - mr) ** 2 for a in rv_r) ** 0.5 * sum((b - mr) ** 2 for b in rt_r) ** 0.5
            rho = cov / den if den else 0.0
            sign = 1.0 if rho >= 0 else -1.0
    vals = [sign * x for x in v]
    my_id = nodes[me]
    my_v = vals[my_id]
    stronger = sum(1 for x in vals if x > my_v)
    pct = 100.0 * (1.0 - stronger / n_nodes)
    return {
        "n_nodes": n_nodes, "n_edges": n_edges, "iters": it + 1,
        "min_deg": min_deg,
        "my_value": round(my_v, 6),
        "rank": stronger + 1,
        "pct": round(pct, 2),
    }


if __name__ == "__main__":
    main()
