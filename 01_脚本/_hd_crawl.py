"""滚雪球 BFS 抓取海克斯大乱斗(queue 2400 / KIWI)的局部对局网络 -> hd_data.json

两条数据通道（与 sona 插件同源）:

① **SGP**（首选，sona 的主力通道）
   `{matchHistory}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY
    ?startIndex=0&count=100&tag=q_2400`  + `Authorization: Bearer <accessToken>`
   - `tag=q_2400` 直接按模式过滤，不用自己筛
   - 一次 **100 局**（2026-09-21 实测 11,876,155 字节 / 2.51s）；`count=200` 会 503，上限就是 100
   - `games[].json` **本身就是完整对局详情**：10 个 participants 是扁平的
     （`puuid` / `championId` / `kills` / `deaths` / `assists` / `win` / `teamId`
     都在第一层，没有 LCU 那种 `stats` 嵌套，也没有 `participantIdentities`），
     所以**一次请求就够**，不必再逐局拉 `/lol-match-history/v1/games/{gid}`
   - 凭据链：`/entitlements/v1/token` → accessToken；`/lol-chat/v1/me` → platformId
     → `sgpServerId = TENCENT_<platformId>` → 查 SGP 地址表拿 base

② **LCU 本地 REST**（回退）
   - 列表一页只给 20 局，本地缓存窗口约 21 局（`begIndex` 翻深了也拿不到）
   - 详情要逐局再请求一次 `/lol-match-history/v1/games/{gid}`

核心思路不变：
- 从"任何曾经同局的人"的战绩里反查主人的历史局
- BFS: 玩家 -> 其最近 N 局 2400 -> 拿到完整 10 人 -> 发现新玩家 -> 继续
- 副产品: 大规模共现超边, 用于复刻官方的谱分解

增量：无论走哪条通道，都是「从最新往前翻，遇到已抓过的 gameId 就停」——打几局抓几局。

用法: python _hd_crawl.py [--players 300] [--games 20] [--workers 16] [--no-sgp]
"""
from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lcu_creds2 import find_lcu  # noqa: E402
from _paths import DATA_DIR, MIN_GAME_SECONDS, valid_game, raise_if_cancelled, atomic_write_text  # noqa: E402

QUEUE_HEX = 2400
OUT = DATA_DIR / "hd_data.json"

# ---------- SGP 通道 ----------
#
# 地址表照搬 sona 插件的 Wd（国服 10 条）。key 是 sgpServerId。
# 国服的 platformId（HN1 / NJ100 / …）要先加 `TENCENT_` 前缀才是 sgpServerId。
SGP_BASE = {
    "TENCENT_HN1": "https://hn1-k8s-sgp.lol.qq.com:21019",
    "TENCENT_HN10": "https://hn10-k8s-sgp.lol.qq.com:21019",
    "TENCENT_TJ100": "https://tj100-sgp.lol.qq.com:21019",
    "TENCENT_TJ101": "https://tj101-sgp.lol.qq.com:21019",
    "TENCENT_NJ100": "https://nj100-sgp.lol.qq.com:21019",
    "TENCENT_GZ100": "https://gz100-sgp.lol.qq.com:21019",
    "TENCENT_CQ100": "https://cq100-sgp.lol.qq.com:21019",
    "TENCENT_BGP2": "https://bgp2-k8s-sgp.lol.qq.com:21019",
    "TENCENT_PBE": "https://pbe-sgp.lol.qq.com:21019",
    "TENCENT_PREPBE": "https://prepbe-sgp.lol.qq.com:21019",
}
# sona 的 gN 集合：命中这些 platformId 才需要加 TENCENT_ 前缀
TENCENT_PLATFORMS = {
    "HN1", "HN2", "HN3", "HN4", "HN5", "HN6", "HN7", "HN8", "HN9", "HN10",
    "HN11", "HN12", "HN13", "HN14", "HN15", "HN16",
    "NJ100", "GZ100", "CQ100", "TJ100", "TJ101", "BGP2", "PBE", "PREPBE",
}
SGP_MAX = 100          # 实测 count=200 会 503，100 是硬上限
SGP_UA = "LeagueOfLegendsClient/14.13.596.7996 (rcp-be-lol-match-history)"


