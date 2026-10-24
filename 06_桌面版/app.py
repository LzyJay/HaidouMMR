# -*- coding: utf-8 -*-
"""海斗隐藏分 · 桌面版外壳（pywebview）

职责边界
--------
窗口 + 调度。窗口负责显示看板；调度负责按界面上按下的按钮，
在当前进程里跑 01_脚本\\ 下的脚本，然后刷新看板。

算法本身一行都没搬到这里 —— 桌面版调用的就是命令行那套脚本，
所以两条链路的结果永远一致，不存在"两套实现算出来不一样"的问题。

数据根目录怎么找
----------------
分享包里的 02_数据\\ 有几十 MB，不打包进 exe（否则每更新一次数据
就要重新打包）。运行时从 exe 所在目录向上探测，找到含「02_数据」
的目录当数据根。exe 放包根、放 06_桌面版\\ 子目录都能工作。

自检
----
    海斗隐藏分.exe --check
只打印路径解析结果然后退出，不加载 GUI 依赖，用来排查
「打包后找不到数据」这类问题。

单实例
------
窗口模式**只允许开一个**。两个实例各点一次「重新计算」会同时写
同一批数据文件（估算结果 / 看板数据 / 看板 HTML），互相踩。

用命名互斥体实现，不是锁文件 —— 进程被强杀时内核自动释放，
不会留下僵尸锁。再次启动时会尝试把已有窗口叫到前台；
叫不到就弹框提示。

⚠️ `--check` / `--selftest-run` 这些命令行诊断**不受单实例限制**，
否则程序卡住时就没法排查了。但也因此：**别在重算过程中跑会写数据的
`--selftest-run`**，那还是会撞车。
"""
from __future__ import annotations

import io
import re
import sys
import threading
import time
from pathlib import Path

# 输出固定 UTF-8，避免被重定向到管道时中文变乱码。
# 注意：--windowed 构建下 sys.stdout 可能是 None，hasattr 检查天然挡住。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


APP_TITLE = "海斗隐藏分 · 个人数据看板"
WIN_W, WIN_H = 1280, 880
WIN_MIN = (1000, 660)
BOARD_NAME = "海斗隐藏分_数据看板.html"
ESTIMATE_NAME = "海斗隐藏分_v2_本次估算.json"
HD_NAME = "hd_data.json"


def _out_write(text: str) -> None:
    """往 stdout 写。--windowed 且没有控制台时 stdout 是 None，别让它炸。"""
    try:
        sys.stdout.write(text)
    except Exception:
        pass


def _err_write(text: str) -> None:
    try:
        sys.stderr.write(text)
    except Exception:
        pass


def _message_box(title: str, text: str) -> None:
    """没有控制台时的兜底提示。

    --windowed 构建下程序崩溃是「什么都没发生」，用户完全看不到原因。
    至少弹个框把 traceback 摆出来。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _ensure_stdio() -> bool:
    """带命令行参数跑时，尽力把 stdout/stderr 接回可见的地方。

    --windowed 构建的 exe 没有自己的控制台，双击或从终端跑时
    sys.stdout 可能是 None —— 那样 --check / --selftest-run 就哑了。

    返回 True 表示输出有地方可去。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return True                      # 句柄被继承了（如重定向到管道/文件）
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # ATTACH_PARENT_PROCESS = -1：接上父进程的控制台（cmd / PowerShell）
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        f = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = f
        sys.stderr = f
        return True
    except Exception:
        return False


# 单实例锁：命名互斥体。句柄一直持有到进程退出，内核自动释放。
_MUTEX_NAME = "HaidouMMR_SingleInstance_v1"
_mutex_handle = None


