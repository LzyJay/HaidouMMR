# -*- coding: utf-8 -*-
"""**数据主人一致性**回归测试（2026-09-28 第十轮崩溃修复后新增）。

锁死「抓新局并重算」崩溃的根因不变量：

  ① `my_game_ids` 里**每一局都必须含数据主人（me）**。
     —— 否则 `_dash_rebuild.py` 在 `p = next(x for x in g["players"] if x["puuid"] == me)`
        处 `StopIteration` 整个看板打崩（2026-09-28 实机崩溃正是这个）。
  ② 全库**含数据主人的局**都应该进 `my_game_ids`（主人无遗漏）。
  ③ `meta.summoner` 必须 == 数据主人在对局里的 Riot ID（名字不能串到别的号）。

这些不变量在「换账号抓取」时最容易被破坏：`_hd_crawl.py` 现在已加防呆
（登录账号 ≠ 现有主人且没 --me 时直接拒绝），这里再补一道断言兜底。

跑法：python 07_测试\\test_owner_consistency.py
"""
import os
import sys
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 审查 M14：改从 _check 拿 check/finish。原来 import _v6_common，违反它头部
# 「本模块不可当公共 helper import」的明文禁令 —— 本文件只需要 hd_data.json，
# 却被隐式绑死「dashboard 数据必须存在」，缺失时报错文案也风马牛不相及，
# 还白加载 0.8MB。_check 正是为这种零依赖断言建的。
from _check import check, finish   # noqa: E402  零副作用断言计数器

# 02_数据 与 07_测试 同级（共享包根目录下），直接按位置推导，避免依赖 _paths 的搜索路径。
DATA_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent / "02_数据"

hd = json.load(open(DATA_DIR / "hd_data.json", encoding="utf-8"))
me = hd["meta"]["puuid"]
my_ids = set(hd["my_game_ids"])
by_gid = {g["gameId"]: g for g in hd["games"]}

print("数据主人一致性回归：meta.puuid=%s  my_game_ids=%d  全库局数=%d"
      % (me[:8] + "…", len(my_ids), len(hd["games"])))

def players_of(gid):
    g = by_gid.get(gid)
    return g.get("players", []) if g else []

puuids_of = lambda g: {p.get("puuid") for p in players_of(g)}

# ---------------------------------------------------------- ① 无孤儿 ----
# my_game_ids 里任何一局都必须含数据主人，否则 _dash_rebuild 会 StopIteration。
orphan = [gid for gid in my_ids if me not in puuids_of(gid)]
check("my_game_ids 每局都含数据主人（无孤儿，否则看板重建 StopIteration）",
      not orphan, "孤儿 %d 局: %s" % (len(orphan), orphan[:5]))

# ----------------------------------------------------- ② 主人无遗漏 ----
# 全库含主人的局都应进 my_game_ids（crawl 的 my_games 就是这个集合）。
all_me = [g["gameId"] for g in hd["games"] if me in puuids_of(g["gameId"])]
missing = [gid for gid in all_me if gid not in my_ids]
check("全库含主人的局都已进 my_game_ids（主人无遗漏）",
      not missing, "遗漏 %d 局" % len(missing))

# ------------------------------------------------- ③ 名字与主人自洽 ----
# meta.summoner 必须是数据主人自己的 Riot ID（不能串到别的号）。
name_of_me = None
for g in hd["games"]:
    for p in g["players"]:
        if p.get("puuid") == me and p.get("name"):
            name_of_me = p["name"]
            break
    if name_of_me:
        break
check("meta.summoner 与数据主人的 Riot ID 一致（名字未串号）",
      bool(name_of_me) and name_of_me == hd["meta"]["summoner"],
      "对局名=%r vs meta.summoner=%r" % (name_of_me, hd["meta"]["summoner"]))

# ------------------------------------------------- ④ meta 与 state 主人一致 ----
# 数据文件的主人必须 == 状态文件记录的 anchor_puuid（换主人流程才会改这两个）。
# 审查 M14②：原来 except 一吞，解析失败时 st_pu=None → `not None` 恒真，
# 「换号流程搞岔 / 文件损坏」这个本测试最该抓的场景被静默放行。
_stf = DATA_DIR / "_desktop_state.json"
if not _stf.is_file():
    print("  ⏭ 状态文件不存在（%s）—— 从未跑过桌面版，④ 跳过" % _stf.name)
else:
    _st_ok = True
    st_pu = None
    try:
        st = json.loads(_stf.read_text(encoding="utf-8"))
        st_pu = st.get("anchor_puuid")
    except Exception as e:
        _st_ok = False
        _st_err = str(e)
    if not _st_ok:
        check("状态文件可解析（解析失败会让锚点与主人对不上）", False,
              "_desktop_state.json 解析失败: %s" % _st_err)
    else:
        check("数据主人 == 状态文件 anchor_puuid（未被换号流程搞岔）",
              not st_pu or st_pu == me, "state=%r vs hd=%r" % (st_pu, me))

finish()
