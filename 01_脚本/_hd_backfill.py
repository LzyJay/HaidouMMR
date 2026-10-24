"""定向 backfill：把主人最近 N 局中出现、但历史列表未被爬过的对手补抓 20 局
解决 BFS 650 名额被高频老对手占满、深夜新对手 n_games<5 的问题。"""
import json, time, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hd_crawl as HC
from _paths import raise_if_cancelled, atomic_write_text

port, token = HC.find_lcu()
if not port:
    # ⚠️ 退出码区分「失败原因」，桌面版日志里一眼能看出是哪一类：
    #    1 = 根本没找到客户端（没开 / 凭据读取坏了）
    #    2 = 客户端在，但数据状态不对（读不到召唤师 / 网络里没有主人的局）
    #    3 = 用户主动停止（_paths.raise_if_cancelled 抛的）
    print("✗ 未找到 League 客户端（没登录，或 LCU 凭据读取失败）"); sys.exit(1)
lcu = HC.LCU(port, token)
me = lcu.get("/lol-summoner/v1/current-summoner")
if not me or not me.get("puuid"):
    print("✗ 拿到了 LCU 凭据，但读不到当前召唤师"
          "（/lol-summoner/v1/current-summoner 返回空）——客户端可能正在切号/重连")
    sys.exit(2)
me_puuid = me["puuid"]

OUT = HC.OUT
old = json.loads(OUT.read_text(encoding="utf-8"))
# 顺手剪掉历史里遗留的无效对局（<5 分钟 = 挂机/重开），见 _paths.MIN_GAME_SECONDS。
# 不剪的话它们会一直跟着数据走，下游每个脚本都得各自再挡一次。
_dropped = [g for g in old["games"] if not HC.valid_game(g)]
games = {g["gameId"]: g for g in old["games"] if HC.valid_game(g)}
my_ids = {gid for gid in old["my_game_ids"] if gid in games}
print(f"载入 {len(games)} 局 / 主人 {len(my_ids)} 局"
      + (f"（剪掉 {len(_dropped)} 局 <{HC.MIN_GAME_SECONDS}s 的无效对局）" if _dropped else ""))

# 主人最近 N 局（按 ts 排序取后 N）
RECENT = 20
my_games_sorted = sorted([g for gid, g in games.items() if gid in my_ids], key=lambda g: g["ts"])
recent = my_games_sorted[-RECENT:]
if not recent:
    # 这一步以前会直接 `min()` 空序列 → ValueError，日志里只有一句 traceback，
    # 看不出「到底是客户端问题还是数据问题」。现在明确报原因。
    print(f"✗ 网络里匹配不到主人的对局：hd_data 的 my_game_ids 有 {len(old.get('my_game_ids') or [])} 个，"
          f"但在 {len(games)} 局里一个都没对上 —— 多半是 hd_data.json 被别的账号的数据覆盖了。"
          f"先跑 `_hd_crawl.py --seed` 重建，再重试本步。")
    sys.exit(2)
t0 = min(g["ts"] for g in recent)
print(f"最近 {len(recent)} 局从 {time.strftime('%m-%d %H:%M', time.localtime(t0/1000))} 起")

# 候选：这些局里所有对手（排除已爬过历史列表的）
def appears_in_network(pu):
    n = 0
    for gid, g in games.items():
        for p in g["players"]:
            if p["puuid"] == pu:
                n += 1
                break
        if n >= 5:
            return True
    return False

cands = set()
for g in recent:
    for p in g["players"]:
        if p["puuid"] != me_puuid:
            cands.add(p["puuid"])
# ⚠️ **必须是 list，不能是 set**（2026-09-22 修的）。
# 下面分批提交时要写 `cands[_i:_i + _CHUNK]`，而 set 不支持下标 →
#   TypeError: 'set' object is not subscriptable
# 这个 bug 是 2026-09-21 给本步加「协作式停止 + 分批提交」时引入的：
# 分批需要按序号切片，但 cands 一直是 set。**而且它只在 cands 非空时触发**
# （空的话上面一句就 `sys.exit(0)` 报"无需补抓"了），所以藏了很久没被发现 ——
# 典型症状就是桌面版每次都报「没有全部成功」，而这一步的输出里只有一句 TypeError。
# 用 sorted() 顺带让补抓顺序确定、可复现。
cands = sorted(pu for pu in cands if not appears_in_network(pu))
print(f"待补抓对手: {len(cands)} 人（网络内出现<5 局）")

if not cands:
    print("无需补抓"); sys.exit(0)

t0c = time.time()
new_ids = set()
crawled_ok = 0
def hist(pu):
    r = lcu.get(f"/lol-match-history/v1/products/lol/{pu}/matches?begIndex=0&endIndex=20")
    out = []
    if r:
        for g in r.get("games", {}).get("games", []):
            if g.get("queueId") == HC.QUEUE_HEX:
                out.append(g["gameId"])
    return pu, out

# 分批提交，批与批之间检查「用户是不是点了停止」。
# 一次性 submit 全部的话，中途停止要等所有任务跑完才返回（见 _hd_crawl.py 里的同款说明）。
_CHUNK = 64

with ThreadPoolExecutor(max_workers=16) as ex:
    done = 0
    for _i in range(0, len(cands), _CHUNK):
        raise_if_cancelled("补抓列表")
        for f in as_completed([ex.submit(hist, pu) for pu in cands[_i:_i + _CHUNK]]):
            pu, ids = f.result()
            crawled_ok += 1
            done += 1
            for gid in ids:
                if gid not in games:
                    new_ids.add(gid)
            # 逐人打进度：桌面版的进度条靠这行算百分比。
            # 不加限流的话这一步要几分钟，中间一行都没有就是黑盒。
            if done % 5 == 0 or done == len(cands):
                print(f"  [列表] {done}/{len(cands)} 人", flush=True)
print(f"列表 {crawled_ok} 人 -> 新局 {len(new_ids)} ({time.time()-t0c:.1f}s)")

# 抓新局详情
got = 0
def fetch(gid):
    return HC.norm_detail(lcu.get(f"/lol-match-history/v1/games/{gid}"))
with ThreadPoolExecutor(max_workers=16) as ex:
    done2 = 0
    _ids = list(new_ids)
    for _i in range(0, len(_ids), _CHUNK):
        raise_if_cancelled("补抓详情")
        for f in as_completed([ex.submit(fetch, gid) for gid in _ids[_i:_i + _CHUNK]]):
            d = f.result()
            done2 += 1
            if d:
                games[d["gameId"]] = d
                got += 1
                if any(p["puuid"] == me_puuid for p in d["players"]):
                    my_ids.add(d["gameId"])
            if done2 % 20 == 0 or done2 == len(_ids):
                print(f"  [详情] {done2}/{len(_ids)} 局", flush=True)
print(f"详情 +{got} 局, 累计 {len(games)} 局 / 主人 {len(my_ids)} 局")

nodes = {p["puuid"] for g in games.values() for p in g["players"]}
data = {
    "meta": {
        "summoner": old["meta"]["summoner"],
        "puuid": me_puuid,
        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "queue": HC.QUEUE_HEX,
        "n_games": len(games),
        "n_players": len(nodes),
        "n_my_games": len(my_ids),
        "n_hist_crawled": len(cands),
        "note": "backfill 定向补抓（深夜新对手）",
    },
    "games": list(games.values()),
    "my_game_ids": sorted(my_ids),
}
atomic_write_text(OUT, json.dumps(data, ensure_ascii=False))
print(f"✓ 保存 {OUT}: {len(games)} 局 / {len(nodes)} 玩家 / 主人 {len(my_ids)} 局")
