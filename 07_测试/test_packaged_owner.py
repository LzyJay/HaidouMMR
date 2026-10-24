# -*- coding: utf-8 -*-
"""打包产物自检：**exe 里的**「换数据主人」接口是不是真的在。

为什么需要它：PyInstaller 打完包之后，「源码改了但没重新打包」这件事**没有任何报错** ——
界面照常开、按钮照常点，但调的是旧代码（用户看到的还是「抓我自己」）。
`_internal\\_scripts` 那批是散文件能靠 sha256 比对兜住，**app.py 冻进 exe 本体**，
只能真的把 exe 跑起来验。

跑法：`python 07_测试\\test_packaged_owner.py`
   · 找不到 exe / 不是 Windows → 跳过（退出码 0），不算失败；
   · 有 exe → 把自己拷进 `_internal\\_scripts` 再让 exe 用 `--selftest-run` 执行。

冻结环境的两个坑（都踩过）：
  ① `import app` → ModuleNotFoundError（入口脚本不是可 import 的模块）；
  ② `sys.modules["__main__"]` 也不是 app —— `runpy.run_path()` 默认 `run_name="__main__"`，
     会把 `__main__` 换成正在跑的这个脚本本身。
  真正拿得到的是 **`sys.modules["__mp_main__"]`**。
  ③ PowerShell 捕获 exe 的中文输出会乱码 → 结果**自己落盘**再读，别信 stdout。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "HaidouMMR" / "HaidouMMR.exe"
INNER = ROOT / "HaidouMMR" / "_internal" / "_scripts"

if not EXE.is_file():
    print("⏭ 跳过：没有 %s（还没打包），不算失败。" % EXE)
    raise SystemExit(0)
if not INNER.is_dir():
    print("⏭ 跳过：没有 %s（不是 onedir 形态），不算失败。" % INNER)
    raise SystemExit(0)

RESULT = Path(tempfile.gettempdir()) / "hd_pkg_result.txt"

PROBE = r'''# -*- coding: utf-8 -*-
import sys, tempfile
from pathlib import Path
OUT = Path(tempfile.gettempdir()) / "hd_pkg_result.txt"
L = []


def ck(n, c, x=""):
    L.append(("  ✓ " if c else "  ✗ ") + n + (("  -> " + str(x)) if (x and not c) else ""))
    return bool(c)


ok = True
m = None
for k, v in list(sys.modules.items()):
    if getattr(v, "Api", None) is not None:
        m = v
        break
ck("能在冻结产物里找到 app 模块（__mp_main__）", m is not None,
   "sys.modules 里没有带 Api 的模块")
if m is None:
    L.append("结果: 拿不到 app，无法验证")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    raise SystemExit(1)

ok &= ck("Api.start_owner 存在", hasattr(m.Api, "start_owner"))
ok &= ck("Api.data_owner 存在", hasattr(m.Api, "data_owner"))
ok &= ck("_switch_owner_state 存在", hasattr(m, "_switch_owner_state"))
ok &= ck("_hd_meta 存在", hasattr(m, "_hd_meta"))

# 数据主人存档（2026-09-22）：秒切 + 一个账号一份 + 软删
ok &= ck("存档 API 齐全（列表 / 切换 / 删除 / 打开目录）",
         all(hasattr(m.Api, n) for n in ("owner_saves", "apply_owner_save",
                                         "drop_owner_save", "open_saves_dir")))
ok &= ck("存档内部函数齐全（建档 / 列表 / 物化评分表 / 切 meta）",
         all(hasattr(m, n) for n in ("_archive_owner", "_list_owner_saves",
                                     "_materialize_elo", "_apply_owner_save",
                                     "_drop_owner_save", "_set_owner_meta")))
import inspect as _ins
ok &= ck("start_owner 默认 deep=False（默认不重抓，优先走存档秒切）",
         _ins.signature(m.Api.start_owner).parameters["deep"].default is False,
         str(_ins.signature(m.Api.start_owner)))
ok &= ck("客户端预检查在（没登录就提示，而不是等到失败）", hasattr(m, "_lcu_ready"))

o = {"puuid": "PU-TEST", "name": "N#1"}
st = m._stages_for("update", 0, [], owner=o)
ok &= ck("update+owner：抓取步带 --me / --me-name",
         "--me" in st[0]["argv"] and "PU-TEST" in st[0]["argv"], st[0]["argv"])
ok &= ck("无锚点时估算步退到 --auto-anchor",
         st[-2]["argv"] == ["--auto-anchor"], st[-2]["argv"])

st2 = m._stages_for("update", 0, ["--anchor", "1928"], owner=o)
ok &= ck("有锚点时估算步仍用锚点（不被 auto 覆盖）",
         "--anchor" in st2[-2]["argv"], st2[-2]["argv"])

st3 = m._stages_for("recalc", 0, [], owner=o)
# ⚠️ 2026-09-22 改了口径：**没有可用锚点时一律 --auto-anchor**（不再只在
# 「显式换主人」时才给）。原来 recalc 无锚点是「不带任何锚点」→ 报出未校准的
# Elo 刻度；切了 LCU 账号 / 朋友第一次用走的正是这条路。见 00_使用说明.md 铁律 43。
ok &= ck("recalc 不换主人（无抓取步）",
         all("--me" not in s["argv"] for s in st3),
         [s["argv"] for s in st3])
ok &= ck("recalc 无锚点时也给自动锚定（不退化成未校准 Elo）",
         st3[0]["argv"] == ["--auto-anchor"], st3[0]["argv"])

argv = m._anchor_argv(None, 0,
                      {"anchor_score": 1928.0, "anchor_game_id": 99, "anchor_games_ago": 49})
ok &= ck("_anchor_argv 无界面锚点时回落到状态里的锚点",
         argv == ["--anchor", "1928.0", "--anchor-game-id", "99"], argv)

L.append("\n结果: " + ("全部通过" if ok else "有失败项"))
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
raise SystemExit(0 if ok else 1)
'''

tmp = INNER / "_pkg_owner_probe.py"
tmp.write_text(PROBE, encoding="utf-8")
if RESULT.exists():
    try:
        RESULT.unlink()
    except OSError:
        pass

print("exe: %s" % EXE)
try:
    r = subprocess.run([str(EXE), "--selftest-run", tmp.name],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       timeout=180)
finally:
    try:
        tmp.unlink()
    except OSError:
        pass

if not RESULT.is_file():
    print("✗ 探针没产出结果文件（exe rc=%s）" % r.returncode)
    print((r.stdout or "")[-600:])
    raise SystemExit(1)

text = RESULT.read_text(encoding="utf-8")
print(text)
# 审查 M12：外层必须同时看 returncode —— 只匹配「有失败项」子串时，
# 「拿不到 app 模块」的失败路径（探针写「结果: 拿不到 app，无法验证」后
# exit 1，正文里没有「有失败项」四个字）会被误判成通过 = 假绿。
if r.returncode != 0:
    print("✗ exe 退出码 %s（探针内部有失败项 / 异常）" % r.returncode)
    raise SystemExit(1)
raise SystemExit(0 if "有失败项" not in text else 1)
