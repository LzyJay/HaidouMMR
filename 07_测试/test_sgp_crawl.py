# -*- coding: utf-8 -*-
"""验证 _hd_crawl.py 的 SGP 通道（不碰真实 hd_data.json）。

把 HC.OUT 指到临时文件再调 HC.main()，所以怎么跑都不会动真实数据。

用法：
    python 07_测试\\test_sgp_crawl.py empty    # 空基线 → SGP 一次 100 局 + 详情
    python 07_测试\\test_sgp_crawl.py incr     # 基线 5 局 → 新局 0（遇旧即停）
    python 07_测试\\test_sgp_crawl.py lag      # 缺最近 60 局 → 一次全补回
    python 07_测试\\test_sgp_crawl.py bfs      # BFS 扩人也走 SGP
    python 07_测试\\test_sgp_crawl.py nosgp    # --no-sgp 回退到 LCU（21 局）

需要 League 客户端在线（SGP 凭据从 LCU 拿）。
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "01_脚本"))
import _hd_crawl as HC  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sgp_test_"))
REAL = HC.OUT
print("真实数据:", REAL, REAL.stat().st_size // 1024, "KB")
print("临时目录:", TMP)

which = sys.argv[1] if len(sys.argv) > 1 else "empty"


def run(argv, out_path):
    HC.OUT = out_path
    sys.argv = ["_hd_crawl.py"] + argv
    t0 = time.time()
    print("\n" + "=" * 70)
    print(">>> _hd_crawl.py", " ".join(argv))
    print("=" * 70)
    try:
        HC.main()
    except SystemExit as e:
        print("SystemExit", e.code)
    print("耗时 %.1fs" % (time.time() - t0))
    HC.OUT = REAL


if which == "empty":
    # A) 空基线：SGP 一次应该拿 100 局（第一页），详情就地带回
    out = TMP / "empty.json"
    run(["--seed", "--players", "0", "--seed-pages", "1"], out)
    if out.exists():
        d = json.loads(out.read_text(encoding="utf-8"))
        gs = d["games"]
        print("\n结果: %d 局 / %d 玩家 / 主人 %d 局" % (
            len(gs), d["meta"]["n_players"], d["meta"]["n_my_games"]))
        ts = sorted(g["ts"] for g in gs)
        print("时间跨度: %s -> %s" % (
            time.strftime("%m-%d %H:%M", time.localtime(ts[0] / 1000)),
            time.strftime("%m-%d %H:%M", time.localtime(ts[-1] / 1000))))
        bad = [g["gameId"] for g in gs
               if sum(1 for p in g["players"] if p["win"]) not in (5,)]
        print("胜负分布异常的局:", bad if bad else "无（全部 5 胜 5 负）")
        print("样本:", json.dumps(gs[0], ensure_ascii=False)[:300])

elif which == "incr":
    # B) 增量：基线里放最近 5 局，应该只抓这 5 局之外的（0 局）
    out = TMP / "incr.json"
    real = json.loads(REAL.read_text(encoding="utf-8"))
    mine = [g for g in real["games"] if any(
        p["puuid"] == real["meta"]["puuid"] for p in g["players"])]
    mine.sort(key=lambda g: -g["ts"])
    base = {"meta": real["meta"], "games": mine[:5],
            "my_game_ids": [g["gameId"] for g in mine[:5]]}
    out.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    print("基线 = 主人最近 5 局:", [g["gameId"] for g in mine[:5]])
    run(["--seed", "--players", "0", "--seed-pages", "3"], out)
    d = json.loads(out.read_text(encoding="utf-8"))
    print("\n结果: %d 局（基线 5 + 新抓 %d）" % (len(d["games"]), len(d["games"]) - 5))

elif which == "lag":
    # C) 模拟「攒了 60 局没抓」：基线里放主人的第 61 局往前，应该一次补回 60 局
    out = TMP / "lag.json"
    real = json.loads(REAL.read_text(encoding="utf-8"))
    mine = [g for g in real["games"] if any(
        p["puuid"] == real["meta"]["puuid"] for p in g["players"])]
    mine.sort(key=lambda g: -g["ts"])
    keep = mine[60:]
    base = {"meta": real["meta"], "games": keep,
            "my_game_ids": [g["gameId"] for g in keep]}
    out.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    print("基线 = 主人第 61 局往前（%d 局），缺最近 60 局" % len(keep))
    run(["--seed", "--players", "0", "--seed-pages", "3"], out)
    d = json.loads(out.read_text(encoding="utf-8"))
    new = len(d["games"]) - len(keep)
    got = {g["gameId"] for g in d["games"]}
    want = {g["gameId"] for g in mine[:60]}
    print("\n结果: 新抓 %d 局；目标 60 局里抓到 %d 局" % (new, len(got & want)))
    print("漏掉的:", sorted(want - got) or "无")

elif which == "nosgp":
    # D) --no-sgp 回退路径还能跑
    out = TMP / "nosgp.json"
    run(["--seed", "--players", "0", "--no-sgp", "--seed-pages", "1"], out)
    if out.exists():
        d = json.loads(out.read_text(encoding="utf-8"))
        print("\n结果(LCU): %d 局" % len(d["games"]))

elif which == "bfs":
    # E) BFS 扩展阶段也走 SGP：30 人 × 5 局
    out = TMP / "bfs.json"
    # 先用空基线跑一次拿到 100 局当底
    HC.OUT = out
    sys.argv = ["_hd_crawl.py", "--seed", "--players", "0", "--seed-pages", "1"]
    HC.main()
    HC.OUT = REAL
    run(["--seed", "--players", "30", "--games", "5", "--workers", "8"], out)
    d = json.loads(out.read_text(encoding="utf-8"))
    print("\n结果: %d 局 / %d 玩家 / 主人 %d 局 / 抓过列表 %d 人" % (
        len(d["games"]), d["meta"]["n_players"], d["meta"]["n_my_games"],
        d["meta"]["n_hist_crawled"]))
    bad = [g["gameId"] for g in d["games"]
           if sum(1 for p in g["players"] if p["win"]) != 5]
    print("胜负分布异常的局:", bad if bad else "无（全部 5 胜 5 负）")
    # 检查每局都是 10 人且 queueId 对
    print("非 10 人的局:", [g["gameId"] for g in d["games"] if len(g["players"]) != 10] or "无")

print("\nDONE", TMP)