class LCU:
    def __init__(self, port: int, token: str):
        self.base = f"https://127.0.0.1:{port}"
        self.auth = base64.b64encode(f"riot:{token}".encode()).decode()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        self.n_ok = 0
        self.n_fail = 0

    def get(self, path: str, timeout: int = 25, retries: int = 2):
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                self.base + path,
                headers={"Authorization": f"Basic {self.auth}", "Accept": "application/json"},
            )
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    raw = resp.read().decode()
                    self.n_ok += 1
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                self.n_fail += 1
                return None
            except Exception:
                if attempt < retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                self.n_fail += 1
                return None
        return None


class SGP:
    """SGP 战绩通道 —— sona 的主力通道，突破 LCU 的本地窗口上限。

    凭据全部从 LCU 拿（所以要先把客户端跑起来）：
        /entitlements/v1/token  -> accessToken
        /lol-chat/v1/me         -> platformId -> sgpServerId = TENCENT_<platformId>
    然后按 sgpServerId 查地址表拿 base，带 `Bearer accessToken` 请求。
    """

    def __init__(self, lcu: LCU):
        self.ok = False
        self.sid = ""
        self.base = ""
        self.access = ""
        self.n_ok = 0
        self.n_fail = 0
        try:
            tok = lcu.get("/entitlements/v1/token") or {}
            self.access = tok.get("accessToken") or ""
            chat = lcu.get("/lol-chat/v1/me") or {}
            plat = (chat.get("platformId") or "").upper()
            self.sid = f"TENCENT_{plat}" if plat in TENCENT_PLATFORMS else plat
            self.base = SGP_BASE.get(self.sid, "")
        except Exception:
            pass
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        self.ok = bool(self.access and self.base)
        self.err = "" if self.ok else (
            "拿不到 accessToken" if not self.access
            else f"没有 {self.sid or '未知服'} 的 SGP 地址")

    def summary(self, puuid: str, start: int = 0, count: int = SGP_MAX,
                tag: str = "q_2400", timeout: int = 60, retries: int = 2):
        """返回 games 列表（每项 {metadata, json}）；失败返回 None。"""
        if not self.ok:
            return None
        count = max(1, min(count, SGP_MAX))
        q = urllib.parse.urlencode({"startIndex": start, "count": count, "tag": tag})
        url = (f"{self.base}/match-history-query/v1/products/lol/player/"
               f"{puuid}/SUMMARY?{q}")
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + self.access,
                "User-Agent": SGP_UA,
                "Accept": "application/json",
            })
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    d = json.loads(resp.read().decode("utf-8", "replace"))
                self.n_ok += 1
                if isinstance(d, dict):
                    return d.get("games") or []
                return d or []
            except urllib.error.HTTPError as e:
                # 503 是 SGP 的限流/超时；退避重试
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                self.n_fail += 1
                return None
            except Exception:
                if attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                self.n_fail += 1
                return None
        return None


def norm_sgp(g: dict) -> dict | None:
    """把 SGP 的 `games[]` 一项转成和 norm_detail() 完全一样的结构。

    SGP 的 `json` 就是完整对局详情，但字段是**扁平**的：
        participants[].puuid / championId / kills / deaths / assists / win / teamId
    没有 LCU 的 `stats` 嵌套，也没有 `participantIdentities`（puuid 直接挂在人身上）。
    所以这里不能复用 norm_detail()，得单独写一份。
    """
    j = (g or {}).get("json") or {}
    if j.get("queueId") != QUEUE_HEX:
        return None
    # 5 分钟以内 = 无效对局（挂机/重开），收了只会污染 Elo 和统计。
    # 见 _paths.MIN_GAME_SECONDS 的完整说明。
    if (j.get("gameDuration") or 0) < MIN_GAME_SECONDS:
        return None
    parts = j.get("participants") or []
    if len(parts) < 10:
        return None
    players = []
    for p in parts:
        if not p.get("puuid"):
            return None
        name = p.get("riotIdGameName") or p.get("summonerName") or "?"
        tagline = p.get("riotIdTagline") or ""
        players.append({
            "puuid": p["puuid"],
            "name": f"{name}#{tagline}" if tagline else name,
            "win": bool(p.get("win")),
            "k": p.get("kills", 0), "d": p.get("deaths", 0), "a": p.get("assists", 0),
            "champion": p.get("championId"),
            "team": p.get("teamId"),
        })

    # 胜方校验 —— 和 norm_detail() 同一套兜底逻辑（畸形对局会让 Elo 除零）
    n_win = sum(1 for p in players if p["win"])
    if n_win == 0 or n_win == len(players):
        win_team = next((t.get("teamId") for t in (j.get("teams") or [])
                         if t.get("win")), None)
        if win_team is not None:
            for p in players:
                p["win"] = (p["team"] == win_team)
        fixed = sum(1 for p in players if p["win"])
        if win_team is None or fixed == 0 or fixed == len(players):
            print(f"  ⚠ 丢弃畸形对局(SGP) {j.get('gameId')}：{len(players)} 人 win 全为 "
                  f"{'true' if n_win else 'false'}，teams 里也拿不到胜方", flush=True)
            return None
        print(f"  ⚠ 修复畸形对局(SGP) {j.get('gameId')}：按 teams 胜方 {win_team} 重算 win "
              f"({fixed}/{len(players)} 胜)", flush=True)

    return {"gameId": j["gameId"], "ts": j.get("gameCreation", 0),
            "duration": j.get("gameDuration", 0), "queueId": QUEUE_HEX,
            "players": players}


