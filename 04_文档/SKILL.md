---
name: 海斗隐藏分估算
description: 估算英雄联盟「海克斯大乱斗(海斗)」的隐藏分(MMR)。用户问"我的海斗隐藏分多少/帮我算隐藏分/我隐藏分涨了还是跌了/海斗 ELO 查询"时使用。通过 League 客户端本地 LCU REST 抓取 queueId=2400(海斗)对局网络(滚雪球 BFS)，用对手真实海斗战绩做 Bradley-Terry 拟合 + 本人 Elo 轨迹 + 截图锚点单点校准。v2 起不再用峡谷排位段位代理。支持 WeGame 渠道版(lockfile 不刷新)。
agent_created: true
---

# 海斗隐藏分估算（v2）

## 文件位置（2026-09-20 重构后，**唯一权威副本**）

- **包根**：`<包根>\`
  - `00_使用说明.md` ← **先读这个**（完整文件清单 / 数据 schema / 可调参数 / 改码铁律 / 验证方法）
  - `01_脚本\` 全部程序 + `_paths.py`（唯一路径入口）+ `验证看板.bat`
  - `02_数据\` 全部 json　·　`03_看板\` HTML　·　`04_文档\` 文档与 SKILL 副本　·　`05_历史版本\` 废弃件
  - `07_测试\` 测试脚本（2026-09-21 从 `%TEMP%` 收进来，按 `__file__` 推路径）
    · `test_progress.py`（213 条，不碰真实数据）· `test_job_e2e.py`（真实 6 步端到端）
    · `test_anchor_v6.py`（**27 条，恒定口径**：重锚 + v6 口径独立复算 —— 逐局 comp **208 局零偏差** /
      `mean(近30 comp) == 65.1 == 锚点`）
    · `test_snapshot.py`（**随数据浮动**：KDA 聚合 / 伤害转化显示值 / estimate 落锚 ±100；
      红了先看是不是抓了新局）
    · `test_traj_offset.py`（**7 条，2026-09-28**：`traj_offset` == 估算 `anchor.offset`（同一量）/ 与 `player_elo` offset 差 ≥ 10，见铁律 56）
    · `test_owner_consistency.py`（**4 条，2026-09-28 崩溃修复回归**：`my_game_ids` 每局含主人（无孤儿）/ 全库含主人的局都进 `my_game_ids` /
      `meta.summoner` 与主人 Riot ID 一致 / state.anchor_puuid == 数据主人，见铁律 57）
    · `test_exe_live.py`（实机起窗口点按钮）· `test_sgp_crawl.py`（SGP/LCU 通道回归）
    · `test_state_backup.py`（**新增 2026-09-22**：锚点状态备份 4 场景回归，其中一个模拟
      `shutil.copyfile` 只建 0 字节空文件，确认 `_backup_state()` 能兜住）
    · `test_dashboard_layout.py`（**新增 2026-09-22**：真 Chromium 实测排版，7 视口 × 8 条 = 56 条。
    · `test_owner_switch.py`（换数据主人端到端，26 条，临时副本、需客户端在线）
    · `test_packaged_owner.py`（**打包产物自检**：确认 exe 里真的是新代码，用 `sys.modules["__mp_main__"]`）
      ⚠️ **jsdom 没有排版引擎**，「显示不全 / 横向滚动条 / 列被截断」只有它能测 ——
      **328 条断言全绿时排版仍可能是坏的**，别被假绿骗了。
      2026-09-27 实测 **53 / 3**（1281/1290/1366 视口英雄列溢出 4~10px）——
      **换回改动前的备份 HTML 结果一模一样 → 既存问题、非当轮引入**，见 `_stale/probe_kd.py` 探针）
      **2026-09-28 第九轮已修 → 56 / 0**：根因是**明细表列宽 CSS 双层覆盖**
      （`table.games th:nth-child(n)`(0,2,2) 压 `.det-team th:nth-child(n)`(0,2,1)；
      `.games th/td`(0,1,1) 与 `.det-team th/td`(0,1,1) 同特异性但源序更后 ⇒ padding/font-size/color 全被劫持）。
      修法 = 列宽唯一落点搬到 CSS + 选择器收紧到 `.det-team table thead th:nth-child(n)` + 英雄列 70→**99px**
      + 横向 padding 8→**7px**；现定 105/99/72/54/54/86/60 = 530）
  - `06_桌面版\` 桌面外壳 + 一键打包；产物 `HaidouMMR\HaidouMMR.exe`（**onedir**，启动约 1.1 秒），**双击即用**
    · `app.py` 窗口外壳（`--check` 自检、`--selftest-run <脚本> [参数]` 冻结环境实跑脚本，均不加载 GUI）
    · `build.bat`/`build.py` 打包（**默认 onedir**，`--onefile` 打单文件便携版；
      含 `--add-data 01_脚本;_scripts`；含 `--windowed`）
      ⚠️ **哪些改动要重新打包，分清楚**：
        - 改 `app.py` → **必须重新打包**（它被冻进 exe 里了）。
          2026-09-21 第四轮改了 `app.py`（`Api.stop()` / `stopping` / `stopped` / `RC_STOPPED`）
          → 已重跑 `06_桌面版\build.py`，产物 265 个文件 / 30.6 MB，`--check` 通过
          **2026-09-27 第七轮 / 第八轮 / 2026-09-28 第九轮都没改 `app.py` → 不需要重新打包**
          （第七轮改 5 个脚本 + 看板 HTML；第八轮改 `_paths.py` / `_dash_rebuild.py` / `_player_elo_build.py` + 看板 HTML + 新增锚点库；
          第九轮改 `_composite.py`（新）/ `_dash_rebuild.py` / `_verify_player.js` + 看板 HTML）
        - 改 `01_脚本\*.py` → onedir 下 `_internal\_scripts\` 是**磁盘上的散文件**，
          运行时直接读。所以**改完立刻生效、不用打包** —— 但要记得同步：
          `cp 01_脚本/X.py HaidouMMR/_internal/_scripts/X.py`，
          然后 `sha256sum` 两边比一下（2026-09-21 实测：只改 `_internal\_scripts\_hd_crawl.py`
          就跑 `HaidouMMR.exe --selftest-run _hd_crawl.py` 验证过，确实立即生效）
          ⚠️ **同一个文件被改两次时要再 `cp` 一次** —— 第七轮 `_player_elo_build.py`
          在 sha256 校验**之后**又加了 `player_elo_meta.json` 同步段，差点漏同步
        - 改 `03_看板\*.html` → **不用打包**，看板 HTML 在 exe 外部（`app.py: BOARD = ROOT/"03_看板"/BOARD_NAME`）
      —— 实测启动：onedir **1.1 s** vs onefile **6.4–8.3 s**（差 6–8 倍，全在自解压+杀软扫描上）
      —— `--windowed` 把 exe 从 Console 子系统（PE Subsystem=3，双击弹黑窗口）
      改成 GUI 子系统（=2）。代价是 `sys.stdout` 可能为 `None`，
      故 `app.py` 的输出全走 `_out_write()` 包装，`_entry()` 在无控制台时退到弹消息框
    · **窗口模式限定单实例**：命名互斥体 `CreateMutexW("HaidouMMR_SingleInstance_v1")`
      （**不是锁文件** —— 强杀时内核自动释放，不留僵尸锁）。
      第二个实例退出前 `FindWindowW`+`ShowWindow(SW_RESTORE)` 叫醒已有窗口，叫不到才弹框。
      原因：两实例各点「重新计算」会并发写估算 json / dashboard json / 看板 HTML（无锁覆写）。
      ⚠️ `--check` / `--selftest-run` **刻意不受锁限制**（卡住时要能排查），
      所以别在重算过程中跑写数据的 `--selftest-run`。
      ⚠️ 64 位下 `FindWindowW.restype` 必须声明 `c_void_p`，否则句柄截断。
      测试：`test_single_instance.py`（11 条，含跨进程抢锁）
    · **桌面版自己会抓数据**（2026-09-21）：主按钮「抓新局并重算」跑**六步**
      （顺序不能改）：`_hd_crawl.py --seed --players 0`（只抓你自己的新局，秒级）
      → `_hd_backfill.py`（定向补抓新对手）→ `_player_elo_build.py`
      → **`_stats_crawl.py`**（本人对局明细，生成看板要用的 `my_stats.json` / `海斗roster.json`）
      → 估算 → `_dash_rebuild.py`。另有「深度抓取」把第一步换成完整 BFS（`--players 300`）。
      ⚠️ **第 4 步不能省**：漏了它，第 6 步会因为新抓的局没有 stats 直接 `KeyError` 失败
      （2026-09-21 实测踩到）。`_dash_rebuild.py` 已加保险：缺 stats 只告警不崩
      📌 **抓取走 SGP 通道，不是 LCU 本地窗口**（2026-09-21 改，铁律 19①）：
      `_hd_crawl.py` **SGP 优先 + LCU 回退**（`--no-sgp` 强制回退）。SGP 一次 `count=100`
      拿 100 局完整对局（11.8MB / 2.5s，`count=200` 会 503），且 `games[].json` 本身就是
      完整详情（`participants` 是**扁平**的，没有 `stats` 嵌套），**一次请求顶「列表 + N 次详情」**。
      LCU 那条路一页只给 20 局、本地窗口约 21 局 —— 别再拿它当上限。
      ⚠️ **LCU 限流是瓶颈**：实测 300 人的战绩列表要 **532 秒**（~0.56 请求/秒，`--workers 16` 无效）——
      那是 LCU 的值；SGP 实测 30 人 5.1s（~5.9 请求/秒），但样本小，深跑仍按十几分钟估。
      `--players 0` 是合法的「只抓窗口不跑 BFS」写法
      ⚠️ **所有脚本取凭据一律走 `_lcu_creds2.find_lcu()`**。`_stats_crawl.py` 曾自带一份
      PowerShell/WMI 实现（注释说「沙箱唯一稳定路径」），实际反作弊下 WMI 的 `CommandLine`
      是空的 → 该脚本必定失败。另注意 `find_lcu()` 返回的 **`PORT` 是 int**，拼 URL 要 `str(PORT)`
    · **进度条 + 剩余时间**：任务跑在后台线程，界面 0.5 秒轮询 `api.progress()`。
      六步**全部**有真实标记（`已抓列表 k` / `[列表] k/N` / `[详情] k/N` / `[Elo] k/N` /
      `[Stats] k/N` / `[1/4]`…`[4/4]` / `[load]`→`聚合完成`）。
      优先级是「**有真实标记就只认标记**」；完全没标记的步骤才按估时线性推进且**封顶 90%**。
      剩余时间按实际速率外推再平滑。**完成信号永远是脚本退出码，估时不准不会误报完成**。
      每次跑完把各步实测耗时写进 `_desktop_state.json`，下次沿用（估时自校准）
      ⚠️ 别写成 `cur = max(标记, min(0.9, 已用/估时))` —— 估时偏小（补抓实测 9.9s~464s）
      会让「脚本说 5%」被演成「快 90% 了」，正好和封顶 0.9 的初衷相反
      ⚠️ **但「估时抢跑」也要拦住**：估时是上次实测值，步骤一变慢就会在第一个标记到达前
      冲到 0.9，标记一到（值可能才 0.5）进度条就**倒退**。2026-09-21 实测第 1 步改走 SGP
      后单次请求 2.5s（估时 3.8s），总进度 `0.0167 → 0.0093`。修法：`_JOB.step_has_marker`
      在 `_run_job` 里按 `bool(st.get("marker"))` 置位，**配了解析器的步骤标记没到就显示 0**，
      估时只留给没有解析器的步骤（铁律 20）
    · **锚点自动跟随**：估算脚本新增 `--anchor-game-id`，按 gameId 在新轨迹里定位锚点那一局，
      桌面版把锚点局记在 `02_数据\_desktop_state.json`，用户不用再填「截图后又打了」。
      ⚠️ **不能用「主人对局数增量」推** —— `--anchor-games-ago` 是相对 Elo 轨迹数的，
      而轨迹只含对手覆盖率 ≥ `--min-cov` 的局，两者增量不同，推出来会指错局、算错分
      ⚠️ 记录锚点必须对 `update` 也生效（曾写成 `kind in ("crawl","recalc")`，把默认按钮漏了）
    · 测试：全部在包内 **`07_测试\`**（原来只散在 `%TEMP%` 里，2026-09-21 收进来并改成按
      `__file__` 推路径）：
      `test_progress.py`（**213 条**，标记解析/百分比单调/估时不抢跑/步骤编排/估时校准/假脚本跑完整流程/协作式停止/**剩余时间不假报**/**谱分解细粒度进度**/**客户端预检查**/**存档秒切·软删**）
      · `test_snapshot.py`（随数据浮动的快照断言：KDA/转化显示值、estimate 落锚）
      · `test_job_e2e.py update`（真实脚本端到端，实测全绿 134.7s，会断言进度不回退）
      · `test_exe_live.py`（实机起窗口、点按钮、采进度）
      · `test_sgp_crawl.py`（抓取通道 SGP/LCU 回归，跑在临时副本上，不碰真实数据）
      · `_verify_toolbar.js`（39 条，走 `验证看板.bat`）
      · `_verify_player.js`（186 条：面板宽度 / 5 分钟无效局 / 每日范围 / 玩家弹层 / 视角切换 / **切视角后整页重算** / **自己输 ID** / 范围选择器 / 三项最高只上色 / 对局评分 / 前后端公式一致性 / **分区位置两模块**）
    · `theme.css` 看板视觉层**唯一来源** · `apply_theme.py` 注入/还原主题
      （`--restore` 只换 style 块，**不回滚数据**）
    · `apply_toolbar.py` 幂等注入操作栏（标记 `<!--HD-TOOLBAR-->`）· `make_icon.py` 生成图标（纯标准库）
    · 桌面版**不做第二套实现**：按钮回调走 `_run_script` → `runpy` 在当前进程内执行
      `01_脚本\` 里的**同一份**脚本，所以两条链路结果永远一致
    · 操作栏靠 `window.pywebview` 判断环境 → **浏览器打开看板时看不到**，故不影响 92+11+39+186 断言
- 主入口脚本：`<包根>\01_脚本\海斗隐藏分估算_v2.py`
- ⚠️ v1（峡谷段位代理版）与 `hd_data.contam_example.bak.json`（污染备份）已删除。
- 本技能与 `04_文档\SKILL.md` 是**互为镜像的两份**，改一份记得同步另一份。
  （⚠️ 实测：`~\.workbuddy\skills\海斗隐藏分估算\SKILL.md` 与 `~\.workbuddy-ai\skills\...`
  是**同一个文件**（junction 到 `<本机路径>/AI\skill\workbuddy\海斗隐藏分估算\`），
  但 `04_文档\SKILL.md` 是**包内另一份独立文件**，三者内容需一致 → 改完要 `cp` 过去并比 sha256。）

## 当前数据规模（2026-09-28 第十轮数据；2026-09-28 第九轮补「表现分映射层」行）

| 项 | 值 |
|---|---|
| 对局 | **37,687 场**（含本人 202 场；已滤掉 <300s 的无效局 —— 现盘上短局为 0） |
| 玩家 | **269,025 名**（`hd_data.json` 58.3 MB；`player_elo.json` 18.2 MB / 269,018 人） |
| BT 拟合 | `prior` 1500.2；本人对手 1,532 名中 1,036 名有独立 θ |
| 结果 | 隐藏分 **1708.0**（95% 半宽 **±22.0**，**铂金 III · 约前 31%**），`raw_elo` 1571.2 / `offset` +136.8，轨迹 164 局 |
| 锚点 | **隐藏分 1708** @ 09-28 01:24（`gameId 10000000006`，在本次轨迹的 **0** 局前）<br>**表现分 65.1**（`01_脚本\_composite.py` 的 `COMPOSITE_ANCHOR`，与隐藏分锚点**互相独立**） |
| 谱分解 | 全网络 269,025 节点 / 37,687 超边 → 分位 **99.85%**（rank 392）；deg≥5 子图 3,528 人 → 0.03% |
| 表现分映射层（2026-09-28 第十轮扩样本） | **158** 个真值点 **MAE 3.195** / 偏差 −0.953 / 最大 12.25 / **±5 内 80.4%**；主页近5场 **2.86**；评分变化 真值 **3.4** vs 本地 **2.99**；公式上限 **100.0**（能打到 100）。⚠️ 点数 +59 而 MAE 只涨 0.2 ⇒ 映射层没退化 |

## 何时用
用户想算/追踪英雄联盟海克斯大乱斗(ARAM Mayhem, queue 2400 / KIWI)的隐藏分。
前提：League 客户端已启动并登录（无需 sona 插件、无需 Riot API key）。

> ✅ **离线可跑**：只要 `hd_data.json` 在，估算脚本**不需要 LCU**（BT 拟合 + Elo + 谱分解全是本地计算）。只有 `_hd_crawl.py` / `_hd_backfill.py` / `_stats_crawl.py` / `_player_probe.py` 抓新数据时才需要客户端在线。
> **回归测试（推荐先做这个）**：`python 01_脚本\海斗隐藏分估算_v2.py --anchor 1541 --anchor-game-id 10000000005`，输出对同一数据**基本确定**（`random.seed(7)` + `Random(11)`；⚠️ 谱分解有随机初始化，重跑允许 **±0.5 分**抖动 —— 不是数据被改坏）。
> ⚠️ **锚点优先用 `--anchor-game-id`**（当前 `10000000005`），别用 `--anchor-games-ago` ——
> 后者是相对**Elo 轨迹**数的，而轨迹只含「对手覆盖率 ≥7/9」的局，**「对局数增量」≠「轨迹长度增量」**，
> 拿增量去推会**指到错的局、分数静默算错**。桌面版把锚点局记在 `02_数据\_desktop_state.json`，自动跟随。
> **看板断言**：双击 `01_脚本\验证看板.bat` → 须 `92` + `11` + `39` + `186` = **328 条全过**。
> **桌面版自检**：`HaidouMMR\HaidouMMR.exe --check`（4 行路径全「存在」）；
> `HaidouMMR\HaidouMMR.exe --selftest-run _paths.py`（`RC 0`，路径指向真实包根）——
> 这条和界面按钮走**同一个** `_run_script`，能跑通就说明界面按钮能用。
> ⚠️ `HaidouMMR.exe` **不能单独拷走**（依赖同目录 `_internal\`）。
> **单实例**：连点两次 `HaidouMMR.exe` 只会出现一个窗口（第二次把第一个叫到前台）。
> ⚠️ 比对 stdout 要**同一种捕获方式**：PowerShell `| Out-File` 捕获中文会乱码，与 Python 直接捕获相比会得到假差异。

## 一句话原理
- 公开的做法是用**全服**几十万场「同局共现」建超图做谱分解（外部无法原样复刻，除非有全服数据）
- **本技能 v2 路线**：直接抓每个人的海斗战绩 —— 用「不含本人」的对局拟合对手实力 θ(团队 Bradley-Terry, Elo 刻度) → 本人 Elo 轨迹 → 用一张截图上的分数做单点平移校准
- ⚠️ 海斗与峡谷排位是**两套独立匹配池**，绝不能用排位段位代理海斗实力（v1 教训）

## 执行步骤（v2）

1. **抓数据**（首次 ~9 分钟，可后台跑）：
   ```bash
   python _hd_crawl.py --players 300 --workers 16
   ```
   产出 `hd_data.json`（滚雪球 BFS：客户端窗口只有几局时，从对手战绩反查本人历史局）。
   主人打了几局后想增量更新：`--seed` 续传 或 直接重跑（会自动去重）。
   ⚠️ 走的是 **SGP 通道**（sona 那条），不是 LCU 本地窗口 —— 一次 `count=100` 拿 100 局
   且带完整详情；LCU 只作为回退（`--no-sgp` 强制走它，一页 20 局 / 本地窗口约 21 局）。
   ⚠️ `<5 分钟`（300 秒）的对局一律无效（挂机/重开），`_paths.MIN_GAME_SECONDS` 说了算；
   `--seed` 载入已有数据时会**顺手剪掉**旧的短局（自愈）。

2. **估算**（离线，不需要客户端）：
   ```bash
   python 海斗隐藏分估算_v2.py --anchor <截图分数> --anchor-game-id <锚点那一局的 gameId>
   ```
   脚本路径：`<包根>\01_脚本\海斗隐藏分估算_v2.py`
   （⚠️ 输出写到 `02_数据\海斗隐藏分_v2_本次估算.json`，由 `_paths.py` 统一解析；在哪个目录执行都行。
   当前锚点：`--anchor 1541 --anchor-game-id 10000000005`）

3. **锚点对齐怎么定**：
   - **首选 `--anchor-game-id <gameId>`** —— 脚本自己在新轨迹里定位同一局，**重抓多少次都不跑偏**。
     当前值存在 `02_数据\_desktop_state.json` 的 `anchor_game_id`。
   - `--anchor-games-ago <N>` 只是**回退**（找不到那个 gameId 时才用）：要从
     `海斗隐藏分_v2_本次估算.json` 的 `games` 数组**现数**"锚点局之后还有几局"。
     ⚠️ **必须数，不要凭感觉**，更不能拿"本人对局数增量"推 —— 锚点对齐错了整个分数都错。

4. **判读**：
   - |Δ| < 95% 半宽（8 局样本约 ±62）→ 说"方向 xx、统计上不显著"，别说"涨了/跌了多少"当实锤
   - 主角看**截图后战绩**（几胜几负）＋**本人 raw Elo vs 对手均值**（自己在匹配圈的位置）
   - 谱分解 deg≥5 高频子图分位与 Elo 模型应互相印证（本数据规模两者均指向"匹配圈中位"）

## 关键坑（换机器也对）

| 坑 | 解决 |
|---|---|
| WeGame 渠道版 lockfile 停在旧日期不刷新 | 读 **LeagueClientUx.exe** 进程命令行取 `--app-port` / `--remoting-auth-token`（`_lcu_creds2.py` 优先走 `NtQueryInformationProcess(60)`，只要 `PROCESS_QUERY_LIMITED_INFORMATION`，**沙箱内也能跑**；缓冲区必须够大 —— 实测需 ~2900 字节，给 16 字节会被 `0xC0000004` 拒绝并掉进需要 `VM_READ` 的 PEB 兜底） |
| match-history 列表的 participantIdentities 只有自己 | 用单局详情 `/lol-match-history/v1/games/{gameId}` 拿全部 10 人 |
| **查别人的战绩列表** | `/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex=20`（sona 同款；SUMMARY 端点 404 不要用） |
| 查别人段位 | `/lol-ranked/v1/ranked-stats/{puuid}`（v5 只查自己）——v2 已不需要，仅备查 |
| 海克斯大乱斗标识 | queueId **2400**，gameMode **KIWI**，mapId 12 |
| 客户端战绩窗口只有 ~6-41 局 | 滚雪球 BFS 从对手战绩反查本人历史（76 局/300 扩展玩家） |
| 英雄显示数字 ID | `/lol-game-data/assets/v1/champion-summary.json`（champions.json 400） |
| 主人早期局对手已不活跃 → θ 缺失 | Elo 轨迹从"对手覆盖率 ≥7/9"的连续段起算，前面自动跳过 |
| **`_desktop_state.bak.json` 变 0 字节**（2026-09-22 实测两次） | 这个文件存 `anchor_game_id`，是**唯一没法自动获取**的信息；`.bak` 是唯一兜底。精确根因：`shutil.copyfile` 在受限环境下**先把目标文件建出来、内容没写进去，然后抛 `OSError`** → `except OSError: pass` 一吞 → **主文件根本没被写（mtime 不变）**，**备份留下 0 字节空壳**。阴险处：原有断言「真实 state 没被改动」照样绿（主文件确实没变），问题溜了两轮。已加固 `app.py`：新增 `_backup_state(raw)`，**彻底不用 copyfile**，改用与主文件同一套「写 `.bak.tmp` → `os.replace`」原子写法 + **验尺寸**，不对就删掉空壳且**不碰上一代好备份**。⚠️ **改 `app.py` 必须重新打包**。守卫文件对要**两个都快照**（`test_progress.py` ⑪ 已补 `.bak`）；回归见 `07_测试\test_state_backup.py` |
| **看板 HTML 比 `dashboard_data.json` 新** | **不是问题**：`apply_theme.py` / `apply_toolbar.py` 只换 `<style>` 块和注入操作栏，**不碰数据块**。要确认就抽 HTML 的 `<script id="dash-data">` 与 json 逐字段比（2026-09-22 实测 0 差异） |
| **文档里的「现状数字」悄悄过期** | 2026-09-22 发现 `00_使用说明.md` 头部还写着 21,053 场 / 115 局 / 1928，而实际是 28,319 场 / 156 局 / **2060** —— 比没有说明更坏（读者拿错基线）。**2026-09-27 又栽一次**：重锚 1541 后头部仍写 — / 1928 @ 09-12、文件清单仍写 45.9 MB / 659 KB / 断言 281 条。**跑完管线后回来核对**：`00_使用说明.md` 头部与第 2/3/4 节、`04_文档\海斗隐藏分_复刻指南.md` 头部与第 7 节、本文件「当前数据规模」与锚点行、`验证看板.bat` 期望值。（第九轮新增要一起核的：**表现分权重 20/13/6/8/18/18/13/4** / 统治力加成 6 项 / `COMPOSITE_CLAMP=(38,100)` / `_composite.py` 唯一真身 / 断言 **319 条** / MAE **2.991** / 满分画像 93·92·90% / 看板 **900,151 字节**；第八轮的：锚点库 74 条 / 分位曲线 μ·σ / 同局位置 58·70） |
| **后台线程收尾顺序：先宣布结束、后落盘**（2026-09-22 实测） | `_run_job` 原来先 `_job_set(running=False, finished=True)`、**之后**才 `_save_state(step_secs=...)`。于是任何「等 `running` 变 False 就认为任务结束」的调用方都会撞上还在跑的 `_save_state`：`test_progress.py` 结尾按 `running` 判定结束就恢复真实 `_STATE_FILE`，而 daemon 线程里的 `_save_state` 才刚 `read_bytes` 完，**本该落在临时目录的备份写穿到真实 `02_数据\`**；`sys.exit()` 强杀 daemon 又让它停在「已写 `.bak.tmp`、未 `os.replace`」的中间态。**更坏的可能**：用临时目录的假状态覆盖真实备份（备份是锚点唯一兜底）。修法两条都要：① `app.py` 把 `finished` 挪到 `_save_state`/`_remember_anchor` **之后**（`finished` 必须真的是「连状态都存好」）；② 测试**别依赖实现顺序** —— ⑫ 段把 `_save_state` 包一层、整个调用期间把 `_STATE_FILE` 钉死在临时目录，并断言「**真实数据目录跑完不许比跑前多出任何文件（含 `.tmp`）**」（只看 state 文件看不出来，必须比目录清单） |
| **只断言主文件 = 假绿** | 守卫一个「文件对」时必须**两个都快照**。`test_progress.py` ⑪ 原来只比 `_desktop_state.json`，而 `.bak` 被搞成 0 字节时主文件确实没变 → 断言照样绿，问题溜了两轮。现在主文件与 `.bak` 都做前后快照，并钉死「`.bak` 不许是 0 字节」 |
 | **抓数据时闪黑命令行窗口**（2026-09-22 修） | `_lcu_creds2.find_lcu()` 要跑 `tasklist`（**控制台程序**），而 `HaidouMMR.exe` 是 `--windowed` 的 **GUI 子系统**（无控制台）→ Windows 给它**新分配一个控制台窗口**。修法：`_lcu_creds2.hidden_kwargs()`（`CREATE_NO_WINDOW` + `STARTF_USESHOWWINDOW`/`SW_HIDE`），**任何从本项目起的控制台子进程都要带**。自检：`hex(k['creationflags'])=='0x8000000'`、`wShowWindow==0` |
| **`_hd_meta()` 一点都不便宜**（2026-09-22 修正认知） | 它的注释原本写「只要 meta，不碰 46 MB 的 games」——**错的**：`json.loads` 必须先把整个文件解析完才能取 `["meta"]`，34,602 局全解析 ≈ **1.2~2 秒**。表现就是主人问的「管理存档为什么读取存档还要 2 秒」（存档本身只有几十 KB）。修法：① `_hd_meta()` 按 **mtime+size 缓存**（实测 1.24s → 0.0004s）；② 只想拿「当前主人是谁」时用 `_current_owner_puuid()`，改读 285 KB 的 `海斗dashboard_data.json`（有 `me_puuid`，每次管线跑完都会重算）→ 列表 **2s → 0.010s**。教训：**"只取一个字段"不等于只读一点数据** |
| **存档切数据主人会白白多占 17 MB/人**（2026-09-22 规避） | `player_elo.json` 一份 16.8 MB（24.8 万玩家），但两个主人的这份表**只差一个 offset 常量**（`mmr = 原始 Elo + offset`，原始 Elo 由全数据集算出、与主人无关；实测大号/小号只差 1.5 分）。所以存档只记 `elo_offset`，全量表只留**一份共享底表** `_owners\_player_elo_base.json`，切换时 `_materialize_elo()` 现场物化（~2-4s）。**物化出的格式与原格式完全一致 → 下游脚本零改动**。每个主人的档因此只有几百 KB |
| **测试里的假流水线会触发自动建档、写真实目录**（2026-09-22 踩到） | ⑫ 段跑假脚本时新增的 `_archive_owner()` 会真写真拷 → 真实 `02_数据\` 多出 `_owners\`，「真实数据目录不许多出新文件」断言立刻红。修法：同 `_STATE_FILE` 的做法，把 `OWNER_SAVE_DIR` / `ELO_BASE_JSON` 也钉到临时目录（`_hd_meta` 顺带打桩，免得为读一个 meta 去解析 46 MB 的 hd_data） |
| **进度条「剩余约 20 秒」实际还要 90 秒**（2026-09-22 修） | 两条叠加：① ETA 用**全程平均速率**外推（`elapsed/frac*(1-frac)`），前面几步比估时快就把速率拉高，最后一步再慢也显示「只剩几十秒」；② `_mark_estimate` 把估算脚本 `[1/4]~[4/4]` 当**四段等分**，实测（整步 95.6s）是 BT 4.5s / Elo 0.01s / **谱分解 89.2s（93%）**，「刚进谱分解」就被报成 50%。修法：ETA 改成**按步外推**（计划剩余 − 已落后量，平台期 1:1 递减；超时后按超出量一半爬升，不假报 0）＋ 阶段权重查实测表 `_EST_STAGE_W`。**进度条逻辑没动**（有标记的步骤仍只认标记）。重测工具 `07_测试\_time_est_stages.py`；改 `app.py` 后**必须重新打包** |
| **切了 LCU 账号再重算 → 隐藏分其实是上一个主人的截图分**（2026-09-22 修，同一个坑踩过两次） | 抓取步不带 `--me` 时以**当前登录账号**为种子（铁律 3），于是切号后 `meta.puuid` 被悄悄换掉；而锚点在任务启动时就定好了，仍属于**旧主人**。结果 `1928`（大号截图）找不到对应局 → 退回「0 局前」→ 钉在新轨迹**末局** → `estimate` **恒等于锚点值**（`变化 +0.0` 就是特征），`_remember_anchor()` 还会把这个假锚点归档到新主人名下**污染落盘**。修法两条：① `_realign_estimate_argv()` —— 估算步**开跑前**再调一次 `_switch_owner_state(当前 meta.puuid)`（旧锚点安全归档、新主人没锚点就清空）；② `_stages_for` 改成**无锚点一律 `--auto-anchor`**（原来只在显式换主人才给，否则退化成未校准 Elo）。体检看三个 puuid 是否一致：`hd_data.meta.puuid` / 估算结果 `owner_puuid` / `state.anchor_puuid`。**网络是并集，旧主人的局不会丢**（用 `_recover_meta.py` 可把 meta 改回大号） |
| **「没有全部成功」但看不出哪一步失败**（2026-09-22 修） | ① **先看 `02_数据\_last_job.log`** —— 里面有 `失败步骤: <名>(rc=N)` + 每个步骤的完整输出（新增 `_write_job_log()` 落的盘）。② 别只靠产物时间线猜：**非致命步骤失败（`fatal: False`）既不中止流水线也不写产物**，`_desktop_state.json` 的 `step_secs` 又只记 rc==0 的耗时。③ 原日志只活在内存 `_JOB["log"]` 且只留 120 行，早期步骤（往往正是失败那步）的输出会被后面的步骤挤掉 |
| **`_hd_backfill.py` 每次必失败**（2026-09-22 修） | 症状：桌面版每次都报「没有全部成功」，第 2 步输出里只有 `TypeError: 'set' object is not subscriptable`。根因：`cands` 是 `set` 却写 `cands[_i:_i+_CHUNK]` 切片 —— **2026-09-21 加「协作式停止 + 分批提交」时引入**，且**只在 `cands` 非空时触发**（空则提前 `exit(0)` 报"无需补抓"），所以藏了很久。修法：`cands = sorted(pu for pu in cands if not appears_in_network(pu))`。⚠️ **连带伤害**：这步失败 → 新局对手 mmr 补不上 → `_verify_dash.js` 的「mmr 覆盖率达标（≥5 人有分）」「mmr 不千篇一律」转红；看到这两条红先查第 2 步 |
| **玩家重名分不清谁是谁**（2026-09-22 修） | `海斗roster.json` 的 `n` 只有裸名字（stats 抓取时丢的），而 `hd_data.json` 里每个玩家的 `name` 本身就是完整 Riot ID（实测 283220/283220 带 `#`）。修法：`_dash_rebuild.py` 建 `PU_FULL` 映射，发 `roster` 时用 `full_name(pu, p["n"])` 回填；`me_name` 也不再 `.split("#")[0]`。**只改 `n` 一个字段**（明细表/视角下拉/弹层全读它，前端零改动），且**历史数据一样能补、不必重抓 stats**。实测带后缀比例 1590/1590 = 100% |
| **断言的「数据假设」会误红** | `_verify_player.js` 的「三项最高不会人人都有」原写 `size >= 3`，把「三种最高必然落在 3 个不同玩家身上」当事实 —— 实测一人可同时拿 KDA 最高 + 伤害最高，最高只落 2 人 → 误红（**功能正确**，逐项由 expKda/expDmg/expTak 三条保证）。改为守**上界** `1 <= size <= 3`（只有 3 个项目；「全 0 时 10 人全标最高」的旧 bug 会得 10）。**教训：断言要守不变量，别守当前数据的样子** |
| **「显示不全 / 横向滚动条 / 列被截断」但 319 条断言全绿**（2026-09-22） | **jsdom 没有排版引擎**（`scrollWidth === clientWidth`），这类问题一条都测不出来 → **必须用真 Chromium 量**：`python 07_测试\test_dashboard_layout.py`（7 视口 × 8 条 = 56 条）。实测踩到的三个坑：① **`white-space` 可继承** —— `.games td{nowrap}` 命中明细行 `colspan` 单元格 → 一路传进明细区 → `.det-foot` 长说明被压成单行 1558px → 撑破 `.det-wrap` → 外层 `overflow:auto` 出滚动条（修：`tr.det-row>td{white-space:normal}`）；② **grid blowout** —— `1fr` 的隐含最小值是 `auto`，实测两列算成 197/1569（修：`minmax(0,1fr)`）；③ **行内元素的 `max-width`/`overflow`/`text-overflow` 全部不生效** —— `.nm` 是 `<span>`，省略号写法等于没写（修：改折行）。另：`table-layout:fixed` + 各列宽度**写在哪很讲究** —— 光写 `.det-team th:nth-child(n)` 不吃（明细表嵌在 `table.games` 里，`table.games th:nth-child(n)` 特异性更高），要写到 `.det-team table thead th:nth-child(n)`；2026-09-28 已把行内那份宽度删掉、统一到 CSS。⚠️ 列宽总和必须 ≤ 最窄双列（1281 视口 ≈550px），否则表会被撑宽。**2026-09-28 已修**：1281/1290/1366 视口英雄列溢出 4~10px（6 字名需 82px、最长 7 字名「弗雷尔卓德之心」需 77px）；根因是列宽被 `table.games th:nth-child(n)`（0,2,2）与 `.games th/td`（0,1,1、同特异性靠源序）**双层覆盖**，且英雄列没给 7 字名留够。修法 = 列宽唯一落点搬到 CSS + 选择器收紧到 `.det-team table thead th:nth-child(n)`（0,2,3）+ 英雄列 70→99px（见铁律 53） |
| **`apply_theme.py` 注入后把返工掉的东西打回来** | `theme.css` 是「样式的唯一来源」，但**只在主动跑 `apply_theme.py` 时才单向写进 HTML** → 随时可能落后好几轮（2026-09-22 实测落后两轮：里面还是 `min-width:680px` 和已删的 `.tt`/`.top-tag`）。**注入前必须先 diff**：抽 HTML 的 `<style>` 与 `theme.css` 比，差异应为 **0 行**；非 0 就先「把 HTML 的 `<style>` 当基线写回 `theme.css`」再打补丁。判据：`apply_theme.py` 打印的 `原样式` 与 `新样式` 字符数应相等 |
| **读 LCU 凭据不必关沙箱** | `_lcu_creds2.find_lcu()` 走 `NtQueryInformationProcess(60)`，只要 `PROCESS_QUERY_LIMITED_INFORMATION`，**沙箱内也能跑**。⚠️ 别再退回「必须非沙箱」的老说法（2026-09-22 已把 `复刻指南` / `估算_说明` 里的旧结论全部标废） |
| **换数据主人时重新校准 offset**（2026-09-22） | 新主人没有截图锚点，就拿 TA 的对手均值去反算 offset —— 结果必然是「新主人的对手均值恰好 = 锚点分」（循环论证），整张玩家表零点漂移、跟历史数据和参照曲线都对不上。正确做法：**有锚点按锚点，没锚点复用上一版 offset**（`_player_elo_build._prev_offset()`），新主人的分由估算脚本 `--auto-anchor` 用同一个 offset 换算 |
| **估算轨迹 offset 与 player_elo offset 不是一回事** | 两个估计器（K=22 轨迹 vs 全量 K=32/16），实测差 **46 分**（479.9 / 433.5；2026-09-28 第十轮 136.8 / 210.5，差 73.7）。`--auto-anchor` 必须优先复用「截图锚定留下的 offset」（`traj_offset` → 上次估算的 `anchor.offset`），只能用 `player_elo_meta.offset` 兜底并打警告。⚠️ **`traj_offset` 与 `anchor.offset` 是同一个量**（app.py 抄过去的），重锚后必须刷新 —— 第十轮踩到 state 里还是第九轮的 −17.8 而当前已是 136.8，差 154.6 分；已修正并在 state 补 `traj_offset_note`；守它的断言 = `07_测试\test_traj_offset.py`（**7 条**） |
| **往 HTML 注入时用了「第一个闭合 body 标签」**（2026-09-22） | `apply_toolbar.py` 的注释里写了闭合 body 的字面量，而注入器和 `test_dashboard_layout.py` 都是 `replace(第一个)` → 工具栏/探针被插进**注释中间**，整段 JS 被打断：真浏览器 7 个视口全报 `Unexpected token '<'`，而 **jsdom 那四条照样全绿**（它们在工具栏已移除的副本上跑）。两个注入器都改成 `rfind`（最后一个），注释里也别出现这个字面量 |
| **验证打包产物里是不是新代码** | `import app` → ModuleNotFoundError；`sys.modules["__main__"]` 也不是 app（`runpy.run_path` 默认 `run_name="__main__"`，会把 `__main__` 换成正在跑的脚本）。真正拿得到的是 **`sys.modules["__mp_main__"]`**。封装好的脚本：`07_测试\test_packaged_owner.py` |
| **`02_数据` 里堆历史备份** | 2026-09-22 已把 81.7 MB 的两坨 `hd_data.backup_*.json` 移入 `_stale\数据备份_20260922\`（未硬删）。确认过**没有任何脚本 glob 扫 `02_数据`**（全走 `_paths.py` 显式常量），所以移出安全 |
| **看板上的「自动配色」没有说明**（2026-09-22 用户问「这两个颜色标注是干嘛的」） | 逐局战绩表的 K/D/A 曾有一套自动配色（`0 死 = 金 / (K+A) ≥ 2×D = 绿 / 否则红`），是删括号值时从括号值平移过来的 —— 用户看来就是**无说明的随机颜色**，且金色与「修为/主序」撞车。**已拆**：明细 `td.d-kda span` / 逐局 `td.kd` / 每日 `.day-wl` **三处一起**改普通色，唯一例外 = 明细里 KDA 最高的 `.hi-kda`（紫，底部有图例）。守住它的断言 = `_verify_player.js` ⑧ 段 3 条 + 真浏览器 2 条 |
| **`player_elo_meta.json` 停在旧锚点 → 兜底路径静默差 387 分**（2026-09-27 发现） | 重锚 1541 后 `player_elo.json._meta` 已是新值，但 `player_elo_meta.json` 还写着 `anchor 1928 / offset 430.6` —— 因为它**只有需要客户端在线的 `_stats_crawl.py` 会写**，离线重跑第 3 步不会更新它。而 `海斗隐藏分估算_v2.py` 的**兜底路径**（既无 `--anchor`、又无上一版 offset 时）会读它的 `offset` → **没有任何报错地算错 387 分**。修法：`_player_elo_build.py` 写盘后同步一份 `_meta`。自查：两份 `_meta` 必须 `==`。**教训：同一个值有两份落点时，只有某一条路径会写的那个落点迟早过期** |
| **统计口径必须逐个反解，别凑**（2026-09-27） | 拿外部界面（近 30 局）做 A/B 反解出三处：① 窗口 KDA 是 **`Σ(K+A)/ΣD`**（旧逐局平均得 4.53，参照 3.55）；② 伤害转化是 **均值伤占 ÷ 均值经占**（用**未取整**均值，旧逐局平均得 0.96，参照 0.97）；③ 窗口综合分是 **逐局过曲线再平均**（旧「窗口均值过曲线」会导致「综合 62.3 vs 逐局平均 57.7」自相矛盾）。⚠️ **别用硬拟合去凑**：会解出「伤害转化占 92.6%、承伤 0.1%」的过拟合权重；锚点只定**位置**（单点平移），权重与曲线保持可解释的启发式标定 |
| **拿 `player_elo.json` 的 mmr 分布算「本区前 X%」**（2026-09-27） | 26.4 万人的 mmr **98% 挤在 ±25 分内**，算出来永远是「前 50%」。根因：offset 的定义就是「让本人对手的最终分均值 = 本人锚点」，整个网络围绕主人滚雪球采样，**零点锚在主人身上、分布被人为压扁**。⇒ 所以「本区前 X%」只能看**本地自算的同场圈分位**，不要拿 mmr 分布去算。反之**同局位置**模块只在同局 10 人里比，mmr 的**相对**大小可信 |
| **截图里的玩家名字 OCR 极不可靠 → 用 K/D/A 匹配**（2026-09-27） | 逐图转录时名字错得离谱（`玩家甲`→`玩家乙`、`玩家J#00011`→`#29019`、`玩家I#00010`→`#49091`），首版按名字只命中 **7/26**。而截图里的 **K/D/A 与本地 `hd_data.json` 逐位一致**（如 gameId `10000000005` 的 16/13/17、9/9/25…）⇒ 改用 `sorted((k,d,a))` 的**整队集合**匹配（退化单人），**零错误**。踩坑：`byid.get(gid)` 全报「局不存在」—— gameId 是 `int`、字典键是 `str`，须 `byid.get(int(gid))` |

