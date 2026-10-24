# -*- coding: utf-8 -*-
"""表现分（对局评分）公式的**唯一真身** —— 纯计算，不碰任何文件。

为什么单独抽一个模块（第九轮，2026-09-27）：
  `_dash_rebuild.py` 是「import 即执行」的脚本（模块级就在读 json、跑聚合），
  所以诊断脚本**没法 import 它**来复用公式。若各自复制一份常量，
  就直接违反本项目自己的铁律 45 ——「同一个值有两份落点时，迟早会过期」。
  ⇒ 公式抽到这里，所有调用方 import 同一个对象。

  谁**不**共享这里（故意保持独立复算，它是验证器）：
    · `01_脚本/_verify_player.js`   —— JS 侧独立实现
  验证器必须独立于被验证对象，否则「两边都错」会一起绿。

改动铁律：**改这里的权重/曲线，必须同步更新**
  ① `_dash_rebuild.py` 重跑 → 注入 HTML（前端 `compositeRawOf()` 读 DATA，自动跟随）
  ② `HaidouMMR/_internal/_scripts/_composite.py`（cp，铁律 11）
  ③ `_verify_player.js` 里写死的期望值
  ④ 文档：`00_使用说明.md` 铁律 46/49、`04_文档/SKILL.md`

"""

# 8 项指标的固定顺序（口径见 `_stats_crawl.py`）。
# ⚠️ `heal_share`（治疗占比）主页口径**不显示**；A/B 实测它是**中性**项
#    （砍掉 MAE 反而上升）。复核后仍在噪声内，且重拟会把权重压到区间下界 4
#    —— 没有证据支持改，保留 6。
METRIC_KEYS = ("dmg_share", "taken_share", "heal_share", "gold_share",
               "dmg_conv", "kill_part", "kda", "win_rate")

# 表现分锚点：把「近 30 局平均表现评分」平移到一个已知值（默认 65.1）。
# 语义 = 只做一次**整体平移**（让 mean(近30局逐局评分) 落在锚点上），不压动态。
# 换锚点就改这一个数（或由 `_player_elo_build.py --anchor` 那一路下发）。
COMPOSITE_ANCHOR = 65.1

# 权重和 = 100。第九轮按 99 个真值点在「可解释区间 [6,18]」内约束拟合后取整，
# 再由用户拍板（2026-09-28）：伤害占比 20、胜率 4。
#
# ⚠️ 第十轮复核（158 点）**没有改这组权重**，依据：
#   · 自由拟合最优 = 16.5/16/4/4/22/18/15.5/4，MAE 3.195 → 3.018（只买 0.177）；
#   · 把用户拍板的 `dmg_share=20 / win_rate=4` 钉住后再拟合其余 6 项，
#     最优 = 20/14.4/4/4/21/18.1/14.5/4，MAE → 3.054（只买 0.141）；
#   · 两版的 `gold_share`/`heal_share` 都被压到区间下界 4，是「参数不够用」的
#     典型信号（真正的缺口在曲线顶端，见文件头分档偏差 −1.94）；
#   · 按局留一 CV 过拟合间隙 +0.176 ⇒ 0.14 的样本内增益落不进 CV 噪声之外。
#   ⇒ 结论：**保持用户拍板值**。改权重 = 推翻用户决定 + 全部文档/测试重写，
#     换 0.14 MAE，不划算。要真正解决顶端低估，得改曲线形状，那是另一件事。
#
# ⚠️ `win_rate` 从 14 降到 4 是第九轮**最大的单点改动**，必须写清理由，别当成拍脑袋：
#   · 数据：参照口径里胜方的平均分比败方高 **+5.89** 分（70.48 vs 64.59）。
#     这 5.89 分里，其余 7 项指标顺带能解释掉 2.88 分（赢的那队数据平均确实好一点），
#     剩下 3.01 分只能靠「胜」这个事实本身解释 —— 海斗里大量「你 C 了队友送」
#     「你躺了队友 C」的局，8 项数据抓不到胜负。所以胜率**必须有**权重。
#   · 但 14 太夸张：w=14 时「同样数据，赢了比输了多 12.04 分」（占满分 12%），
#     而实测只有 5.89 分（5.9%）。w=4 时是 3.44 分。
#   · 两个**互相独立**的口径都指向 4~5：
#       ① MAE 最小 → w=5（MAE 2.989）
#       ② 本地胜负差 == 参照胜负差 → w=4（本地 6.27 vs 参照 5.89）
#   · w=0（彻底拿掉）反而更差：MAE 3.289，且胜方偏差掉到 −2.80（系统性低估赢家）。
#   · 第十轮 158 点复核：胜负两组偏差几乎相同（胜 −0.960 / 负 −0.945）⇒ w=4 没有
#     制造新的胜败偏斜，保持。
#   ⇒ 结论：胜率是**小修正项**（约 3~5 分，占 3~5%），不是主导项。
COMPOSITE_WEIGHTS = {
    "dmg_share": 20,       # 伤害占比：输出能力        （v6 14 → 20）
    "taken_share": 13,     # 承伤占比：敢扛敢打        （v6 10 → 13）
    "heal_share": 6,       # 治疗占比：辅助贡献        （保持；A/B 实测中性）
    "gold_share": 8,       # 经济占比：资源获取        （保持）
    "dmg_conv": 18,        # 伤害转化：输出效率        （v6 14 → 18）
    "kill_part": 18,       # 参团率：团战参与          （保持）
    "kda": 13,             # KDA                       （v6 16 → 13）
    "win_rate": 4,         # 胜率                      （v6 14 → 4，见上面长注释）
}

