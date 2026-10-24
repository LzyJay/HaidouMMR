# -*- coding: utf-8 -*-
"""测试断言计数器的**唯一真身** —— 零依赖、零副作用。

2026-10-03 建立。取代 7 个测试文件里各写一遍的 `check` / `ck`。

为什么不用 `_v6_common.py` 直接共享
-----------------------------------
`_v6_common.py` 有**模块顶层副作用**，不能当公共 helper 被其余文件 import：

  · `:51-53`  找不到 `02_数据/海斗dashboard_data.json` 就 `sys.exit(2)`
  · `:55`     导入时即加载 0.8 MB dashboard JSON
  · `:27-29`  `ROOT = os.path.dirname(HERE)` 隐含「本文件在 07_测试/」的位置假设

若 9 个文件都 import 它，等于把 `test_exe_live`（探测窗口）、`test_progress`
（exec `app.py`）、`test_owner_switch`（起临时副本跑 e2e）全部绑到
「dashboard 数据必须存在」上。本模块刻意不做任何一件事：不 import 数据、
不 sys.exit、不含 ROOT/HERE 位置假设，因此在任意层级被 import 都安全。

三个家族，契约不同，不要混用
--------------------------
  A 计数器式（本模块）      打印每条 + 统计 通过/失败 数 + 以退出码表态
    _v6_common.py / test_snapshot.py /
    test_traj_offset.py / test_owner_switch.py / test_progress.py /
    test_dashboard_layout.py
  B 标志式                 一个 `global ok` 布尔，不计数
    test_exe_live.py / test_state_backup.py
  C 收集式                 往 list 追加、返回 bool、ASCII `->`
    test_packaged_owner.py

用法
----
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _check import check, finish

    check("锚点数 > 50", len(A) > 50)
    check("区间是 975~2682", got == (975, 2682), got)   # extra 只在失败时打印
    finish()

需要函数内局部作用域（计数器不能是模块全局）时用 `Counter`：

    c = Counter()
    c.check(...)
    c.print_summary()
    return c.exit_code()
"""
import sys

__all__ = ["Counter", "CNT", "check", "finish", "counts", "exit_code", "print_summary"]

_OK = "\u2713"            # ✓
_BAD = "\u2717"           # ✗
_ARROW = "  \u2192 "      #   →


class Counter:
    """断言计数器。

    两种用法：
      · 模块级单例 CNT —— 绝大多数测试文件用 `from _check import check`
      · 局部实例         —— 计数器必须是函数内局部变量时用（test_dashboard_layout）
    """

    def __init__(self):
        self.pass_n = 0
        self.fail_n = 0

    def check(self, name, cond, extra=""):
        """断言一条。extra 只在**失败**时打印，用于显示实际值。

        extra 用真值判定（与 7 份历史实现里的多数派一致）：
        空串 / 0 / None / [] 都不打印箭头。纯显示差异，不影响计数与退出码。
        """
        if cond:
            self.pass_n += 1
            print("  %s %s" % (_OK, name))
        else:
            self.fail_n += 1
            if extra:
                print("  %s %s%s%s" % (_BAD, name, _ARROW, extra))
            else:
                print("  %s %s" % (_BAD, name))
        return bool(cond)

    def print_summary(self, prefix="\u7ed3\u679c", rule="", footers=()):
        """打印汇总行（**不**退出）。rule 是可选分隔线，footers 是附加行。"""
        print("\n%s: %d \u901a\u8fc7 / %d \u5931\u8d25"
              % (prefix, self.pass_n, self.fail_n))
        if rule:
            print(rule)
        for line in footers:
            print(line)

    def exit_code(self):
        """0 = 全绿，1 = 有失败。给「不走 sys.exit 而 return」的调用方用。"""
        return 1 if self.fail_n else 0

    def finish(self, prefix="\u7ed3\u679c", rule="", footers=()):
        """打印汇总并 sys.exit。放在每个测试文件的最后一行。"""
        self.print_summary(prefix=prefix, rule=rule, footers=footers)
        sys.exit(self.exit_code())


# 模块级单例：让 `from _check import check` 能直接用
CNT = Counter()


def check(name, cond, extra=""):
    """断言一条（用模块级单例 CNT）。见 Counter.check。"""
    return CNT.check(name, cond, extra)


def print_summary(prefix="\u7ed3\u679c", rule="", footers=()):
    """打印汇总行但不退出。给「自己控制退出码」的测试文件用。"""
    CNT.print_summary(prefix=prefix, rule=rule, footers=footers)


def counts():
    """返回 (通过数, 失败数)。给收尾格式自定义的测试文件用。"""
    return CNT.pass_n, CNT.fail_n


def exit_code():
    """0 = 全绿，1 = 有失败。"""
    return CNT.exit_code()


def finish(prefix="\u7ed3\u679c", rule="", footers=()):
    """打印汇总并 sys.exit。**放在每个测试文件的最后一行。**

    prefix   汇总行前缀，默认「结果」->  结果: 27 通过 / 0 失败
    rule     可选分隔线
    footers  附加行（test_owner_switch 用它打印临时目录路径）
    """
    CNT.finish(prefix=prefix, rule=rule, footers=footers)
