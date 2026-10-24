# -*- coding: utf-8 -*-
"""把 06_桌面版\\theme.css 注入看板 HTML 的 <style> 块。

用法
----
    python 06_桌面版\\apply_theme.py              # 应用主题
    python 06_桌面版\\apply_theme.py --restore    # 还原成改版前的样式
    python 06_桌面版\\apply_theme.py --dry-run    # 只看会改什么，不写盘
    python 06_桌面版\\apply_theme.py --check      # 只查「theme.css 会不会丢样式」，不写盘
    python 06_桌面版\\apply_theme.py --force      # 明知会丢样式也照写（危险）

设计说明
--------
- 只替换 <style> 块，不碰数据块、不碰 DOM 结构 ——
  所以 _verify_dash.js 的 44 条断言和 iso 的 11 条都不受影响。
- 幂等：同样的输入重复运行，结果一致。
- --restore 用的是备份 HTML 里的 style 块，而不是整个备份文件。
  这样「先更新了数据、再想还原样式」不会把新数据一起回滚掉。

⚠️ 铁律 55（2026-09-28 第十轮新增）：**theme.css 是「整块替换」的唯一真源，必须与
   看板 HTML 的 <style> 保持同步**。历史上吃过亏：分区位置模块新增的 69 行样式
   （`.pos-grid` / `.anc-tab` / `.pos-card` / `.pbtn` / `.pos-row2` / `.pos-svg` / `.pnote`
   以及 `.det-team` 的宽度与注释）只写进了 HTML，theme.css 没跟上 ——
   这时候谁跑一次 apply_theme.py，那 69 行就被**静默抹掉**，看板上分区位置模块直接掉样式，
   而脚本还会打印「✓ 已写入」。
   ⇒ 现在写入前会逐行比对：只要「当前看板样式里有、theme.css 里没有」的行非空，
     就**中止**并列出这些行，除非显式加 `--force`。
   ⇒ 正确做法：先改 HTML 的 <style>（那是所见即所得的落点），再跑
      `python -c "..."` 或 `--check` 把 theme.css 同步成一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "03_看板" / "海斗隐藏分_数据看板.html"
THEME = Path(__file__).resolve().parent / "theme.css"
BACKUP = ROOT / "_backup_20260920_改UI前" / "03_看板" / "海斗隐藏分_数据看板.html"

STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)
DATA_MARK = '<script id="dash-data"'


def _extract_style(html: str, what: str) -> str:
    m = STYLE_RE.search(html)
    if not m:
        raise SystemExit("在 %s 里找不到 <style> 块，已中止。" % what)
    return m.group(1)


def _lost_lines(old_css: str, new_css: str):
    """old 里有、new 里没有的非空行（strip 后按集合比，忽略空行与纯注释位移）。"""
    have = {l.strip() for l in new_css.splitlines() if l.strip()}
    return [l.strip() for l in old_css.splitlines() if l.strip() and l.strip() not in have]


def _atomic_write(path: Path, text: str) -> None:
    """原子写（审查 H5）：tmp → 验尺寸 → os.replace；bytes 落盘不做换行转换。"""
    import os
    tmp = path.with_name(path.name + ".tmp")
    data = text.encode("utf-8")
    tmp.write_bytes(data)
    if tmp.stat().st_size != len(data):
        raise OSError("写入不完整: %s" % path.name)
    os.replace(tmp, path)


def main() -> int:
    dry = "--dry-run" in sys.argv
    restore = "--restore" in sys.argv

    if not BOARD.is_file():
        raise SystemExit("找不到看板：%s" % BOARD)
    with open(BOARD, encoding="utf-8", newline="") as _f:   # newline=""：字节保真（审查 H5）
        html = _f.read()

    if restore:
        if not BACKUP.is_file():
            raise SystemExit(
                "找不到备份：\n  %s\n无法还原样式。" % BACKUP
            )
        new_css = _extract_style(BACKUP.read_text(encoding="utf-8"), "备份文件")
        origin = "备份（改版前的原始样式）"
    else:
        if not THEME.is_file():
            raise SystemExit("找不到主题文件：%s" % THEME)
        new_css = THEME.read_text(encoding="utf-8")
        origin = str(THEME)

    old_css = _extract_style(html, "看板")
    lost = _lost_lines(old_css, new_css)
    if lost:
        print("⚠ 目标样式比当前看板少 %d 行（照写会丢这些规则）：" % len(lost))
        for l in lost[:60]:
            print("   - " + l[:110])
        if len(lost) > 60:
            print("   … 还有 %d 行" % (len(lost) - 60))
        if restore:
            print("  （--restore 是回到改版前样式，丢样式属预期）")
        elif "--check" in sys.argv or "--dry-run" in sys.argv:
            print("  → 只查不写，未写盘。")
            return 2
        elif "--force" not in sys.argv:
            print("  → 已中止（未写盘）。先把这些规则补进 theme.css，"
                  "或加 --force 明确接受。")
            return 2
        else:
            print("  → 你加了 --force，继续写入。")
        print()

    if old_css == new_css:
        print("样式与目标一致，无需写入。")
        return 0

    if "--check" in sys.argv:
        print("--check：theme.css 与看板样式有差异（无丢失），未写盘。")
        return 0

    new_html = STYLE_RE.sub(
        lambda _m: "<style>" + new_css + "</style>", html, count=1
    )

    print("来源    : %s" % origin)
    print("原样式  : %d 字符" % len(old_css))
    print("新样式  : %d 字符" % len(new_css))
    print("HTML    : %d -> %d 字符" % (len(html), len(new_html)))

    if dry:
        print("\n--dry-run：未写盘。")
        return 0

    # 安全检查：替换后数据块必须还在，否则宁可失败也不写坏文件
    if DATA_MARK not in new_html:
        raise SystemExit("替换后数据块丢失，已中止（未写盘）。")

    _atomic_write(BOARD, new_html)
    print("\n✓ 已写入 %s" % BOARD)
    if not restore:
        print("  还原：python 06_桌面版\\apply_theme.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