# 每指标参照曲线 [(指标值, 得分)] 分段线性，区间外夹紧。参照点标定依据：
# 5 人队伍的"平均人" = 该占比 20%（治疗/承伤同理）；KDA 2.0≈及格 3.0≈良好；
# 参团率 63%≈大乱斗平均；伤害转化 1.0=伤占≈经济占。
#
# ⚠️ 第九轮修正（2026-09-28）：**每条曲线的末控制点得分一律抬到 100**。
#    原来末点只有 78~97（如 heal 78、gold 86、win 90），导致
#    「8 项全打满」的加权和只有 93.5 —— 这套公式**永远打不到 100 分**，
#    而实测出现过 100 和 99.7。抬端点后 8 项全打满 = raw 100。
#    语义变得很直白：**每一项打到极致就是该项满分**。
#    实测代价：MAE 3.020 → 2.990（变好），最大误差 10.55 → 8.97（变好）。
#    ⚠️ 副作用：部分曲线的末段变陡（如 gold_share 24%→28% 对应 76→100）。
#       这是「顶端区分度」的必然代价，不是 bug。
#    ⚠️ 第十轮复核：曲线**形状**确实是剩余误差的主因（>70 档偏差 −1.94），
#       但单调校正的诚实 CV 增益只有 0.194，且改形状要重定 8×N 个控制点、
#       全部文档与验证脚本跟着变 —— 收益/风险不划算，本轮不动。将来若要做，
#       优先动的是 `dmg_share` 顶端（7.9% 伤害占比的局参照值能给到 62 分，
#       我们只给 ~49）与底端（参照值底端比我们平）。
COMPOSITE_CURVES = {
    "dmg_share":  [(6, 4), (10, 22), (13, 36), (16, 48), (19, 60), (23, 76), (27, 88), (33, 100)],
    "taken_share": [(5, 8), (9, 26), (13, 44), (17, 60), (21, 74), (26, 86), (32, 100)],
    "heal_share": [(1, 22), (5, 34), (10, 45), (16, 55), (23, 64), (32, 72), (45, 100)],
    "gold_share": [(10, 18), (14, 38), (17, 50), (20, 62), (24, 76), (28, 100)],
    "dmg_conv":   [(0.3, 6), (0.6, 24), (0.9, 44), (1.2, 62), (1.5, 78), (1.9, 90), (2.4, 100)],
    "kill_part":  [(25, 12), (40, 30), (52, 46), (63, 61), (74, 75), (85, 88), (96, 100)],
    "kda":        [(0.4, 6), (0.8, 20), (1.3, 36), (2.0, 52), (2.8, 67), (3.6, 79), (4.6, 89), (6.5, 100)],
    "win_rate":   [(25, 14), (35, 28), (45, 44), (52, 55), (60, 68), (68, 80), (78, 100)],
}