def norm_detail(det: dict) -> dict | None:
    if not det or det.get("queueId") != QUEUE_HEX:
        return None
    # 5 分钟以内 = 无效对局（挂机/重开）—— 见 _paths.MIN_GAME_SECONDS
    if (det.get("gameDuration") or 0) < MIN_GAME_SECONDS:
        return None
    ident = {pi["participantId"]: pi["player"] for pi in det.get("participantIdentities", [])}
    parts = det.get("participants", [])
    if len(ident) < 10 or len(parts) < 10:
        return None
    players = []
    for p in parts:
        pl = ident.get(p.get("participantId")) or {}
        st = p.get("stats", {})
        if not pl.get("puuid"):
            return None
        players.append({
            "puuid": pl["puuid"],
            "name": f"{pl.get('gameName')}#{pl.get('tagLine')}",
            "win": bool(st.get("win")),
            "k": st.get("kills", 0), "d": st.get("deaths", 0), "a": st.get("assists", 0),
            "champion": p.get("championId"),
            "team": st.get("teamId", p.get("teamId")),
        })

    # 胜方校验：LCU 偶尔会返回「10 个人 win 全是 false」的畸形记录。
    #
    # 2026-09-21 实测 gameId 10000000004（624 秒的对局，team 100/200 各 5 人，
    # 但 participants[].stats.win 全为 false）。这种记录：
    #   ① 拿去算 Elo 会让「胜方平均分」除以 0，直接把 _player_elo_build.py 打崩；
    #   ② 拿去统计胜率也是错的。
    # 所以先试着用 teams[].win 里的真实胜方把它修好；修不了就丢掉并告警 ——
    # 绝不让一条坏记录污染整份数据。
    n_win = sum(1 for p in players if p["win"])
    if n_win == 0 or n_win == len(players):
        win_team = next((t.get("teamId") for t in (det.get("teams") or [])
                         if t.get("win")), None)
        if win_team is not None:
            for p in players:
                p["win"] = (p["team"] == win_team)
        fixed = sum(1 for p in players if p["win"])
        if win_team is None or fixed == 0 or fixed == len(players):
            print(f"  ⚠ 丢弃畸形对局 {det.get('gameId')}：{len(players)} 人 win 全为 "
                  f"{'true' if n_win else 'false'}，teams 里也拿不到胜方", flush=True)
            return None
        print(f"  ⚠ 修复畸形对局 {det.get('gameId')}：按 teams 胜方 {win_team} 重算 win "
              f"({fixed}/{len(players)} 胜)", flush=True)

    return {"gameId": det["gameId"], "ts": det.get("gameCreation", 0),
            "duration": det.get("gameDuration", 0), "queueId": QUEUE_HEX, "players": players}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=300, help="最多扩展多少玩家的战绩列表")
    ap.add_argument("--games", type=int, default=20,
                    help="每个玩家取其最近多少局（SGP 通道下就是 count，纯 2400，上限 100）")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--seed", action="store_true", help="从已有 hd_data.json 继续扩展")
    ap.add_argument("--seed-pages", type=int, default=10,
                    help="种子最多往前翻几页（SGP 一页 100 局 / LCU 一页 20 局）；"
                         "增量模式下遇到已抓过的局就提前停")
    ap.add_argument("--no-sgp", action="store_true",
                    help="只用 LCU 本地窗口，不碰 SGP 通道（调试用）")
    # ---------- 数据主人可切换（2026-09-22 用户要求「视角是谁就抓谁」） ----------
    #
    # 默认主人 = 当前登录账号。给 `--me <puuid>` 就把主人换成别人：
    #   · 种子改成按那个 puuid 拉 TA 的战绩（SGP 通道本来就按 puuid 查，天然支持）
    #   · `meta.puuid` / `meta.summoner` / `my_game_ids` 一并换成 TA
    #   · 网络本身是**并集** —— `--seed` 只增不减，旧的对局全部保留，
    #     所以换回去只要再抓一次种子（遇到已抓过的局会立刻停，很快）
    ap.add_argument("--me", type=str, default=None,
                    help="把数据主人换成这个 puuid（默认=当前登录账号）")
    ap.add_argument("--me-name", type=str, default=None,
                    help="主人的显示名（Riot ID，形如 名字#1234）；不给就从数据里找")
    args = ap.parse_args()

    port, token = find_lcu()
    if not port:
        print("✗ 未找到 League 客户端(LeagueClientUx.exe 未运行)")
        sys.exit(1)
    lcu = LCU(port, token)
    me = lcu.get("/lol-summoner/v1/current-summoner")
    if not me:
        print("✗ LCU 连接失败")
        sys.exit(1)
    login_puuid = me.get("puuid")
    login_name = f"{me.get('gameName')}#{me.get('tagLine')}"
    print(f"✓ LCU 登录账号: {login_name}")

    # ---------- 数据主人（_paths / app.py 传 --me 时可以不是登录账号） ----------
    # 为什么允许「主人 ≠ 登录账号」：用户要求「看板视角是谁，抓取/估算就算谁」——
    # 换人要能换主人，而不是只能看自己。网络仍是并集，所以这是可逆的。
    me_puuid = (args.me or "").strip() or login_puuid
    owner_is_login = (me_puuid == login_puuid)
    me_name = ((args.me_name or "").strip() or (login_name if owner_is_login else "")) or None
    if not owner_is_login:
        print(f"★ 数据主人切换为 --me 指定的玩家: {me_puuid[:8]}…"
              f"（登录账号是 {login_name}；网络为并集，旧对局全部保留）")

    # ---- 防呆（2026-09-28 实机崩溃根因修复）----
    # 正常「抓新局并重算」不带 --me，此时 me_puuid 直接取**当前登录账号**。
    # 如果当时 LCU 登录的是**别的号**，就会静默把那个号的对局写进 my_game_ids / meta，
    # 而看板重建时遇到「my_game_ids 里不含数据主人」的局会 StopIteration 崩。
    # 因此：登录账号 ≠ 现有数据主人 且 没显式 --me 时，**直接拒绝**并给出明确指引，
    # 而不是污染数据。要抓别的号请用「切换数据主人」按钮（会带 --me）。
    if not (args.me or "").strip() and OUT.exists():
        try:
            _old_pu = json.loads(OUT.read_text(encoding="utf-8")).get("meta", {}).get("puuid")
        except Exception:
            _old_pu = None
        if _old_pu and _old_pu != login_puuid:
            print(f"✗ 当前登录账号 {login_name} ({login_puuid[:8]}…) 与现有数据主人"
                  f" {_old_pu[:8]}… 不一致，且未用 --me 指定主人。")
            print("  为避免把别的号的对局混进数据（会导致看板重建崩溃），已中止。请二选一：")
            print("    ① 在 League 客户端登录正确的账号后重试；")
            print(f"    ② 用「切换数据主人」按钮，或显式 --me {_old_pu} 指定要抓的号。")
            sys.exit(1)

    # SGP 通道（sona 的主力通道）：一次 100 局，且响应里带完整详情
    sgp = SGP(lcu)
    if args.no_sgp:
        sgp.ok = False
        sgp.err = "命令行指定了 --no-sgp"
    if sgp.ok:
        print(f"✓ SGP: {sgp.sid}  {sgp.base}  （一次最多 {SGP_MAX} 局）")
    else:
        print(f"⚠ SGP 不可用（{sgp.err}），改用 LCU 本地窗口（约 21 局）")

    games: dict[int, dict] = {}
    if args.seed and OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        dropped = 0
        for g in old.get("games", []):
            # 顺手剪掉历史里遗留的短局（<5 分钟）——
            # 早期版本没有这个过滤，不清掉的话它们会一直跟着数据走，
            # 而且下游每个脚本都得各自再挡一次。这里剪一次就自愈了。
            if not valid_game(g):
                dropped += 1
                continue
            games[g["gameId"]] = g
        print(f"  载入已有 {len(games)} 局, 继续扩展"
              + (f"（剪掉 {dropped} 局 <{MIN_GAME_SECONDS}s 的无效对局）" if dropped else ""))

    # ---------- 数据主人的显示名（换人时用得上） ----------
    def _name_of(puuid):
        """在已载入的对局里找这个 puuid 的显示名（完整 Riot ID 名字#后缀）。"""
        for g in games.values():
            for p in g["players"]:
                if p["puuid"] == puuid and p.get("name"):
                    return p["name"]
        return None

    if me_name is None and OUT.exists():
        try:
            _old = json.loads(OUT.read_text(encoding="utf-8"))
            # 老数据里如果记的正好是这个 puuid，直接沿用它的名字
            # （换回旧主人时省得每次都要填 --me-name）
            if _old.get("meta", {}).get("puuid") == me_puuid:
                me_name = _old["meta"].get("summoner")
        except Exception:
            pass
    if me_name is None:
        me_name = _name_of(me_puuid)
    if me_name:
        print(f"  数据主人: {me_name}")

    # 种子: 主人近期的 2400 对局 —— 只抓「还没抓过」的那几局
    #
    # 无论走哪条通道，逻辑都是「从最新往前翻，遇到已抓过的 gameId 就停」。
    # 列表按时间倒序，碰到旧的说明后面全是旧的 —— 所以打几局就抓几局，打多少都不漏。
    #
    # SGP 优先：tag=q_2400 直接过滤，一页 100 局，而且 games[].json 就是完整详情，
    #           不用再逐局请求（实测一次 11.8MB / 2.5s）。
    # LCU 回退：一页只有 20 局，本地窗口约 21 局，够用但不保险。
    seed_ids: list[int] = []
    seen_ids: set[int] = set()
    hit_old = False
    src = ""
    pages_read = 0

    if sgp.ok:
        page = 0
        while page < max(1, args.seed_pages):
            gs = sgp.summary(me_puuid, start=page * SGP_MAX, count=SGP_MAX, tag="q_2400")
            if gs is None:
                print(f"  ⚠ SGP 第 {page + 1} 页查询失败，本步改走 LCU 本地窗口")
                sgp.ok = False
                seed_ids, seen_ids, hit_old = [], set(), False
                break
            if not gs:
                break
            src = "SGP"
            pages_read += 1
            fresh = 0
            for g in gs:
                gid = ((g or {}).get("json") or {}).get("gameId")
                if not gid or gid in seen_ids:
                    continue
                seen_ids.add(gid)
                if gid in games:
                    hit_old = True      # 已抓过 → 后面都是旧的，收工
                    break
                d = norm_sgp(g)
                if d:
                    games[d["gameId"]] = d     # SGP 直接给详情，不用再拉
                    seed_ids.append(gid)
                    fresh += 1
            if hit_old or len(gs) < SGP_MAX:
                break
            if fresh == 0:
                break                       # 服务器忽略了 startIndex，别再空翻
            page += 1

    if not src:
        # ---------- LCU 回退 ----------
        PAGE = 20
        max_pages = args.seed_pages if (args.seed and games) else 1
        for pi in range(max_pages):
            # 这里按 puuid 查，所以 --me 换主人时也走同一条路（LCU 支持任意 puuid）。
            r = lcu.get(f"/lol-match-history/v1/products/lol/{me_puuid}/matches"
                        f"?begIndex={pi * PAGE}&endIndex={(pi + 1) * PAGE}")
            gs = (r or {}).get("games", {}).get("games", [])
            if not gs:
                break
            src = "LCU"
            pages_read += 1
            fresh = 0
            for g in gs:
                if g.get("queueId") != QUEUE_HEX:
                    continue
                gid = g["gameId"]
                if gid in seen_ids:
                    continue
                seen_ids.add(gid)
                if gid in games:
                    hit_old = True          # 已抓过 → 后面都是旧的，收工
                    break
                seed_ids.append(gid)
                fresh += 1
            if hit_old or len(gs) < PAGE:
                break
            if fresh == 0 and pi > 0:
                break                       # 服务器忽略了 begIndex，别再空翻

    unit = "页 × 100 局" if src == "SGP" else "页 × 20 局"
    print(f"  种子窗口[{src or '无'}]: 翻了 {pages_read} {unit}"
          f"{'，遇到已抓过的局就停' if hit_old else ''}")
    if games:
        print(f"  增量对比: 已有 {len(games) - len(seed_ids)} 局 → 这次新局 {len(seed_ids)} 局"
              + ("（没有新打的）" if not seed_ids else ""))
    else:
        print(f"  首次抓取: 这次拿到 {len(seed_ids)} 局")

    # LCU 通道只拿到 gameId，详情还得逐局拉；SGP 通道已经在上面填好了
    need_detail = [gid for gid in seed_ids if gid not in games]
    if need_detail:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for f in as_completed([ex.submit(lcu.get, f"/lol-match-history/v1/games/{gid}")
                                   for gid in need_detail]):
                d = norm_detail(f.result())
                if d:
                    games[d["gameId"]] = d
    print(f"  种子局详情: 累计 {len(games)} 局")
    raise_if_cancelled("种子局")

    crawled: set[str] = set()
    my_games: set[int] = {gid for gid, g in games.items()
                          if any(p["puuid"] == me_puuid for p in g["players"])}

    def players_in(gid):
        return [p["puuid"] for p in games[gid]["players"]]

    # BFS
    for rnd in range(1, 21):
        # 每轮开头检查一次「用户是不是点了停止」—— 一轮 300 人 × N 线程大约十几秒，
        # 这个粒度对「中途停止」够用，而且停在**轮与轮之间**（还没开始写盘）最安全。
        raise_if_cancelled(f"第 {rnd} 轮 BFS")
        # 候选: 图里还没拉过战绩列表的人, 按"与主人共现次数"降序(一阶优先)
        cocur: dict[str, int] = {}
        for gid, g in games.items():
            w = 2 if gid in my_games else 1
            for p in g["players"]:
                if p["puuid"] != me_puuid and p["puuid"] not in crawled:
                    cocur[p["puuid"]] = cocur.get(p["puuid"], 0) + w
        if not cocur:
            break
        budget = args.players - len(crawled)
        if budget <= 0:
            break
        batch = sorted(cocur.items(), key=lambda kv: -kv[1])[:budget]
        batch = [pu for pu, _ in batch]
        print(f"\n[R{rnd}] 待扩展 {len(batch)} 人 (已抓列表 {len(crawled)}) ...")

        # 1) 拉战绩列表 -> gameId（SGP 通道顺带把详情一起带回来）
        t0 = time.time()
        new_ids: set[int] = set()
        got = 0

        def hist_sgp(pu):
            """SGP：tag=q_2400 直接过滤，一次 count 就是 count 局纯 2400 + 完整详情。"""
            gs = sgp.summary(pu, start=0, count=args.games, tag="q_2400")
            return pu, gs

        def hist_lcu(pu):
            r = lcu.get(f"/lol-match-history/v1/products/lol/{pu}/matches?begIndex=0&endIndex={args.games}")
            out = []
            if r:
                for g in r.get("games", {}).get("games", []):
                    if g.get("queueId") == QUEUE_HEX:
                        out.append(g["gameId"])
            return pu, out

        # 分批提交（每批 workers*4 个），批与批之间检查停止标志。
        # 为什么不一次性 submit 300 个：那样中途点停止，异常虽然立刻抛出，
        # 但 ThreadPoolExecutor.__exit__ 会 shutdown(wait=True) 把 300 个任务
        # 全部等完才返回 —— 停止按钮等于没用。分批之后最多多等一批（几秒）。
        _chunk = max(4, args.workers * 4)

        if sgp.ok:
            for _i in range(0, len(batch), _chunk):
                raise_if_cancelled("SGP 列表")
                with ThreadPoolExecutor(max_workers=args.workers) as ex:
                    for f in as_completed([ex.submit(hist_sgp, pu) for pu in batch[_i:_i + _chunk]]):
                        pu, gs = f.result()
                        if gs is None:
                            sgp.ok = False          # 通道挂了，剩下的轮次走 LCU
                            continue                # 这个人不算抓过，下一轮还能补
                        crawled.add(pu)
                        for g in gs:
                            gid = ((g or {}).get("json") or {}).get("gameId")
                            if gid and gid not in games:
                                new_ids.add(gid)
                        # 详情已在响应里，直接落
                        for g in gs:
                            d = norm_sgp(g)
                            if d and d["gameId"] not in games:
                                games[d["gameId"]] = d
                                got += 1
                                if any(p["puuid"] == me_puuid for p in d["players"]):
                                    my_games.add(d["gameId"])
            print(f"   [SGP] 列表 {len(batch)} 人 -> 新局 {len(new_ids)}，"
                  f"其中 {got} 局详情已就地带回 ({time.time()-t0:.1f}s, "
                  f"ok={sgp.n_ok}/fail={sgp.n_fail})")
            if not sgp.ok:
                print("   ⚠ SGP 中途不可用，本轮剩余玩家改走 LCU")
        else:
            for _i in range(0, len(batch), _chunk):
                raise_if_cancelled("LCU 列表")
                with ThreadPoolExecutor(max_workers=args.workers) as ex:
                    for f in as_completed([ex.submit(hist_lcu, pu) for pu in batch[_i:_i + _chunk]]):
                        pu, ids = f.result()
                        crawled.add(pu)
                        for gid in ids:
                            if gid not in games:
                                new_ids.add(gid)
            print(f"   [LCU] 列表 {len(batch)} 人 -> 新局 {len(new_ids)} ({time.time()-t0:.1f}s)")

        # 2) 拉详情（SGP 已带回的不用再拉；只剩 LCU 通道的 gameId 需要逐局请求）
        todo = [gid for gid in new_ids if gid not in games]
        if todo:
            t0 = time.time()
            for _i in range(0, len(todo), _chunk):
                raise_if_cancelled("对局详情")
                with ThreadPoolExecutor(max_workers=args.workers) as ex:
                    for f in as_completed([ex.submit(lcu.get, f"/lol-match-history/v1/games/{gid}")
                                           for gid in todo[_i:_i + _chunk]]):
                        d = norm_detail(f.result())
                        if d:
                            games[d["gameId"]] = d
                            got += 1
                            if any(p["puuid"] == me_puuid for p in d["players"]):
                                my_games.add(d["gameId"])
            print(f"   详情 +{got} 局, 累计 {len(games)} 局, 含主人 {len(my_games)} 局 "
                  f"({time.time()-t0:.1f}s, lcu ok={lcu.n_ok}/fail={lcu.n_fail})")
        elif new_ids:
            print(f"   详情已随 SGP 带回, 累计 {len(games)} 局, 含主人 {len(my_games)} 局")
        if len(crawled) >= args.players:
            break

    # ---------- 落盘 ----------
    # 主人一局都没有 → 十有八九是 --me 给错了（或那个号根本不打海斗）。
    # ⚠️ 这种情况**绝不能写盘**：写下去就把 meta.puuid 换成了一个空主人，
    # 下游每个脚本都会因为「找不到主人的对局」而整条跑不下去。
    if not my_games:
        print(f"\n✗ 数据主人 {me_name or me_puuid[:8]} 在这份网络里一局都没有，"
              f"不写盘（保留上一版 hd_data.json）")
        sys.exit(1)

    if me_name is None:
        me_name = _name_of(me_puuid) or (me_puuid[:8] + "…")

    # 记下「上一次主人是谁」：网络是并集，所以换回去只要再跑一次种子。
    prev_puuid = None
    try:
        if OUT.exists():
            _p = json.loads(OUT.read_text(encoding="utf-8")).get("meta", {}).get("puuid")
            if _p and _p != me_puuid:
                prev_puuid = _p
    except Exception:
        pass

    nodes = {p["puuid"] for g in games.values() for p in g["players"]}
    data = {
        "meta": {
            "summoner": me_name,
            "puuid": me_puuid,
            "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "queue": QUEUE_HEX,
            "n_games": len(games),
            "n_players": len(nodes),
            "n_my_games": len(my_games),
            "n_hist_crawled": len(crawled),
        },
        "games": list(games.values()),
        "my_game_ids": sorted(my_games),
    }
    if prev_puuid:
        data["meta"]["prev_puuid"] = prev_puuid
        data["meta"]["owner_switched_at"] = data["meta"]["crawled_at"]
    atomic_write_text(OUT, json.dumps(data, ensure_ascii=False))
    print(f"\n✓ 保存 {OUT}: {len(games)} 局 / {len(nodes)} 玩家 / "
          f"数据主人 {me_name} {len(my_games)} 局")


if __name__ == "__main__":
    main()