LCU 调用模板：
```python
auth = base64.b64encode(f"riot:{token}".encode()).decode()
headers = {"Authorization": f"Basic {auth}"}
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
urllib.request.urlopen(Request(f"https://127.0.0.1:{port}{path}", headers=headers), context=ctx)
```

## 模型设计要点（别改坏）
- **对手 θ 拟合必须排除本人的对局**（`exclude={me}`）→ 对手实力独立于本人表现，避免循环论证
- 团队 Elo 拟合：多轮遍历(40)，K 从 26 衰减到 3，每轮向基准收缩(BT_SHRINK≈0.012) + 重新居中 → 稳定不发散
- 本人 Elo：K=22，起点 = 全体 θ 中位数；覆盖率 <7/9 的局不进轨迹
- 锚点做**整体平移**（offset = 锚点分 − 锚点局 Elo），斜率假设 1:1（小窗口近似）
- 谱分解(官方方法复刻)：超图 Θ = D^-1/2·H·De^-1·Hᵀ·D^-1/2，幂迭代求第二特征向量，
  每步按标准内积**正交化掉 φ₁=√deg**（不是去均值！）→ 局部稀疏网络会退化，仅 deg≥5 子图可信

## 局限（对用户必须讲清）
- 不是官方精确值；官方需全服 28.5M 场超图，外部拿不到
- ±62（8 局）级别的不确定度是 Elo 游走固有的，方向可信、幅度别当真；30+ 局收窄到 ±35
- 匹配圈的**绝对层级**靠单个锚点固定；斜率 1:1 是近似，长窗口应收集第二锚点做线性校准
  ⚠️ **2026-09-27 实测：主人能拿到的锚点全是"固定开黑队友"（196 局 100% 同队、0 敌对）** ——
  同队 ⇒ 胜负完全同步 ⇒ **Elo 数学上无法区分队内实力**（本地 raw 跨度仅 116.6，外部刻度跨 1698）。
  ⇒ 这批锚点**只能当坐标点、不能用来重标定 mmr**。要真正验"量级"，得拿到**敌对**玩家的分数。