# ---- 溢出后夹紧区间（第九轮新增，第十轮已复核）----
#
# 口径是「**先算无上限的原始分、再夹到 0~100**」：
# 截图里出现过 100（打满）和 38（接近底部）。
# 我们的 raw 是有界加权平均，所以必须显式夹紧才能复现这两个端点行为。
#   · 上限 100：不加的话，抬端点后 raw 最高可到 100 + offset ≈ 103.6（>100 很怪）。
#     第十轮扫描确认 **100 就是最优**：96→MAE 3.212 / 98→3.208 / 100→3.195 /
#     102→3.208 / 104→3.220。
#   · 地板 38：真值点里最低**仍然**是 38，而我们的底分只有 12.8
#     （raw 9.18 + offset 3.64）。地板 38 让 MAE 3.295（地板 30）→ 3.195。
# ⚠️ 第九轮写「地板只有 1 个真值点支撑（n=1），将来有更多底部样本再复核」——
#    第十轮就是那次复核，158 点结论：
#      地板  38 → MAE 3.195（2 个点被夹）
#      地板  44 → MAE 3.140（4 个点被夹）← 最优
#      地板  50 → MAE 3.174（10 个点被夹）
#    但夹到 44 会让模型**再也出不了 38**，而 38 是实测到的最低真值 ——
#    拿保真度换 0.055 MAE（1.7%）不划算，**保持 38**。
COMPOSITE_CLAMP = (38.0, 100.0)

# ---- 局内统治力加成（第九轮新增，第十轮复核）----
#
# 机制来源（不是拍脑袋）：把真值点按分数排序后发现 ——
#   · 真值 ≥85 的点，「6 项里最好排名」**全部 = 1**（每局都在某一项上全场第一）
#   · 真值 55~70 的点里，只有 1/4 满足
# ⇒ 总分高的局里除了「各项都好」，还有「**某一项碾压全场**」的成分。
#   这正是用户猜的「评分是有溢出的」—— 只是溢出不在总分上，在单项统治力上。
#
# 第十轮 158 点复核（机制**更强**了，单调关系很干净）：
#   分数档位        n    至少 1 项全场第 1 的比例   平均加成
#   ≥85           14          100.0%              2.43
#   75~85         24           66.7%              1.12
#   55~70         85           25.9%              0.34
#   <50            9            0.0%              0.00
#
# 口径：本局在下面 6 项里**有几项是全场 10 人第 1**（并列也算），每项 +1 分。
#   · 不含 `heal_share`：治疗第 1 通常是辅助位，和「统治力」不是一回事
#     （实测加进去 MAE 从 2.895 涨到 2.932，变差）。
#   · 不含 `win_rate`：它是 0/100 的二值，谈不上「全场第一」，加进去也变差（2.959）。
# ⚠️ 加成**必须计入 offset 标定**（见 `offset_for` 的 mean_bonus 参数）：
#    锚点要求 mean(近30局逐局最终分) == 65.1，加成不参与标定会让它漂 +0.73 分。
#    计入标定后加成只值 0.03 MAE —— 收益很小，但它让「高分 = 至少有一项碾压」
#    这条机制在公式里有落点，不是白加的。
# ⚠️ 第十轮扫过 per：0.0→3.205 / 0.5→3.170 / **1.0→3.195** / 1.5→3.255 /
#    2.0→3.338 / 3.0→3.602（158 点）。per 越大顶端偏差反而**更负**
#    （−0.84 → −1.18）⇒ 加成不能拿来补顶端低估，保持 1.0。
DOMINANCE_KEYS = ("dmg_share", "taken_share", "gold_share",
                  "dmg_conv", "kill_part", "kda")
DOMINANCE_PER = 1.0     # 每拿到一项全场第一加多少分；设 0 等于关掉这个机制


def curve_score(key, val):
    """分段线性插值，区间外夹紧到端点。"""
    pts = COMPOSITE_CURVES[key]
    if val <= pts[0][0]:
        return pts[0][1]
    if val >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= val <= x2:
            return y1 + (y2 - y1) * (val - x1) / (x2 - x1) if x2 > x1 else y2
    return pts[-1][1]


