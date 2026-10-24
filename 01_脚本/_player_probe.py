# -*- coding: utf-8 -*-
"""抓某个玩家（任意 puuid）最近的海斗对局，并给出一个隐藏分估计。

用途：看板「逐局战绩 → 明细」里点某个玩家，看他的战绩和隐藏分。

数据通道（与 _hd_crawl.py 同一套，SGP 优先、LCU 回退）：
  SGP  `GET {base}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY
            ?startIndex=0&count=100&tag=q_2400`   + Bearer accessToken
       —— 一次就够，响应里的 games[].json 本身就是完整对局详情
  LCU  一页 20 局 + 逐局拉详情（回退用）

隐藏分怎么来的（两条，都报出来，让用户自己判断可信度）：
  ① **known**：他已经在主网络（player_elo.json）里有分 → 直接给，最可靠。
     （主网络 = 围绕主人的 29384 局滚雪球网络；样本 ≥5 局才算有分）
  ② **local**：他不在主网络里（或样本太少）→ 拿他最近这些局做一次**在线 Elo 走查**：
     对手的分数直接从 player_elo.json 里取（那套分数已经用「公平匹配原理」平移过，
     刻度就是隐藏分刻度），按时间序逐局更新他自己的分。
     报出「有分的对手平均每局几人」——覆盖率低就意味着这个数不可信。

⚠️ local 只是个近似：对手的分数是主网络的终值，不是同时刻值。所以只当参考，
   别当官方数。覆盖率 <3 人/局基本等于瞎猜，脚本会明确标出来。

输出：02_数据/_player_probe.json（给桌面版读）
用法：python _player_probe.py --puuid <puuid> [--name 昵称] [--games 100] [--no-sgp]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import DATA_DIR, MIN_GAME_SECONDS, valid_game, atomic_write_text  # noqa: E402
import _hd_crawl as HC  # noqa: E402

OUT = DATA_DIR / "_player_probe.json"
PE_FILE = DATA_DIR / "player_elo.json"
CHAMP_MAP_FILE = DATA_DIR / "champ_map.json"

ELO_BASE = 1500.0
ELO_SCALE = 400.0
K_PROVISIONAL = 32.0
K_STEADY = 16.0
N_PROVISIONAL = 10
MIN_OPP_KNOWN = 3          # 一局里至少几个对手有分，这局才拿来更新 Elo
RELIABLE_N = 5             # 主网络里样本 ≥ 这个数才算「有分」


def load_pe():
    """player_elo.json → {puuid: (mmr, n_games)} 和 offset（把 mmr 还原成 Elo 原始刻度）。"""
    if not PE_FILE.exists():
        return {}, 0.0
    d = json.loads(PE_FILE.read_text(encoding="utf-8"))
    meta = d.pop("_meta", {}) or {}
    return d, float(meta.get("offset") or 0.0)


def fetch_sgp(sgp, puuid: str, n: int):
    """SGP 一次拿 n 局。返回 [归一化对局] 或 None（失败）。"""
    if not sgp.ok:
        return None
    gs = sgp.summary(puuid, start=0, count=n, tag="q_2400")
    if gs is None:
        return None
    out = []
    for g in gs:
        d = HC.norm_sgp(g)
        if d:
            out.append(d)
    return out


def fetch_lcu(lcu, puuid: str, n: int, workers: int):
    """LCU 回退：列表一页 20 局 → 逐局拉详情。"""
    r = lcu.get(f"/lol-match-history/v1/products/lol/{puuid}/matches"
                f"?begIndex=0&endIndex={n}")
    ids = []
    if r:
        for g in r.get("games", {}).get("games", []):
            if g.get("queueId") == HC.QUEUE_HEX:
                ids.append(g["gameId"])
    if not ids:
        return []
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(lcu.get, f"/lol-match-history/v1/games/{gid}")
                               for gid in ids]):
            d = HC.norm_detail(f.result())
            if d:
                out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puuid", required=True, help="要查的玩家 puuid")
    ap.add_argument("--name", default="", help="显示用昵称（只写进结果文件）")
    ap.add_argument("--games", type=int, default=100, help="抓多少局（SGP 上限 100）")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--no-sgp", action="store_true", help="只用 LCU（调试）")
    args = ap.parse_args()

    t0 = time.time()
    pu = args.puuid.strip()
    print(f"▸ 目标玩家: {args.name or pu}  ({pu[:8]}…)")

    port, token = HC.find_lcu()
    if not port:
        print("✗ 未找到 League 客户端(LeagueClientUx.exe 未运行)")
        sys.exit(1)
    lcu = HC.LCU(port, token)

    sgp = HC.SGP(lcu)
    if args.no_sgp:
        sgp.ok = False
        sgp.err = "命令行指定了 --no-sgp"

    source = ""
    games = None
    if sgp.ok:
        print(f"  SGP: {sgp.sid}  拉最近 {min(args.games, HC.SGP_MAX)} 局 q_2400 ...")
        games = fetch_sgp(sgp, pu, min(args.games, HC.SGP_MAX))
        if games is not None:
            source = "SGP"
    if games is None:
        print(f"  ⚠ SGP 不可用（{sgp.err}），改用 LCU 本地窗口")
        games = fetch_lcu(lcu, pu, args.games, args.workers)
        source = "LCU"

    # 无效对局（<5 分钟）不算 —— 见 _paths.MIN_GAME_SECONDS
    n_all = len(games or [])
    games = [g for g in (games or []) if valid_game(g)]
    if n_all != len(games):
        print(f"  排除 {n_all - len(games)} 局 <{MIN_GAME_SECONDS}s 的无效对局（挂机/重开）")
    if not games:
        print("✗ 一局有效对局都没抓到")
        sys.exit(1)
    games.sort(key=lambda g: g.get("ts") or 0)
    print(f"  拿到 {len(games)} 局有效对局 "
          f"（{time.strftime('%m-%d %H:%M', time.localtime(games[0]['ts'] / 1000))} → "
          f"{time.strftime('%m-%d %H:%M', time.localtime(games[-1]['ts'] / 1000))}）")

    # ---------- 主网络里已有的分 ----------
    PE, offset = load_pe()
    known = None
    if pu in PE:
        e = PE[pu] or {}
        if (e.get("n_games") or 0) >= RELIABLE_N:
            known = {"mmr": e.get("mmr"), "n_games": e.get("n_games")}
    if known:
        print(f"  ✓ 主网络已有分: {known['mmr']}（{known['n_games']} 局样本）")
    else:
        print(f"  主网络没有可用分（{'未出现' if pu not in PE else '样本 < %d 局' % RELIABLE_N}），"
              f"改用本地 Elo 走查")

    # ---------- 本地在线 Elo 走查 ----------
    # 对手分 = player_elo 的 mmr 还原回 Elo 原始刻度（mmr - offset），
    # 这样最后加回 offset 得到的还是隐藏分刻度。
    rating = ELO_BASE
    n_used = 0
    opp_counts = []
    rows = []
    for i, g in enumerate(games):
        me_p = next((p for p in g["players"] if p["puuid"] == pu), None)
        if me_p is None:
            continue
        opps = [p for p in g["players"] if p["puuid"] != pu]
        known_raw = [PE[p["puuid"]]["mmr"] - offset
                     for p in opps if p["puuid"] in PE
                     and (PE[p["puuid"]].get("n_games") or 0) >= RELIABLE_N]
        opp_counts.append(len(known_raw))
        if len(known_raw) >= MIN_OPP_KNOWN:
            rb = sum(known_raw) / len(known_raw)
            ea = 1.0 / (1.0 + 10 ** ((rb - rating) / ELO_SCALE))
            k = K_PROVISIONAL if n_used < N_PROVISIONAL else K_STEADY
            rating += k * ((1.0 if me_p["win"] else 0.0) - ea)
            n_used += 1
        rows.append({
            "gameId": g["gameId"], "ts": g.get("ts"), "dur": g.get("duration"),
            "win": bool(me_p["win"]),
            "championId": me_p.get("champion"), "champion": str(me_p.get("champion")),
            "k": me_p.get("k", 0), "d": me_p.get("d", 0), "a": me_p.get("a", 0),
            "opp_avg": (round(sum(known_raw) / len(known_raw) + offset, 1)
                        if known_raw else None),
            "opp_known": len(known_raw),
        })

    local = None
    if n_used:
        local = {
            "mmr": round(rating + offset, 1),
            "n_used": n_used,
            "n_games": len(rows),
            "opp_known_avg": round(sum(opp_counts) / max(len(opp_counts), 1), 1),
            "reliable": (sum(opp_counts) / max(len(opp_counts), 1)) >= MIN_OPP_KNOWN,
        }
        flag = "可信度尚可" if local["reliable"] else "⚠ 对手覆盖太低，只当参考"
        print(f"  ✓ 本地 Elo 走查: {local['mmr']}（用了 {n_used}/{len(rows)} 局，"
              f"每局有分对手 {local['opp_known_avg']} 人）— {flag}")

    # 英雄名映射（本地有 champ_map.json 就翻中文）
    if CHAMP_MAP_FILE.exists():
        cm = json.loads(CHAMP_MAP_FILE.read_text(encoding="utf-8"))
        for r in rows:
            r["champion"] = cm.get(str(r["championId"]), str(r["championId"]))

    # 新 → 旧，前端直接顺序渲染
    rows.reverse()

    wins = sum(1 for r in rows if r["win"])
    out = {
        "puuid": pu,
        "name": args.name or "",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "n_games": len(rows),
        "n_win": wins, "n_loss": len(rows) - wins,
        "win_rate": round(100 * wins / max(len(rows), 1), 1),
        "known": known,
        "local": local,
        "offset": round(offset, 1),
        "min_game_seconds": MIN_GAME_SECONDS,
        "games": rows,
    }
    atomic_write_text(OUT, json.dumps(out, ensure_ascii=False))
    print(f"✓ 保存 {OUT.name}: {len(rows)} 局 / 胜率 {out['win_rate']}% "
          f"/ 耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