- 可靠的是**方向与相对幅度**；v1(峡谷段位版 1907) 已被 v2(纯海斗版 1957) 取代，勿混用

## 网页看板（展示层，2026-09-28 v6 第九轮重标定 — 绝对锚定综合分 + 逐局平均 + 统治力加成 + 在线 Elo 对手分）
- 流程（v6 关键差异）：
  1. `_hd_crawl.py` 滚雪球 BFS → hd_data.json（**对手覆盖不足时 `--seed --players 650` 增量扩爬**，BFS 按与主人共现度优先，会把只在主人局里出现过的对手补齐各自的 ~20 局历史）
  2. `海斗隐藏分估算_v2.py --anchor 1541 --anchor-game-id 10000000005` → 海斗隐藏分_v2_本次估算.json
  3. `_player_elo_build.py`：全数据集（含主人的局——对手评分与主人无关，无需排除；"避免循环论证"只约束主人自己的评分，由 v2 负责）按**时间序在线 Elo**（前 10 局 K=32、之后 K=16）→ 校准 = **公平匹配原理**：主人全部局的对手最终分均值 ≈ 主人 MMR → offset 平移到 **1541** 锚点 → player_elo.json（26.4 万人全量）。⚠️ 写盘后**同步 `player_elo_meta.json`**（铁律 45）
  4. `_stats_crawl.py` 拉主人全部局 LCU 详情 → my_stats.json（含 heal_share）+ 海斗roster.json（每行 10 人全量 stats）
  5. `_dash_rebuild.py` 聚合 → dashboard_data.json → 注入 HTML。**roster mmr 在此步从 PE 直查并过滤 n_games≥5**（<5 局不给点估计，显示「样本不足」）；本人行 mmr 取 `v2["estimate"]` = **1541**
  6. `node _verify_dash.js` + `node _verify_toolbar.js` + `node _verify_player.js`（**326 条断言：101+39+186**）