def metrics(rs):
    """窗口均值。⚠️ KDA / 伤害转化是**聚合口径**（v6，2026-09-27）：
      · KDA = Σ(K+A) / ΣD —— 不是逐局 (K+A)/max(D,1) 再平均。后者会被「1 杀 20 死」这种局
        抬高，实测近 30 局 4.53，而参照值 3.55。
      · 伤害转化 = 均值伤害占比 ÷ 均值经济占比 —— 不是逐局转化再平均。实测 0.974→0.97，
        与参照值 0.97 一致（旧口径 0.96）。
    其余占比 / 参团 / 胜率两边口径本来就一致，不用动。

    实测：本人近 30 局这 7 项 + 战绩与参照界面**逐位吻合，差 0.00**。
    第十轮又用新卡（2026-09-28 01:59）复核一次，仍是**逐位 0.00 差**
    （18.9 / 19.2 / 19.6 / 0.97 / 67.5 / 3.61 / 56.7，17胜13负）
    ⇒ 这一层是准的，别动；要改的是「指标 → 分」的映射（曲线/权重/加成）。
    """
    if not rs:
        return None
    n = len(rs)
    w = sum(1 for r in rs if r["win"])
    return {
        "n": n, "win_rate": round(100 * w / n, 1),
        "kda": round(sum(r["k"] + r["a"] for r in rs) / max(sum(r["d"] for r in rs), 1), 2),
        "dmg_share": round(sum(r["dmg_share"] for r in rs) / n, 1),
        "taken_share": round(sum(r["taken_share"] for r in rs) / n, 1),
        "gold_share": round(sum(r["gold_share"] for r in rs) / n, 1),
        "heal_share": round(sum(r["heal_share"] for r in rs) / n, 1),
        "dmg_conv": round(sum(r["dmg_share"] for r in rs)
                          / max(sum(r["gold_share"] for r in rs), 1e-9), 2),
        "kill_part": round(sum(r["kill_part"] for r in rs) / n, 1),
    }


def roster_metrics(team_players, p, team_kills):
    """从 roster 的原始数值重算「这个玩家这一局」的 8 项指标。

    分母一律取**我方 5 人**之和，与 `_stats_crawl.py` 的口径完全一致。
    已实测：本人这 5 项占比与 my_stats.json 逐局吻合（0 处偏差），
    所以 roster 里的 dmg/taken/gold/heal/k/d/a 足够反推**任何人**的对局评分 ——
    这也是第九轮能拿 99 个「本局评分」真值点做标定的前提。

    精度也照抄 `_stats_crawl.py`：占比 1 位小数、转化 2 位小数。
    不这么抄的话「我」的对局评分和 series 里的 comp 会差 ±0.1 分
    （my_stats.json 存的是四舍五入后的占比，roster 重算用的是全精度）。

    ⚠️ 转化用**原始分数**相除再四舍五入，不是拿两个已四舍五入的占比去相除
    （后者会再飘 ±0.01，进而让综合分飘 ±0.1）。
    """
    td = sum(x["dmg"] for x in team_players) or 1
    tt = sum(x["taken"] for x in team_players) or 1
    tg = sum(x["gold"] for x in team_players) or 1
    th = sum(x["heal"] for x in team_players) or 1
    conv = (p["dmg"] / td) / ((p["gold"] / tg) or 1)
    return {
        "dmg_share": round(p["dmg"] / td * 100.0, 1),
        "taken_share": round(p["taken"] / tt * 100.0, 1),
        "gold_share": round(p["gold"] / tg * 100.0, 1),
        "heal_share": round(p["heal"] / th * 100.0, 1),
        "dmg_conv": round(conv, 2),
        "kill_part": round(min((p["k"] + p["a"]) / max(team_kills, 1) * 100.0, 100.0), 1),
        "kda": round((p["k"] + p["a"]) / max(p["d"], 1), 2),
        "win_rate": 100.0 if p["win"] else 0.0,
    }


def composite_raw(rows):
    """绝对原始综合分（**未平移、未夹紧、未加统治力加成**）。rows = my_stats 行列表：
    单行 → 用该局自身值过曲线；多行 → 用窗口均值过曲线。
    ⚠️ v6（2026-09-27）起，**窗口综合分不再走多行分支** —— 改成 `window_mean_raw()`：
    逐局过曲线再求平均（「平均本局评分」口径）。
    多行分支保留是因为前端 `compositeOf()` 的历史结构还在，但后端已无调用点；
    新代码要用窗口分请用 `window_mean_raw()`，别用 `composite_raw(多行)`。
    """
    if not rows:
        return None
    if len(rows) == 1:
        m = dict(rows[0])
        m.setdefault("win_rate", 100.0 if m.get("win") else 0.0)
    else:
        m = metrics(rows)
    total = 0.0
    for k, w in COMPOSITE_WEIGHTS.items():
        total += curve_score(k, m[k]) * w
    return total / 100.0


