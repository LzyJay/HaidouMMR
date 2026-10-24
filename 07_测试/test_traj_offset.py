# -*- coding: utf-8 -*-
"""`traj_offset` 一致性回归测试（2026-09-28 第十轮收尾新增，见铁律 56）。

为什么单开一个文件
------------------
它盯的那个 bug 是**静默**的：`_desktop_state.json` 的 `traj_offset` 是
「最近一次截图锚定时、把估算轨迹 Elo 平移到隐藏分刻度所需的 offset」，
只在 `--auto-anchor`（**没有截图**的其他主人）时才被用到 ——
所以本人的看板**看不出任何异常**，但给朋友算分会整体差一百多分。

第十轮就踩到：锚点从 1541 改成 1708 后，`traj_offset` 还停在第九轮的 **−17.8**，
而当前估算已经是 **136.8**，差 **154.6 分**（state 的 mtime 早于估算文件 ⇒ 状态是用旧估算写的）。

钉住的不变量
------------
1. `state.traj_offset` **必须等于**当前估算的 `anchor.offset` ——
   不是「差不多」，是**同一个量**（`app.py:_remember_anchor()` 里就一句
   `kw["traj_offset"] = a["offset"]`）。重锚后不跟着刷新 = bug。
2. 那个 offset 必须自洽：`anchor_score − anchor_elo == offset`。
3. `traj_offset` **不能等于** `player_elo_meta.offset` —— 那是**另一把尺子**
   （全量在线 Elo K=32/16 vs 轨迹 K=22），只能当 `--auto-anchor` 的兜底。
4. state 里必须留 `traj_offset_note` 口径说明（别让下一个人再猜）。

跑法：python 07_测试\\test_traj_offset.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "02_数据")

sys.path.insert(0, HERE)
from _check import check, finish   # 2026-10-03 unified assert helper (was check)
def _load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        print("[X] 找不到 " + p)
        sys.exit(2)
    return json.load(io.open(p, encoding="utf-8"))


st = _load("_desktop_state.json")
est = _load("海斗隐藏分_v2_本次估算.json")
pe = _load("player_elo_meta.json")

a = est.get("anchor") or {}
s_traj = st.get("traj_offset")
e_off = a.get("offset")
p_off = pe.get("offset")

print("实测：state.traj_offset=%s / 估算 anchor.offset=%s / player_elo.offset=%s"
      % (s_traj, e_off, p_off))
print()

# ① 状态里的锚点与当前估算是同一局（不是两代数据混着）
check("state 锚点分 == 估算锚点分",
      st.get("anchor_score") == a.get("anchor_score"),
      "%s vs %s" % (st.get("anchor_score"), a.get("anchor_score")))
check("state 锚点局 == 估算锚点局",
      st.get("anchor_game_id") == a.get("anchor_game_id"),
      "%s vs %s" % (st.get("anchor_game_id"), a.get("anchor_game_id")))

# ② 只有截图锚定的 offset 才有资格存进 traj_offset
check("估算锚点来源是 screenshot（不是 auto）",
      a.get("anchor_source") == "screenshot", a.get("anchor_source"))

# ③★核心：traj_offset 与 anchor.offset 是同一个量
ok_same = (s_traj is not None and e_off is not None
           and abs(float(s_traj) - float(e_off)) < 1e-6)
check("state.traj_offset == 估算 anchor.offset（同一个量，重锚后必须跟着刷新）",
      ok_same, "state %s vs est %s（差 %s）"
      % (s_traj, e_off,
         "n/a" if s_traj is None or e_off is None else round(float(s_traj) - float(e_off), 1)))

# ④ offset 的定义自洽：anchor_score − anchor_elo == offset
if a.get("anchor_score") is not None and a.get("anchor_elo") is not None:
    calc = round(float(a["anchor_score"]) - float(a["anchor_elo"]), 1)
    check("offset 定义自洽（anchor_score − anchor_elo == offset）",
          abs(calc - float(e_off)) < 0.11,
          "%s − %s = %s vs offset %s"
          % (a["anchor_score"], a["anchor_elo"], calc, e_off))
else:
    check("offset 定义自洽（anchor_score − anchor_elo == offset）", False,
          "估算里缺 anchor_score / anchor_elo")

# ⑤ 两把尺子不许相等（traj_offset 绝不能被 player_elo offset 覆盖）
if s_traj is not None and p_off is not None:
    gap = abs(float(s_traj) - float(p_off))
    check("traj_offset 与 player_elo offset 是两把尺子（差 ≥ 10，严禁互覆盖）",
          gap >= 10, "差 %s（traj %s / pe %s）" % (round(gap, 1), s_traj, p_off))
else:
    check("traj_offset 与 player_elo offset 是两把尺子（差 ≥ 10，严禁互覆盖）", False,
          "缺 traj_offset 或 player_elo offset")

# ⑥ 口径说明不许丢
check("state 里留了 traj_offset_note 口径说明",
      bool(st.get("traj_offset_note")),
      "缺 traj_offset_note —— 下一个人还会再猜一次")

finish()