- ⚠️ **v4 的两个已修复 bug（勿回退）**：
  - **综合分恒 50**：滑窗 z-score 用窗口均值当基准 → 窗口和自己比 z≡0 → 恒 50。v5 起改为**绝对锚定参照曲线**（每指标固定参照点分段线性，如 5 人均分=占比20%、KDA 2.0 及格、参团 63% 大乱斗平均）加权求和，再按锚点做**单点平移**（只挪位置不压动态）。**v6 锚点 = 65.1**
  - **mmr 千篇一律**：独立 BT 拟合（K 衰减+收缩）在稀疏网络坍缩（467/560 人同一分）+ my_theta 跨坐标系错位。v5 改在线 Elo + 对手均值校准
- **v6 综合分公式（2026-09-28 第九轮重标定；⚠️ 唯一真身 = `01_脚本\_composite.py`）**：
  权重 **伤占20 / 承伤13 / 治疗6 / 经济8 / 转化18 / 参团18 / KDA13 / 胜率4**（和 = 100），曲线参照点存 `COMPOSITE_CURVES`（**8 条末控制点 y 全部 = 100**，保证能打到 100）；
  **窗口综合分 = 逐局过曲线 → 加权 → 对逐局分求平均 → + 统治力加成 → + `COMP_OFFSET` → `COMPOSITE_CLAMP = (38.0, 100.0)`**。
  `COMPOSITE_ANCHOR = 64.9`，`COMP_OFFSET = 64.9 − (window_mean_raw(近30局) 59.957 + 加成均值 0.733) = **+4.210**`。
  ⇒ `mean(近30 逐局 comp) == 64.9`（**精确等于锚点**，`test_anchor_v6.py` 钉住）；近10/近20 同 offset 真实波动（65.5 / 64.6）。