def window_mean_raw(rows):
    """窗口原始分 = 窗口内**逐局原始分**的算术平均（不过 offset、不加加成）。

    这就是「平均本局评分 / 近期局内表现评分」口径（v6 起）。
    和 `composite_raw(多行)` 的区别：这里先逐局过曲线再平均，不是先平均再过曲线。

    ⚠️ 这个函数**不含统治力加成**（加成需要全场 10 人数据，纯指标层拿不到）。
    要算「带加成的窗口分」请用 `window_mean_final()`。
    """
    if not rows:
        return None
    return sum(composite_raw([r]) for r in rows) / len(rows)


def window_mean_final(rows, offset, bonuses=None):
    """窗口**最终分** = 窗口内逐局 `finalize()` 之后再取算术平均。

    ⚠️ 必须**逐局先夹紧再平均**，不能「先平均再夹紧」—— 夹紧是非线性操作，
    两者不等价。口径是「每局先出一个分，再平均」，所以按逐局来。

    bonuses：与 rows 等长的逐局加成列表；None 表示全 0。
    """
    if not rows:
        return None
    bs = bonuses if bonuses is not None else [0.0] * len(rows)
    tot = sum(finalize(composite_raw([r]), offset, b) for r, b in zip(rows, bs))
    return tot / len(rows)


# ============================================================================
# 统计工具（2026-10-03 抽自三份同源副本）
# ============================================================================
def pearson(a, b, min_n=2, nd=4):
    """Pearson 相关系数。样本不足或任一方零方差时返回 None。

    **min_n / nd 是历史差异的参数化，不要统一。**

    来源：三份逐字/近似相同的实现，2026-10-03 合并到本模块
    （`_composite.py` 是「公式唯一真身」，统计口径也该只有一个落点）：

        位置                      min_n   nd
        _dash_rebuild.py            2      4
        _player_elo_build.py        2      4

    参数化而非写死的原因：两处的 `round(...,4)` 与不舍入不是笔误，
    是各自报告口径的差异；强行拉平会让相关系数小数位变化。

    min_n  最小样本数。2 个点的相关系数恒为 ±1、没有信息量。
    nd     小数位；None 表示不舍入（原样返回 float）。
    """
    n = len(a)
    if n < min_n:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if not da or not db:
        return None
    r = num / (da * db)
    return round(r, nd) if nd is not None else r


def offset_for(rows, anchor=COMPOSITE_ANCHOR, mean_bonus=0.0):
    """平移量 = 锚点 −（该窗口逐局原始分均值 + 逐局加成均值）。只平移位置，不压动态。

    ⚠️ `mean_bonus` 必须传！锚点是一个**关于最终分**的外部真值
    （参照值「近期局内表现评分 64.9」），而最终分 = raw + offset + 加成。
    所以 offset 必须把加成的均值吸收掉，否则 mean(近30局最终分) 会漂
    （实测漂 +0.73 分，看板上的「综合评分」就和锚点对不上了）。
    """
    v = window_mean_raw(rows)
    if v is None:
        return 0.0
    return anchor - (v + mean_bonus)


def clamp_score(v):
    """按 `COMPOSITE_CLAMP` 夹紧；None 时原样返回（v6 行为）。"""
    if v is None or COMPOSITE_CLAMP is None:
        return v
    lo, hi = COMPOSITE_CLAMP
    return min(max(v, lo), hi)


def finalize(raw, offset, bonus=0.0):
    """原始分 → 最终分：先加平移量、再加统治力加成，最后夹紧。

    ⚠️ 加成的**位置**在夹紧之前：加成是「原始分的一部分」，溢出夹紧
    也是作用在「算完的总分」上。放在夹紧之后会让 100 分被加成顶穿。
    """
    if raw is None:
        return None
    return clamp_score(raw + offset + bonus)


# ============================================================================
# 局内统治力加成
# ============================================================================
def dominance_n1(lobby_metrics, my_metrics, keys=None):
    """本局我在 `DOMINANCE_KEYS` 里有几项是**全场 10 人第 1**（并列也算第 1）。

    lobby_metrics：本局**全部 10 人**的指标 dict 列表（`roster_metrics()` 的产物）。
    my_metrics   ：我自己的那一份（值要与 lobby_metrics 里那一份一致）。

    ⚠️ 分母是「全场 10 人」而不是「我方 5 人」—— 这是**唯一**一处跨队比较，
    因为「统治力」的语义就是「整局里没人比你这项更高」。
    """
    keys = keys or DOMINANCE_KEYS
    n = 0
    for k in keys:
        try:
            top = max(m[k] for m in lobby_metrics)
        except (KeyError, ValueError):
            continue
        if my_metrics.get(k, -1e9) >= top:
            n += 1
    return n


