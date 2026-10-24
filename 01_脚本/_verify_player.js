/* 看板回归断言（累积三轮需求，2026-09-21）
 *
 *   ① 窗口最大化时面板要跟着变宽（.wrap 不能再是死值 1240px）
 *   ② 5 分钟以内的对局一律无效 —— 看板里一局都不能出现
 *   ③ 每日战绩默认最近 10 天，且时间范围可切换
 *   ④ 明细里的玩家名可点 → 弹层给交手记录；「抓取数据」按钮只在桌面版出现
 *   ⑤ 转义兜底（属性注入面）
 *   ⑥ 看板视角可切换（谁算「我」）
 *   ⑦ 隐藏分轨迹 + 表现分指标 都能调显示范围
 *   ⑧ 明细里本局 KDA/伤害/承伤 最高者高亮（三色区分）
 *   ⑨ 弹层「对局评分」列 + 联手/交手分开
 *   ⑩ 前端窗口均值/综合分与后端公式逐字段一致（口径不漂移）
 *
 * 用法（需要 jsdom）：
 *   set NODE_PATH=<node workspace>\node_modules
 *   node 01_脚本\_verify_player.js
 */
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const HTML = path.join(ROOT, "03_看板", "海斗隐藏分_数据看板.html");
const THEME = path.join(ROOT, "06_桌面版", "theme.css");
const MIN_GAME_SECONDS = 300;      // 与 01_脚本\_paths.py 的 MIN_GAME_SECONDS 对齐

const src = fs.readFileSync(HTML, "utf-8");
const theme = fs.readFileSync(THEME, "utf-8");

let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log("  \u2713 " + name); }
  else { fail++; console.log("  \u2717 " + name + (extra ? "  \u2192 " + extra : "")); }
};

const styleBlock = (src.match(/<style>([\s\S]*?)<\/style>/) || [])[1] || "";

function load(withPywebview) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errs.push("jsdomError: " + e.message));
  vc.on("error", (...a) => errs.push("console.error: " + a.join(" ")));
  const dom = new JSDOM(src, {
    runScripts: "dangerously",
    virtualConsole: vc,
    url: "file:///" + HTML.replace(/\\/g, "/"),
    beforeParse(window) {
      if (withPywebview) {
        window.pywebview = { api: {
          // 桌面版工具栏要用到的（不补全的话工具栏脚本会先报错，
          // 掩盖掉我们要验的东西）—— 与 _verify_toolbar.js 的桩保持一致
          last_anchor: () => Promise.resolve({ ok: false }),
          start: () => Promise.resolve({ ok: true, job: 1, step_total: 5 }),
          progress: () => Promise.resolve({
            running: false, finished: false, frac: 0, elapsed: 0,
            eta: null, step: 0, step_total: 0, step_name: "",
            log: [], steps: [], ok: null, error: null,
          }),
          recalc: () => Promise.resolve({ ok: true, steps: [] }),
          rebuild: () => Promise.resolve({ ok: true, steps: [] }),
          open_data_dir: () => Promise.resolve({ ok: true }),
          probe_player: () => Promise.resolve({
            ok: true,
            probe: {
              puuid: "x", name: "对手", fetched_at: "2026-09-21 21:00:00",
              source: "SGP", n_games: 3, n_win: 2, n_loss: 1, win_rate: 66.7,
              known: { mmr: 1901.8, n_games: 83 },
              local: { mmr: 1912.4, n_used: 2, n_games: 3, opp_known_avg: 3.6, reliable: true },
              min_game_seconds: 300,
              games: [
                { gameId: 3, ts: 1789000000000, win: true, champion: "EZ", k: 1, d: 2, a: 3, opp_avg: 1900, opp_known: 4 },
                { gameId: 2, ts: 1788000000000, win: false, champion: "蔚", k: 4, d: 5, a: 6, opp_avg: 1880, opp_known: 3 },
                { gameId: 1, ts: 1787000000000, win: false, champion: "劫", k: 7, d: 8, a: 9, opp_avg: null, opp_known: 0 },
              ],
            },
          }),
        } };
      }
    },
  });
  return new Promise(res => setTimeout(() => res({ dom, errs }), 500));
}