def _acquire_single_instance() -> bool:
    """抢占单实例锁。True = 本进程拿到了；False = 已经有别的实例在跑。

    为什么用命名互斥体而不是锁文件：
    进程崩溃、被任务管理器杀掉时，内核会**自动**释放互斥体，
    不会留下「僵尸锁」把程序永久挡在门外。锁文件就会。
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE

        h = k32.CreateMutexW(None, False, _MUTEX_NAME)
        if not h:
            return True                 # 建不出互斥体就别拦着 —— 宁可多开也不能开不了
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            return False
        _mutex_handle = h               # 全局持有，进程退出时自动释放
        return True
    except Exception:
        return True                     # 锁机制出问题时放行，别把程序锁死


def _focus_existing_window() -> bool:
    """把已经在跑的窗口叫到前台，让用户知道「已经开着了」。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        u32 = ctypes.windll.user32
        # 64 位下必须声明 restype，否则句柄会被截断成 int 而出错
        u32.FindWindowW.restype = ctypes.c_void_p
        u32.FindWindowW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]

        hwnd = u32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            return False
        u32.ShowWindow(hwnd, 9)         # SW_RESTORE：最小化了也拉回来
        u32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _resolve_root() -> Path:
    """定位分享包根目录（即含 02_数据 的那一层）。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        for cand in (base, base.parent, base.parent.parent):
            if (cand / "02_数据").is_dir():
                return cand
        return base
    return Path(__file__).resolve().parents[1]


ROOT = _resolve_root()
BOARD = ROOT / "03_看板" / BOARD_NAME
DATA_DIR = ROOT / "02_数据"

# 「停止」用的标志文件。**必须和 01_脚本\\_paths.py 里的 CANCEL_FILE 保持一致**
# （同一个 DATA_DIR 下的同一个文件名），否则脚本那边检查不到。
# 脚本在自己的主循环安全点轮询它，见 _paths.raise_if_cancelled。
CANCEL_FILE = DATA_DIR / "_cancel.flag"
# 用户主动停止时脚本抛的退出码（见 _paths.raise_if_cancelled）。
# 和「失败」区分开：停止不是错误，不该弹一堆失败信息吓人。
RC_STOPPED = 3


def _scripts_dir() -> Path:
    """脚本目录。

    打包后 PyInstaller 把 01_脚本\\ 解压到 _MEIPASS\\_scripts\\；
    源码模式直接用仓库里的 01_脚本\\。
    """
    if getattr(sys, "frozen", False):
        d = Path(getattr(sys, "_MEIPASS", "")) / "_scripts"
        if d.is_dir():
            return d
    return ROOT / "01_脚本"


class _LineTee(io.TextIOBase):
    """把写入按行转发给回调，同时留一份全文。

    为什么要自己接：抓取要跑好几分钟，如果像原来那样只把输出攒到
    StringIO 里，界面在整个过程中拿不到任何信息，进度条只能干等。
    接住每一行，进度条才有真实依据。

    echo：真实 stdout。redirect_stdout 是**全局替换** sys.stdout 的，
    不额外回显的话，从终端启动 exe 时整个抓取过程一行输出都看不到
    （全被这里吞了）。--windowed 且没有控制台时传 None 即可。
    """

    def __init__(self, on_line=None, echo=None):
        self._on_line = on_line
        self._echo = echo
        self.buf = io.StringIO()
        self._pending = ""

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        self.buf.write(s)
        if self._echo is None and not self._on_line:
            return len(s)
        self._pending += s
        while True:
            i = self._pending.find("\n")
            if i < 0:
                break
            line, self._pending = self._pending[:i], self._pending[i + 1:]
            line = line.rstrip("\r")
            if self._echo is not None:
                try:
                    self._echo.write(line + "\n")
                    self._echo.flush()
                except Exception:
                    self._echo = None     # 控制台没了就别再试
            if self._on_line:
                try:
                    self._on_line(line)
                except Exception:
                    pass                   # 回调出错不能影响被跑的脚本
        return len(s)

    def flush(self):
        pass

    def writable(self):
        return True


def _run_script(name: str, argv, on_line=None):
    """在当前进程内跑一个脚本，捕获它的 stdout。

    为什么不用 subprocess：打包成 exe 后没有独立的 python.exe，
    起不了子进程。runpy 直接在 exe 进程里执行，代价是要自己接住
    脚本里的 SystemExit。

    on_line：每产出一行就回调一次（用于进度条）。给了它就边跑边回调，
    不给就攒到最后一起返回。
    另外还会把每一行回显到真实 stdout（如果存在），否则从终端启动时
    抓取过程全程无输出。
    """
    import contextlib
    import runpy
    import traceback

    path = _scripts_dir() / name
    if not path.is_file():
        return 2, "找不到脚本：%s" % path

    scripts = str(_scripts_dir())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)

    old_argv = sys.argv
    real_out = sys.stdout if hasattr(sys.stdout, "write") else None
    tee = _LineTee(on_line, echo=real_out)
    rc = 0
    sys.argv = [str(path)] + [str(a) for a in argv]
    try:
        with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
            runpy.run_path(str(path), run_name="__main__")
    except SystemExit as e:
        code = e.code
        rc = code if isinstance(code, int) else (0 if code is None else 1)
    except BaseException:
        tee.write("\n" + traceback.format_exc())
        rc = 1
    finally:
        sys.argv = old_argv
    return rc, tee.buf.getvalue()


_webview = None


# --------------------------------------------------------------------------
# 任务进度：给界面上的进度条 + 剩余时间用
# --------------------------------------------------------------------------
# 各步骤的预期耗时（秒）。2026-09-21 在本机实测，只用来算百分比和剩余时间。
#
# ⚠️ 估时不准**不会**导致误报完成 —— 完成的唯一信号是脚本真的退出（rc）。
#    进度条只是个参考，永远不会因为时间到了就跳到 100%。
#
# 抓取实测：300 人的战绩列表耗时 532 秒（LCU 限流，约 0.56 请求/秒，
# 加并发也没用）—— 所以「完整 BFS」一次要十几分钟。
# 而只抓客户端窗口（--players 0）是秒级的，配合 _hd_backfill.py 定向补抓
# 新对手，就够把新打的局算准了。所以默认走 update，crawl 留给「深度」。
SEC_CRAWL_SEED = 10        # 只抓客户端窗口里那几局（增量更新，秒级；实测 2.9~5.4s）
SEC_CRAWL_FULL = 540       # 完整 BFS 300 人（LCU 限流下约 9 分钟起；实测 532s）
SEC_BACKFILL = 90          # 定向补抓新对手（实测 9.9s ~ 464s，见下方说明）
SEC_ELO = 6                # 重建全量玩家评分（实测 2.6~3.2s）
SEC_STATS = 30             # 抓本人对局明细 9 项指标（实测 11~25s / 161 局）
SEC_ESTIMATE = 115         # 估算隐藏分（BT 拟合 + 谱分解；实测 112.5s）
SEC_DASH = 4               # 重建看板（实测 1.5s）
#
# 上面的秒数都是**实测值**，而且只用来决定「每一步占总进度的多大比例」。
# 每一步的真实百分比一律取自脚本自己打的进度标记，不靠这些数除出来 ——
# 所以估时偏了只会让进度条分布不均，不会让某一步假报「快完了」。
#
# ⚠️ 补抓那一步的耗时**强依赖这次新出现了多少对手**：
#    刚跑过再跑只要 ~10 秒；攒了两周（177 个新对手）要 464 秒。
#    这个差异没法用一个常数表达，所以：
#    ① 每次跑完把各步实测耗时记进 _desktop_state.json，下次直接沿用（见 secs_hint）；
#    ② 首跑偏了也只是进度条分布难看一点，完成与否永远看脚本退出码。

_RE_CRAWL_DONE = re.compile(r"已抓列表\s+(\d+)")
_RE_EST_STAGE = re.compile(r"\[(\d)/4\]")
# 谱分解的细粒度标记（2026-09-22 新增，见 海斗隐藏分估算_v2.py 的 spectral_axis）
_RE_SPEC_TAG = re.compile(r"\[(谱\d)\]\s*(建超图|建图完成|迭代结束)")
_RE_SPEC_IT = re.compile(r"\[(谱\d)\]\s*(\d+)/(\d+)")

# 估算步各大阶段对应的**步内进度**（2026-09-22 实测，整步 100.1s；用
# 07_测试\_time_est_stages.py 量的每行时间戳）：
#   数据加载 0→1.52s · BT 拟合 →5.85s · [3/4]+谱1 建图 5.85~6.04s
#   谱1 幂迭代 6.04→98.50s（**92.4%**，0.308s/次）· 谱2 98.50→99.93s · 输出 →100%
#
# ⚠️ 这几段**远不是等分的**：旧代码按 (n-1)/4 等分，于是「刚进谱分解」就报 50%
# （实际才 6%），进度虚高、剩余时间被算成「只剩几十秒」（主人实机反馈）。
#
# 数值 = 该标记出现时已完成的工作占比。实测 [2/4] 与 [3/4] 几乎同刻
# （Elo 轨迹只 0.01s），但必须**严格递增** —— `_run_job` 只在标记变大时才更新，
# 等值会被直接丢掉（那样进度就再也不动了）。
_EST_STAGE_W = {1: 0.015, 2: 0.055, 3: 0.057, 4: 0.998}

# 谱分解两段各自的 (建图起, 迭代起, 迭代止, 结束)。
# ⚠️ 两段区间**不能重叠**：进度只增不减，重叠会让第二段彻底没有进度。
# 谱2（min_deg=5）只占 1.4%，所以区间很窄 —— 它不是耗时主体，给个意思就够。
_SPEC_SEG = {
    "谱1": (0.058, 0.060, 0.984, 0.9845),   # 实测 6.04% ~ 98.40%
    "谱2": (0.985, 0.987, 0.997, 0.9975),   # 实测 98.50% ~ 99.83%
}
_RE_BF_CANDS = re.compile(r"待补抓对手:\s*(\d+)")
_RE_BF_LIST = re.compile(r"\[列表\]\s*(\d+)/(\d+)")
_RE_BF_DETAIL = re.compile(r"\[详情\]\s*(\d+)/(\d+)")
_RE_ELO_DONE = re.compile(r"\[Elo\]\s*(\d+)/(\d+)")
_RE_STATS_DONE = re.compile(r"\[Stats\]\s*(\d+)/(\d+)")


def _frac_of(m, lo: float, span: float):
    """把 `a/b` 形式的标记换算到 [lo, lo+span] 区间。

    结果要 round —— 0.05 + 0.35 * 1.0 在浮点下是 0.4000000000000001，
    会比紧随其后的「阶段收尾」值 0.40 大一点点，进度条上看是退了一格。
    """
    a, b = int(m.group(1)), max(1, int(m.group(2)))
    return round(lo + span * min(1.0, a / b), 6)


def _mark_crawl(line: str, st: dict):
    """从 _hd_crawl.py 的输出里解析进度。

    它每轮开头会打 `[R2] 待扩展 250 人 (已抓列表 50) ...` ——
    括号里那个「已抓列表」就是已经拉过战绩的人数，正好除以目标人数。

    只抓客户端窗口时（--players 0）没有这一行，靠「保存」那行收尾。
    """
    if "✓ 保存" in line:
        return 1.0
    if "种子局详情" in line:
        return 0.5                 # 窗口那几局抓完了，BFS 还没开始
    m = _RE_CRAWL_DONE.search(line)
    if m:
        return min(1.0, int(m.group(1)) / max(1, st.get("players") or 1))
    return None


def _mark_backfill(line: str, st: dict):
    """_hd_backfill.py 的进度。

    这个脚本要跑好几分钟（16 线程直连 LCU，没有限流），
    所以脚本里专门加了 `[列表] k/N 人` / `[详情] k/N 局` 两段增量输出，
    进度条就靠它们，而不是靠估时瞎猜。
    """
    if "✓ 保存" in line:
        return 1.0
    if "无需补抓" in line:
        return 0.9
    if "载入" in line:
        return 0.03
    m = _RE_BF_CANDS.search(line)
    if m:
        st["cands"] = int(m.group(1))
        return 0.04
    m = _RE_BF_LIST.search(line)
    if m:
        return _frac_of(m, 0.05, 0.35)
    if "列表 " in line and "->" in line:
        return 0.40                # 列表阶段收尾
    m = _RE_BF_DETAIL.search(line)
    if m:
        return _frac_of(m, 0.42, 0.50)
    if "详情 +" in line:
        return 0.95
    return None


def _mark_elo(line: str, st: dict):
    """_player_elo_build.py：`[Elo] k/N 局` 是逐段进度。

    注意用 `in` 不用 `startswith` —— 脚本里那行是 `f"  ✓ 输出 ..."`，
    前面带两个空格缩进，startswith 会漏掉，进度就永远停在 95%。
    """
    if "✓ 输出" in line:
        return 1.0
    if "加载" in line:
        return 0.05
    m = _RE_ELO_DONE.search(line)
    if m:
        return _frac_of(m, 0.05, 0.9)
    return None


def _mark_stats(line: str, st: dict):
    """_stats_crawl.py：`[Stats] k/N 局` 逐局进度。

    注意 `✓ 输出` 那行前面带两个空格，所以用 `in` 而不是 `startswith`。
    """
    if "✓ 输出" in line:
        return 1.0
    if "待抓" in line:
        return 0.02
    m = _RE_STATS_DONE.search(line)
    if m:
        return _frac_of(m, 0.02, 0.9)
    return None


def _mark_dash(line: str, st: dict):
    """_dash_rebuild.py：只有三个天然节点，够用了。"""
    if "看板数据已更新" in line:
        return 1.0
    if "聚合完成" in line:
        return 0.85
    if line.startswith("[load]"):
        return 0.3
    return None


def _mark_estimate(line: str, st: dict):
    """估算脚本的阶段标记 —— 按**实测权重**换算（含谱分解的细粒度进度）。

    认两类标记：
      · `[1/4]`…`[4/4]` 四个大阶段（实测占比：加载 1.5% / BT 4.3% / 谱 93.8%）
      · `[谱1|谱2] 建超图 / k/300 / 迭代结束` —— 谱1 幂迭代独占整步 **92.4%**
    没有第二类的话，进度条会在 `[3/4]` 之后卡在 9% 一分半（主人 2026-09-22 反馈）。

    ⚠️ `迭代结束 (300/300)` 这类行同时能被两个正则命中，所以**先判 tag 再判 k/n**。
    """
    if "详细数据 ->" in line:
        return 1.0
    m = _RE_SPEC_TAG.search(line)
    if m:
        seg = _SPEC_SEG.get(m.group(1))
        if seg:
            build_lo, it_lo, it_hi, done = seg
            return {"建超图": build_lo, "建图完成": it_lo, "迭代结束": done}[m.group(2)]
    m = _RE_SPEC_IT.search(line)
    if m:
        seg = _SPEC_SEG.get(m.group(1))
        if seg:
            _, it_lo, it_hi, _ = seg
            k = int(m.group(2))
            n = max(1, int(m.group(3)))
            return round(it_lo + (it_hi - it_lo) * min(1.0, k / n), 6)
    m = _RE_EST_STAGE.search(line)
    if m:
        return _EST_STAGE_W.get(int(m.group(1)), 0.0)
    return None


def _stages_for(kind: str, players: int, anchor_argv, secs_hint=None, owner=None):
    """把一次操作拆成有序的步骤列表。

    kind:
      update  = 抓客户端窗口的新局 → 补抓新对手 → 全量评分 → 本人明细 → 估算 → 看板（快，默认）
      crawl   = 上面第一步换成完整 BFS（慢，但网络更全）
      recalc  = 只估算 + 看板（不抓数据）
      rebuild = 只看板

    secs_hint：上次跑各步骤的**实测**耗时（{步骤名: 秒}）。给了就用它替换
    默认估时，进度条与剩余时间会随使用次数越来越准。

    owner：`{"puuid": ..., "name": ...}` —— **换数据主人**。
      给了它，抓取步会带上 `--me`（按 TA 的 puuid 拉战绩、并把 meta 切过去）；
      只在 update/crawl 两种类型下生效（recalc/rebuild 不抓数据，换不了主人）。
    """
    # 只有会真的去抓数据的类型，才谈得上「换主人」
    crawl_owner = owner if kind in ("update", "crawl") else None

    def crawl_stage(n, secs):
        argv = ["--seed", "--players", str(n)]
        if crawl_owner:
            argv += ["--me", crawl_owner["puuid"]]
            if crawl_owner.get("name"):
                argv += ["--me-name", crawl_owner["name"]]
        return {
            "name": "抓取最新对局", "script": "_hd_crawl.py",
            "argv": argv,
            "secs": secs, "marker": _mark_crawl,
            "players": max(1, n), "fatal": True,
        }

    backfill = {
        "name": "补抓新对手", "script": "_hd_backfill.py", "argv": [],
        "secs": SEC_BACKFILL, "marker": _mark_backfill, "fatal": False,
    }
    elo = {
        "name": "重建全量玩家评分", "script": "_player_elo_build.py", "argv": [],
        "secs": SEC_ELO, "marker": _mark_elo, "fatal": True,
    }
    # 这一步不能省：看板的 9 项指标（伤害/承伤/经济占比、治疗占比…）全来自
    # my_stats.json / 海斗roster.json，而只有它生成这两个文件。
    # 漏掉它，_dash_rebuild.py 会因为新局没有 stats 直接崩掉。
    stats = {
        "name": "抓本人对局明细", "script": "_stats_crawl.py", "argv": [],
        "secs": SEC_STATS, "marker": _mark_stats, "fatal": True,
    }
    estimate = {
        "name": "估算隐藏分", "script": "海斗隐藏分估算_v2.py",
        # 没有截图锚点（换了数据主人、TA 又没截过图）→ `--auto-anchor`：
        # 复用「截图锚定留下的 offset」把轨迹换算成隐藏分刻度，自动给分。
        # 规则：先自动给分，之后拿到 TA 的实际分数截图再覆盖。
        #
        # ⚠️ 2026-09-22 修：原来写成「只有显式换主人才 auto」，于是
        #   「切了 LCU 账号再点按钮」/「朋友第一次用（没有自己的截图）」时
        #   退化成**不带任何锚点** → 报出未校准的 Elo 刻度（或更糟：拿上一个
        #   主人的截图分去锚新轨迹）。没有可用锚点时一律自动给分。
        "argv": (list(anchor_argv) if anchor_argv else ["--auto-anchor"]),
        "secs": SEC_ESTIMATE,
        "marker": _mark_estimate, "fatal": True,
    }
    dash = {
        "name": "重建看板", "script": "_dash_rebuild.py", "argv": [],
        "secs": SEC_DASH, "marker": _mark_dash, "fatal": True,
    }

    if kind == "update":
        # --players 0：只处理客户端列表窗口，不跑 BFS。秒级。
        out = [crawl_stage(0, SEC_CRAWL_SEED), backfill, elo, stats, estimate, dash]
    elif kind == "crawl":
        out = [crawl_stage(players, SEC_CRAWL_FULL), backfill, elo, stats, estimate, dash]
    elif kind == "recalc":
        out = [estimate, dash]
    elif kind == "rebuild":
        out = [dash]
    else:
        return []

    # 用上次实测耗时校准权重（夹到 1 秒 ~ 1 小时，挡住脏数据）
    if secs_hint:
        for s in out:
            v = secs_hint.get(s["name"])
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if 1.0 <= v <= 3600.0:
                s["secs"] = round(v, 1)
    return out


_JOB_LOCK = threading.Lock()
_JOB_ID = 0
_LOG_MAX = 120
_JOB = {
    "id": 0, "kind": None, "running": False, "finished": False,
    "ok": None, "error": None,
    # 本次任务算的是谁（换数据主人时才有值，界面会显示出来）
    "owner": None,
    "started": 0.0, "step": 0, "step_total": 0, "step_name": "",
    "step_frac": 0.0, "step_started": 0.0, "step_secs": 0.0,
    # step_marked：这一步有没有解析出过真实标记。
    # 有标记时进度只认标记，绝不拿「已用/估时」去盖它 ——
    # 否则估时偏小（补抓实测 9.9s vs 464s）就会让某一步刚跑几秒就假报 90%。
    "step_marked": False,
    # step_has_marker：这一步**有没有**配解析器。
    #
    # 为什么要单独记这个：有解析器的步骤，标记值才是权威，估时只会在标记到达前
    # 抢跑。而估时是「上次实测值」，步骤一旦变慢就会在标记到达前冲到 0.9 ——
    # 等脚本的第一个标记（可能才 0.5）一到，进度条反而从 0.9 掉回 0.5。
    # 2026-09-21 实测踩到：第 1 步改走 SGP 后一次请求要 2.5s，估时 3.8s，
    # 于是「种子局详情」这个 0.5 的标记把进度从 0.9 拽了回去。
    # 结论：**有标记的步骤就别猜**，老老实实等标记说话（见 _job_progress）。
    "step_has_marker": False,
    "plan": [], "log": [], "steps": [], "used_ago": 0,
    # stopping：用户已经点了停止，脚本正在收尾（还没退出）。
    # stopped：这次任务是因为「用户停止」结束的，不是失败 —— 界面据此换文案。
    "stopping": False, "stopped": False,
}


def _job_set(**kw):
    with _JOB_LOCK:
        _JOB.update(kw)


def _job_log(line: str):
    if not line.strip():
        return
    with _JOB_LOCK:
        _JOB["log"].append(line)
        if len(_JOB["log"]) > _LOG_MAX:
            del _JOB["log"][:-_LOG_MAX]


_JOB_LOG_FILE = DATA_DIR / "_last_job.log"


# --------------------------------------------------------------------------
# 数据主人（2026-09-22 用户要求「看板视角是谁，抓取/估算就算谁」）
# --------------------------------------------------------------------------
# meta 缓存（2026-09-22）：按 mtime_ns + size 失效，文件一变自动重读。
_META_CACHE = {"mtime": None, "size": None, "meta": {}}


def _hd_meta() -> dict:
    """读 `hd_data.json` 的 meta。

    ⚠️ 旧注释「只要 meta，不碰 46 MB 的 games」是**错的**：`json.loads` 必须先把
    **整个文件**解析完才能取 `["meta"]` —— 34,602 局全解析 ≈ **2 秒**（2026-09-22
    主人问「管理存档为什么读取存档还要 2 秒」，根因就在这）。

    所以：
      · 这里按 mtime+size 缓存，同一份文件重复读零成本；文件一改立刻失效，不会脏读。
      · 只想拿「当前主人是谁」的地方请用 `_current_owner_puuid()`（读 285 KB 的
        dashboard_data，~0.05 秒），别调这里。
    """
    import json

    f = DATA_DIR / HD_NAME
    try:
        st = f.stat()
        if _META_CACHE["mtime"] == st.st_mtime_ns and _META_CACHE["size"] == st.st_size:
            return _META_CACHE["meta"]
        meta = json.loads(f.read_text(encoding="utf-8")).get("meta") or {}
        _META_CACHE.update({"mtime": st.st_mtime_ns, "size": st.st_size, "meta": meta})
        return meta
    except Exception:
        return {}


def _current_owner_puuid() -> str:
    """当前数据主人的 puuid —— **快**：读 285 KB 的 `海斗dashboard_data.json`。

    那份数据每次管线跑完都会重算，`me_puuid` 与当前主人一致；只有读不到
    （例如还没跑过任何一次）才退回解析 46 MB 的 hd_data。
    """
    import json

    try:
        d = json.loads((DATA_DIR / "海斗dashboard_data.json").read_text(encoding="utf-8"))
        pu = (d.get("me_puuid") or "").strip()
        if pu:
            return pu
    except Exception:
        pass
    return (_hd_meta().get("puuid") or "").strip()


def _lcu_ready() -> bool:
    """游戏客户端在不在？（能拿到 LCU 端口就算在）

    为什么要有这个预检查：抓取 / 明细脚本都要 LCU，客户端没开时脚本会
    `sys.exit(1)`，用户看到的只是「没有全部成功」+ 一堆报错 —— 根本不知道
    要先登录游戏（2026-09-22 主人要求「没登录就写明先登录再点抓取」）。
    这里提前问一次，好在界面上直接弹提示（`need_login` 标记）。

    ⚠️ 探测必须走 `_lcu_creds2.find_lcu()`：它内部用 `hidden_kwargs()` 起 `tasklist`，
    不给 GUI 子系统的 exe 弹黑窗口（铁律 33）。任何探测都不许自己裸调 subprocess。

    探测本身异常时**返回 True**（放行）—— 别让一个坏探测把正常任务卡死。
    """
    try:
        d = str(_scripts_dir())
        if d not in sys.path:
            sys.path.insert(0, d)
        import _lcu_creds2
        port, _token = _lcu_creds2.find_lcu()
        return bool(port)
    except Exception:
        return True


def _switch_owner_state(puuid: str) -> None:
    """把状态文件里的「当前主人锚点」切到 `puuid`。

    `_desktop_state.json` 的平铺字段（`anchor_score` / `anchor_game_id` /
    `anchor_games_ago`）语义是「**当前数据主人**的锚点」。换主人时必须：
      · 先把旧主人的锚点归档进 `anchors[旧主人]` —— 不然换回来就找不到了，
        而锚点是**唯一没法自动获取**的信息（丢了就静默算错分）
      · 再把新主人的锚点提出来铺平；TA 没有截图锚点就清空 →
        估算步改走 `--auto-anchor`（用户定的规则：先自动给分，有截图再覆盖）
    """
    st = _load_state()
    cur = st.get("anchor_puuid")
    if cur == puuid:
        return
    anchors = dict(st.get("anchors") or {})
    if cur and st.get("anchor_score") is not None:
        anchors[cur] = {
            "anchor_score": st.get("anchor_score"),
            "anchor_game_id": st.get("anchor_game_id"),
            "anchor_games_ago": st.get("anchor_games_ago"),
            "at": st.get("at"),
        }
    a = anchors.get(puuid) or {}
    _save_state(anchors=anchors, anchor_puuid=puuid,
                anchor_score=a.get("anchor_score"),
                anchor_game_id=a.get("anchor_game_id"),
                anchor_games_ago=a.get("anchor_games_ago"),
                at=time.strftime("%Y-%m-%d %H:%M:%S"))


def _write_job_log(kind, results, ok_all, stopped) -> None:
    """把本次任务的「每步 rc/耗时 + 完整输出」落盘，供事后排查。

    为什么要落盘（2026-09-22 用户报「没有全部成功」时暴露的缺口）：
    任务日志原来只活在内存 `_JOB["log"]` 里，而且只留最近 120 行 ——
    关掉窗口或跑下一个任务就没了，早期步骤（比如第 2 步）的输出还会被后面的步骤挤掉。
    偏偏**非致命步骤失败既不中止流水线、也不写任何产物**（`_hd_backfill.py` 就是
    `fatal: False`），于是事后只能靠产物时间线去「猜」是哪一步失败，拿不到原因。

    落盘后就是一句话的事：`02_数据\\_last_job.log` 里直接写着
    `[!!] 补抓新对手 rc=1` + 它当时的完整输出。
    """
    try:
        bad = [s for s in results if s.get("rc") != 0]
        lines = [
            "=== 海斗隐藏分 · 任务日志 ===",
            "时间: " + time.strftime("%Y-%m-%d %H:%M:%S"),
            "类型: " + str(kind),
        ]
        if _JOB.get("owner"):
            # 换了数据主人时，日志必须写明「这次算的是谁」——
            # 否则事后对着一份「分数变了」的结果完全没法判断是不是换人了。
            lines.append("数据主人: " + str(_JOB["owner"]))
        lines += [
            "结果: " + ("全部成功" if ok_all
                       else ("已停止（用户点了停止）" if stopped else "未全部成功")),
            "",
            "--- 各步骤 ---",
        ]
        for s in results:
            lines.append("%s %-18s rc=%-3s %7.1fs" % (
                "[OK]" if s.get("rc") == 0 else "[!!]",
                s.get("name", "?"), s.get("rc"), s.get("secs") or 0.0))
        if bad:
            lines.append("")
            lines.append("失败步骤: " + "、".join(
                "%s(rc=%s)" % (s["name"], s["rc"]) for s in bad))
        lines += ["", "--- 完整输出（按步骤）---"]
        for s in results:
            lines += ["", "### %s   rc=%s" % (s.get("name"), s.get("rc"))]
            body = (s.get("out") or "").rstrip()
            lines.append(body if body else "（无输出）")
        lines += ["", "=== 日志结束 ==="]
        _JOB_LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass                    # 日志写不下不影响主流程


# --------------------------------------------------------------------------
# 数据主人存档（2026-09-22）：一个账号一份，切换时直接换档、不重算
# --------------------------------------------------------------------------
# 为什么要它（主人原话）：「我大号已经抓取计算过了，你存一份本地看板数据让我从
# 小号切换回大号时**直接切换**，而不是耗几分钟去计算」。所以每个主人跑过一次
# 管线就把整套产物存一份；再切回来 = 换档 + 重算 meta + 重注入看板，秒级完成。
#
# ⚠️ 体积（方案 D）：`player_elo.json` 一份就 16.8 MB（24.8 万玩家），但它的
#    内容**只差一个 offset 常量**（`mmr = 原始 Elo + offset`，而原始 Elo 是全数据集
#    算出来的、与主人无关；实测两个主人只差 1.5 分）。所以：
#      · 每份存档只存这个主人的 `elo_offset`（几十字节）
#      · 全量表只存**一份**共享底表 `_player_elo_base.json`
#      · 切换时现场物化成该主人的 `player_elo.json`（~2-4 秒）
#    加一个主人只多几百 KB，而不是 17 MB。下游脚本一行都不用改 ——
#    物化出来的文件格式与原来完全一致。
OWNER_SAVE_DIR = DATA_DIR / "_owners"
ELO_BASE_JSON = OWNER_SAVE_DIR / "_player_elo_base.json"
# 一个主人的整套产物里，除 player_elo.json 外的那几份（都是小文件）
OWNER_SAVE_FILES = ("海斗隐藏分_v2_本次估算.json", "海斗dashboard_data.json",
                    "海斗roster.json", "my_stats.json", "player_elo_meta.json")


def _owner_dir(puuid: str) -> Path:
    return OWNER_SAVE_DIR / (puuid or "").strip()


def _write_bytes_atomic(dst: Path, data: bytes) -> None:
    """原子写 + 验尺寸。

    ⚠️ 不用 `shutil.copyfile`：受限环境下它会**先把目标文件建出来、内容没写进去**
    就抛错，留下 0 字节空壳（2026-09-22 踩到两次，见 `_backup_state` 的说明）。
    """
    import os

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(data)
    if tmp.stat().st_size != len(data):
        raise OSError("写入不完整: %s" % dst.name)
    os.replace(tmp, dst)


def _owner_save_meta(puuid: str) -> dict:
    """读某份存档的 meta.json（没有就返回 {}）。"""
    import json

    try:
        return json.loads((_owner_dir(puuid) / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _archive_owner(puuid: str = "", name: str = "") -> dict:
    """把**当前**这套产物按 puuid 存一份 —— 一个账号只留一份，重算即覆盖。

    存：5 个小产物 + meta.json（隐藏分 / 局数 / 锚点来源 / elo offset / 算法时间）。
    `player_elo.json` 不单独存整份，只把 offset 记进 meta（见上面方案 D 的说明）。
    """
    import json
    import time

    pu = (puuid or (_hd_meta().get("puuid") or "")).strip()
    if not pu:
        return {"ok": False, "msg": "读不到当前数据主人，没法存档。"}
    est = {}
    try:
        est = json.loads((DATA_DIR / ESTIMATE_NAME).read_text(encoding="utf-8"))
    except Exception:
        pass
    hd_meta = _hd_meta()
    pe_meta = {}
    try:
        pe_meta = json.loads((DATA_DIR / "player_elo_meta.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    d = _owner_dir(pu)
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for fn in OWNER_SAVE_FILES:
        src = DATA_DIR / fn
        if src.is_file():
            _write_bytes_atomic(d / fn, src.read_bytes())
            files.append(fn)
    # 共享底表：当前 player_elo.json 原样存一份（16.8 MB，全局只有一份）
    try:
        _write_bytes_atomic(ELO_BASE_JSON, (DATA_DIR / "player_elo.json").read_bytes())
    except OSError:
        pass
    info = {
        "puuid": pu,
        "name": (name or hd_meta.get("summoner") or "").strip(),
        "n_my_games": hd_meta.get("n_my_games"),
        "n_games": hd_meta.get("n_games"),
        "estimate": est.get("estimate"),
        "ci_half_95": est.get("ci_half_95"),
        "anchor_source": (est.get("anchor") or {}).get("anchor_source"),
        "elo_offset": pe_meta.get("offset"),
        "files": files,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_bytes_atomic(d / "meta.json",
                        json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"))
    return {"ok": True, "saved": info}


def _list_owner_saves() -> dict:
    """列出所有存档（给看板的「管理存档」面板用）。

    ⚠️ 这里**不要**用 `_hd_meta()` 拿当前主人 —— 那要解析 46 MB 的 hd_data（~2 秒），
    而面板只是读几十 KB 的存档 meta。用 `_current_owner_puuid()`（~0.05 秒）。
    """
    import json

    cur = _current_owner_puuid()
    out = []
    if OWNER_SAVE_DIR.is_dir():
        for d in sorted(OWNER_SAVE_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            info = _owner_save_meta(d.name)
            if not info:
                continue
            info = dict(info)
            try:
                info["size_kb"] = round(
                    sum(f.stat().st_size for f in d.glob("*") if f.is_file()) / 1024.0, 1)
            except OSError:
                info["size_kb"] = 0
            info["current"] = (d.name == cur)
            out.append(info)
    # 当前生效但还没存档的主人也要列出来 —— 否则用户不知道「它还没被存过」
    if cur and not any(x.get("puuid") == cur for x in out):
        nm = ""
        try:
            nm = json.loads((DATA_DIR / "海斗dashboard_data.json")
                            .read_text(encoding="utf-8")).get("summoner") or ""
        except Exception:
            pass
        out.insert(0, {"puuid": cur, "name": nm or (cur[:8] + "…"),
                       "n_my_games": None, "size_kb": 0,
                       "current": True, "unsaved": True})
    base_mb = 0.0
    if ELO_BASE_JSON.is_file():
        try:
            base_mb = round(ELO_BASE_JSON.stat().st_size / 1048576.0, 1)
        except OSError:
            base_mb = 0.0
    return {"ok": True, "saves": out, "base_mb": base_mb,
            "dir": str(OWNER_SAVE_DIR)}


def _materialize_elo(puuid: str, offset: float) -> bool:
    """用共享底表 + 这个主人的 offset，现场生成 `player_elo.json`（方案 D）。

    底表里存的是「上一次算出来的 mmr（含它当时的 offset）」，所以逐条：
        `新 mmr = 底表 mmr − 底表 offset + 目标 offset`
    两个都是一位小数，加减后仍是一位小数 → 与直接跑 `_player_elo_build.py`
    得到的表**完全等价**（不会引入舍入漂移）。
    """
    import json

    if not ELO_BASE_JSON.is_file():
        return False
    base = json.loads(ELO_BASE_JSON.read_text(encoding="utf-8"))
    bm = dict(base.pop("_meta", {}) or {})
    base_off = float(bm.get("offset") or 0.0)
    delta = float(offset) - base_off
    bm.update({"offset": round(float(offset), 1), "owner_puuid": puuid,
               "materialized_from_base": True,
               "base_offset": round(base_off, 1)})
    out = {"_meta": bm}
    for pu, v in base.items():
        if isinstance(v, dict) and "mmr" in v:
            out[pu] = {"mmr": round(float(v["mmr"]) + delta, 1),
                       "n_games": v.get("n_games", 0)}
    _write_bytes_atomic(DATA_DIR / "player_elo.json",
                        json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return True


def _set_owner_meta(puuid: str, name: str = "") -> dict:
    """把 `hd_data.json` 的 meta 切到 `puuid` —— **纯本地重算**，不抓任何数据。

    网络是并集，主人的对局只要之前抓过就还在里面，所以这里只是把「谁是我的局」
    重新筛一遍。⚠️ `my_game_ids` 必须写**两份**（顶层 + meta）：`_stats_crawl.py`
    读的是顶层那份，估算脚本读 meta 那份。
    """
    import json
    import time as _t

    f = DATA_DIR / HD_NAME
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "msg": "读不了 %s：%s" % (HD_NAME, e)}
    games = d.get("games") or []
    my_ids = [g["gameId"] for g in games
              if any(p.get("puuid") == puuid for p in (g.get("players") or []))]
    if not my_ids:
        return {"ok": False,
                "msg": "TA 在这份对局网络里一局都没有 —— 先切到 TA 抓一次再存档。"}
    meta = d.setdefault("meta", {})
    prev = meta.get("puuid")
    if name:
        nm = name
    else:
        nm = (meta.get("summoner") if prev == puuid else "") or ""
        if not nm:                      # 从对局里捞 TA 的显示名（最省事的兜底）
            for g in games:
                for p in (g.get("players") or []):
                    if p.get("puuid") == puuid and p.get("name"):
                        nm = p["name"]
                        break
                if nm:
                    break
    if prev and prev != puuid:
        meta["prev_puuid"] = prev
    meta.update({"puuid": puuid, "summoner": nm or (puuid[:8] + "…"),
                 "n_my_games": len(my_ids), "n_games": len(games),
                 "n_players": len({p.get("puuid") for g in games
                                   for p in (g.get("players") or [])}),
                 "owner_switched_at": _t.strftime("%Y-%m-%d %H:%M:%S")})
    d["my_game_ids"] = sorted(my_ids)
    meta["my_game_ids"] = d["my_game_ids"]
    _write_bytes_atomic(f, json.dumps(d, ensure_ascii=False).encode("utf-8"))
    return {"ok": True, "puuid": puuid, "name": meta["summoner"],
            "n_my_games": len(my_ids)}


def _apply_owner_save(puuid: str) -> dict:
    """把某份存档换上来 —— 秒级切换数据主人，**不重算**。"""
    pu = (puuid or "").strip()
    info = _owner_save_meta(pu)
    if not info:
        return {"ok": False,
                "msg": "TA 还没有存档。先切到 TA 抓一次（跑完会自动存一份），"
                       "以后就能秒切了。"}
    # ① 先把**当前**主人也存一份，免得切走就丢了
    cur = _hd_meta().get("puuid")
    if cur and cur != pu:
        try:
            _archive_owner(cur)
        except Exception:
            pass
    # ② 换产物（5 个小文件）。
    # ⚠️ 存档里缺失的产物（当时没生成），必须先把 DATA_DIR 里的同名残留
    # **软删**（挪进 _stale\，铁律：只移动不硬删）—— 不然上一个主人的
    # roster / my_stats 会和新主人的 meta 混搭，重建看板时报莫名其妙的
    # 「一局 stats 都没有」（审查 M7）。
    _have = set(info.get("files") or [])
    _missing = [fn for fn in OWNER_SAVE_FILES if fn not in _have]
    if _missing:
        _parked = ROOT / "_stale" / ("owner_save_orphan_%s" % time.strftime("%Y%m%d_%H%M%S"))
        for fn in _missing:
            _res = DATA_DIR / fn
            if _res.is_file():
                try:
                    _parked.mkdir(parents=True, exist_ok=True)
                    _res.rename(_parked / fn)
                except OSError:
                    pass            # 挪不动就留着 —— 下游脚本能显式报出来
    for fn in (info.get("files") or []):
        src = _owner_dir(pu) / fn
        if src.is_file():
            _write_bytes_atomic(DATA_DIR / fn, src.read_bytes())
    # ③ 物化 player_elo.json（共享底表 + 这个主人的 offset）
    off = info.get("elo_offset")
    if off is not None:
        try:
            _materialize_elo(pu, float(off))
        except Exception:
            pass
    # ④ 切 meta（按 puuid 从并集网络里重筛 TA 的局）
    r = _set_owner_meta(pu, info.get("name") or "")
    if not r.get("ok"):
        return r
    # ⑤ 切锚点状态（旧主人的锚点会被安全归档，TA 没有截图锚点就清空 → 走 auto）
    _switch_owner_state(pu)
    return {"ok": True, "name": r.get("name"), "n_my_games": r.get("n_my_games"),
            "estimate": info.get("estimate"),
            "msg": "已切到存档：%s（%s 局 / 隐藏分 %s）"
                   % (r.get("name"), r.get("n_my_games"), info.get("estimate"))}


def _drop_owner_save(puuid: str) -> dict:
    """删掉一份存档 —— **软删**（整目录挪进 `_stale/`，铁律：只移动不硬删）。"""
    import time as _t

    pu = (puuid or "").strip()
    d = _owner_dir(pu)
    if not d.is_dir():
        return {"ok": False, "msg": "没有这份存档。"}
    stale = ROOT / "_stale"
    stale.mkdir(exist_ok=True)
    info = _owner_save_meta(pu)
    tag = _t.strftime("%Y%m%d_%H%M%S")
    parked = stale / ("owner_save_%s_%s" % (pu[:8], tag))
    try:
        d.rename(parked)
    except OSError as e:
        return {"ok": False, "msg": "删除失败：%s" % e}
    return {"ok": True, "msg": "已移到 _stale\\%s（没有硬删）" % parked.name,
            "name": info.get("name")}


def _realign_estimate_argv(st: dict) -> None:
    """估算步**开跑前**，把「这次算谁 / 用哪个锚点」重新对齐一次。

    为什么不能只在任务启动时定死：抓取步不带 `--me` 时以**当前 LCU 登录账号**
    为种子（铁律 3）—— 也就是说「数据主人」可能在这一步被悄悄换掉。

    2026-09-22 真踩到：主人切了 LCU 账号（小号）后点「抓新局并重算」，
    meta.puuid 变成小号，但状态里的锚点还是大号的 `1928 @ 10000000003`；
    估算找不到那一局 → 退回「0 局前」→ 把 1928 钉在小号轨迹最后一局上，
    于是 estimate 恒等于锚点值（看着像算出来了，其实是**大号的截图分**）。
    更糟的是 `_remember_anchor()` 会把这个假锚点归档到小号名下，污染落盘。

    这里在估算步之前补一次 `_switch_owner_state(当前 meta.puuid)`：
      · 旧主人的锚点被安全归档（不会丢）
      · 新主人没有自己的截图 → 锚点清空 → 走 `--auto-anchor` 自动给分
    朋友第一次用（没有 TA 的截图）走的就是同一条路。
    """
    pu = (_hd_meta().get("puuid") or "").strip()
    if pu:
        _switch_owner_state(pu)
    argv = _anchor_argv(_JOB.get("anchor_arg"), _JOB.get("used_ago"),
                        _load_state())
    st["argv"] = list(argv) if argv else ["--auto-anchor"]


def _run_job(kind: str, stages, job_id: int):
    """工作线程：顺序跑完各步骤。只更新简单字段，进度换算交给 _job_progress()。"""
    import traceback

    results = []
    ok_all = True
    stopped = False
    try:
        for i, st in enumerate(stages, 1):
            # 上一步刚被用户停掉的话，后面一步都不要再开
            if stopped:
                break
            _job_set(step=i, step_name=st["name"], step_frac=0.0,
                     step_marked=False, step_has_marker=bool(st.get("marker")),
                     step_secs=st["secs"], step_started=time.time())

            # ⚠️ 估算步开跑前必须重新对齐「算谁 / 用哪个锚点」——
            # 抓取步可能已经把数据主人换掉了（不带 --me 时以当前登录账号为种子）。
            # 详见 _realign_estimate_argv 的说明。
            if st.get("name") == "估算隐藏分":
                _realign_estimate_argv(st)

            holder = {"f": 0.0}

            def on_line(ln, _st=st, _h=holder):
                _job_log(ln)
                fn = _st.get("marker")
                if fn:
                    f = fn(ln, _st)
                    if f is not None and f > _h["f"]:
                        _h["f"] = min(1.0, f)
                        _job_set(step_frac=_h["f"], step_marked=True)

            t_s = time.time()
            rc, out = _run_script(st["script"], st["argv"], on_line=on_line)
            dt = time.time() - t_s
            results.append({"name": st["name"], "rc": rc,
                            "secs": round(dt, 1), "out": out})
            _job_set(step_frac=1.0, steps=list(results))
            if rc == RC_STOPPED:
                # 用户点了停止 —— 这不是失败，单独标出来，
                # 界面会显示「已停止」而不是「没有全部成功」。
                stopped = True
                ok_all = False
                _job_log("【%s】已按你的要求停止。" % st["name"])
                break
            if rc != 0:
                ok_all = False
                _job_log("【%s】失败，退出码 %s" % (st["name"], rc))
                if st.get("fatal", True):
                    _job_log("后面的步骤已中止。")
                    break
        # ⚠️ **顺序很重要：先把状态写盘，最后才宣布 finished。**
        #
        # 反过来的话，任何「等 `running` 变 False 就认为任务结束」的调用方，
        # 都会在 `_save_state` 还没跑完的时候就动手 —— 2026-09-22 被这个时序咬到：
        # `test_progress.py` 结尾按 `running` 判定任务结束后立刻恢复真实 `_STATE_FILE`，
        # 而此时 daemon 线程里的 `_save_state` 才刚读到内容、还没写 →
        # 本该落在临时目录的备份**落到了真实的 `02_数据\`**，还因为测试最后 `sys.exit()`
        # 会强杀 daemon 线程，停在「已写 `.bak.tmp`、未 `os.replace`」的中间态
        # （实测在真实数据目录里留下 `_desktop_state.bak.json.tmp`）。
        # 更坏的可能：时序再差一点，就会用临时目录里的假状态覆盖真实备份。
        #
        # 把这次的实测耗时记下来，下次同一步骤的估时直接沿用 ——
        # 进度条和剩余时间会越用越准，而不是永远用出厂常数。
        #
        # 必须按**步骤名**合并：`recalc` 只跑 2 步、`rebuild` 只跑 1 步，
        # 直接覆盖会把别的步骤上次测到的值丢掉。
        if results:
            merged = dict((_load_state() or {}).get("step_secs") or {})
            # 只记**成功跑完**（rc=0）的步骤耗时。被停止 / 失败的步骤只跑了半截，
            # 把半截耗时记进去，下次同一步的估时会被算得离谱地短 ——
            # 进度条会在真正的标记到达前先冲到顶，然后被标记拽回去（倒退）。
            merged.update({s["name"]: s["secs"] for s in results if s["rc"] == 0})
            _save_state(step_secs=merged)
        # 估算成功后记下「锚点对应哪一局」，下次重抓数据就能自动跟随。
        #
        # 判据是「估算这一步真的跑过了」，不是看 kind ——
        # 早先写 `kind in ("crawl", "recalc")` 漏掉了默认的 "update"，
        # 结果最常用的「抓新局并重算」永远不记锚点，自动跟随形同虚设。
        if ok_all and any(s["name"] == "估算隐藏分" for s in results):
            _remember_anchor()
            # 顺手把「当前主人」这套产物存一份档（一个账号一份，重算即覆盖）。
            # 下次切回 TA 就能秒切，不用再跑几分钟 —— 主人 2026-09-22 的要求。
            # 放在 finished 之前：`finished` 必须真的代表「连存档都落好了」。
            try:
                _archive_owner()
            except Exception:
                pass
        # 收尾前把「哪一步失败」说清楚，并把整份任务日志落盘。
        # 为什么：非致命步骤失败（fatal=False）不中止流水线、也不写产物，
        # 事后完全查不到原因 —— 2026-09-22 用户报「没有全部成功」时就是这么卡住的。
        _bad = [s for s in results if s.get("rc") != 0]
        if _bad and not stopped:
            _job_log("✗ 未全部成功，失败的步骤：" + "、".join(
                "%s(rc=%s)" % (s["name"], s["rc"]) for s in _bad))
        _write_job_log(kind, results, ok_all, stopped)
        # 状态都落盘了，这才算真的结束
        _job_set(running=False, finished=True, ok=ok_all,
                 stopped=stopped, stopping=False, steps=results)
    except BaseException:
        _job_set(running=False, finished=True, ok=False,
                 error=traceback.format_exc(), steps=results)
        # 崩了也要留日志 —— 这正是最需要日志的时候
        _job_log("✗ 任务异常终止：" + traceback.format_exc().strip().splitlines()[-1])
        _write_job_log(kind, results, False, False)


def _job_progress() -> dict:
    """给界面轮询的进度快照。纯读取，不改状态。"""
    with _JOB_LOCK:
        j = dict(_JOB)
        j["log"] = list(_JOB["log"])
        j["steps"] = [{"name": s["name"], "rc": s["rc"], "secs": s["secs"]}
                      for s in _JOB["steps"]]

    if not j["running"] and not j["finished"]:
        return {"running": False, "finished": False, "frac": 0.0,
                "elapsed": 0.0, "eta": None, "step": 0, "step_total": 0,
                "step_name": "", "log": [], "steps": [], "ok": None,
                "error": None, "stopping": False, "stopped": False}

    now = time.time()
    elapsed = max(0.0, now - (j["started"] or now))
    plan = j["plan"] or []
    total_secs = sum(p["secs"] for p in plan) or 1.0

    if j["finished"]:
        frac, eta = 1.0, 0.0
    else:
        idx = max(0, j["step"] - 1)                    # 当前步骤（0-based）
        done_weight = sum(p["secs"] for p in plan[:idx])
        cur_weight = plan[idx]["secs"] if idx < len(plan) else 0.0
        in_step = max(0.0, now - (j["step_started"] or now))
        if j["step_marked"]:
            # 脚本已经吐出过真实标记 → 只认标记。
            # 不跟「已用/估时」取 max：估时一旦偏小，那项会瞬间冲到封顶值，
            # 反而把「刚开头」演成「快完了」，正好和封顶 0.9 的初衷相反。
            cur = min(1.0, j["step_frac"])
        elif j.get("step_has_marker"):
            # 这一步配了解析器，但脚本还没吐出任何能认的行 —— 不许拿估时抢跑。
            #
            # 估时是「上次实测耗时」，步骤变慢时它会在标记到达前先冲到 0.9；
            # 等第一个真实标记（值往往比 0.9 小得多）一到，进度条就**倒退**。
            # 2026-09-21 实测：第 1 步改走 SGP 后单次请求要 2.5s（估时 3.8s），
            # 「种子局详情」= 0.5 的标记把总进度从 0.0167 拽回 0.0093。
            # 有标记就等标记，宁可在开头停几秒，也不要出现回退。
            cur = 0.0
        else:
            # 完全没有标记的步骤才按估时线性推进，且封顶 0.9 ——
            # 慢很多时也不会卡在 99% 假装快完成。
            cur = min(0.9, in_step / cur_weight) if cur_weight > 0 else 0.0
        frac = min(1.0, (done_weight + cur_weight * cur) / total_secs)

        # ---- 剩余时间：按「当前这一步」外推，不用全程平均速率 ----
        #
        # 旧公式 eta = elapsed / frac * (1 - frac) 用的是**全程**平均速率：
        # 前面几步比估时快，整体速率就被拉高，于是最后一步再慢也会被算成
        # 「只剩几十秒」。2026-09-22 主人实机反馈：整体 89% 时显示「剩余约
        # 20 秒」，实际还要 90 秒。
        #
        # 新口径 = 当前步剩余 + 后面步骤的估时 × 全局快慢系数。
        rest_weight = sum(p["secs"] for p in plan[idx + 1:])
        planned_done = done_weight + cur_weight * cur
        # 全局快慢系数 = 实际已用 / 计划已用（>1 = 整体比估时慢）
        speed = (elapsed / planned_done) if planned_done >= 1.0 else 1.0
        speed = min(3.0, max(0.25, speed))
        # 当前步剩余 = 计划剩余 − 已经落后计划的量。
        # 平台期（标记不再更新）里 in_step 一直在涨，这一项就 1:1 往下走 ——
        # 显示的剩余时间跟着真实剩余一起减少，而不是冻结在 20 秒。
        step_remain = (1.0 - cur) * cur_weight - max(0.0, in_step - cur * cur_weight)
        if step_remain <= 0.0:
            # 已经超出整步估时 → 说明估时这次不可信，按超出量的一半保守往上爬，
            # 绝不假报「还剩 0 秒」（旧公式在这种时候也是往 0 掉的）。
            step_remain = max(1.0, (in_step - cur_weight) * 0.5)
        eta = max(0.0, step_remain + rest_weight * speed)

    return {
        "running": j["running"], "finished": j["finished"],
        "ok": j["ok"], "error": j["error"],
        "stopping": bool(j.get("stopping")), "stopped": bool(j.get("stopped")),
        "frac": round(frac, 4), "elapsed": round(elapsed, 1),
        "eta": round(eta, 1) if eta is not None else None,
        "step": j["step"], "step_total": j["step_total"],
        "step_name": j["step_name"],
        "log": j["log"], "steps": j["steps"],
        "total_secs": round(total_secs, 1),
        "owner": j.get("owner"),
    }


def _anchor_argv(anchor, games_ago, stored=None):
    """把界面上的锚点参数转成命令行参数。非法值返回 None。

    优先带 `--anchor-game-id`：只要锚点分数没变（说明没换新截图），
    脚本就能自己在新轨迹里找到同一局，用户一个数都不用填。
    分数变了 ⇒ 换了新截图 ⇒ 锚点局也换了，这时只能按用户填的「几局前」来。
    """
    argv = []
    if anchor in (None, "", 0, "0"):
        # 界面没给锚点 —— 换数据主人时就是这样。改用状态文件里
        # 「**当前数据主人**的锚点」（`_switch_owner_state()` 已经切好）。
        # 没有就用空 argv 返回，由调用方决定要不要退回 `--auto-anchor`。
        anchor = (stored or {}).get("anchor_score")
        if anchor in (None, "", 0, "0"):
            return []
        games_ago = (stored or {}).get("anchor_games_ago", games_ago)
    try:
        score = float(anchor)
    except (TypeError, ValueError):
        return None

    argv += ["--anchor", str(score)]

    gid = (stored or {}).get("anchor_game_id")
    old_score = (stored or {}).get("anchor_score")
    same_shot = False
    if gid is not None and old_score is not None:
        try:
            same_shot = abs(float(old_score) - score) < 0.5
        except (TypeError, ValueError):
            same_shot = False

    if same_shot:
        argv += ["--anchor-game-id", str(int(gid))]
    else:
        try:
            argv += ["--anchor-games-ago", str(max(0, int(games_ago or 0)))]
        except (TypeError, ValueError):
            return None
    return argv


def _start_job(kind: str, anchor, games_ago, players: int = 300, owner=None) -> dict:
    """启动一个后台任务，立刻返回；进度靠 progress() 轮询。

    owner：`{"puuid","name"}` —— 换数据主人（见 `_stages_for` 说明）。
    """
    global _JOB_ID
    # ⚠️ 检查与占位必须同一把锁（审查 H2）：原来检查完就放锁，经过 ~1 秒的
    # LCU 探测才落 running=True —— 两个入口（用户点按钮 / start_owner 与
    # apply_owner_save 自动触发的 rebuild）都能穿过这个窗口双任务并发，
    # 在同进程里互踩 sys.argv / stdout / 数据文件。
    # 现在先占位（界面会显示「准备中」），下面的前置检查失败就回滚。
    # 注意：锁内直接 _JOB.update，别调 _job_set —— Lock 不可重入，会死锁。
    with _JOB_LOCK:
        if _JOB["running"]:
            return {"ok": False, "msg": "已经有一个任务在跑了，等它结束再来。"}
        _JOB.update(running=True, finished=False, ok=None, error=None,
                    kind=kind, owner=None, plan=[], step=0, step_total=0,
                    step_name="准备中", step_frac=0.0, step_secs=0.0,
                    step_started=time.time(), stopping=False, stopped=False,
                    log=[], steps=[])

    def _abort(msg, **extra):
        """前置检查失败：回滚占位再返回，让界面回到「没有任务」状态。"""
        _job_set(running=False, finished=False, stopping=False)
        out = {"ok": False, "msg": msg}
        out.update(extra)
        return out

    # 会抓数据的任务：先确认游戏客户端在运行。
    # 不做这一步的话，用户看到的是「没有全部成功」+ 一堆看不懂的报错（2026-09-22
    # 主人要求「游戏没登录就写明先登录再点抓取」）。放在切状态**之前** ——
    # 启动不了就别动锚点状态。
    if kind in ("update", "crawl") and not _lcu_ready():
        return _abort("未检测到游戏客户端。请先登录游戏再点抓取。\n\n"
                      "（抓取时会把**当前登录账号**设为数据主人；"
                      "换账号后重算会以新账号为准）",
                      need_login=True)

    # ⚠️ 换主人必须在**启动前**就把状态切好：
    # 抓取步会改 hd_data 的 meta.puuid，而估算步的锚点参数是此刻定下来的 ——
    # 两者必须属于同一个人，否则会拿 A 的锚点去锚 B 的轨迹（静默算错分）。
    if owner and kind in ("update", "crawl"):
        _switch_owner_state(owner["puuid"])

    stored = _load_state()
    anchor_argv = _anchor_argv(anchor, games_ago, stored)
    if anchor_argv is None:
        return _abort("锚点必须是数字，局数必须是整数。")

    try:
        players = max(1, int(players))
    except (TypeError, ValueError):
        players = 300

    try:
        used_ago = max(0, int(games_ago or 0))
    except (TypeError, ValueError):
        used_ago = 0

    stages = _stages_for(kind, players, anchor_argv,
                         secs_hint=(stored or {}).get("step_secs"),
                         owner=owner)
    if not stages:
        return _abort("未知的任务类型：%s" % kind)

    _JOB_ID += 1
    jid = _JOB_ID
    # 清掉上次可能残留的停止标志 —— 不清的话新任务刚开始就会被脚本自己停掉。
    try:
        CANCEL_FILE.unlink()
    except OSError:
        pass
    _job_set(id=jid, kind=kind, running=True, finished=False, ok=None,
             error=None, started=time.time(), used_ago=used_ago,
             # 界面填的锚点分数（可能是 None）——估算步开跑前要用它重算一次 argv
             anchor_arg=anchor,
             owner=((owner.get("name") or owner.get("puuid", "")[:8] + "…")
                    if owner and kind in ("update", "crawl") else None),
             plan=[{"name": s["name"], "secs": s["secs"]} for s in stages],
             step=0, step_total=len(stages), step_name="准备中",
             step_frac=0.0, step_secs=0.0, step_started=time.time(),
             stopping=False, stopped=False,
             log=[], steps=[])

    th = threading.Thread(target=_run_job, args=(kind, stages, jid), daemon=True)
    th.start()
    return {"ok": True, "job": jid, "step_total": len(stages)}


# --------------------------------------------------------------------------
# 锚点自动跟随（按 gameId，不是数局数）
# --------------------------------------------------------------------------
# 锚点分数（截图里那个数）是唯一没法自动获取的东西 —— 它只存在于截图里。
# 但它指向的是**某一局**，所以只要记住那一局的 gameId，
# 重抓数据之后让估算脚本自己去新轨迹里找同一局就行，用户不用数「几局前」。
#
# ⚠️ 为什么不用「主人对局数增加了多少」去推：
#    `--anchor-games-ago` 是相对 **Elo 轨迹** 数局数的，而轨迹只包含
#    对手覆盖率达标的那部分对局（见估算脚本 [2/4] 的 min_cov 过滤）。
#    新爬进来的局有些进不了轨迹，所以「主人对局数增量」≠「轨迹长度增量」，
#    拿它去推会指到错误的局上，算出来的分是错的。
_STATE_FILE = DATA_DIR / "_desktop_state.json"


def _load_state() -> dict:
    """读状态。主文件读不出就退回上一次的备份。

    这个文件里存着「锚点对应哪一局」，是**唯一没法自动获取**的信息。
    它要是丢了，`_anchor_argv` 会退回用户填的「又打了几局」（通常 0），
    于是锚到最新一局上 —— 分数直接算错，而且**没有任何报错**。
    所以留一份上一代备份，读不出来时用它。
    """
    import json

    for f in (_STATE_FILE, _state_bak()):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d:
            return d
    return {}


def _state_bak():
    return _STATE_FILE.with_name(_STATE_FILE.stem + ".bak" + _STATE_FILE.suffix)


def _backup_state(raw: bytes) -> None:
    """把主文件的**当前内容**原子写成 .bak，并保证写出来的不是空壳。

    ⚠️ **不要用 `shutil.copyfile`**（2026-09-22 实测两次踩到）：
    它在受限/沙箱环境下会**先把目标文件建出来、内容却没写进去**，然后抛 `OSError`；
    调用处若 `except OSError: pass` 一吞，就留下一个 **0 字节**的
    `_desktop_state.bak.json`。而 `_load_state()` 读它 JSON 解析失败会静默跳过 ——
    备份「看着在、其实不存在」，恰好把「锚点丢失」这条**唯一**兜底路径废掉，
    而锚点一丢就是**静默算错分**，没有任何报错。

    改用与主文件**同一套**「写 .tmp → `os.replace`」原子写法 ——
    那套在本机是被长期验证可用的（主文件一直这么写）。写完再验尺寸；
    万一尺寸还是不对，就**把空的 .bak 删掉**：宁可没有备份，也不要留个骗人的空壳。

    主文件本身为空（raw 为空）时不写备份 —— 留个空壳比没有更坏。
    """
    import os

    if not raw:
        return
    bak = _state_bak()
    tmp = bak.with_name(bak.name + ".tmp")
    try:
        tmp.write_bytes(raw)
        if tmp.stat().st_size != len(raw):
            raise OSError("备份临时文件写入不完整")
        os.replace(tmp, bak)
    except OSError:
        # 清理：只删「空的」临时件与空壳 .bak，不要动上一代好备份
        for p in (tmp, bak):
            try:
                if p.exists() and p.stat().st_size == 0:
                    p.unlink()
            except OSError:
                pass


def _save_state(**kw) -> None:
    """增量更新状态文件，原子写 + 留备份。

    为什么原子写：`write_text` 是先截断再写，进程在中间被打断就会留下
    **0 字节**的文件（2026-09-21 真出现过，原因查不到，但后果是锚点丢失）。
    改成「写临时文件 → os.replace」，要么完整替换要么完全不动。

    备份走 `_backup_state()`（原子写 + 验尺寸），**别再用 `shutil.copyfile`**
    —— 受限环境下它会留下 0 字节空壳（见该函数说明）。
    """
    import json
    import os

    st = _load_state()
    st.update(kw)
    try:
        raw = _STATE_FILE.read_bytes() if _STATE_FILE.exists() else b""
        if raw:
            _backup_state(raw)                           # 先留上一代（原子 + 验尺寸）
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        payload = json.dumps(st, ensure_ascii=False, indent=1).encode("utf-8")
        tmp.write_bytes(payload)
        if tmp.stat().st_size != len(payload):
            raise OSError("tmp 写入不完整")
        os.replace(tmp, _STATE_FILE)                     # 原子替换
    except OSError:
        pass                    # 存不下就算了，顶多下次要手动填局数


def _remember_anchor() -> None:
    """估算成功后，把「锚点对应哪一局」记下来，下次自动跟随。

    只在估算成功后调用 —— 失败时输出文件可能是旧的，记下来反而有害。
    """
    import json

    f = DATA_DIR / ESTIMATE_NAME
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    a = d.get("anchor") or {}
    if a.get("anchor_game_id") is None:
        return
    st = _load_state()

    # ⚠️ 自动锚定（`--auto-anchor`，换数据主人后 TA 没截图时走的路）**不归档**：
    # 它的分数是「Elo + 复用的 offset」现算出来的，下次数据一变就该重算；
    # 当成截图锚点存下来反而会让 `_anchor_argv` 拿它去 `--anchor`，
    # 把「自动」伪装成「精确」。下次继续走 --auto-anchor。
    if a.get("anchor_source") == "auto":
        return

    kw = dict(anchor_game_id=a["anchor_game_id"],
              anchor_score=a.get("anchor_score"),
              anchor_games_ago=a.get("anchor_games_ago"),
              at=time.strftime("%Y-%m-%d %H:%M:%S"))
    # 只有**截图锚定**的 offset 才能当「估计器系统偏差」的基准存下来 ——
    # 估算脚本的 --auto-anchor 就靠它给新主人算分（见 海斗隐藏分估算_v2.auto_offset）。
    if a.get("offset") is not None:
        kw["traj_offset"] = a["offset"]
    pu = d.get("owner_puuid") or st.get("anchor_puuid")
    if pu:
        anchors = dict(st.get("anchors") or {})
        anchors[pu] = {
            "anchor_score": a.get("anchor_score"),
            "anchor_game_id": a.get("anchor_game_id"),
            "anchor_games_ago": a.get("anchor_games_ago"),
            "at": kw["at"],
        }
        kw["anchors"] = anchors
        kw["anchor_puuid"] = pu
    _save_state(**kw)


def _get_webview():
    """惰性导入 GUI 依赖：自检分支不需要它。"""
    global _webview
    if _webview is None:
        try:
            import webview
        except ImportError:
            _err_write(
                "缺少 pywebview，请先安装：\n"
                "    python -m pip install pywebview\n"
            )
            raise SystemExit(1)
        _webview = webview
    return _webview


_CSS = (
    "body{margin:0;min-height:100vh;display:flex;align-items:center;"
    "justify-content:center;background:#070d1a;color:#e3eaf8;"
    'font-family:"Microsoft YaHei",system-ui,sans-serif;line-height:1.85}'
    ".box{max-width:600px;padding:32px 36px;border:1px solid #20355f;"
    "border-radius:14px;background:#0e1a33}"
    "h1{font-size:19px;margin:0 0 16px;color:#f2c45d;font-weight:700}"
    "p{margin:0 0 11px;font-size:13.5px;color:#8ea2c8}"
    "code{background:#0a1326;border:1px solid #20355f;border-radius:6px;"
    "padding:2px 7px;font-size:12.5px;color:#8fb0ff;word-break:break-all}"
    ".k{color:#e3eaf8}"
)


def _error_page(title: str, lines) -> str:
    body = "".join("<p>%s</p>" % ln for ln in lines)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>%s</title><style>%s</style></head>"
        '<body><div class="box"><h1>%s</h1>%s</div></body></html>'
    ) % (title, _CSS, title, body)


class Api:
    """暴露给看板的操作接口。前端通过 window.pywebview.api.xxx() 调用。"""

    def info(self):
        return {
            "root": str(ROOT),
            "board": str(BOARD),
            "board_exists": BOARD.is_file(),
            "data_dir": str(DATA_DIR),
            "scripts_dir": str(_scripts_dir()),
            "frozen": bool(getattr(sys, "frozen", False)),
        }

    def last_anchor(self):
        """读上次用的锚点参数，给界面预填。

        `anchor_game_id` 是「锚点对应那一局」的 id —— 界面不用管它，
        但 `_start_job` 会用它让脚本自动定位，所以重抓数据后用户不用数局数。
        """
        import json

        f = DATA_DIR / ESTIMATE_NAME
        if not f.is_file():
            return {"ok": False, "msg": "还没有估算结果，先点一次「抓最新数据并重算」"}
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "msg": "读取估算结果失败：%s" % e}
        a = d.get("anchor") or {}
        st = _load_state()
        mine = d.get("owner_puuid")
        # 换过数据主人但还没重算 → 这份估算结果属于**上一个主人**，
        # 它的锚点绝不能当成新主人的（否则会拿 A 的截图分去锚 B 的轨迹）。
        switched = bool(mine and st.get("anchor_puuid") and mine != st["anchor_puuid"])
        if switched:
            a = {}
        return {
            "ok": True,
            "anchor": a.get("anchor_score") if a else st.get("anchor_score"),
            "games_ago": a.get("anchor_games_ago", 0) if a else (st.get("anchor_games_ago") or 0),
            "anchor_time": a.get("anchor_game_time"),
            "anchor_game_id": a.get("anchor_game_id") or st.get("anchor_game_id"),
            "estimate": None if switched else d.get("estimate"),
            "ci": None if switched else d.get("ci_half_95"),
            "stale": switched,
            "owner_puuid": mine,
            "owner_name": d.get("owner_name") or d.get("meta", {}).get("summoner"),
        }

    def start(self, kind="update", anchor=None, games_ago=0, players=300):
        """启动一个后台任务，**立刻返回**，进度靠 progress() 轮询。

        kind:
          update  = 抓客户端窗口的新局 → 补抓新对手 → 全量评分 → 估算 → 看板
                    （快，几十秒到几分钟。日常就点这个）
          crawl   = 同上，但第一步换成完整 BFS 抓 N 人（慢，十几分钟，网络更全）
          recalc  = 只估算 + 重建看板（不抓数据）
          rebuild = 只重建看板

        为什么改成后台任务：抓取要跑好几分钟，原来那种「一次调用等到底」
        的做法期间界面完全拿不到信息，进度条无从显示。
        """
        return _start_job(kind, anchor, games_ago, players)

    def start_owner(self, puuid, name="", deep=False):
        """把**数据主人**换成 `puuid`。

        **2026-09-22 起默认不重抓**：优先走「存档秒切」——
          · TA 有存档（每次管线跑完会自动存一份，见 `_archive_owner`）→
            换档 + 重算 meta + 重注入看板，**几秒**完成，不抓不重算；
          · TA 没有存档 → 才走抓取这条老路（`deep` 决定要不要完整 BFS）。
        主人原话：「我大号已经抓取计算过了，切换时直接切换，而不是耗几分钟去计算」。

        与 `start()` 的区别：`start()` 抓的是「当前数据主人」的新局；
        这个会把**主人本身**换掉 —— `hd_data` 的 meta / 看板的 `me_puuid`、
        逐局战绩、指标全部变成 TA 的。

        ⚠️ 对网络的影响是**并集**：`--seed` 只增不减，旧对局全部保留，
        所以换回去只要再抓一次种子（遇到已抓过的局立刻停，很快）。

        deep=True  → 完整 BFS（慢，十几分钟）：新人第一次用，把 TA 周围的网络抓出来
        deep=False → 只读客户端列表窗口（默认）：TA 在网络里已有很多局时用

        锚点：TA 有截图锚点（之前存过）就直接用；没有就 `--auto-anchor` 自动给分，
        之后拿到 TA 的实际分数截图再跑一次就会覆盖成精确值。
        """
        puuid = (puuid or "").strip()
        if not puuid:
            return {"ok": False, "msg": "没拿到这个玩家的 puuid，换不了主人。"}
        cur = _hd_meta().get("puuid")
        if not cur:
            return {"ok": False, "msg": "读不到 hd_data.json 的 meta，先点「抓新局并重算」。"}
        if puuid == cur:
            return {"ok": False, "msg": "TA 已经是当前数据主人了，直接点「抓新局并重算」就行。"}
        # ① 有存档 → **直接换档**（秒级），不重抓也不重算。
        #    主人 2026-09-22 的要求：「我大号已经抓取计算过了，切换时直接切换，
        #    而不是耗几分钟去计算」。存档在每次管线跑完时自动生成。
        saved = _owner_save_meta(puuid)
        if saved:
            r = _apply_owner_save(puuid)
            if r.get("ok"):
                # 只重注入看板（~2 秒），让页面上显示的就是 TA 的数据
                j = _start_job("rebuild", None, 0, 0)
                r.update(j)
                r["from_save"] = True
                r["msg"] = (r.get("msg") or "") + \
                    "（存档是 %s 算的，直接切换、没有重抓。想刷新网络请点「抓新局并重算」）" \
                    % (saved.get("at") or "?")
                return r
            # 存档坏了 → 别把用户卡死，落到下面走抓取
        # ② 没有存档 → 老路：抓一次（deep=True 才是完整 BFS；默认只抓窗口新局 + 补抓）
        kind = "crawl" if deep else "update"
        return _start_job(kind, None, 0, 300,
                          owner={"puuid": puuid, "name": (name or "").strip()})

    def data_owner(self):
        """当前数据主人是谁（看板操作栏用来提示「你现在看的是谁的数据」）。"""
        m = _hd_meta()
        st = _load_state()
        return {
            "ok": True,
            "puuid": m.get("puuid"), "name": m.get("summoner"),
            "prev_puuid": m.get("prev_puuid"),
            "switched_at": m.get("owner_switched_at"),
            "has_anchor": st.get("anchor_score") is not None,
            "anchor_puuid": st.get("anchor_puuid"),
            "auto_offset": st.get("traj_offset"),
        }

    def owner_saves(self):
        """列出所有「数据主人存档」（看板「管理存档」面板的数据源）。"""
        return _list_owner_saves()

    def apply_owner_save(self, puuid):
        """秒切到某份存档（换产物 + 物化评分表 + 切 meta/锚点），最后只重注入看板。"""
        r = _apply_owner_save(puuid)
        if not r.get("ok"):
            return r
        j = _start_job("rebuild", None, 0, 0)
        r.update(j)
        return r

    def drop_owner_save(self, puuid):
        """删除一份存档（**软删**：挪进 _stale\\，不硬删）。"""
        return _drop_owner_save(puuid)

    def open_saves_dir(self):
        """打开存档目录（排查/手动备份用）。"""
        d = str(OWNER_SAVE_DIR)
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                import subprocess
                subprocess.Popen(["explorer", d])
            return {"ok": True, "dir": d}
        except Exception as e:
            return {"ok": False, "msg": str(e), "dir": d}

    def progress(self):
        """当前任务的进度快照。界面每 0.5 秒轮一次。"""
        return _job_progress()

    def stop(self):
        """请求停止当前正在跑的任务（界面上的「■ 停止」按钮）。

        机制：写一个标志文件，脚本在自己主循环的**安全点**检查它，
        命中就抛 SystemExit(3) 安全退出（见 _paths.raise_if_cancelled）。

        为什么不用线程中断（PyThreadState_SetAsyncExc 那套）：
        脚本是用 runpy 在**本进程**里跑的，异步抛异常可能正好落在
        `json.dump(...)` 中间 —— 留下半个 JSON，看板下次直接读不了。
        协作式标志只在「一轮抓完 / 一局算完 / 一个 epoch 算完」这种
        安全点生效，绝不会写盘写到一半被打断。

        代价是响应有延迟：等于一个循环体的耗时（抓取侧 1~3 秒，
        估算侧一个 epoch 约 3 秒）。界面在等待期间显示「停止中 …」。

        停止后**本次结果不保存** —— 数据保持上一次的完整状态。
        """
        with _JOB_LOCK:
            if not _JOB["running"]:
                return {"ok": False, "msg": "现在没有在跑的任务。"}
            if _JOB.get("stopping"):
                return {"ok": True, "msg": "已经在停止了，等它收尾。"}
            _JOB["stopping"] = True
        try:
            CANCEL_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"),
                                   encoding="utf-8")
        except OSError as e:
            with _JOB_LOCK:
                _JOB["stopping"] = False
            return {"ok": False, "msg": "写停止标志失败：%s" % e}
        _job_log("收到停止请求，正在等当前这一小步跑完 ...")
        return {"ok": True, "msg": "已请求停止"}

    def recalc(self, anchor=None, games_ago=0):
        """兼容旧调用：等价于 start('recalc', ...)，仍然立刻返回。"""
        return self.start("recalc", anchor, games_ago)

    def rebuild(self):
        """兼容旧调用：等价于 start('rebuild')。"""
        return self.start("rebuild")

    def open_data_dir(self):
        """在资源管理器里打开数据目录。"""
        import subprocess

        try:
            subprocess.Popen(["explorer", str(DATA_DIR)])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def probe_player(self, puuid, name="", games=100):
        """抓某个玩家（任意 puuid）最近的海斗对局，并估算他的隐藏分。

        看板「逐局战绩 → 明细」里点玩家名 → 弹层里的「抓取数据」按钮走这里。

        和 start() 的区别：这个是**同步**的。它只跑一个脚本、实测 2~10 秒
        就完事，没必要再套一层进度条。pywebview 每次 JS→Python 调用都在
        自己的线程里执行，所以这里阻塞不会卡住界面渲染。

        互斥：主任务（抓取/重算）在跑时直接拒绝 —— `_run_script` 会临时替换
        sys.argv 和全局 stdout，两边同时跑会互相串输出、互相抢 sys.path。
        """
        import json

        if not puuid:
            return {"ok": False, "msg": "缺少 puuid"}
        # 审查 H2：检查后必须占位到脚本跑完 —— 原来检查完就放锁，_run_script
        # 执行期间用户点「抓新局并重算」仍能启动主任务，两边同进程 runpy
        # 互踩 sys.argv / stdout / 数据文件。占位后 finally 统一回滚。
        with _JOB_LOCK:
            if _JOB["running"]:
                return {"ok": False, "msg": "正在跑抓取/重算任务，等它结束再查别人。"}
            _JOB.update(running=True, finished=False, ok=None, error=None,
                        kind="probe", owner=None, plan=[], step=0, step_total=0,
                        step_name="查询玩家", step_frac=0.0, step_secs=0.0,
                        step_started=time.time(), stopping=False, stopped=False,
                        log=[], steps=[])
        try:
            return self._probe_locked(puuid, name, games)
        finally:
            with _JOB_LOCK:
                if _JOB.get("kind") == "probe":
                    _JOB.update(running=False, finished=False, stopping=False)

    def _probe_locked(self, puuid, name, games):
        import json
        try:
            n = max(1, min(int(games or 100), 100))     # SGP 上限就是 100
        except (TypeError, ValueError):
            n = 100

        argv = ["--puuid", str(puuid), "--games", str(n)]
        if name:
            argv += ["--name", str(name)]
        rc, out = _run_script("_player_probe.py", argv)
        if rc != 0:
            return {"ok": False, "msg": "抓取脚本退出码 %s" % rc, "log": out[-4000:]}
        try:
            d = json.loads((DATA_DIR / "_player_probe.json").read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "msg": "读取抓取结果失败：%s" % e, "log": out[-4000:]}
        return {"ok": True, "probe": d, "log": out[-2000:]}


def _self_check() -> int:
    print("frozen    ", bool(getattr(sys, "frozen", False)))
    print("ROOT      ", ROOT)
    print("BOARD     ", BOARD, "->", "存在" if BOARD.is_file() else "缺失")
    print("DATA_DIR  ", DATA_DIR, "->", "存在" if DATA_DIR.is_dir() else "缺失")
    print("SCRIPTS   ", _scripts_dir(), "->", "存在" if _scripts_dir().is_dir() else "缺失")
    return 0 if BOARD.is_file() else 1


def _self_test_run(argv) -> int:
    """在冻结环境里真跑一个脚本，验证 runpy 链路通不通。

        海斗隐藏分.exe --selftest-run <脚本名> [脚本参数...]

    这条路径和界面按钮走的是**同一个** `_run_script`，
    所以它能跑通，就说明「重新计算」按钮能跑通。

    注意：只支持 Python 脚本。传 .js（如 _verify_toolbar.js）会报
    SyntaxError —— runpy 会把它当 Python 解析。断言脚本请走
    01_脚本\\验证看板.bat（Node + jsdom）。
    """
    if not argv:
        print("用法：--selftest-run <脚本名> [脚本参数...]")
        return 2
    name, rest = argv[0], list(argv[1:])
    print("frozen    ", bool(getattr(sys, "frozen", False)))
    print("SCRIPTS   ", _scripts_dir(), "->", "存在" if _scripts_dir().is_dir() else "缺失")
    print("RUN       ", name, " ".join(rest))
    rc, out = _run_script(name, rest)
    print("RC        ", rc)
    print("---- stdout ----")
    _out_write(out if out.endswith("\n") else out + "\n")
    print("---- /stdout ----")
    return 0 if rc == 0 else 1


def _run_gui() -> int:
    webview = _get_webview()

    if not BOARD.is_file():
        webview.create_window(
            APP_TITLE,
            html=_error_page("找不到看板文件", [
                "程序在下面这个位置没找到 <code>%s</code>：" % BOARD_NAME,
                '<span class="k">%s</span>' % BOARD,
                '数据根目录定位到：<br><span class="k">%s</span>' % ROOT,
                "请确认 <code>03_看板\\</code> 与 <code>02_数据\\</code> "
                "和本程序在同一个分享包里，然后重新打开。",
            ]),
            width=760, height=560, resizable=True,
        )
        webview.start()
        return 0

    webview.create_window(
        APP_TITLE,
        url=str(BOARD),
        js_api=Api(),
        width=WIN_W, height=WIN_H,
        min_size=WIN_MIN,
        text_select=True,
    )
    webview.start()
    return 0


def main() -> int:
    """GUI 入口。--windowed 构建下崩溃时用户看不到任何东西，所以兜底弹框。"""
    try:
        return _run_gui()
    except BaseException:
        import traceback
        _message_box(APP_TITLE, "程序启动失败：\n\n" + traceback.format_exc())
        raise


def _cli_mode() -> bool:
    return "--check" in sys.argv or "--selftest-run" in sys.argv


def _run_cli() -> int:
    if "--check" in sys.argv:
        return _self_check()
    i = sys.argv.index("--selftest-run")
    return _self_test_run(sys.argv[i + 1:])


def _entry() -> int:
    """真正的入口。抽成函数是为了让「无控制台兜底」这条分支可被测试。"""
    if not _cli_mode():
        # 只允许开一个窗口：两个实例各自点「重新计算」会同时写同一批
        # 数据文件（估算结果 / 看板数据 / 看板 HTML），互相踩。
        if not _acquire_single_instance():
            if not _focus_existing_window():
                _message_box(
                    APP_TITLE,
                    "海斗隐藏分已经在运行了。\n\n"
                    "请到任务栏找它的窗口；\n"
                    "如果找不到，先在任务管理器里结束它，再重新打开。",
                )
            return 0
        return main()

    if _ensure_stdio():
        return _run_cli()

    # 没有控制台可接 —— 捕获输出，用弹窗显示，否则用户什么都看不到
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = _run_cli()
    _message_box(APP_TITLE, buf.getvalue() or "（没有输出）")
    return rc


if __name__ == "__main__":
    raise SystemExit(_entry())
