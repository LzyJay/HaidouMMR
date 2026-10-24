# -*- coding: utf-8 -*-
"""锚点状态备份（`_desktop_state.json`）的回归测试。

为什么要有这个文件（2026-09-22 实测两次踩到）：
`02_数据\\_desktop_state.bak.json` 曾经是 **0 字节** —— 根因是 `shutil.copyfile`
在受限环境下**先把目标文件建出来、内容却没写进去**，然后抛 `OSError`，
而调用处 `except OSError: pass` 一吞就留下空壳。备份「看着在、其实不可用」，
恰好把「锚点 gameId 丢失」唯一的兜底路径废掉 —— 而锚点一丢就是**静默算错分**。

现在 `_backup_state()` 改用与主文件同一套「写 .tmp → os.replace」原子写法，
并在写完验尺寸；尺寸不对就**把空壳删掉**。

本测试守护的**不变量**：**任何情况下都不许留下 0 字节的 `.bak`**
（要么是完整内容，要么干脆不存在），且写入被干扰时不能破坏上一代好备份。

不碰真实数据：把 app 的 `_STATE_FILE` / `_state_bak` 全部指向临时目录。

跑法：`python 07_测试\\test_state_backup.py`  → 期望「全部通过」，退出码 0。
"""
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "06_桌面版" / "app.py"

SEED = {"anchor_game_id": 10000000002, "anchor_score": 1928.0, "at": "2026-09-21 23:07:00"}
_ok = True


def check(name, cond, extra=""):
    global _ok
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        _ok = False


def load_app():
    spec = importlib.util.spec_from_file_location("hd_app_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # app.py 有 __main__ 保护，import 不会开窗口
    return mod


def main() -> int:
    global _ok
    if not APP.is_file():
        print("✗ 找不到 %s" % APP)
        return 1

    app = load_app()
    tmp = Path(tempfile.mkdtemp(prefix="hd_state_test_"))
    app._STATE_FILE = tmp / "_desktop_state.json"
    real_write_bytes = pathlib.Path.write_bytes
    try:
        print("临时 state: %s" % app._STATE_FILE)

        # ---- 场景 A：正常路径 ----
        print("\n[场景 A] 正常写：主文件有内容")
        app._STATE_FILE.write_text(json.dumps(SEED, ensure_ascii=False), encoding="utf-8")
        app._save_state(anchor_games_ago=45)
        main_raw = app._STATE_FILE.read_bytes()
        bak_raw = app._state_bak().read_bytes()
        check("主文件非空", len(main_raw) > 0, "%dB" % len(main_raw))
        check("备份非空（不是 0 字节）", len(bak_raw) > 0, "%dB" % len(bak_raw))
        check("备份内容 = 写入前的主文件", json.loads(bak_raw) == SEED)
        check("主文件已含新键", json.loads(main_raw).get("anchor_games_ago") == 45)
        check("没留下 .tmp 残file", not list(tmp.glob("*.tmp")))

        # ---- 场景 B：备份落盘被干扰（模拟「只建空文件、不写内容」）----
        # 这正是 copyfile 那个病灶的等价形态。不变量：绝不留 0 字节 .bak。
        print("\n[场景 B] 备份写盘被干扰 → 不许留下 0 字节 .bak")
        bak = app._state_bak()
        bak.unlink()                        # B1：本来没有备份

        def bad_write_bytes(self, data):
            if self.name.endswith(".bak.json.tmp"):
                real_write_bytes(self, b"")          # 只建空文件
                return 0
            return real_write_bytes(self, data)

        pathlib.Path.write_bytes = bad_write_bytes
        try:
            app._save_state(anchor_games_ago=46)
        finally:
            pathlib.Path.write_bytes = real_write_bytes
        check("没有留下 0 字节的 .bak（空壳应被删掉）",
              (not bak.exists()) or bak.stat().st_size > 0,
              "%dB / exists=%s" % (bak.stat().st_size if bak.exists() else -1, bak.exists()))

        print("\n[场景 B2] 已有好备份时被干扰 → 不许破坏上一代")
        real_write_bytes(bak, json.dumps(SEED, ensure_ascii=False).encode("utf-8"))
        app._STATE_FILE.write_text(json.dumps(SEED, ensure_ascii=False), encoding="utf-8")
        pathlib.Path.write_bytes = bad_write_bytes
        try:
            app._save_state(anchor_games_ago=47)
        finally:
            pathlib.Path.write_bytes = real_write_bytes
        check("上一代好备份仍在且非空",
              bak.exists() and bak.stat().st_size > 0,
              "%dB" % (bak.stat().st_size if bak.exists() else -1))
        check("上一代备份内容没被写坏", json.loads(bak.read_bytes()) == SEED)

        # ---- 场景 C：源为空时不留空壳备份 ----
        print("\n[场景 C] 主文件是空的 → 不留空备份")
        app._STATE_FILE.write_bytes(b"")
        bak.unlink()
        app._backup_state(b"")
        check("未生成备份文件", not bak.exists())
        check("也没留下临时件", not list(tmp.glob("*.tmp")))

        # ---- 场景 D：_load_state 的兜底顺序 ----
        print("\n[场景 D] 主文件坏了 → 退回备份")
        app._STATE_FILE.write_bytes(b"{ not json")
        bak.write_text(json.dumps(SEED, ensure_ascii=False), encoding="utf-8")
        check("_load_state 从备份读回来了", app._load_state() == SEED)

        print("\n" + ("全部通过" if _ok else "有失败项"))
    finally:
        pathlib.Path.write_bytes = real_write_bytes
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
