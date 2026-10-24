/* 海斗看板验证：双锚点校准 + v6 逐局平均综合分 + 10 人明细 + 趋势/隔离性 */
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const HTML = path.resolve(__dirname, "..", "03_看板", "海斗隐藏分_数据看板.html");
const html = fs.readFileSync(HTML, "utf-8");
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errs.push("jsdomError: " + e.message));
vc.on("error", (...a) => errs.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, { runScripts: "dangerously", virtualConsole: vc, url: "file:///" + HTML.replace(/\\/g, "/") });
const w = dom.window, d = w.document;
const DATA = JSON.parse(d.querySelector("#dash-data").textContent);
let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (extra ? "  → " + extra : "")); }
};

setTimeout(() => { try {
  console.log("== 运行时错误 ==");
  check("无 JS 报错", errs.length === 0, errs.join(" | "));

  console.log("== ① KPI 校准区 ==");
  const kpis = d.querySelectorAll("#kpis .kpi");
  check("KPI 卡片 = 6", kpis.length === 6, "实际 " + kpis.length);
  const kpiText = d.querySelector("#kpis").textContent;
  check("含「当前海斗隐藏分」", /当前海斗隐藏分/.test(kpiText));
  check("含「综合评分」KPI", /综合评分/.test(kpiText));
  // 锚点值一律从 DATA 读，不写死
  const _ancK = String(Math.round((DATA.estimate || {}).anchor_score));
  check(`含「参考锚点 ${_ancK}」（动态读 estimate.anchor_score）`,
        kpiText.includes("参考锚点") && kpiText.includes(_ancK), kpiText.slice(0, 90));
  check("全看板不再出现「官方锚点 / 官方值 / 官方依据」（口径已改参考锚点）",
        !/官方锚点/.test(html) && !/官方值/.test(html) && !/官方依据/.test(html));
  const _cAnc = String((DATA.anchors.composite_anchor || {}).value);
  check(`含综合分锚点 ${_cAnc} 与公式 raw 值`,
        kpiText.includes(_cAnc) && /raw/.test(kpiText), kpiText.slice(0, 120));
  // 2026-09-21 新增：当前隐藏分那张卡要标「上次估算时间」
  const est = DATA.estimate || {};
  check("estimate.computed_at 已下发且形如 YYYY-MM-DD HH:MM",
        /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(est.computed_at || ""), String(est.computed_at));
  check("KPI 里出现「上次估算 <时间>」标记",
        /上次估算\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(kpiText),
        kpiText.slice(0, 200));
  // computed_at 的**语义**是「上次估算的时间」，来源是估算文件的 mtime。
  //
  // ⚠️ 千万别拿磁盘 mtime 跟它比对 —— 那是 2026-09-21 实测踩到的坑：
  //    mtime 是**文件属性**，拷贝 / zip 解压 / 备份恢复全都会变；
  //    computed_at 是**数据内容**，跟着数据走。交付包解压后那条断言必红，
  //    接手的人会以为东西坏了（实际只是文件被搬过）。
  //    所以这里只验**语义**，三条都能在任意机器上成立：
  //      ① 格式合法（上一条已单独验）
  //      ② 不早于抓取时间 —— 估算必然发生在抓取之后
  //      ③ 不是未来时间
  const _ca = est.computed_at || "", _cr = est.crawled_at || "";
  const _caD = new Date(_ca.replace(" ", "T"));
  const _notFuture = isFinite(_caD) && _caD.getTime() <= Date.now() + 60000;
  const _afterCrawl = !_cr || _ca.slice(0, 16) >= _cr.slice(0, 16);
  check("computed_at 语义正确（不早于抓取时间 + 不是未来时间）",
        _notFuture && _afterCrawl,
        `computed_at=${_ca} crawled_at=${_cr} 未来=${!_notFuture} 早于抓取=${!_afterCrawl}`);

  console.log("== ② 12 指标卡（含 2026-09-21 新增三项）+ 综合评分卡含锚点信息 ==");
  const cards = d.querySelectorAll("#metGrid .met-card");
  check("met-card 数量 = 12", cards.length === 12, "实际 " + cards.length);
  const lbOf = c => c.querySelector(".lb").textContent;
  const compCard = [...cards].find(c => /综合评分/.test(lbOf(c)));
  check("综合评分卡存在", !!compCard);
  if (compCard) {
    const ft = compCard.querySelector(".ft").textContent;
    check(`综合评分卡含锚点 ${_cAnc}`, ft.includes(_cAnc), ft);
    check("综合评分卡含 v6 字样（逐局过曲线再平均）", /v6/.test(ft), ft);
  }
  // 新增三项：场均死亡 / 分均伤害 / 分均经济
  ["场均死亡", "分均伤害", "分均经济"].forEach(lb => {
    const c = [...cards].find(x => lbOf(x).includes(lb));
    check(`新增指标卡存在：${lb}`, !!c);
    if (c) check(`${lb} 有数值（不是空）`,
                 (c.querySelector(".big").textContent || "").trim().length > 0,
                 c.querySelector(".big").textContent);
  });
  // 新增三项**不进**综合评分权重：权重表里不能出现这三个 key
  const Wt = (DATA.anchors && DATA.anchors.composite_formula
              && DATA.anchors.composite_formula.weights) || {};
  check("新增三项不进综合评分权重（deaths/dpm/gpm 不在 weights 里）",
        !("deaths" in Wt) && !("dpm" in Wt) && !("gpm" in Wt), Object.keys(Wt).join(","));
  check("权重之和仍是 100（没被新指标改动）",
        Math.abs(Object.values(Wt).reduce((s, x) => s + x, 0) - 100) < 1e-9,
        String(Object.values(Wt).reduce((s, x) => s + x, 0)));

  console.log("== ③ 综合评分方法论卡 ==");
  const methodCards = [...d.querySelectorAll("#methodGrid .m-card")];
  const v6Cards = methodCards.filter(c => /v6 综合评分公式|v6 双锚点校准/.test(c.querySelector("h3").textContent));
  check("存在 v6 综合评分公式卡", v6Cards.length >= 1);
  const anchorCard = methodCards.find(c => /双锚点校准/.test(c.querySelector("h3").textContent));
  check("双锚点校准卡存在", !!anchorCard);
  if (anchorCard) {
    const h5 = anchorCard.innerHTML;
    check(`包含「隐藏分锚点 ${_ancK}」`, h5.includes(_ancK));
    check(`包含「综合分锚点 ${_cAnc}」`, h5.includes(_cAnc));
    check("说明「只挪位置不压动态」", /只挪位置不压动态/.test(h5));
  }

  console.log("== ③.5 v6 核心断言（逐局平均口径） ==");
  // 综合分不再恒 50：series 的 comp 必须有真实波动
  const comps = [...new Set((DATA.series || []).map(r => r.comp))];
  check("series comp 有波动（不同值 >= 10）", comps.length >= 10, "不同值 " + comps.length);
  check("comp 全部在 0-100", comps.every(v => v > 0 && v < 100));
  check(`comp30 恒等于综合分锚点 ${_cAnc}（校准定义）`,
        Math.abs(DATA.metrics.composite - Number(_cAnc)) < 0.01, String(DATA.metrics.composite));
  check("近10 ≠ 前20（动态保留）", DATA.metrics.composite10 !== DATA.metrics.composite20,
    `${DATA.metrics.composite10} vs ${DATA.metrics.composite20}`);

  console.log("== ④ 趋势图切指标 ==");
  const tbtns = d.querySelectorAll("#trendCtrl .tbtn");
  check("指标按钮 >= 8 个", tbtns.length >= 8, "实际 " + tbtns.length);
  check("默认渲染 svg", !!d.querySelector("#trendBox svg"));
  check("默认指标 = 综合评分", d.querySelector("#trendCur").textContent.includes("综合评分"));
  const healBtn = [...tbtns].find(b => b.textContent.trim() === "治疗占比");
  check("治疗占比按钮存在", !!healBtn);
  if (healBtn) {
    healBtn.click();
    check("切到治疗占比", d.querySelector("#trendCur").textContent.includes("治疗占比"));
    check("切换后 svg 重绘", !!d.querySelector("#trendBox svg"));
  }

  console.log("== ⑤ 10 人明细：4 项（KDA / 伤害 / 承伤 / 隐藏分） ==");
  const rows = d.querySelectorAll("#gameBody tr");
  check("表格有行", rows.length >= 20, "rows " + rows.length);
  const xb = rows[0].querySelector(".xbtn");
  check("明细按钮存在", !!xb);
  if (xb) {
    xb.click();
    const det = d.querySelector("#gameBody tr.det-row");
    check("展开 det-row", !!det);
    const teams = det.querySelectorAll(".det-team");
    check("2 个队伍面板", teams.length === 2);
    const pls = det.querySelectorAll(".det-team tbody tr");
    check("10 名玩家", pls.length === 10);
    const me = det.querySelectorAll("tr.me");
    check("本人高亮 1 行", me.length === 1);
    const head = det.querySelector(".det-team thead").textContent;
    check("列含「玩家/英雄/K/D/A/伤害/承伤/隐藏分」",
      /玩家/.test(head) && /英雄/.test(head) && /K\s*\/\s*D\s*\/\s*A/.test(head) &&
      /伤害/.test(head) && /承伤/.test(head) && /隐藏分/.test(head), head);
    check("列无「治疗」(v4 已改为隐藏分)", !/治\s*疗/.test(head));
    // 隐藏分单元格：有 mmr-v / mmr-na 至少一种
    const mmrV = det.querySelectorAll(".mmr-v").length;
    const mmrNa = det.querySelectorAll(".mmr-na").length;
    check("mmr 单元存在（10 个都有单元格）", mmrV + mmrNa === 10, `mmrV=${mmrV}, mmrNa=${mmrNa}`);
    check("mmr 覆盖率达标（>=5 人有分）", mmrV >= 5, `mmrV=${mmrV}`);
    // v5 核心修复：mmr 不能千篇一律
    const vals = [...det.querySelectorAll(".mmr-v")].map(e => parseFloat(e.textContent));
    check("mmr 不千篇一律（同局不同值 >= 4）", new Set(vals).size >= 4, "不同值 " + new Set(vals).size);
    // mmr 数值范围合理（1600-2400）
    if (vals.length) {
      // 2026-09-27 重锚到 1541 后整条量表下移约 387，旧区间 1600-2400 已失效。
      // 判据改成**相对锚点**：同局 10 人是按 MMR 相近匹配到一起的，不该离锚点太远。
      const _ancN = Number((DATA.estimate || {}).anchor_score) || 1500;
      check(`mmr 数值在锚点 ${_ancN} 附近合理区间（±600）`,
            vals.every(v => v >= _ancN - 600 && v <= _ancN + 600), "vals=" + vals.join(","));
    }
    check("伤害数值 k 格式化", /(\d+(\.\d)?k)/.test(det.textContent));
    check("明细底部说明存在", !!det.querySelector(".det-foot"));
    xb.click();
    check("再点收起", !d.querySelector("#gameBody tr.det-row"));
  }

  console.log("== ⑥ 隔离性：趋势指标按钮 vs 逐局表局数按钮互不干扰 ==");
  // 2026-10-03 由 _verify_dash_iso.js 并入。原文件是独立脚本，与本文件共用同一个
  // `errs` 数组和同一份 boilerplate，但断言目标不同（它守「交互隔离」，本文件守「数值口径」）。
  //
  // ⚠️ 本段所有局部变量一律加 `iso_` 前缀：上面的 ⑤ 段已经声明了 `xb`(:295) 与 `det`(:299)，
  //    而本段原本在 for 循环里也用 `const xb` / `const det` —— 同作用域重复声明会直接语法报错，
  //    更糟的情况是静默覆盖主流程变量、把既有断言弄成假绿。
  //
  // 注：iso 原有的「指标按钮 >= 8 个」与上面 ④ 的同名断言重复，已删（铁律 45：
  //     同一个值两份落点，迟早过期）。iso 独有的「挨个切一遍」留下来了 —— ④ 只测了治疗占比一项。
  const iso_r76 = d.getElementById("allGamesBtn") || [...d.querySelectorAll(".tbtn[data-n]")].find(b => b.dataset.n === "76");
  // ⚠️ 合并后这里**不能**断言「默认是综合评分」—— 上面 ④ 段已经把趋势切到「治疗占比」了。
  //    iso 原文件是独立脚本，它跑时趋势按钮确实没被碰过，所以那条断言成立；并进来之后就不成立了。
  //    隔离性的真正语义是「点局数按钮**不改变**趋势指标」，所以改成前后对比 —— 与执行顺序无关。
  const iso_trendBefore = (d.querySelector("#trendCtrl .tbtn.on") || {}).textContent || "";
  iso_r76.click();
  const iso_rowsAfter76 = d.querySelectorAll("#gameBody tr").length;
  const iso_totalGames = JSON.parse(d.getElementById("dash-data").textContent).mine.length;
  const iso_trendOn1 = d.querySelector("#trendCtrl .tbtn.on") || {};
  const iso_trendSvg1 = !!d.querySelector("#trendBox svg");
  check("[隔离] rows >= 总局数", iso_rowsAfter76 >= iso_totalGames, "rows=" + iso_rowsAfter76 + ", total=" + iso_totalGames);
  check("[隔离] 趋势 svg 仍存在", iso_trendSvg1);
  check("[隔离] 点「全部」局数后趋势指标没被改掉",
        !!iso_trendOn1.textContent && iso_trendOn1.textContent === iso_trendBefore,
        "点之前=" + iso_trendBefore + " 点之后=" + (iso_trendOn1.textContent || "(无)"));

  const iso_kdaBtn = [...d.querySelectorAll("#trendCtrl .tbtn")].find(b => b.textContent.trim() === "KDA");
  iso_kdaBtn.click();
  const iso_k40 = [...d.querySelectorAll(".tbtn[data-n]")].find(b => b.dataset.n === "40");
  iso_k40.click();
  const iso_cur2 = d.querySelector("#trendCur").textContent;
  const iso_on2 = d.querySelector("#trendCtrl .tbtn.on");
  const iso_rows40 = [...d.querySelectorAll(".tbtn[data-n]")].filter(b => b.classList.contains("on"))[0];
  check("[隔离] cur 含 KDA", /KDA/.test(iso_cur2), iso_cur2);
  check("[隔离] 趋势按钮仍为 KDA（不受局数按钮干扰）", iso_on2 && /KDA/.test(iso_on2.textContent), iso_on2 && iso_on2.textContent);
  check("[隔离] 局数按钮是 40", iso_rows40 && iso_rows40.dataset.n === "40");
  check("[隔离] 表格行 <= 40", d.querySelectorAll("#gameBody tr").length <= 40);

  const iso_metricBtns = [...d.querySelectorAll("#trendCtrl .tbtn")];
  // ⚠️ 按钮数是**数据驱动**的：trendCtrl() 只给「覆盖率 ≥0.7」的指标出按钮
  //    （看板 HTML 的 TREND_METRICS / trendCoverage）。「局后隐藏分」只在轨迹那几局有值，
  //    轨迹短的主人（比如小号 20 局 / 窗口 30 局 = 0.667）就会少一个 → 8 个。
  //    所以守的是「每个都能切」，不是写死 9 个（9 是大号数据的形状）；个数由 ④ 守。
  let iso_allOK = true;
  const iso_labels = [];
  for (const b of iso_metricBtns) {
    b.click();
    const svg = d.querySelector("#trendBox svg");
    const cur = d.querySelector("#trendCur");
    iso_labels.push(b.textContent.trim());
    if (!svg || !cur || !cur.textContent.includes(b.textContent.trim())) {
      iso_allOK = false;
      console.log("    ✗ 切到 [" + b.textContent.trim() + "] 失败");
    }
  }
  check("[隔离] 所有指标按钮全可切换", iso_allOK, iso_labels.join(","));

  // 回到 20 局，逐行展开明细
  [...d.querySelectorAll(".tbtn[data-n]")].find(b => b.dataset.n === "20").click();
  const iso_allRows = d.querySelectorAll("#gameBody tr");
  let iso_expandAllOK = true;
  for (let i = 0; i < Math.min(5, iso_allRows.length); i++) {
    const xb2 = iso_allRows[i].querySelector(".xbtn");
    if (!xb2) { iso_expandAllOK = false; console.log(`   row ${i} 无明细按钮`); continue; }
    xb2.click();
    const det2 = iso_allRows[i].nextElementSibling;
    if (!det2 || !det2.classList.contains("det-row")) { iso_expandAllOK = false; }
    else {
      const iso_detHTML = det2.querySelector("td").innerHTML;
      if (!/mmr-v|mmr-na/.test(iso_detHTML)) { iso_expandAllOK = false; }
    }
    xb2.click();  // 收起
  }
  check("[隔离] 5 行明细均能展开且含 mmr 列", iso_expandAllOK);

  // 「运行时无新错误」放在**最后**而不是开头（iso 原文件的位置）：errs 是从页面加载就开始累积的，
  // 在收尾处检查能同时覆盖「加载期报错」与「④⑤⑥ 三段交互引发的报错」。开头那次保留在 :25。
  console.log("== ⑦ 累计运行时错误（含上面三段交互引发的）==");
  check("交互全程无新 JS 报错", errs.length === 0, errs.join(" | "));

  console.log("== 其他 ==");
  check("隐藏分轨迹图渲染", !!d.querySelector("#chartBox svg"));
  check("英雄榜渲染", d.querySelectorAll("#champList .champ-row").length >= 5);
  check("方法论卡片 >= 6（含 v5 二锚点）", methodCards.length >= 6, "实际 " + methodCards.length);

  console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
  process.exit(fail ? 1 : 0);
  // 审查 M17：断言失败后继续解引用会让脚本以裸堆栈收场、丢掉全部汇总。
  // 顶层兜底：崩溃也按失败退出并留下线索。
} catch (e) {
  console.error("✗ 未捕获异常（某条断言红后继续解引用？）:", e && (e.stack || e.message || e));
  process.exit(1);
} }, 400);