- **局内统治力加成（第九轮新增）**：`DOMINANCE_KEYS` **6 项**（**不含 `heal_share` / `win_rate`**）里，本局拿到**全场 10 人第 1** 的项数 × `DOMINANCE_PER = 1.0`。
  ⚠️ 这是全公式**唯一跨队口径**（其他指标分母都是「我方 5 人」）；**计入 offset 标定**，不是事后补丁。真值 ≥85 的 10 个点「6 项里最好排名」全部 = 1。
- **第九轮实测**：**MAE 5.222 → 2.991**（偏差 −0.696 / 最大 9.00 / **±5 内 80.8%**）；**评分变化真值 1.9 vs 本地 1.92**。
  自查命令：`python 01_脚本\_composite.py`（末行须「⇒ 公式上限 100.0，能打到 100」）。
  ⚠️ **任何复算同一批分数的代码必须与发货口径一致**（2026-09-28 自查修掉的真缺陷）：漏算统治力加成、漏过 `COMPOSITE_CLAMP`，就会得出「工具说 A、看板说 B」两套真值。**动 `_composite.py` 的常量后，回头核所有复算路径。**
- **满分画像（加成计入标定；主人要求「刚好到 100 分时 8 项各需达到什么程度」）**：拿到 **0** 项全场第一 → 8 项达区间 **93%**；**1** 项 → **92%**；**2** 项 → **90%**。
- **为什么胜率只有 4 分（第九轮数据实验，别再往上调）**：外部刻度 **胜方均值 70.48 vs 败方 64.59，差 +5.89**；w=14 时赢了多 **12.04 分（12%）**、w=4 时 3.44 分；**w=0 反而更差**（MAE 3.289、胜方偏差 −2.80）；**砍掉 `heal_share` MAE 反涨到 5.357 ⇒ 必须保留**。两个独立口径都指向 4~5。
- **v6 的三处口径修正（2026-09-27，A/B 反解出来的，别改回去）**：
  | 指标 | v5（旧） | v6（与聚合口径一致） |
  |---|---|---|
  | 窗口 KDA | 逐局 `(K+A)/D` 再平均（4.53） | **`Σ(K+A)/ΣD`**（**3.55**，参照值也是 3.55） |
  | 伤害转化 | 逐局 `dmg_share/gold_share` 再平均（0.96） | **均值伤占 ÷ 均值经占**，用**未取整**均值（**0.97**，参照值也是 0.97） |
  | 窗口综合分 | 先算窗口 8 项均值、再过曲线（→「综合 62.3 vs 逐局平均 57.7」自相矛盾） | **逐局过曲线、再对逐局分求平均** |
  ⚠️ **别用硬拟合去凑外部的分**：会解出「伤害转化占 92.6%、承伤 0.1%」这种过拟合权重。锚点只定**位置**。