def dominance_bonus(n1, per=None):
    """第 1 项数 → 加成分。`per=0` 等于关掉这个机制。"""
    if not n1:
        return 0.0
    return (DOMINANCE_PER if per is None else per) * n1


def max_raw(W=None):
    """理论最高原始分（未平移）。**用来钉住「公式能不能打到 100」**：
    加完 offset 后必须 ≥ 100，否则这套公式永远出不了满分，与实测矛盾。
    `W` 省略时用现行权重；传候选权重可以预检「这套权重还打不打得到 100」。"""
    W = W or COMPOSITE_WEIGHTS
    return sum(COMPOSITE_CURVES[k][-1][1] * w for k, w in W.items()) / 100.0


def min_raw(W=None):
    """理论最低原始分（未平移）。"""
    W = W or COMPOSITE_WEIGHTS
    return sum(COMPOSITE_CURVES[k][0][1] * w for k, w in W.items()) / 100.0


def saturation_profile(target=100.0, offset=0.0, W=None, bonus=0.0):
    """**满分画像**：要达到 target 分，8 项需要在各自区间里同时走到多靠右的位置。

    口径：把每条曲线按**指标值区间**线性参数化（t=0 最左端、t=1 最右端），
    求「8 项都在同一相对水平 t」时要多大 t 才凑够 target。
    ⇒ 比「其余 7 项固定、只推这一项」（会得出「需要 244 分」这种废话）有用得多。

    `bonus`：假设本局拿到 k 项全场第一时的加成分（默认 0）。
    返回 dict：{t, values, scores, full_raw, full_score, need, reachable}
    """
    W = W or COMPOSITE_WEIGHTS
    need = target - offset - bonus

    def value_at(k, t):
        pts = COMPOSITE_CURVES[k]
        return pts[0][0] + t * (pts[-1][0] - pts[0][0])

    def f(t):
        return sum(curve_score(k, value_at(k, t)) * W[k] for k in W) / 100.0

    full_raw = f(1.0)
    if full_raw < need:
        return {"reachable": False, "need": need, "full_raw": full_raw,
                "full_score": min(full_raw + offset + bonus, target),
                "deficit": need - full_raw}
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) < need:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2
    return {"reachable": True, "t": t, "need": need, "full_raw": full_raw,
            "full_score": min(full_raw + offset + bonus, target),
            "values": {k: round(value_at(k, t), 2) for k in W},
            "scores": {k: round(curve_score(k, value_at(k, t)), 1) for k in W}}


if __name__ == "__main__":
    # 自检：打印理论上限/下限 —— 用来确认「公式能不能打到 100」
    print("指标数        %d" % len(METRIC_KEYS))
    print("权重和        %g" % sum(COMPOSITE_WEIGHTS.values()))
    print("理论 raw 区间 %.2f ~ %.2f" % (min_raw(), max_raw()))
    print("锚点          %.1f" % COMPOSITE_ANCHOR)
    print("夹紧区间      %s" % (COMPOSITE_CLAMP,))
    print("统治力加成    %d 项 × %.1f 分/项（%s）"
          % (len(DOMINANCE_KEYS), DOMINANCE_PER, ", ".join(DOMINANCE_KEYS)))
    print("⇒ 公式上限 %.1f，%s" % (max_raw(),
          "能打到 100" if max_raw() >= 100 else "**打不到 100**（需放开曲线端点或调权重）"))
    # 满分画像（offset 取 0，只看「raw 层面」要多少）
    for b in (0.0, DOMINANCE_PER, 2 * DOMINANCE_PER):
        p = saturation_profile(target=100.0, offset=0.0, bonus=b)
        if p["reachable"]:
            print("   加成 %+.1f 时：8 项都达到各自区间的 %.0f%% 即满分"
                  % (b, p["t"] * 100))
        else:
            print("   加成 %+.1f 时：打不到 100（缺 %.2f）" % (b, p["deficit"]))
