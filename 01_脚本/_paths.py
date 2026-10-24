# -*- coding: utf-8 -*-
r"""统一路径解析 —— 包内任何脚本要读写文件，都从这里取路径。

目录结构：
    海斗隐藏分_分享包\
        00_使用说明.md
        01_脚本\        <- 本文件所在
        02_数据\
        03_看板\
        04_文档\
        05_历史版本\

改动原则：新增文件时优先在这里加一个常量，不要在脚本里写路径字符串。
"""
import sys
from pathlib import Path


def _resolve_root():
    """定位包根目录。

    源码模式：本文件位于 01_脚本\\ 下，包根 = 上两级。
    打包模式（PyInstaller frozen）：__file__ 指向临时解压目录（_MEIPASS），
    拿它推包根必错；改为以 exe 所在目录为起点向上探测含「02_数据」的目录，
    这样 exe 放在包根、06_桌面版\\ 还是更深子目录都能正确定位。
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        for cand in (base, base.parent, base.parent.parent):
            if (cand / "02_数据").is_dir():
                return cand
        return base
    return Path(__file__).resolve().parents[1]


ROOT = _resolve_root()
SCRIPTS = ROOT / "01_脚本"
DATA_DIR = ROOT / "02_数据"
BOARD = ROOT / "03_看板"
DOCS = ROOT / "04_文档"
LEGACY = ROOT / "05_历史版本"

HD_DATA         = DATA_DIR / "hd_data.json"
CHAMP_MAP       = DATA_DIR / "champ_map.json"
ESTIMATE        = DATA_DIR / "海斗隐藏分_v2_本次估算.json"
PLAYER_ELO      = DATA_DIR / "player_elo.json"
PLAYER_ELO_META = DATA_DIR / "player_elo_meta.json"
MY_STATS        = DATA_DIR / "my_stats.json"
ROSTER          = DATA_DIR / "海斗roster.json"
DASHBOARD_DATA  = DATA_DIR / "海斗dashboard_data.json"
DASHBOARD_HTML  = BOARD / "海斗隐藏分_数据看板.html"


# ---------- 对局有效性 ----------
#
# **5 分钟以内的对局一律视为无效**（2026-09-21 用户要求）。
#
# 为什么：海斗 5 分钟以内结束基本只有两种情况 —— 有人开局就挂机/掉线被推平，
# 或者重开局。这类局的胜负跟实力无关，KDA/伤害/占比全是噪声：
#   · 拿去算 Elo，等于往轨迹里灌一个随机胜负，把分数带偏；
#   · 拿去统计胜率和 9 项表现指标，分母被灌水；
#   · 看板「逐局战绩」里那一行也毫无参考价值。
# 实测（2026-09-21）：本人 163 局里有 7 局 < 300 秒（最短 103 秒），
# 全网 29384 局里有 1065 局。
#
# 落点（四道防线，缺一不可）：
#   ① `_hd_crawl.py`  —— 抓的时候就不收（norm_detail / norm_sgp 返回 None），
#                        载入已有数据时**顺手剪掉**旧的短局（自愈）
#   ② `_player_elo_build.py` / ③ `海斗隐藏分估算_v2.py` —— 计算前再滤一遍
#   ④ `_dash_rebuild.py` —— 展示前再滤一遍（看板口径必须和算分口径一致）
#
# 想改阈值就改这一个数，所有脚本跟着走。
MIN_GAME_SECONDS = 300


def game_duration(g) -> int:
    """从对局 dict 里取时长（秒）。取不到当 0（= 无效）。

    三个字段名都要认，因为不同文件里叫法不一样：
      hd_data.json      → `duration`
      SGP / LCU 原始响应 → `gameDuration`
      海斗roster.json    → `dur`
    """
    if isinstance(g, (int, float)):
        return int(g)
    if isinstance(g, dict):
        for k in ("duration", "gameDuration", "dur"):
            v = g.get(k)
            if v:
                return int(v)
    return 0


def valid_game(g) -> bool:
    """这局算不算有效对局。见上面 MIN_GAME_SECONDS 的说明。"""
    return game_duration(g) >= MIN_GAME_SECONDS


# ---------- 协作式「中途停止」（2026-09-21 用户要求抓取面板加停止按钮） ----------
#
# 为什么用**标志文件**而不是线程中断：
#   · 桌面版 `app.py` 是用 runpy 在**同一个进程**里跑这些脚本的，
#     没有独立解释器可以 kill；`PyThreadState_SetAsyncExc` 那种异步注入
#     又可能在 json.dump 写到一半时炸掉，留下半个文件（看板直接读不了）。
#   · 标志文件是协作式的：脚本在自己主循环的**安全点**（一轮抓完 / 一局算完）
#     才检查，绝不会写盘写到一半被打断。
# 代价是响应延迟 = 一个循环体的耗时（抓取侧约 1~3 秒，估算侧一个 epoch 几秒），
# 这对「点停止」来说完全够用。
#
# 桌面版点停止 → 写这个文件；任务开始时 → 清掉它。
CANCEL_FILE = DATA_DIR / "_cancel.flag"


def cancel_requested() -> bool:
    """用户是否点了「停止」。脚本主循环里调它。"""
    try:
        return CANCEL_FILE.exists()
    except OSError:
        return False


def clear_cancel() -> None:
    """清掉停止标志（每次任务开始时调一次）。"""
    try:
        CANCEL_FILE.unlink()
    except OSError:
        pass


def raise_if_cancelled(where: str = "") -> None:
    """在安全点检查停止标志，命中就抛 SystemExit(3)。

    用 SystemExit 而不是普通异常：脚本里到处是 `except Exception:` 的兜底，
    普通异常会被吞掉、循环继续跑，停止按钮就成了摆设。
    退出码 3 是「用户主动停止」，桌面版据此把任务标成「已停止」而不是「失败」。
    """
    if cancel_requested():
        print(f"\n⏹ 用户已停止{'（' + where + '）' if where else ''}，正在安全退出 ...", flush=True)
        raise SystemExit(3)


def ensure_dirs():
    for p in (SCRIPTS, DATA_DIR, BOARD, DOCS, LEGACY):
        p.mkdir(parents=True, exist_ok=True)


# ---------- 原子写（2026-10-12 代码审查 H5） ----------
#
# 为什么：write_text / open(...,"w") 是「先截断再写」，进程在写盘窗口内崩溃 /
# 被强杀，就会留下 0 字节或半份文件 —— hd_data.json（46 MB）、看板 HTML、
# player_elo.json 全是这么写的。2026-09-21 的 _desktop_state.json 0 字节事故
# 已经证明过这条路径真实存在，但当时只修了状态文件。
# 做法：写同目录 .tmp → 验尺寸 → os.replace（同分区原子）。与桌面版
# app.py 的 _write_bytes_atomic() 同一套思路，这里给命令行脚本用。
# 附带收益：走 bytes 落盘，不做 universal-newlines 换行转换，
# 不会把整份文件 LF/CRLF 改写一遍（app.py/apply_toolbar.py 注释里的 diff 噪音坑）。

def atomic_write_bytes(path, data: bytes) -> None:
    """原子写 bytes：tmp → 验尺寸 → os.replace。"""
    import os
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        if tmp.stat().st_size != len(data):
            raise OSError("临时文件写入不完整: %s" % path.name)
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()          # 失败时清掉残缺 tmp，别留垃圾
        except OSError:
            pass
        raise


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """原子写文本：按指定编码落 bytes，不做换行符转换。"""
    atomic_write_bytes(path, text.encode(encoding))


if __name__ == "__main__":
    for k in ("ROOT", "SCRIPTS", "DATA_DIR", "BOARD", "DOCS", "LEGACY",
              "HD_DATA", "CHAMP_MAP", "ESTIMATE", "PLAYER_ELO", "PLAYER_ELO_META",
              "MY_STATS", "ROSTER", "DASHBOARD_DATA", "DASHBOARD_HTML"):
        print("%-16s %s" % (k, globals()[k]))