/* ---------- 与后端 _dash_rebuild.py 独立重算，用来钉住「口径不漂移」 ---------- */
const r1 = v => Math.round(v * 10) / 10;
const r2 = v => Math.round(v * 100) / 100;
function meanOf(rows){
  const n = rows.length;
  let kS = 0, dS = 0, w = 0;
  rows.forEach(r => { kS += r.k + r.a; dS += r.d; if (r.win) w++; });
  const m = { n: n, kda: r2(kS / Math.max(dS, 1)), win_rate: r1(100 * w / n) };
  // v6（2026-09-27）改**聚合口径**，与后端 metrics() 逐字对齐：
  //   · KDA = Σ(K+A) / ΣD（上面已改；旧写法是逐局 (K+A)/max(D,1) 再平均 → 实测 4.53，与口径不符）
  //   · 伤害转化 = 均值伤害占比 ÷ 均值经济占比（旧写法「逐局转化再平均」→ 0.96，与口径不符）
  // ⚠️ 必须用**未取整**的均值算转化，再取整；先 r1 再相除会跨过 .x5 边界差 0.01。
  const rawShare = {};
  ["dmg_share", "taken_share", "heal_share", "gold_share", "kill_part"]
    .forEach(k => { rawShare[k] = rows.reduce((s, r) => s + r[k], 0) / n; });
  m.dmg_conv = r2(rawShare.dmg_share / Math.max(rawShare.gold_share, 1e-9));
  ["dmg_share", "taken_share", "heal_share", "gold_share", "kill_part"]
    .forEach(k => { m[k] = r1(rawShare[k]); });
  return m;
}
function curveOf(pts, val){
  if (val <= pts[0][0]) return pts[0][1];
  if (val >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
  for (let i = 0; i < pts.length - 1; i++){
    const x1 = pts[i][0], y1 = pts[i][1], x2 = pts[i + 1][0], y2 = pts[i + 1][1];
    if (x1 <= val && val <= x2) return x2 > x1 ? y1 + (y2 - y1) * (val - x1) / (x2 - x1) : y2;
  }
  return pts[pts.length - 1][1];
}
function compOf(rows, F){
  // v6（2026-09-27）：多行窗口 = **逐局过曲线再取算术平均**，与后端 _win_comp() 同构。
  // 旧写法「先把窗口均值过曲线」（meanOf → curve）已废弃 —— 那会让看板的
  // 「综合评分」与逐局「对局评分」列自相矛盾（实测近 30 局：旧 62.3 vs 逐局均值 57.7）。
  // 「近期局内表现评分 / 平均本局评分」就是这个逐局平均的定义。
  // 必须用 offset_exact（未取整）—— 用显示用的 round(offset,1) 会差最多 0.05，
  // 正好能跨过 .x5 取整边界，同一个窗口两边差 0.1 分。
  // 第九轮（2026-09-28）补两件事：
  //   ① 逐局**夹紧**（F.clamp = [38,100]）—— 夹紧是非线性的，必须逐局先夹再平均，
  //      「先平均再夹紧」和后端 _win_comp() 会差 0.1~0.3 分。
  //   ② 逐局**局内统治力加成** = dominance.per × row.n1（本局拿到全场第一的项数），
  //      加在平移之后、夹紧之前（与后端 finalize() 同序）。
  if (!rows.length) return null;
  const off = (F.offset_exact != null) ? Number(F.offset_exact) : Number(F.offset || 0);
  const CL = F.clamp;
  const PER = (F.dominance && Number(F.dominance.per)) || 0;
  const one = r => {
    const m = Object.assign({}, r, { win_rate: r.win ? 100 : 0 });
    let t = 0;
    for (const k in F.weights) t += curveOf(F.curves[k], Number(m[k]) || 0) * F.weights[k];
    let v = t / 100 + off + PER * (Number(r.n1) || 0);
    if (CL) v = Math.min(Math.max(v, Number(CL[0])), Number(CL[1]));
    return v;
  };
  let s = 0;
  for (const r of rows) s += one(r);
  return r1(s / rows.length);
}
const kdaOf = p => p.d > 0 ? (p.k + p.a) / p.d : (p.k + p.a);
const rosterAll = rg => rg.teams.reduce((a, t) => a.concat(t.players), []);

(async () => {
  console.log("== ① 面板宽度跟随窗口 ==");
  check("theme.css 的 .wrap 不再是死值 1240px",
        !/\.wrap\{[^}]*max-width:1240px/.test(theme));
  check("theme.css 的 .wrap 用 min(2200px,96vw)",
        /\.wrap\{[^}]*max-width:min\(2200px,96vw\)/.test(theme));
  check("已注入到看板 HTML（别忘了跑 apply_theme.py）",
        /\.wrap\{[^}]*max-width:min\(2200px,96vw\)/.test(styleBlock),
        "HTML 里的 style 块还是旧宽度 —— 跑 python 06_桌面版\\apply_theme.py");

  const a = await load(false);
  const w = a.dom.window, d = a.dom.window.document;
  const DATA = JSON.parse(d.querySelector("#dash-data").textContent);
  const ME_PU = DATA.me_puuid;

  console.log("== ② 5 分钟以内的对局全部剔除 ==");
  const rosterDur = Object.keys(DATA.roster).map(g => DATA.roster[g].dur || 0);
  const short = rosterDur.filter(v => v < MIN_GAME_SECONDS);
  check("10 人明细里没有 <" + MIN_GAME_SECONDS + "s 的对局", short.length === 0,
        "还有 " + short.length + " 局：" + short.slice(0, 5).join(","));
  check("明细局数 = 逐局表局数（口径一致）",
        Object.keys(DATA.roster).length === DATA.mine.length,
        `roster=${Object.keys(DATA.roster).length} mine=${DATA.mine.length}`);
  const dailyN = (DATA.daily || []).reduce((s, x) => s + x.n, 0);
  check("每日战绩的局数合计 = 逐局表局数", dailyN === DATA.mine.length,
        `daily=${dailyN} mine=${DATA.mine.length}`);
  check("KPI 的总局数用的是过滤后的数",
        DATA.stats.total.n === DATA.mine.length,
        `kpi=${DATA.stats.total.n} mine=${DATA.mine.length}`);

  console.log("== ③ 每日战绩：默认最近 10 天 + 可调范围 ==");
  check("范围选择器存在", !!d.getElementById("dailyCtrl"));
  const dBtns = [...d.querySelectorAll("#dailyCtrl .tbtn[data-d]")];
  check("5 个范围按钮（7/10/14/30/全部）", dBtns.length === 5,
        "实际 " + dBtns.length + "：" + dBtns.map(b => b.dataset.d).join(","));
  check("默认选中「最近 10 天」",
        !!d.querySelector("#dailyCtrl .tbtn.on") &&
        d.querySelector("#dailyCtrl .tbtn.on").dataset.d === "10");
  const rows0 = d.querySelectorAll("#dailyList .day-row").length;
  check("默认只渲染 10 行", rows0 === Math.min(10, DATA.daily.length),
        `渲染 ${rows0} 行 / 共 ${DATA.daily.length} 天`);
  check("范围说明文字存在", (d.getElementById("dailySub").textContent || "").length > 0);

  dBtns.find(b => b.dataset.d === "0").click();
  check("点「全部」→ 渲染全部天数",
        d.querySelectorAll("#dailyList .day-row").length === DATA.daily.length);
  dBtns.find(b => b.dataset.d === "7").click();
  check("点「最近 7 天」→ 7 行",
        d.querySelectorAll("#dailyList .day-row").length === Math.min(7, DATA.daily.length));
  check("切换后选中态跟着走",
        d.querySelector("#dailyCtrl .tbtn.on").dataset.d === "7");
  dBtns.find(b => b.dataset.d === "10").click();
  check("切回 10 天", d.querySelectorAll("#dailyList .day-row").length === Math.min(10, DATA.daily.length));

  console.log("== ④ 点玩家名看 TA 的战绩 ==");
  check("玩家弹层 DOM 存在", !!d.getElementById("pModal"));
  check("弹层默认隐藏（不带 .on）", !d.getElementById("pModal").classList.contains("on"));

  // 展开第一行明细
  const xb = d.querySelector("#gameBody tr .xbtn");
  check("逐局表有明细按钮", !!xb);
  xb.click();
  const det = d.querySelector("#gameBody tr.det-row");
  const names = [...det.querySelectorAll(".nm.pn")];
  check("明细里有可点玩家名（9 个别人）", names.length === 9, "实际 " + names.length);
  check("每个可点名都带 puuid", names.every(n => (n.dataset.pu || "").length > 10));
  check("本人的名字不可点（.pn 里没有「我」行）",
        ![...det.querySelectorAll("tr.me .nm")].some(n => n.classList.contains("pn")));

  names[0].click();
  const modal = d.getElementById("pModal");
  check("点名字 → 弹层打开", modal.classList.contains("on"));
  check("弹层标题是那个玩家", d.getElementById("pmName").textContent.length > 0);
  const body = d.getElementById("pmBody");
  check("弹层含「与你联手」区块", /与你联手/.test(body.textContent));
  check("弹层含「与你交手」区块", /与你交手/.test(body.textContent));
  check("弹层含隐藏分区块", /隐藏分/.test(body.textContent));
  check("弹层含同场明细表", body.querySelectorAll(".pm-tbl tbody tr").length >= 1,
        "行数 " + body.querySelectorAll(".pm-tbl tbody tr").length);
  check("浏览器里「抓取数据」按钮是禁用的",
        d.getElementById("pmProbeBtn") && d.getElementById("pmProbeBtn").disabled);
  check("浏览器里给出「用桌面版」的提示",
        /桌面版/.test(d.getElementById("pmProbeSt").textContent));

  d.getElementById("pmClose").click();
  check("点关闭 → 弹层收起", !modal.classList.contains("on"));

  console.log("== ④b 桌面版：抓取按钮可用并能渲染结果 ==");
  const b = await load(true);
  const db = b.dom.window.document;
  db.querySelector("#gameBody tr .xbtn").click();
  const namesB = [...db.querySelectorAll("#gameBody tr.det-row .nm.pn")];
  namesB[0].click();
  const btnB = db.getElementById("pmProbeBtn");
  check("桌面版里「抓取数据」按钮可用", btnB && !btnB.disabled);
  check("桌面版提示文案不是「需要桌面版」",
        !/请用桌面版/.test(db.getElementById("pmProbeSt").textContent));
  btnB.click();
  await new Promise(r => setTimeout(r, 300));
  const out = db.getElementById("pmProbeOut").textContent;
  check("抓取结果渲染出来了", /主网络已有分/.test(out) && /本地走查分/.test(out), out.slice(0, 120));
  check("抓取结果带对局表",
        db.querySelectorAll("#pmProbeOut .pm-tbl tbody tr").length === 3);
  check("抓取结果标注了数据源", /SGP/.test(out));

  console.log("== ⑤ 转义兜底（属性注入面）==");
  // 玩家名会写进 title= / data-nm= / data-pu= 这些**属性**里，
  // 名字里只要有一个 `"` 就能截断属性、注入任意 HTML。所以 esc() 必须连引号一起转。
  // 审查 M15：w.eval("esc") 在页面没定义 esc 时直接抛 ReferenceError，
  // 下一行 typeof 守卫成了死代码 —— 先判型再取。
  const escFn = w.eval("typeof esc === 'function' ? esc : null");
  check("esc 是函数", typeof escFn === "function");
  if (typeof escFn === "function"){
    check("esc 转义 &<>\"'",
          escFn('a&b<c>d"e\'f') === "a&amp;b&lt;c&gt;d&quot;e&#39;f",
          escFn('a&b<c>d"e\'f'));
    check("esc 对 null/undefined 不炸", escFn(null) === "" && escFn(undefined) === "");
  }

  /* ================= ⑥ 看板视角可切换 ================= */
  console.log("== ⑥ 看板视角可切换（谁算「我」） ==");
  const vsel = d.getElementById("viewSel");
  check("Hero 区有视角下拉", !!vsel && !!d.getElementById("viewBar"));
  check("数据里带 me_puuid / me_name", !!ME_PU && !!DATA.me_name, ME_PU);
  // 2026-09-21 用户要求：下拉里只留同场 >=5 局的备选（不足 5 局重建不出可用序列）
  const VMIN = DATA.view_min_games;
  check("下发了 view_min_games（前端说明文案要用）", typeof VMIN === "number" && VMIN > 0,
        String(VMIN));
  check("视角候选里**每个人**同场局数都 >= view_min_games",
        (DATA.view_players || []).length > 0 &&
        (DATA.view_players || []).every(v => v.games >= VMIN),
        "最小 " + Math.min.apply(null, (DATA.view_players || []).map(v => v.games)));
  check("候选名单按同场局数从多到少排（方便找熟人）",
        (DATA.view_players || []).every((v, i, a) => i === 0 || a[i - 1].games >= v.games));
  // 主人不进候选名单：前端在下拉里单独放一个「数据主人」项（后端 _view 循环里
  // 显式 `if _pu == me: continue`），避免同一个人在下拉里出现两次。
  check("候选名单里不重复出现数据主人（主人单独一项）",
        !(DATA.view_players || []).some(v => v.pu === ME_PU));
  check("下拉第一项就是数据主人本人",
        vsel.options.length > 0 && vsel.options[0].value === ME_PU,
        vsel.options[0] ? vsel.options[0].value : "(没有 option)");
  check("默认选中数据主人",
        vsel.value === ME_PU &&
        /数据主人/.test(vsel.options[vsel.selectedIndex].textContent));
  check("默认视角下不显示「当前视角」提示",
        !/当前视角/.test(d.getElementById("summonerLine").textContent));
  check("默认视角下没有 chip-view",
        d.querySelectorAll("#heroChips .chip-view").length === 0);

  // 选一个同场 >=5 局的玩家当视角
  const other = (DATA.view_players || []).find(v => v.games >= 5 && v.pu !== ME_PU);
  check("候选里有同场 >=5 局的人", !!other, other ? `${other.n} ${other.games} 局` : "无");
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  if (other){
    // 视角玩家「有隐藏分」的那些局：用来验证对手质量确实把他排除了
    const gidTarget = Object.keys(DATA.roster).find(g =>
      rosterAll(DATA.roster[g]).some(p => p.pu === other.pu && p.mmr != null));
    check("能找到「视角玩家有分」的局（对手质量断言用）", !!gidTarget, String(gidTarget));

    vsel.value = other.pu;
    fire(vsel, "change");
    check("切换后 Hero 标出「当前视角」",
          /当前视角/.test(d.getElementById("summonerLine").textContent) &&
          d.getElementById("summonerLine").textContent.includes(other.n));
    check("切换后出现 chip-view 且写明同场局数",
          d.querySelectorAll("#heroChips .chip-view").length === 1 &&
          new RegExp("同场 " + other.games + " 局").test(d.getElementById("heroChips").textContent));
    check("视角说明文字更新", new RegExp(other.n).test(d.getElementById("viewNote").textContent));
    check("下拉选中态跟着走", vsel.value === other.pu);

    // 明细：标记换到那个玩家身上，且他自己变成不可点
    // （先切到「全部 156 局」，否则目标局可能不在默认的最近 20 局里）
    d.getElementById("allGamesBtn").click();
    const btnT = [...d.querySelectorAll("#gameBody tr .xbtn")]
      .find(x => x.dataset.gid === gidTarget);
    check("全部局模式下能找到目标局", !!btnT, String(gidTarget));
    if (btnT){
      btnT.click();
      const det2 = d.querySelector("#gameBody tr.det-row");
      const meRow = det2.querySelector("tr.me");
      check("明细里的「我」行换成视角玩家",
            !!meRow && meRow.querySelector(".nm").textContent.trim() === other.n,
            meRow ? meRow.querySelector(".nm").textContent : "没有 tr.me");
      check("视角玩家自己的名字不可点",
            !!meRow && !meRow.querySelector(".nm").classList.contains("pn"));
      check("「我方」标签改成「视角方」",
            /视角方/.test(det2.textContent) && !/我方/.test(det2.textContent));
      check("明细底部说明用的是「·视角」", /·视角/.test(det2.textContent));
      check("可点玩家名变成 9 个（排除的是视角玩家）",
            det2.querySelectorAll(".nm.pn").length === 9,
            "实际 " + det2.querySelectorAll(".nm.pn").length);
      btnT.click();
    }
    check("逐局表副标题写明「视角「X」」与对手质量排除 TA",
          new RegExp("视角「" + other.n + "」").test(d.getElementById("gameSub").textContent) &&
          /对手质量/.test(d.getElementById("gameSub").textContent),
          d.getElementById("gameSub").textContent);

    // 对手质量：排除对象换成视角玩家。
    // 注意不能拿「人数」当判据 —— 主人和视角玩家**都有分**，两种模式下都正好
    // 排除掉一个持分者，人数天然相同。判据要用**均值**：主人是当前锚点（1541），
    // 视角玩家是另一个数，换人后均值必然变。
    const rgs = rosterAll(DATA.roster[gidTarget]);
    const avgOf = keep => {
      const v = rgs.filter(keep).map(p => p.mmr);
      return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
    };
    const expOwn = avgOf(p => p.pu !== ME_PU && p.mmr != null);
    const expView = avgOf(p => p.pu !== other.pu && p.mmr != null);
    const nOwn = rgs.filter(p => p.pu !== ME_PU && p.mmr != null).length;
    const nView = rgs.filter(p => p.pu !== other.pu && p.mmr != null).length;
    check("两种视角排除的人不同 → 对手质量均值必然不同",
          Math.abs(expOwn - expView) > 1e-6,
          `主人 ${expOwn} / 视角 ${expView}`);
    const nowO = JSON.parse(w.eval(`JSON.stringify(oppMmr({gameId:${gidTarget}}))`));
    check("视角模式下「对手质量」= 排除视角玩家后的均值",
          !!nowO && nowO.n === nView && Math.abs(nowO.avg - expView) < 1e-6,
          `页面 n=${nowO && nowO.n} avg=${nowO && nowO.avg} / 应为 n=${nView} avg=${expView}`);

    // 设为视角的持久化（file:// 下 localStorage 可能不可用，代码必须容错）
    check("切换视角没引发 JS 报错（localStorage 容错生效）", a.errs.length === 0, a.errs.join(" | "));

    // 弹层里的「设为视角」按钮 + 回到主人
    d.querySelector("#gameBody tr .xbtn").click();
    d.querySelector("#gameBody tr.det-row .nm.pn").click();
    const vbtn = d.getElementById("pmViewBtn");
    check("弹层里有「设为视角」按钮", !!vbtn && !vbtn.disabled);
    const vpu = d.querySelector("#gameBody tr.det-row .nm.pn").dataset.pu;
    const vnm = d.querySelector("#gameBody tr.det-row .nm.pn").dataset.nm;
    if (vbtn && !vbtn.disabled){
      vbtn.click();
      check("点「设为视角」→ 弹层关闭且视角切到 TA",
            !modal.classList.contains("on") && vsel.value === vpu &&
            d.getElementById("summonerLine").textContent.includes(vnm),
            `vsel=${vsel.value} 期望=${vpu}`);
      check("换视角后 chip-view 显示新视角",
            d.querySelectorAll("#heroChips .chip-view").length === 1 &&
            d.getElementById("heroChips").textContent.includes(vnm));
      // 再打开一个弹层，应该出现「回到数据主人视角」
      d.querySelector("#gameBody tr .xbtn").click();
      d.querySelector("#gameBody tr.det-row .nm.pn").click();
      const bk = d.getElementById("pmViewBack");
      check("视角≠主人时弹层出现「回到数据主人视角」", !!bk);
      // 视角玩家自己的弹层：按钮必须是禁用态（明细里点不到自己，直接调 openPlayer）
      w.eval(`openPlayer(${JSON.stringify(vpu)}, ${JSON.stringify(vnm)})`);
      check("视角玩家自己的弹层里「设为视角」按钮是禁用的",
            !!d.getElementById("pmViewBtn") && d.getElementById("pmViewBtn").disabled,
            d.getElementById("pmViewBtn") ? "disabled=" + d.getElementById("pmViewBtn").disabled : "没有按钮");
      if (bk){
        bk.click();
        check("点「回到数据主人视角」→ 视角复位",
              !/当前视角/.test(d.getElementById("summonerLine").textContent) && vsel.value === ME_PU);
      }
    }
    d.getElementById("pmClose").click();
    d.querySelectorAll("#gameBody tr .xbtn").forEach(x => {
      if (x.textContent.includes("收起")) x.click();
    });
    d.querySelectorAll("#gameBody tr.det-row").forEach(r => r.remove());

    // 换回主人，确认对手质量复位
    vsel.value = ME_PU;
    fire(vsel, "change");
    check("切回数据主人视角 → Hero 不再显示「当前视角」",
          !/当前视角/.test(d.getElementById("summonerLine").textContent) && vsel.value === ME_PU);
    const backO = JSON.parse(w.eval(`JSON.stringify(oppMmr({gameId:${gidTarget}}))`));
    check("切回后「对手质量」把视角玩家算回来",
          !!backO && backO.n === nOwn, `页面 ${backO && backO.n} / 应为 ${nOwn}`);
    check("逐局表副标题的视角提示已清空",
          (d.getElementById("gameSub").textContent || "").length === 0,
          d.getElementById("gameSub").textContent);
  }

  /* ========= ⑥b 切视角 → 整块看板跟着重算（用户 2026-09-21 的核心诉求） =========
     用户原话：「并且你在我切换人之后数据看板要跟着变啊」。
     上一轮只改了三处标记（Hero / 明细「我」行 / 对手质量），KPI、轨迹、指标、
     逐局表、英雄榜、每日战绩全都还是主人的数 —— 这次改成**整页读视角数据层 V**。
     所以这里不是抽查一两处，而是把六个面板逐个快照比对。 */
  console.log("== ⑥b 切视角后整页数据跟着重算 ==");
  const PANELS = [
    ["#kpis", "KPI 校准区"],
    ["#chartBox", "隐藏分轨迹图"],
    ["#metGrid", "表现分指标"],
    ["#gameBody", "逐局战绩表"],
    ["#champList", "英雄榜"],
    ["#dailyList", "每日战绩"],
  ];
  const panelHTML = s => (d.querySelector(s) || {}).innerHTML || "";
  const otherB = (DATA.view_players || []).find(v => v.pu !== ME_PU);
  check("有可切换的视角玩家（用于整页重算断言）", !!otherB, otherB ? otherB.n : "无");
  if (otherB){
    // 指标窗口先切到「全部」，否则两人窗口长度不同会让「变了」这个判据失去意义
    d.getElementById("allGamesBtn").click();
    const ownSnap = PANELS.map(([s]) => panelHTML(s));
    const ownTh = d.getElementById("thScore").textContent;
    const ownTraj = d.getElementById("trajTitle").textContent;

    vsel.value = otherB.pu;
    fire(vsel, "change");

    PANELS.forEach(([s, nm], i) => {
      const now = panelHTML(s);
      check(`切视角后「${nm}」跟着重算（内容变了且非空）`,
            now.length > 0 && now !== ownSnap[i],
            now === ownSnap[i] ? "和主人视角一模一样（没跟着变）" : (now.length ? "" : "渲染成空"));
    });
    check("逐局表末列口径换掉：主人「局后估分」→ 视角「对局评分」",
          ownTh === "局后估分" && d.getElementById("thScore").textContent === "对局评分",
          `主人 ${ownTh} / 视角 ${d.getElementById("thScore").textContent}`);
    check("轨迹图标题换掉：主人「隐藏分轨迹」→ 视角「对局评分轨迹」",
          ownTraj === "隐藏分轨迹" &&
          /对局评分/.test(d.getElementById("trajTitle").textContent),
          d.getElementById("trajTitle").textContent);
    check("轨迹图不再挂参考锚点线（别人没有隐藏分序列）",
          !/参考锚点/.test(d.getElementById("trajAnchorLb").textContent),
          d.getElementById("trajAnchorLb").textContent);
    // 逐局表「全部 N 局」必须等于视角玩家真实出场局数
    const vN = Object.keys(DATA.roster)
      .filter(g => rosterAll(DATA.roster[g]).some(p => p.pu === otherB.pu)).length;
    check("逐局表「全部 N 局」= 视角玩家实际出场局数",
          d.getElementById("allGamesBtn").textContent.includes(String(vN)),
          `${d.getElementById("allGamesBtn").textContent} / 应为全部 ${vN} 局`);
    check("指标面板副标题点名视角玩家",
          (d.getElementById("metricsSub").textContent || "").includes(otherB.n),
          d.getElementById("metricsSub").textContent);
    check("「近10 vs 前20」固定口径对照在非主人视角下不显示（那是主人的数）",
          !/前20/.test(d.getElementById("metricsFoot").textContent),
          d.getElementById("metricsFoot").textContent);

    /* ---- 自己输 ID / 名字 ---- */
    const inp = d.getElementById("viewInp");
    const vmsg = d.getElementById("viewMsg");
    check("Hero 区有「自己输 ID」的输入框", !!inp);
    check("有切换按钮 #viewGo", !!d.getElementById("viewGo"));
    const enter = el => el.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // ① 输主人的名字 → 切回主人
    inp.value = DATA.me_name;
    enter(inp);
    check("输数据主人的名字 → 切回主人视角",
          vsel.value === ME_PU && !/当前视角/.test(d.getElementById("summonerLine").textContent),
          `vsel=${vsel.value}`);
    check("切换成功后输入框自动清空", inp.value === "");

    // ② 输视角玩家的名字 → 切到 TA
    inp.value = otherB.n;
    enter(inp);
    check("输某个玩家的名字 → 切到 TA",
          vsel.value === otherB.pu &&
          d.getElementById("summonerLine").textContent.includes(otherB.n),
          `vsel=${vsel.value} 期望=${otherB.pu}`);

    // ③ 输 puuid 也能切（下拉里没有的人也能直达）
    inp.value = otherB.pu;
    enter(inp);
    check("输完整 puuid 也能切", vsel.value === otherB.pu);

    // ④ 名字片段：只输「名字（去掉 #后缀）」也要能切过去
    // ⚠️ 原来这里写死片段 "a"，只有候选名字里真含 "a" 时才成立 ——
    //    候选名单随数据主人变（小号那 3 个候选名字里一个 "a" 都没有），断言立刻变色。
    //    改成用**这个玩家自己的名字**（不带 #tag）当片段，与数据无关。
    const fragName = (otherB.n || "").replace(/#.*$/, "");
    inp.value = fragName;
    enter(inp);
    check("输名字（不带 #后缀）也能切过去（片段匹配不是白提示）",
          vsel.value === otherB.pu, `vsel=${vsel.value} 期望=${otherB.pu} 片段=${fragName}`);

    // 命中多人时只提示、不瞎猜（不静默切到第一个）。
    // ⚠️ 片段**不能写死**：候选名单随数据主人变，而且含 `#` 的输入会走精确匹配分支
    //    （how 不是「名字片段」），压根不会触发多人提示 —— 所以这里先从真实候选里
    //    找一段**确实命中 ≥2 人**的公共子串，找不到才跳过这条。
    const names = (DATA.view_players || []).map(p => p.n || "").filter(Boolean);
    const hit2 = s => names.filter(n => n.indexOf(s) >= 0).length >= 2;
    let frag2 = "";
    for (const L of [2, 3, 1]) {          // 先试 2~3 字，再退到单字；**跳过含 # 的**
      for (const a of names) {
        for (let i = 0; i + L <= a.length; i++) {
          const s = a.slice(i, i + L);
          if (s.indexOf("#") < 0 && hit2(s)) { frag2 = s; break; }
        }
        if (frag2) break;
      }
      if (frag2) break;
    }
    inp.value = frag2;
    enter(inp);
    check("片段命中多人时给出「匹配到 N 个人」提示（数据里找不到公共片段时本条不适用）",
          !frag2 || /匹配到\s*\d+\s*个人/.test(vmsg.textContent),
          `片段=${frag2 || "(未找到)"} msg=${vmsg.textContent || "(空)"}`);

    // ⑤ 查无此人：只提示、不动视角
    const keepPu = vsel.value;
    inp.value = "zzz_不存在的召唤师_zzz";
    enter(inp);
    check("输不存在的 ID → 提示没找到", /没找到/.test(vmsg.textContent), vmsg.textContent || "(空)");
    check("输不存在的 ID → 视角不动（不静默清空）", vsel.value === keepPu, vsel.value);
    check("查无此人时输入框内容保留（方便改字）", inp.value === "zzz_不存在的召唤师_zzz");

    // 收尾：切回主人，别把后面 ⑦⑧⑨ 的断言带偏
    vsel.value = ME_PU;
    fire(vsel, "change");
    check("整页重算不引发 JS 报错", a.errs.length === 0, a.errs.join(" | "));
  }

  /* ================= ⑦ 轨迹 + 指标 显示范围可调 ================= */
  console.log("== ⑦ 隐藏分轨迹 + 表现分指标 显示范围可调 ==");
  const tCtrl = d.getElementById("trajCtrl"), mCtrl = d.getElementById("metCtrl");
  check("轨迹图有范围选择器", !!tCtrl);
  check("指标面板有范围选择器", !!mCtrl);
  const tBtns = [...(tCtrl ? tCtrl.querySelectorAll(".tbtn[data-tn]") : [])];
  const mBtns = [...(mCtrl ? mCtrl.querySelectorAll(".tbtn[data-mn]") : [])];
  check("轨迹 4 档（20/50/100/全部）", tBtns.length === 4, "实际 " + tBtns.length);
  check("指标 6 档（10/20/30/50/100/全部）", mBtns.length === 6, "实际 " + mBtns.length);
  check("轨迹默认「全部」", !!tCtrl.querySelector(".tbtn.on") && tCtrl.querySelector(".tbtn.on").dataset.tn === "0");
  check("指标默认「最近 30 局」", !!mCtrl.querySelector(".tbtn.on") && mCtrl.querySelector(".tbtn.on").dataset.mn === "30");
  check("轨迹「全部」按钮写明总局数",
        new RegExp("全部 " + DATA.traj.length + " 局").test(d.getElementById("trajAllBtn").textContent),
        d.getElementById("trajAllBtn").textContent);
  check("指标「全部」按钮写明总局数",
        new RegExp("全部 " + DATA.allseries.length + " 局").test(d.getElementById("metAllBtn").textContent),
        d.getElementById("metAllBtn").textContent);

  const trajPts = () => d.querySelectorAll("#chartBox svg circle").length - 1;  // 减掉「现在」的圈
  const nAll = trajPts();
  check("默认画出全部计分段的点", nAll >= DATA.traj.length - 1,
        `画出 ${nAll} / traj ${DATA.traj.length}`);
  tBtns.find(x => x.dataset.tn === "20").click();
  check("点「最近 20 局」→ 轨迹只剩 20 个点", trajPts() === 20, "实际 " + trajPts());
  // ⚠️ 轨迹只有 20 个点时，「最近 20 局」与「全部」等价 → 文案会写成「显示全部」。
  //    原来写死「显示最近 20 局」，是按大号（轨迹 122 局）的形状写的，小号数据一来就红。
  const _tsub = d.getElementById("trajSub").textContent;
  const _allEq20 = DATA.traj.length <= 20;
  check("轨迹说明写明「显示最近 20 局」（轨迹不足 20 局时等价于「全部」）",
        /显示最近 20 局/.test(_tsub) || (_allEq20 && /显示全部/.test(_tsub)), _tsub);
  // 备注只在「真的被截断」时才写（看板 `tnote.textContent = shown < total ? ... : ""`）。
  // 轨迹正好 20 局时点「最近 20 局」= 全部，没有截断 → 备注本来就该是空的。
  const _tnote = d.getElementById("trajNote").textContent;
  check("轨迹范围备注与「是否真截断」一致（不足 20 局时无可截断内容 → 空）",
        _allEq20 ? _tnote === "" : /最近 20 局/.test(_tnote),
        `traj=${DATA.traj.length} note=[${_tnote}]`);
  tBtns.find(x => x.dataset.tn === "0").click();
  check("切回「全部」→ 点数恢复", trajPts() === nAll, `实际 ${trajPts()} / ${nAll}`);

  const cardVal = lb => {
    const c = [...d.querySelectorAll("#metGrid .met-card")]
      .find(x => x.querySelector(".lb").textContent.includes(lb));
    return c ? parseFloat(c.querySelector(".big").textContent) : NaN;
  };
  const A = DATA.allseries;
  // 2026-09-27 重锚：综合分锚点从 62.3 改成 64.9，且改成从 DATA 读，不再写死。
  const CANC = Number(DATA.anchors.composite_anchor.value);
  check("默认 30 局：胜率 = 后端 metrics.last30.win_rate",
        cardVal("胜率") === DATA.metrics.last30.win_rate,
        `${cardVal("胜率")} vs ${DATA.metrics.last30.win_rate}`);
  check(`默认 30 局：综合评分 = ${CANC}（锚点定义）`,
        cardVal("综合评分") === CANC, String(cardVal("综合评分")));
  mBtns.find(x => x.dataset.mn === "10").click();
  check("切到 10 局：胜率 = 后端 metrics.last10.win_rate",
        cardVal("胜率") === DATA.metrics.last10.win_rate,
        `${cardVal("胜率")} vs ${DATA.metrics.last10.win_rate}`);
  check("切到 10 局：综合评分 = 后端 composite10",
        cardVal("综合评分") === DATA.metrics.composite10,
        `${cardVal("综合评分")} vs ${DATA.metrics.composite10}`);
  check("切到 10 局：标题跟着改", /近 10 局均值/.test(d.getElementById("metTitle").textContent),
        d.getElementById("metTitle").textContent);
  check("切到 10 局：趋势图重画", !!d.querySelector("#trendBox svg"));
  mBtns.find(x => x.dataset.mn === "0").click();
  check("切到「全部」：胜率 = 全部 156 局独立重算值",
        cardVal("胜率") === meanOf(A).win_rate,
        `${cardVal("胜率")} vs ${meanOf(A).win_rate}`);
  check("切到「全部」：标题写「全部 … 均值」", /^全部/.test(d.getElementById("metTitle").textContent),
        d.getElementById("metTitle").textContent);
  check("切到「全部」：综合评分 = 独立重算值",
        cardVal("综合评分") === compOf(A, DATA.anchors.composite_formula),
        `${cardVal("综合评分")} vs ${compOf(A, DATA.anchors.composite_formula)}`);
  mBtns.find(x => x.dataset.mn === "30").click();
  check("切回 30 局：胜率复位", cardVal("胜率") === DATA.metrics.last30.win_rate);

  /* ================= ⑧ 明细：三项最高高亮 ================= */
  console.log("== ⑧ 明细里本局 KDA/伤害/承伤 最高者高亮 ==");
  const gidA = d.querySelector("#gameBody tr .xbtn").dataset.gid;
  d.querySelector("#gameBody tr .xbtn").click();
  const detA = d.querySelector("#gameBody tr.det-row");
  const trsA = [...detA.querySelectorAll(".det-team tbody tr")];
  check("明细仍是 10 行", trsA.length === 10, "实际 " + trsA.length);
  const nmOf = tr => tr.querySelector(".nm").textContent.trim();
  const topNames = cls => trsA.filter(tr => tr.querySelector("." + cls)).map(nmOf);
  const allA = rosterAll(DATA.roster[String(gidA)]);
  const expKda = allA.filter(p => kdaOf(p) === Math.max(...allA.map(kdaOf))).map(p => p.n).sort();
  const expDmg = allA.filter(p => p.dmg === Math.max(...allA.map(x => x.dmg))).map(p => p.n).sort();
  const expTak = allA.filter(p => p.taken === Math.max(...allA.map(x => x.taken))).map(p => p.n).sort();
  check("KDA 最高的玩家被标出来", topNames("hi-kda").sort().join("|") === expKda.join("|"),
        `页面 ${topNames("hi-kda").join(",")} / 应为 ${expKda.join(",")}`);
  check("伤害最高的玩家被标出来", topNames("hi-dmg").sort().join("|") === expDmg.join("|"),
        `页面 ${topNames("hi-dmg").join(",")} / 应为 ${expDmg.join(",")}`);
  check("承伤最高的玩家被标出来", topNames("hi-taken").sort().join("|") === expTak.join("|"),
        `页面 ${topNames("hi-taken").join(",")} / 应为 ${expTak.join(",")}`);
  check("三项用三种不同颜色区分（.hi-kda/.hi-dmg/.hi-taken 各自有 color）",
        ["hi-kda", "hi-dmg", "hi-taken"].every(c => new RegExp("\\." + c + "\\{[^}]*color").test(styleBlock)),
        "看板 style 块里缺某个颜色的定义");
  check("三种颜色互不相同",
        new Set(["--top-kda", "--top-dmg", "--top-taken"].map(v => {
          const m = styleBlock.match(new RegExp(v.replace(/-/g, "\\-") + ":\\s*(#[0-9a-fA-F]{3,8})"));
          return m ? m[1].toLowerCase() : null;
        })).size === 3);
  // ⚠️ 2026-09-21 返工：用户要求「只把最高的数标注颜色」「恢复原样」。
  //   任何额外装饰（角标 / 名字后标签 / 单元格底色 / 边框 / 加粗单元格）
  //   都会把列挤宽、把明细表顶出横向滚动 —— 这里逐条钉死「一个都不许有」。
  check("没有「最高」角标（.top-tag 已删）",
        detA.querySelectorAll(".top-tag").length === 0,
        "实际 " + detA.querySelectorAll(".top-tag").length);
  check("没有给单元格加 class 装饰（td.top-* 已删）",
        detA.querySelectorAll("td.top-kda, td.top-dmg, td.top-taken").length === 0,
        "实际 " + detA.querySelectorAll("td.top-kda, td.top-dmg, td.top-taken").length);
  check("名字后面没挂彩色标签（.tt / .tt-* 已删）",
        detA.querySelectorAll(".tt, .tt-kda, .tt-dmg, .tt-taken").length === 0,
        "实际 " + detA.querySelectorAll(".tt, .tt-kda, .tt-dmg, .tt-taken").length);
  // ⚠️ 2026-09-22 换判据：原来断言的是「min-width 回到 560」，那是**反向**的 ——
  //   正是 `min-width:560px` 让内层表不能收缩，叠加 `.det-wrap` 轨道 `1fr` 的隐含
  //   最小宽度（= 内容最小宽度）后撑破容器，顶出横向滚动条、右侧列被截断
  //   （用户反馈「又显示不全了，拖条很影响观看体验」）。
  //   现在守的是**真正的不变量**：明细表用固定布局铺满容器，且**不存在**会撑宽它的 min-width。
  check("明细表用固定布局铺满容器，且没有会撑宽的 min-width（横向滚动条的元凶）",
        /\.det-team table\{[^}]*table-layout:fixed/.test(styleBlock) &&
        /\.det-team table\{[^}]*min-width:0/.test(styleBlock) &&
        !/\.det-team table\{[^}]*min-width:560px/.test(styleBlock),
        (styleBlock.match(/\.det-team table\{[^}]*\}/) || [""])[0]);
  // ⚠️ 这里断言的是**上界**，不是「至少 3 人」（2026-09-22 修）。
  //
  // 原来写的是 `size >= 3`，那是把「三种最高必然落在 3 个不同玩家身上」当成了事实 ——
  // 其实不是：一局里同一个玩家完全可以同时拿 KDA 最高和伤害最高（肥起来的 C 位就是这样），
  // 那样最高只落在 2 个人身上。实测 2026-09-22 抓进 3 局新数据后，战绩表第一行换了一局，
  // 那局正好是「一人兼 KDA+伤害最高」，`>=3` 就红了 —— 而功能本身完全正确。
  // 这条断言真正要守的不变量是「**不会人人都有**」：只有 3 个项目，所以最多 3 人；
  // 当年踩的 bug 是「全 0 时 10 个人全被标成最高」，那会得到 10。所以上界才是对的判据。
  // 逐项的**正确性**由上面三条（对比 expKda/expDmg/expTak）保证，这里只兜人数。
  const _topSet = new Set([...topNames("hi-kda"), ...topNames("hi-dmg"), ...topNames("hi-taken")]);
  check("三项最高不会人人都有（人数 ≤ 3，且至少 1 人）",
        _topSet.size >= 1 && _topSet.size <= 3,
        "标记人数 " + _topSet.size);
  // ⚠️ 2026-09-22 用户要求：**K/D/A 一律普通颜色**，全站只有明细表里 KDA 最高的那一项带高亮。
  //   原来有一套自动配色（0 死 = 金 / (K+A) ≥ 2×D = 绿 / 否则红），用户截图圈出两处问
  //   「这两个颜色标注是干嘛的」—— 属于无声的噪音标注。现在全部拆掉：
  //   最高那个靠 **class `.hi-kda`** 上色（CSS），不再有任何内联 `color:`。
  const kdaInline = trsA.filter(tr => {
    const sp = tr.querySelector(".d-kda span");
    return sp && /color\s*:/.test((sp.getAttribute("style") || ""));
  });
  check("明细里 K/D/A 只由 .hi-kda 上色，无人带内联配色（其余一律普通色）",
        kdaInline.length === 0, "带内联配色的行数 " + kdaInline.length);
  const kdInline = [...d.querySelectorAll("#gameBody td.kd")]
    .filter(td => /color\s*:/.test(td.getAttribute("style") || ""));
  check("逐局战绩表的 K/D/A 不做自动配色（一律普通色）",
        kdInline.length === 0, "带内联配色的 K/D/A 单元格 " + kdInline.length);
  const dayInline = [...d.querySelectorAll("#dailyList .day-wl *")]
    .filter(e => /color\s*:/.test(e.getAttribute("style") || ""));
  check("每日战绩的 KDA 也不做自动配色（一律普通色）",
        dayInline.length === 0, "带内联配色的元素 " + dayInline.length);
  check("明细底部有配色图例", !!detA.querySelector(".det-legend"));
  check("图例用的也是 hi-* 那套色（和数字同色）",
        ["hi-kda", "hi-dmg", "hi-taken"].every(c => !!detA.querySelector(".det-legend ." + c)));

  console.log("== ⑧b 明细新增「对局评分」列 ==");
  const headA = detA.querySelector(".det-team thead").textContent;
  check("表头含「对局评分」", /对局评分/.test(headA), headA);
  const compCells = [...detA.querySelectorAll(".comp-cell .comp-v")].map(e => parseFloat(e.textContent));
  check("10 个人都有对局评分", compCells.length === 10, "实际 " + compCells.length);
  const expComp = allA.map(p => Math.round(p.comp));
  check("对局评分与下发的 roster.comp 一致",
        compCells.join(",") === expComp.join(","),
        `${compCells.join(",")} vs ${expComp.join(",")}`);
  check("对局评分有分布（不是人人一样）", new Set(compCells).size >= 5, "不同值 " + new Set(compCells).size);
  const meCompCell = detA.querySelector("tr.me .comp-v");
  const meSeq = DATA.mine.findIndex(g => String(g.gameId) === String(gidA)) + 1;
  const rowComp = (DATA.allseries.find(r => r.seq === meSeq) || {}).comp;
  check("「我」的对局评分 = 趋势图里那一局的综合分",
        !!meCompCell && parseFloat(meCompCell.textContent) === Math.round(rowComp),
        `${meCompCell && meCompCell.textContent} vs ${rowComp}`);
  d.querySelector("#gameBody tr .xbtn").click();

  /* ================= ⑨ 弹层：对局评分 + 联手/交手分开 ================= */
  console.log("== ⑨ 弹层「对局评分」+ 联手/交手分开 ==");
  // 找一个既当过队友又当过对手的人（数据里现在有 2 个）
  const stat = {};
  Object.keys(DATA.roster).forEach(g => {
    const rg = DATA.roster[g];
    const vT = (rosterAll(rg).find(p => p.pu === ME_PU) || {}).t;
    let vt = null;
    rg.teams.forEach(t => { if (t.players.some(p => p.pu === ME_PU)) vt = t.t; });
    rg.teams.forEach(t => t.players.forEach(p => {
      if (!p.pu || p.pu === ME_PU) return;
      const e = stat[p.pu] || (stat[p.pu] = {ally: 0, foe: 0, n: p.n});
      if (t.t === vt) e.ally++; else e.foe++;
    }));
  });
  const both = Object.keys(stat).filter(pu => stat[pu].ally > 0 && stat[pu].foe > 0);
  check("数据里存在「既联手又交手」的玩家（用于验证分开显示）", both.length >= 1,
        "找到 " + both.length + " 个");
  if (both.length){
    const pu = both[0];
    w.eval(`openPlayer(${JSON.stringify(pu)}, ${JSON.stringify(stat[pu].n)})`);
    const b2 = d.getElementById("pmBody");
    const secs = [...b2.querySelectorAll(".pm-sec > h4")].map(h => h.textContent);
    check("有「与你联手的记录（同队）」区块", secs.some(s => /与你联手的记录/.test(s)), secs.join(" / "));
    check("有「与你交手的记录（敌对）」区块", secs.some(s => /与你交手的记录/.test(s)), secs.join(" / "));
    const heads = [...b2.querySelectorAll(".pm-tbl thead")].map(h => h.textContent);
    check("弹层表格列是「对局评分」不是「TA 隐藏分」",
          heads.every(h => /对局评分/.test(h) && !/TA 隐藏分/.test(h)), heads.join(" | "));
    check("两张表的行数 = 联手/交手局数",
          [...b2.querySelectorAll(".pm-tbl tbody")].map(t => t.querySelectorAll("tr").length).join(",") ===
          [stat[pu].ally, stat[pu].foe].join(","),
          [...b2.querySelectorAll(".pm-tbl tbody")].map(t => t.querySelectorAll("tr").length).join(",") +
          " 应为 " + [stat[pu].ally, stat[pu].foe].join(","));
    check("KPI 里有「与你联手」和「与你交手」",
          /与你联手/.test(b2.textContent) && /与你交手/.test(b2.textContent));
    check("KPI 里保留「TA 的隐藏分」（全网络刻度）", /TA 的隐藏分/.test(b2.textContent));
    check("KPI 里有「对局评分（均值）」", /对局评分（均值）/.test(b2.textContent));
    // 对局评分数值必须等于 roster 里的 comp
    const exp = [];
    Object.keys(DATA.roster).forEach(g => {
      const rg = DATA.roster[g];
      rg.teams.forEach(t => t.players.forEach(p => { if (p.pu === pu) exp.push(Math.round(p.comp)); }));
    });
    const got = [...b2.querySelectorAll(".comp-cell .comp-v")].map(e => parseFloat(e.textContent));
    check("弹层对局评分 = 下发的 roster.comp（逐局）",
          got.length === exp.length && got.every((v, i) => exp.includes(v)),
          `页面 ${got.length} 个 / 应为 ${exp.length} 个`);
    check("弹层表格里没有「隐藏分」列头（只有 KPI 里保留）",
          heads.every(h => !/隐藏分/.test(h)));
    d.getElementById("pmClose").click();
  }

  /* ================= ⑩ 前端公式与后端一致 ================= */
  console.log("== ⑩ 前端窗口均值/综合分与后端公式一致 ==");
  const F = DATA.anchors.composite_formula;
  const mine30 = meanOf(A.slice(-30));
  const keys = ["win_rate", "kda", "dmg_share", "taken_share", "heal_share",
                "gold_share", "dmg_conv", "kill_part"];
  const bad = keys.filter(k => mine30[k] !== DATA.metrics.last30[k]);
  check("最近 30 局的 8 项指标逐字段等于后端 metrics.last30",
        bad.length === 0, bad.map(k => `${k}: ${mine30[k]} vs ${DATA.metrics.last30[k]}`).join("; "));
  check(`comp(最近 30 局) = ${CANC}（锚点定义）`,
        compOf(A.slice(-30), F) === CANC, String(compOf(A.slice(-30), F)));
  check("comp(最近 10 局) = 后端 composite10",
        compOf(A.slice(-10), F) === DATA.metrics.composite10,
        `${compOf(A.slice(-10), F)} vs ${DATA.metrics.composite10}`);
  check("comp(前 20 局) = 后端 composite20",
        compOf(A.slice(-30, -10), F) === DATA.metrics.composite20,
        `${compOf(A.slice(-30, -10), F)} vs ${DATA.metrics.composite20}`);
  const badOne = A.filter(r => compOf([r], F) !== r.comp);
  check("每一局的单局 comp 都能用同一条曲线重算出来（0 处偏差）",
        badOne.length === 0, badOne.slice(0, 3).map(r => `#${r.seq} ${compOf([r], F)} vs ${r.comp}`).join("; "));
  check("series 是 allseries 的尾 30 局（口径同源）",
        DATA.series.length === 30 &&
        DATA.series.every((r, i) => r.seq === A[A.length - 30 + i].seq));
  // 第九轮（2026-09-28）：comp 现在是「平移 + 统治力加成」之后按 COMPOSITE_CLAMP
  // 夹紧的，所以合法区间是 [38, 100] 而不是开区间 (0,100) ——
  // **100 是可达的**，所以不能再写 `>= 100` 当越界。
  const CLV = (F.clamp && F.clamp.length === 2) ? F.clamp : [0, 100];
  const badAll = A.filter(r => r.comp < Number(CLV[0]) - 0.05 || r.comp > Number(CLV[1]) + 0.05);
  check(`全部单局 comp 都在夹紧区间 [${CLV[0]}, ${CLV[1]}]`, badAll.length === 0,
        badAll.length + " 局越界");

  /* ================= ⑪ 换数据主人入口（2026-09-22） ================= */
  // 用户要求「看板视角是谁，抓取/估算就算谁」。入口两处 —— 视角栏一个按钮、
  // 玩家弹层一个按钮；都只该在**桌面版**出现，且都要走操作栏那份「点两次确认」。
  console.log("== ⑪ 换数据主人入口 ==");
  const voA = d.getElementById("viewOwner");
  check("视角栏有「以 TA 为数据主人」按钮（DOM 在）", !!voA);
  check("浏览器模式里它隐藏（没有 pywebview）",
        voA && voA.style.display === "none", voA && voA.style.display);
  const voB = db.getElementById("viewOwner");
  check("桌面模式下、视角=数据主人时也隐藏",
        voB && voB.style.display === "none", voB && voB.style.display);
  check("桌面版导出 window.hdStartOwner（视角栏 / 弹层共用同一个入口）",
        typeof b.dom.window.hdStartOwner === "function");
  const vpB = JSON.parse(b.dom.window.eval("JSON.stringify((DATA.view_players||[])[0]||null)"));
  check("有可切换的视角候选（view_players 非空）", !!vpB);
  if (vpB){
    b.dom.window.eval(`setView(${JSON.stringify(vpB.pu)}, ${JSON.stringify(vpB.n)})`);
    check("桌面模式切到别人后按钮自己冒出来（靠 hd-view-changed 事件）",
          voB.style.display !== "none", voB.style.display);
    check("按钮文案带上那个人的名字", voB.textContent.indexOf(vpB.n) >= 0, voB.textContent);
    b.dom.window.eval("setView(" + JSON.stringify(ME_PU) + "," +
      JSON.stringify(b.dom.window.eval("ME_NAME")) + ")");
    check("切回数据主人后按钮又收起", voB.style.display === "none", voB.style.display);
  }
  // 弹层里的那个按钮：浏览器模式必须禁用，桌面版可用
  d.querySelector("#gameBody tr .xbtn").click();
  const nmA = d.querySelector("#gameBody tr.det-row .nm.pn");
  if (nmA){
    nmA.click();
    const poA = d.getElementById("pmOwnerBtn");
    check("玩家弹层里有「以 TA 为数据主人」按钮", !!poA);
    check("浏览器模式里该按钮禁用（没有 pywebview）", poA && poA.disabled);
    d.getElementById("pmClose").click();
  } else {
    check("明细里有可点的玩家名（用于开弹层）", false);
  }
  db.querySelector("#gameBody tr .xbtn").click();
  const nmB = db.querySelector("#gameBody tr.det-row .nm.pn");
  if (nmB){
    nmB.click();
    const poB = db.getElementById("pmOwnerBtn");
    check("桌面模式里该按钮可用", poB && !poB.disabled);
    check("弹层按钮文案带那人名字",
          poB && poB.textContent.indexOf(nmB.textContent.trim()) >= 0, poB && poB.textContent);
    check("弹层里写清了「换的是数据主人、旧对局不会丢」",
          /数据主人/.test(db.getElementById("pmOwnerSt").textContent) ||
          /数据主人/.test(db.getElementById("pmBody").textContent));
    db.getElementById("pmClose").click();
  } else {
    check("桌面模式明细里有可点的玩家名", false);
  }

  console.log("\n== 运行时错误 ==");
  check("浏览器模式无 JS 报错", a.errs.length === 0, a.errs.join(" | "));
  check("桌面模式无 JS 报错", b.errs.length === 0, b.errs.join(" | "));

  console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
  process.exit(fail ? 1 : 0);
})().catch(e => {
  // 审查 M17：断言失败后继续解引用会把「红」变成崩溃、丢掉全部汇总。
  // 顶层兜底：崩溃也按失败退出并留下线索，而不是一串裸堆栈。
  console.error("✗ 未捕获异常（某条断言红后继续解引用？）:", e && (e.stack || e.message || e));
  process.exit(1);
});