- 校验铁律（v6）：`node _verify_dash.js` 断言含 **series comp 不同值≥10**（防恒50）、**同局 mmr 不同值≥4**（防千篇一律）、`comp30 == 锚点`（从 `DATA.anchors.composite_anchor.value` 动态读，**不许写死数字**）、mmr 覆盖≥5人/局
- 布局：Hero（含视角下拉 + 自己输 ID）→ KPI×6（第一张卡带「上次估算 <时间>」）→ 隐藏分轨迹 SVG（非主人视角改画对局评分）→ **分区位置（同场圈分位 / 同局位置，只显示本地自算口径）** → 近30局均值 **12 卡** + 评分趋势图（指标切换）→ 逐局表（每行▸明细展开当局 10 人：玩家/英雄/KDA/伤害/承伤/隐藏分/对局评分，本人金色高亮+mmr=1541，三项最高只给数字上色）→ 英雄榜+每日 → 方法论+诚实边界（v6 公式 + 双锚点说明）
- ⚠️ **展示口径铁律（主人 2026-09-04 深更纠错）**：① 逐局表「对手质量」列 = **当局其余 9 人隐藏分均值（mmr，有分者平均，title 标 n/9）**，绝不可显示 θ/Elo 内部刻度（1450~1560 的小数会让主人以为算错）；θ 只存在于估算内部，任何展示数字都必须是**当前锚点刻度**（现 1541）的隐藏分；② 轨迹图/明细悬停同理（chart 大图用 ts→gameId 反查 roster 算 oppMmr）
- ⚠️ **「本区排名百分比」不能用 `player_elo.json` 的 mmr 算**（26.4 万人的 mmr 98% 挤在 ±25 分内，永远得到「前 50%」）—— 要用**对局评分（comp）分布**。详见铁律 47
- ⚠️ **明细展开布局**：det-wrap 双列 grid 在窄容器会被 `.det-team{overflow:hidden}` 裁右列 → 每队表格包 `.tbl-scroll{overflow-x:auto}` + table min-width:560px + `@media(max-width:1280px)` 单列堆叠，任何宽度都能看全
- ⚠️ 「全部 N 局」按钮 data-n/text 由 JS init 动态写（`allGamesBtn.dataset.n = DATA.mine.length`），勿在 HTML 写死（76→79 教训）；iso 验证脚本要点击全部按钮时用 `#allGamesBtn` 而非固定 data-n
- 更新数据即：重跑 `_hd_crawl.py --seed`（如需）→ `_player_elo_build.py` → `_stats_crawl.py` → `_dash_rebuild.py` → 双验证脚本
- ⚠️ **新局对手低频坑（2026-09-05 新增，必查）**：BFS 扩展名额（--players 650）按"与主人共现次数"降序分配 → **主人最新几局的新对手共现=1，挤不进名额，历史没被爬 → mmr 覆盖率骤降**（实测深夜新局 0/9 有分）。修复：跑 `_hd_backfill.py`（定向补抓主人最近 20 局中"网络内出现<5 局"的对手每人 20 局历史，约 2 分钟）→ 重跑 _player_elo_build → _dash_rebuild。判断是否需要：看板最新局 mmr 有分 <5/9 时

## 看板交互层（2026-09-21 第三轮：视角切换 / 范围选择器 / 三项最高 / 对局评分）
- **看板视角 `#viewSel`**：Hero 区下拉切「谁算我」，存 `localStorage`（`hd_view_pu`/`hd_view_nm`）。
  判定统一走 `isView(p)`（优先比 `p.pu === VIEW.pu`，老快照无 `pu` 才退化比名字），
  **不要再写 `p.n === DATA.summoner.split("#")[0]`** —— 名字匹配换视角会把同名的别人一起标上。
  候选名单 `DATA.view_players` 由 `_dash_rebuild.py` 预计算下发（见下一节）。
- **指标 / 轨迹的范围选择器**：`#metCtrl`（`data-mn`，默认 30）+ `#trajCtrl`（`data-tn`，默认 0=全部），纯前端切片。
  前提是后端下发 `DATA.allseries`（全部 196 局逐局 9 项指标 + 单局 comp + `k`/`d`/`a`）。
  前端 `metMean()` / `compositeOf()` 必须逐字段对齐后端 `metrics()` / `_win_comp()`：
  占比 1 位小数、`dmg_conv`/`kda` 2 位，
  **KDA 走聚合口径 `Σ(K+A)/ΣD`**（不是逐局 KDA 再平均），
  **转化 = 均值伤占 ÷ 均值经占（用未取整均值）**（所以要 `k`/`d`/`a`）。
  ⚠️ **`offset` 必须用 `offset_exact`（未取整）**：拿显示用的 `round(offset,1)` 算会差最多 0.05，
  正好跨过 `.x5` 取整边界 → 同一窗口前后端差 **0.1 分**（实测 `composite10` 64.7 vs 64.8）。
  窗口分界 `trendSplit(N)`：N>12 取 N-10（窗口 30 局时与旧「前20/近10」行为完全一致），否则折半。
- **对局评分 `comp`（每人每局）**：`_dash_rebuild.py` 的 `_roster_player_comp()` 算好写进
  `roster[gid].teams[].players[].comp`。分母取**我方 5 人**之和（同 `_stats_crawl.py`），
  精度也照抄（占比 1 位、`dmg_conv` 用**原始分数相除**再四舍五入 2 位）——
  不这么抄「我」的对局评分会和趋势图的 comp 差 ±0.1。实测修完 **208/208 逐局完全相等**。
  **v6 与 v5 的唯一区别是「窗口综合分」的算法**（单局 comp 两者本来就一样）：
  v5 = `compositeRawOf(m)`（窗口均值过曲线）；v6 = `compositeOf(rows)`（**逐局过曲线再平均**）。
  展示：10 人明细新增列；弹层把「TA 隐藏分」列**换成**「对局评分」
  （跨局不变的隐藏分放逐局表里没信息量），并按时**同队/敌对**拆成「与你联手 / 与你交手」两张表 + 拆 KPI。

## 看板交互层（2026-09-21 第四轮：整页视角数据层 / 输 ID / 参考锚点 / 三项新指标 / 只上色）

### ① 视角数据层 `V`（**本轮核心**）
用户原话：「**并且你在我切换人之后数据看板要跟着变啊**」。
第三轮只改了 3 处标记（Hero / 明细「我」行 / 对手质量），KPI、轨迹、指标、逐局表、
英雄榜、每日战绩全都还是主人的数 —— 这是本轮返工的原因。

- 前端新增一层 `computeView(pu)` → 全局 `V`，**所有面板改读 `V` 而不是 `DATA`**：
  `V.rows`（逐局序列）/ `V.games` / `V.daily` / `V.champions` / `V.traj` / `V.trajLabel` /
  `V.trajAnchor` / `V.name` / `V.owner` / `V.mmrN`。
- 数据来源：`DATA.roster` 里该 puuid 的**原始数值**（k/d/a/dmg/taken/gold/heal + 该局 `dur` +
  队伍 `kills`）重建整套逐局序列，**分母取他自己那一队的 5 人**（和 `_roster_player_comp()` 同口径）。
  为此 `_dash_rebuild.py` 必须下发 `roster[].dmg/gold/heal` 和 `series[].dmg/gold/dur`。
- ⚠️ **唯一重建不出来的东西：别人的逐局隐藏分轨迹**。在线 Elo 序列是数据主人独有的，
  别人只有一个 BT 全网拟合快照（跨局不变）。所以非主人视角下轨迹图改画**对局评分**，
  图题 `#trajTitle` / 纵轴 / 图例 / 悬停 / 底部说明 `#trajAnchorLb` **全部跟着换**，
  且**不画隐藏分参考锚点线、不画 CI 误差棒**。
- 缓存：`_vsCache`（`viewSeries(pu)` 结果缓存）+ `_pIdx`（`playerIndex()` 全量 1479 人索引，
  供「自己输 ID」查人）。
- 切视角后调 `refreshView(); renderAll();`，`renderAll()` = hero/kpis/**renderPos**/metrics/chart/gamesTable/
  champs/daily/method 一把重画。**新增面板必须加进 `renderAll()`**，否则切视角时它不动。
- ⚠️ 非主人视角下「近10 vs 前20」固定口径对照（`#metricsFoot`）**不显示** ——
  那两个数是后端按主人算好的，摆出来会误导。

### ② 视角候选只留同场 ≥5 局 + 自己输 ID
- `VIEW_MIN_GAMES = 5`（`_dash_rebuild.py`）。实测（2026-09-27）：全量同场过的 1479 人里
  **只有 12 人**同场 ≥5 局（含主人），其余 1467 人不足 —— 塞进下拉等于没法用。
