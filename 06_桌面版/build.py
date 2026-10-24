# -*- coding: utf-8 -*-
"""一键把桌面版打包成可执行的程序。

用法
----
    python 06_桌面版\\build.py             # 打包（默认 onedir，启动快）
    python 06_桌面版\\build.py --onefile   # 打成单文件 exe（便携，启动慢 6-8 倍）
    python 06_桌面版\\build.py --check     # 只查环境和预检，不打包
    python 06_桌面版\\build.py --clean     # 打包前先清掉中间产物

通常不用直接跑它 —— 双击 build.bat 即可，那个 bat 只负责找到 Python
然后调本脚本。

为什么默认 onedir 而不是 onefile
--------------------------------
本机实测（2026-09-20，用 --check 计时，走完整启动路径）：

    onefile   6411 / 7360 / 8343 / 8107 ms   ← 而且越跑越慢
    onedir    1097 / 1012 / 1028 / 1054 ms   ← 快 6-8 倍
    裸 Python 1380 / 1259 ms（参考基准）

onefile 每次启动都要把 15 MB 解压到 %TEMP%，然后杀软逐个文件扫一遍 ——
这是它慢的全部原因，跟应用本身没关系。onedir 直接读磁盘，没有这一步。

代价：产物从一个 exe 变成一个文件夹（约 31 MB / 255 个文件）。
需要「拷到哪都能跑」的单文件版时，用 --onefile。

为什么逻辑放在 Python 而不是 bat
--------------------------------
bat 的代码页、延迟展开、中文路径问题太多，出错时也不好定位。
放 Python 里能给出清楚的报错，而且每一步都可以单独验证。

打包流程
--------
1. 在 06_桌面版\\.venv-build\\ 建独立环境（不动你全局的 Python）
2. 装 pywebview + pyinstaller
3. 跑 app.py --check 预检 —— 找不到看板就中止，不会打个坏包出来
4. PyInstaller 打包（onedir 默认）
5. 挪到包根（onedir → HaidouMMR\\ 目录；onefile → HaidouMMR.exe），
   并对产物再跑一次 --check
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 固定 UTF-8 输出：否则输出被重定向到管道时，Windows 会按 locale
# 编码（GBK）写出，中文日志全变乱码。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENV = HERE / ".venv-build"
APP = HERE / "app.py"
ICON = HERE / "icon.ico"
EXE_NAME = "HaidouMMR"
DEPS = ("pywebview", "pyinstaller")

# 默认 onedir；显式加 --onefile 才打单文件
ONEFILE = "--onefile" in sys.argv
# 旧版本挪这儿，不直接删（受限环境里批量删 255 个文件会被安全策略拦下，
# 而且留一份旧版等于自带回滚路径）
STALE = ROOT / "_stale"


def out_exe() -> Path:
    """最终产物的 exe 路径。"""
    if ONEFILE:
        return ROOT / (EXE_NAME + ".exe")
    return ROOT / EXE_NAME / (EXE_NAME + ".exe")


def run(cmd, **kw):
    print("    $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], **kw)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_venv() -> Path:
    py = venv_python()
    if py.is_file():
        print("[*] 复用已有环境: %s" % VENV)
        return py

    print("[*] 创建独立环境: %s" % VENV)
    r = run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0 or not py.is_file():
        sys.exit("[X] 创建 venv 失败。请确认 Python 3.13+ 安装正常。")
    return py


def ensure_deps(py: Path) -> None:
    r = run([py, "-c", "import webview, PyInstaller"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0:
        print("[*] 依赖已就绪")
        return

    print("[*] 安装依赖: %s" % ", ".join(DEPS))
    run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    r = run([py, "-m", "pip", "install", *DEPS])
    if r.returncode != 0:
        sys.exit("[X] 安装依赖失败，检查网络或代理。")


def preflight(py: Path) -> bool:
    return run([py, APP, "--check"]).returncode == 0


def _dir_stats(d: Path):
    n = 0
    size = 0
    for p in d.rglob("*"):
        if p.is_file():
            n += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return n, size


def build(py: Path) -> None:
    # 想彻底重来：python build.py --clean
    if "--clean" in sys.argv:
        for d in (HERE / "build", HERE / "dist"):
            if d.exists():
                print("[*] 清理 %s" % d)
                shutil.rmtree(d, ignore_errors=True)

    mode = "--onefile" if ONEFILE else "--onedir"

    # onefile 时先把旧产物删掉 —— PyInstaller 覆盖时自己也会删，
    # 由我们来更可控（受限环境里批量删除会被安全策略拦下）。
    if ONEFILE:
        old_exe = HERE / "dist" / (EXE_NAME + ".exe")
        if old_exe.is_file():
            old_exe.unlink()

    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean", mode, "--noupx",
        # --windowed（= --noconsole）：打成 GUI 子系统。
        # 不加的话是 Console 子系统，Windows 会给它分配一个黑窗口 ——
        # 双击时先弹命令行窗口再开看板，很难看。
        # 代价：没有控制台，sys.stdout/stderr 可能为 None。
        # app.py 的 _ensure_stdio() 负责在带参数跑时把输出接回去。
        "--windowed",
        "--name", EXE_NAME,
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--collect-all", "webview",
        # 把 01_脚本 一起打进去：桌面版的「重新计算」按钮要在 exe 进程内
        # 跑这些脚本（打包后没有独立 python.exe，起不了子进程）。
        # 运行时解压/读取到 _scripts\，由 app.py 加进 sys.path。
        "--add-data", "%s;_scripts" % (ROOT / "01_脚本"),
    ]
    if ICON.is_file():
        # 必须给绝对路径：--specpath 会把 spec 放进 build\，
        # 相对路径会被解析成 build\icon.ico 而找不到。
        cmd += ["--icon", str(ICON)]
    else:
        print("[!] 没找到 icon.ico，将使用默认图标")
    cmd.append(str(APP))

    if run(cmd).returncode != 0:
        sys.exit("[X] PyInstaller 打包失败，看上面的日志。")

    if ONEFILE:
        src = HERE / "dist" / (EXE_NAME + ".exe")
        if not src.is_file():
            sys.exit("[X] 没找到打包产物: %s" % src)
        dst = ROOT / (EXE_NAME + ".exe")
        shutil.copy2(src, dst)
        print("\n✓ 已生成 %s  (%.1f MB)"
              % (dst, dst.stat().st_size / 1024 / 1024))
        return

    # onedir：产物是个目录，要整棵挪过去
    src_dir = HERE / "dist" / EXE_NAME
    if not src_dir.is_dir():
        sys.exit("[X] 没找到打包产物目录: %s" % src_dir)

    dst_dir = ROOT / EXE_NAME
    if dst_dir.exists():
        STALE.mkdir(exist_ok=True)
        tag = time.strftime("%Y%m%d_%H%M%S")
        parked = STALE / ("%s_%s" % (EXE_NAME, tag))
        dst_dir.rename(parked)
        print("[*] 旧版本已挪到 %s" % parked)
        print("    （确认新版正常后，_stale\\ 可以整个删掉）")

    shutil.move(str(src_dir), str(dst_dir))

    n, size = _dir_stats(dst_dir)
    print("\n✓ 已生成 %s\\%s.exe" % (dst_dir, EXE_NAME))
    print("    %d 个文件 / %.1f MB   —— 双击这个 exe 即可" % (n, size / 1024 / 1024))


def main() -> int:
    print("=" * 58)
    print(" 海斗隐藏分 · 桌面版打包  [%s]" % ("onefile" if ONEFILE else "onedir"))
    print("=" * 58)

    if not APP.is_file():
        sys.exit("[X] 找不到 app.py: %s" % APP)

    py = ensure_venv()
    ensure_deps(py)

    print("\n[*] 预检 ...")
    if not preflight(py):
        print("\n[X] 预检失败：找不到看板 HTML。")
        print("    确认 02_数据\\ 和 03_看板\\ 与本脚本所在包结构一致。")
        return 1

    if "--check" in sys.argv:
        print("\n[*] --check：环境与预检均通过，未执行打包。")
        return 0

    print("\n[*] 开始打包（约 1-2 分钟）...")
    build(py)

    print("\n[*] 对产物再跑一次自检 ...")
    if run([out_exe(), "--check"]).returncode != 0:
        print("\n[!] 产物自检未通过，见上面的输出。")
        return 1

    print("\n完成。双击 %s 即可打开看板。" % out_exe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