- 后端下发 `view_players`（≥5 局，按局数降序）+ `view_min_games`（前端说明文案用）。
  **主人不在 `view_players` 里**（后端循环里 `if _pu == me: continue`），
  前端在下拉里单独放第一项「数据主人」，避免同一个人出现两次。
- `#viewInp` + `#viewGo`：输名字（支持部分匹配）/ 完整 puuid 都能切。
  `findPlayer(q)` 优先级：精确 puuid > 精确名字 > 名字片段（取**同场最多**的那个，
  并在 `#viewMsg` 提示「匹配到 N 个人」）。查无此人只提示、**不动视角**、输入框内容保留。
- `viewSameGames(pu)` 不在下拉名单时退回 `viewSeries(pu).length`。

### ③ 「当前隐藏分」卡加「上次估算时间」
- `estimate.computed_at` 直接取 `02_数据\海斗隐藏分_v2_本次估算.json` 的 **mtime**
  （估算脚本不在 JSON 里写时间戳，mtime 语义完全对得上，省得改估算脚本）；读不到退回 `crawled_at`。
- KPI 第一张卡的 `.ft` 里显示 `上次估算 2026-09-21 23:06`。
- ⚠️ **别拿磁盘 mtime 去对 `computed_at`**（2026-09-21 实测踩到）：mtime 是**文件属性**，
  拷贝 / zip 解压 / 备份恢复全都会变；`computed_at` 是**数据内容**，跟着数据走。
  第一版断言就是这么写的，结果**交付包解压后必红** —— 接手的人会以为东西坏了。
  现在改成只验**语义**：格式合法 + 不早于 `crawled_at` + 不是未来时间（这三条任意机器都成立）。

### ④ 文案：官方锚点 → **参考锚点**
全看板 6 处（副标题 / KPI / 方法卡 / 图内文字）「官方锚点 / 官方值 / 官方依据」全部改成
「参考锚点 / 参考值 / 公开依据」。唯一保留的「官方」是页脚那句
「**非官方数值**」—— 那是免责声明，不能改。

### ⑤ 三项新指标（只展示，不进综合评分权重）
- 新增 `deaths`（场均死亡）/ `dpm`（分均伤害）/ `gpm`（分均经济），指标卡 9 → **12**。
- ⚠️ **不进 `COMPOSITE_WEIGHTS`**：权重和=100，动它就要重标定整条参照曲线、
  64.9 锚点位置会飘。这三项只是想多看到几个数，不值得为此改动已校准好的综合分。
- `dpm`/`gpm` 是**逐局算完再平均**（不是 `sum/sum` —— 后者被长局带偏）；
  时长缺失的局**跳过**，不按 0 算（全缺时显示 `—`）。

### ⑥ 三项最高高亮：**只给最高的数上色**
- 第一版挂了「名字后彩色标签 + 单元格同色底 + 最高角标 + 边框」四重装饰，
  用户反馈「**叫你高亮做区分，你给我都整的显示不全了**」—— 装饰把列挤宽、明细表被横向截断。
- 现在**只给最高那个数字换颜色**，不加任何盒模型：
  `.hi-kda` 紫 `#c79bff` / `.hi-dmg` 橙 `#ff8f5e` / `.hi-taken` 青 `#5ad2e6`。
- `.det-team table` 的 `min-width` 回到 **560**（680 是给装饰让位的，装饰没了就不需要）。
- 颜色定义**不带 `.det-team` 前缀**（底部图例 `.det-legend i` 也在用同一套色），
  且 `!important` 覆盖 KDA 列原有的绿/红。
- KDA 列拿不到最高时**保留原色**（绿/红），拿到最高才被 `hi-kda` 盖成紫色。
- ⚠️ 判定要带 `MX[key] > 0` 兜底，否则全 0 时 10 个人全是「最高」。
- `_verify_player.js` 里逐条钉死「`.top-tag` / `td.top-*` / `.tt*` 一个都不许有」+
  `min-width:560px`，防止以后再有人加装饰。

## 数据主人可切换（2026-09-22 第五轮：**视角是谁，抓取/估算就算谁**）

用户原话：「**我的看板视角是谁，抓取数据算隐藏分就算谁，并且我让你换人数据主人也要能换啊**」。

### 现状（一句话）
`_hd_crawl.py --me <puuid>` 把「数据主人」换成别人；**网络是并集**（`--seed` 只增不减，
旧对局全保留）→ 换回去只要再抓一次种子。看板视角栏 / 玩家弹层各有一个按钮，
都走桌面版操作栏的 `window.hdStartOwner`（**点两次确认**，不用 `window.confirm` ——
pywebview 里原生弹窗不一定可用、会被静默吞掉）。

### 三条硬规矩（违反任何一条都会静默算错分）
1. **换主人时绝不能重新校准量表**。`offset = 锚点分 - 主人对手最终分均值`；
   拿新主人的对手均值去反算 → 「新主人的对手均值恰好等于锚点分」（循环论证），
   整张玩家表的零点漂移，历史数据和参照曲线全对不上。
   规则：**有截图锚点就按锚点校准；没有就复用上一版 offset**（`_prev_offset()`）。
2. **估算轨迹的 offset ≠ player_elo 的 offset**（两个估计器，实测差 **46 分**：
   轨迹 479.9 / player_elo 433.5）。`--auto-anchor` 的取值顺序：
   ① `_desktop_state.traj_offset` → ② 上一次**截图锚定**的估算结果里的 `anchor.offset`
   → ③ 兜底才用 `player_elo_meta.offset`（会打印警告）。
3. **锚点按 puuid 分开存**：`_desktop_state.json` 平铺字段 = 当前主人的锚点，
   `anchors[puuid]` = 归档，`anchor_puuid` = 平铺字段属于谁。
   `--auto-anchor` 的结果**不归档**（现算的，数据一变就该重算；归档会伪装成「截图锚点」）。

### 调用顺序（漏一步就会出错）
```
_switch_owner_state(puuid)      # ① 启动前切锚点（抓取步会改 hd_data.meta.puuid，
                                #    而估算参数是启动时定的 —— 两者必须同一个人）
_hd_crawl.py --seed --players N --me <pu> --me-name <名>   # ② 换主人 + 抓种子（BFS）
_player_elo_build.py [--anchor X] [--me <pu>]              # ③ 有锚点按锚点，没有就复用 offset
_stats_crawl.py                                            # ④ 明细（roster 也被换成 TA 的）
海斗隐藏分估算_v2.py --auto-anchor                          # ⑤ TA 没截图 → 自动给分
_dash_rebuild.py                                           # ⑥ 看板
```
- `deep=True`（完整 BFS）是**默认**：只抓新局不解决对手覆盖率
  （实测 TA 的对手只有 **26%** 有独立 θ，主人自己约 60%）。
- 新主人在网络里一局都没有 → `_hd_crawl.py` **拒绝写盘**（写了会把 meta 换成一个空主人）。

### 验证
- `python 07_测试\test_owner_switch.py`（临时副本端到端，26 条，需客户端在线）
- `python 07_测试\test_packaged_owner.py`（确认 exe 里真的是新代码，见坑表）

---

## 看板配色规则（2026-09-22 用户定：**说不清用途的颜色一律不加**）

用户截图圈出逐局战绩表的两个 K/D/A 单元格问「**这两个颜色标注是干嘛的**」——
那是删括号值时从括号值**平移**到 k/d/a 上的自动配色
（`0 死 = 金 / (K+A) ≥ 2×D = 绿 / 否则红`）。对用户而言就是**无说明的随机颜色**，
而且金色同时是「修为 / 灵石 / 主序」的色，语义直接撞车。

**现行规则**：
- K/D/A **一律普通正文色**。三处落点**必须一起改**（只改一处会漏）：
  ① 明细表 `td.d-kda span`（`detHTML()` 里的 `mk()`）
  ② 逐局战绩表 `td.kd`（`gamesTable()`）
  ③ 每日战绩 `.day-wl` 里的 KDA（`dailyHTML()` 的 `dailyList` 渲染）
- **唯一例外**：明细表里 KDA 最高的那一个 `<span class="hi-kda">`（紫 `--top-kda`），
  且明细底部 `.det-legend` 有图例解释它是什么。
- 色值**只由 CSS 给**（`.hi-kda{color:var(--top-kda)!important}`），JS 只输出 class 名、
  **不写内联 `color:`**；`<b>` 必须 `color:inherit`（否则自带颜色会盖掉高亮）。
  → 所以 `theme.css` 是唯一改色点；改完跑 `apply_theme.py`（铁律 38：先确认无漂移）。
- 守住它的断言：`_verify_player.js` ⑧ 段 **3 条**（明细 / 逐局 / 每日都不许有内联配色）
  + `07_测试\test_dashboard_layout.py` **2 条**（量的是**计算色**，jsdom 量不出来）。
- 推论：以后想给任何数据「自动上色」之前先问「**用户看到这个颜色能得出什么结论**」——
  答不上来就不加。

---

## 中途停止（2026-09-21 第四轮：抓取面板「■ 停止」）
用户原话：「**抓取数据面板上增加停止按钮，让我可以中途停止**」。

- **协作式标志文件**，不是线程中断：`01_脚本\_paths.py` 的 `CANCEL_FILE =
  DATA_DIR / "_cancel.flag"` + `cancel_requested()` / `clear_cancel()` /
  `raise_if_cancelled(where)`（抛 `SystemExit(3)`）。
- ⚠️ **为什么不能用 `PyThreadState_SetAsyncExc`**：桌面版 `app.py` 是用 `runpy` 在
  **同一个进程**里跑这些脚本的，没有独立解释器可以 kill；异步注入异常可能正好落在
  `json.dump(...)` 中间 → 留下半个 JSON → 看板下次直接读不了。
  标志文件只在**安全点**（一轮抓完 / 一局算完 / 一个 epoch 算完）生效，绝不会写盘写到一半被打断。
- **用 `SystemExit` 而不是普通异常**：脚本里到处是 `except Exception:` 的兜底，
  普通异常会被吞掉、循环继续跑，停止按钮就成了摆设。
- 退出码 **3 = 用户主动停止**（`app.py` 的 `RC_STOPPED`），与「失败」区分开：
  界面据此显示「已停止」而不是「没有全部成功」。
- 安全点落位（5 个长跑脚本，`test_progress.py` ⑬ 有断言逐个钉住）：
  `_hd_crawl.py`（种子局后 + 每轮 BFS 开头 + 分批间）/ `_hd_backfill.py`（`_CHUNK = 64` 分批间）/
  `_player_elo_build.py`（每 200 局）/ `海斗隐藏分估算_v2.py`（每个 BT epoch 开头）/
  `_stats_crawl.py`（`_CHUNK = 96` 分批间）。
- ⚠️ **ThreadPoolExecutor 必须分批提交**（`_chunk = max(4, args.workers * 4)`）：
  一次性 `submit(300)` 的话，中途停止异常虽然立刻抛出，但
  `ThreadPoolExecutor.__exit__` 会 `shutdown(wait=True)` 把 300 个任务**全部等完**才返回
  —— 停止按钮等于没用。
- `app.py`：`_JOB` 加 `stopping`/`stopped`；`_start_job` **清标志 + 重置这两个字段**
  （不清的话新任务一启动就被脚本自己停掉）；新增 `Api.stop()`（幂等）；
  **步骤耗时只记 `rc == 0` 的**（半截耗时记进去，下次估时会被算得离谱地短 →
  进度条先冲到顶再被标记拽回去）。
- 面板精简（同一条需求）：删掉锚点输入框 / 「截图后又打了」/ 只重算 / 只重建 / 锚点解释区；
  只留「抓新局并重算 / 深度抓取 / 数据目录」三个按钮 + `#hdMeta`（只显示
  「锚点局 09-27 18:54 · 隐藏分 1541」）+ 进度条里的 `#hdBtnStop`。
  锚点参数改为**静默透传**（从上次估算结果读出来原样给后端，后端按 gameId 自动跟随）。

## ⚠️ 编辑本项目的铁律：**同一个文件的多次改动必须串行**

2026-09-21 第四轮真翻过车：改 `03_看板\海斗隐藏分_数据看板.html` 时把多个 Edit
放在同一条消息里并行发出，**其中一次写入被覆盖、静默丢失**，结果：

- `.hi-*` 三条颜色样式**整段没落盘** —— JS 在用 `hi-kda/hi-dmg/hi-taken`，
  但 CSS 里根本没有这些规则 → 需求「三项最高上色」**完全没生效**
- `.det-team table` 的 `min-width` 还是 680（应为 560）
- 「官方锚点」还残留 1 处

而回执全都是「Successfully edited」。**是跑断言发现红的才暴露出来。**

规矩：
1. 同一文件的多次 Edit **串行发**，一条消息里不发两个改同一文件的 Edit。
2. 改完大文件（尤其是 `海斗隐藏分_数据看板.html`）后 **`grep` 复查关键行**，别信回执。
3. 改 CSS 后顺手确认「JS 里用的 class 在 style 块里真的有定义」——
   这类丢失肉眼在浏览器里也能看出来，但在 jsdom 断言里更稳。

## 交付前必做：在**解压副本**上复验一遍

打完 zip 别直接交。把 zip 解压到 `%TEMP%` 下一个全新目录，在那儿跑：

```bat
HaidouMMR\HaidouMMR.exe --check        :: 顺便验证 _resolve_root() 不在本机路径上写死
cd 01_脚本 && node _verify_dash.js / _verify_toolbar.js / _verify_player.js
python 07_测试\test_progress.py
```

2026-09-21 就是靠这一步抓出 `computed_at` vs 磁盘 mtime 那条必红断言的 ——
在原包里全绿，一解压就红。**只在原包上跑等于没验交付**。验完删掉临时目录。

## 表现分基础指标（2026-09-04 看板 v3；2026-09-21 第四轮 +3 项）
- 外部界面里的「近30局均值」指标全部能从单局 stats 字段算出（仅依赖 LCU 本地接口，无需 Riot API key）
- 指标公式：
  - 伤害占比 = totalDamageDealtToChampions / 我方 5 人该字段总和
  - 承伤占比 = totalDamageTaken / 我方 5 人该字段总和
  - 治疗占比 = totalHeal / 我方 5 人该字段总和（**v3 新增**；含自疗/吸血，占比较高属正常）
  - 经济占比 = goldEarned / 我方 5 人该字段总和
  - 伤害转化 = 伤害占比 / 经济占比（不是 dmg/gold！）
  - 参团率 = (K+A) / 队伍总击杀（无 timeline 精度的简化算法）
  - KDA = (K+A) / max(D, 1)
  - 胜率 / 局数
  - **场均死亡 = 逐局死亡数平均**（2026-09-21 第四轮新增，只展示）
  - **分均伤害 DPM = 逐局 `dmg / dur * 60` 再平均**（同上）
  - **分均经济 GPM = 逐局 `gold / dur * 60` 再平均**（同上）
- ⚠️ **新增三项不进 `COMPOSITE_WEIGHTS`**：权重和=100，动它就要重标定整条参照曲线、
  64.9 锚点位置会飘。它们只是想多看到几个数，不值得为此改动已校准好的综合分。
- ⚠️ `dpm`/`gpm` 必须**逐局算完再平均**（不是 `sum(dmg)/sum(dur)` —— 后者被长局带偏）；
  时长缺失的局**跳过**，不按 0 算（全缺时显示 `—`）。前端 `metMean()` 与后端 `metrics()` 同口径。
- 综合分：外部界面的公式未公开，看板提供**我的加权启发式**（center(距 25/25/20/10 基准偏差)+KDA+参团+胜率 四权，9 项加权）
- 「评分趋势」折线图可切换 9 种指标：综合分/局后隐藏分/KDA/伤害/承伤/治疗/经济占比/伤害转化/参团率；**纯单局折线（主人要求，2026-09-04 先后取消散点/5局滑动均值）**：淡金折线=单局实际值按序连线（空值断开，无平滑叠加），圆点（胜金/负灰）只作胜负标记保留悬停，底色右区=近10局，金蓝虚线=前20/近10均值，图例给 Δ。⚠️ 主人明确不要平滑曲线，勿再加滑动均值/均线类叠加
- **已知差异（2026-09-27 已基本消除）**：原先「近30局自算值 vs 外部界面差距大（伤占 17.4% vs 23.4%）」，
  逐个反解后 **占比/参团/胜率/KDA/转化 五项已完全一致**
  （`dmg_share 18.9 / kill_part 67.4 / win_rate 53.3 / kda 3.55 / dmg_conv 0.97`），
  只剩「单局评分」有系统性偏差（平均绝对误差 5.52 分，未逐局拟合、只做单点平移）。
  区域百分比这类外部刻度**本地复现不出来**，看板只给本地自算口径。

## LCU 凭据稳定路径（WeGame 渠道铁律，2026-09-21 修正）
- lockfile 永远停在 2021 不刷新 → **不要依赖 lockfile**
- **统一走 `_lcu_creds2.find_lcu()`**：`tasklist` 找 `LeagueClientUx.exe` 的 PID →
  `NtQueryInformationProcess(ProcessCommandLineInformation=60)` 读命令行。
  **只需要 `PROCESS_QUERY_LIMITED_INFORMATION`(0x1000)，不需要 `PROCESS_VM_READ`**，
  所以沙箱里也能跑。
- ⚠️ **缓冲区必须够大**：走信息类 60 时若给 16/256 字节，内核返回
  `STATUS_INFO_LENGTH_MISMATCH`(0xC0000004) 并通过 `ReturnLength` 告知实需
  （实测 **~2902 字节**，命令行 1442 字符）。**必须按 `ReturnLength` 扩容重试**，
  否则会掉进需要 `VM_READ` 的 PEB 兜底路径 → `ERROR_ACCESS_DENIED`(5) → 报「未找到客户端」。
  这是 2026-09-21 之前的**根因 bug**。
- ❌ **不要再走 PowerShell / WMI**（`Get-CimInstance Win32_Process`）：反作弊保护下
  `CommandLine` 返回**空**。`_stats_crawl.py` 曾照这个错建议自己写了一份，
  结果该脚本**必定**以「✗ 找不到 League 客户端」退出，而且长期没人发现。
  2026-09-21 已改为复用 `_lcu_creds2`。
- 解析命令行时两种写法都要认：`--app-port=NNNN`（合并 token）和 `--app-port NNNN`（分开）。
  用 `shlex.split` 处理（整行可能被双引号包裹）。
- ⚠️ `find_lcu()` 返回的 **`PORT` 是 `int`**，拼 URL 必须 `str(PORT)`。
- participants 字段是 `teamId`（不是 `team`！）

## 抓取与看板重建（一体化）
1. `_hd_crawl.py` 滚雪球 BFS 抓对局网络 → hd_data.json（含 my_game_ids / 每局 10 人 name+kda）
2. `海斗隐藏分估算_v2.py` 拟合 BT + 在线 Elo + 谱分解 → 海斗隐藏分_v2_本次估算.json
3. `_player_elo_build.py` → player_elo.json（全量玩家评分）
4. `_stats_crawl.py` 拉主人全部局 LCU 详情 → my_stats.json（9 项含 heal_share）
   + 海斗roster.json（每局 10 人全量 stats，含 summonerName/伤害/承伤/经济/治疗）
5. `_dash_rebuild.py` 聚合全部（metrics/series 30局/roster 索引）→ 海斗dashboard_data.json + 注入 海斗隐藏分_数据看板.html
6. jsdom 校验：`node _verify_dash.js` + `_verify_toolbar.js` + `_verify_player.js`（101+39+186 = 326 条断言，勿手工数数代替）
- ⚠️ **第 4 步是看板的前置依赖**，不能跳过：`_dash_rebuild.py` 的 9 项指标全靠它。
  新抓的局若没有 stats，看板会 `KeyError` 崩（已加保险：缺 stats 只告警不崩）。
- ⚠️ **第 4 步失败时不要覆盖旧数据**：`_stats_crawl.py` 在「N 局全失败」时拒绝写盘。
  （血的教训：修凭据时引入 `str(PORT)` 的 TypeError，161 局全失败却照样写盘，
  把 `my_stats.json` / `海斗roster.json` 覆盖成 `[]`。）
- 增量更新：LCU 在线时几分钟完成全链路；client 关了则第 4 步无法补 heal/10人明细