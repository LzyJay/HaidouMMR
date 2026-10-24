/* 海斗看板：桌面版操作栏门控验证
 *
 * 只验一件事：操作栏**只在桌面窗口（pywebview）里显示**，
 * 浏览器打开时不可见 —— 这保证了 44+11 条断言不被工具栏污染。
 *
 * 另附：按钮 / 输入框的 DOM 齐全性（界面改坏了这里会红）。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const HTML = path.resolve(__dirname, "..", "03_看板", "海斗隐藏分_数据看板.html");
const src = fs.readFileSync(HTML, "utf-8");

let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (extra ? "  → " + extra : "")); }
};

// 静默虚拟控制台：页面自身的报错由 _verify_dash.js 负责，这里不重复计
function load(withPywebview) {
  return new Promise(resolve => {
    const dom = new JSDOM(src, {
      runScripts: "dangerously",
      pretendToBeVisual: true,
      virtualConsole: new VirtualConsole(),
      url: "file:///" + HTML.replace(/\\/g, "/"),
      beforeParse(window) {
        if (withPywebview) {
          window.pywebview = {
            api: {
              last_anchor: () => Promise.resolve({ ok: false }),
              // 任务式接口：start 立刻返回，进度靠 progress 轮询
              start: () => Promise.resolve({ ok: true, job: 1, step_total: 5 }),
              progress: () => Promise.resolve({
                running: false, finished: false, frac: 0, elapsed: 0,
                eta: null, step: 0, step_total: 0, step_name: '',
                log: [], steps: [], ok: null, error: null,
              }),
              // 换数据主人（2026-09-22 新增接口）
              start_owner: (pu, nm, deep) => Promise.resolve({ ok: true, job: 2, step_total: 6 }),
              data_owner: () => Promise.resolve({ ok: true, puuid: 'ME', name: 'X#1', has_anchor: true }),
              recalc: () => Promise.resolve({ ok: true, steps: [] }),
              rebuild: () => Promise.resolve({ ok: true, steps: [] }),
              stop: () => Promise.resolve({ ok: true, stopping: true }),
              open_data_dir: () => Promise.resolve({ ok: true }),
            },
          };
        }
      },
    });
    setTimeout(() => resolve(dom), 600);
  });
}

(async () => {
  console.log("== ① 浏览器环境：操作栏必须不可见 ==");
  const a = await load(false);
  const da = a.window.document;
  const barA = da.getElementById("hdBar");
  check("hdBar 在 DOM 里（只是隐藏）", !!barA);
  check("hdBar 不带 .on → 不显示", barA && !barA.classList.contains("on"));
  check("注入标记 HD-TOOLBAR 只有 1 个（幂等）",
        (src.match(/<!--HD-TOOLBAR-->/g) || []).length === 1,
        "实际 " + (src.match(/<!--HD-TOOLBAR-->/g) || []).length);
  check("数据块 dash-data 未被污染", !!da.getElementById("dash-data"));
  check("看板主体仍在（KPI 卡片 = 6）",
        da.querySelectorAll("#kpis .kpi").length === 6,
        "实际 " + da.querySelectorAll("#kpis .kpi").length);

  console.log("== ② 桌面环境：操作栏必须显示且控件齐全 ==");
  const b = await load(true);
  const db = b.window.document;
  const barB = db.getElementById("hdBar");
  check("hdBar 带 .on → 显示", barB && barB.classList.contains("on"));
  check("三个按钮齐全（抓新局 / 深度抓取 / 数据目录）",
        ["hdBtnUpdate", "hdBtnCrawl", "hdBtnFolder"].every(id => !!db.getElementById(id)));
  check("主按钮是「抓新局并重算」",
        !!db.getElementById("hdBtnUpdate") &&
        db.getElementById("hdBtnUpdate").className.indexOf("primary") >= 0);
  check("中途停止按钮存在（hdBtnStop）", !!db.getElementById("hdBtnStop"));
  check("停止按钮默认隐藏（display:none，只在任务跑的时候出现）",
        db.getElementById("hdBtnStop") &&
        db.getElementById("hdBtnStop").style.display === "none",
        "实际 display=" + (db.getElementById("hdBtnStop") || {}).style.display);
  check("状态摘要区存在（hdMeta：锚点局 / 隐藏分）", !!db.getElementById("hdMeta"));
  check("面板已精简：锚点输入框 / 只重算 / 只重建 / 提示区都不在",
        !db.getElementById("hdAnchor") && !db.getElementById("hdAgo") &&
        !db.getElementById("hdBtnRecalc") && !db.getElementById("hdBtnRebuild") &&
        !db.getElementById("hdHint"));

  console.log("== ③ 进度条 DOM（用户要求：计算时显示进度与剩余时间）==");
  check("进度条容器存在", !!db.getElementById("hdProg"));
  check("进度条填充元素存在", !!db.getElementById("hdProgFill"));
  check("百分比文字存在", !!db.getElementById("hdProgPct"));
  check("剩余时间文字存在", !!db.getElementById("hdProgTime"));
  check("步骤名文字存在", !!db.getElementById("hdProgStep"));
  check("进度条默认隐藏（不带 .on）",
        db.getElementById("hdProg") &&
        !db.getElementById("hdProg").classList.contains("on"));
  check("输出区存在", !!db.getElementById("hdOut"));
  check("收起/展开按钮存在", !!db.getElementById("hdToggle"));

  console.log("== ③b 登录提示弹层（2026-09-22 新增：客户端没开时弹给用户看）==");
  const pop = db.getElementById("hdPopup");
  check("提示弹层存在（页面内覆盖层，不是原生 alert/confirm）", !!pop);
  check("弹层默认隐藏", pop && !pop.classList.contains("on"));
  check("弹层在 #hdBar **外面**（面板收起时也要能弹出来）",
        pop && pop.parentNode && pop.parentNode.id !== "hdBar",
        "父节点=" + (pop && pop.parentNode ? pop.parentNode.id : "?"));
  check("弹层有标题 / 正文 / 关闭按钮",
        !!db.getElementById("hdPopTitle") && !!db.getElementById("hdPopMsg") &&
        !!db.getElementById("hdPopOk"));
  check("样式只由 theme.css 给（页面内不许内联样式）",
        pop && !pop.getAttribute("style"), pop && pop.getAttribute("style"));

  console.log("== ③c 管理存档面板（2026-09-22 新增）==");
  check("「管理存档」按钮存在（hdBtnSaves）", !!db.getElementById("hdBtnSaves"));
  const sv = db.getElementById("hdSaves");
  check("存档面板存在且默认收起（不带 .on）", !!sv && !sv.classList.contains("on"));
  check("面板有列表容器 / 汇总 / 存档目录按钮",
        !!db.getElementById("hdSavesList") && !!db.getElementById("hdSavesSum") &&
        !!db.getElementById("hdSavesDir"));
  check("存档面板默认不占位（样式只由 theme.css 给）",
        sv && !sv.getAttribute("style"), sv && sv.getAttribute("style"));

  console.log("== ④ 换数据主人按钮（2026-09-22 新增）==");
  const bOwn = db.getElementById("hdBtnOwner");
  check("按钮存在（hdBtnOwner）", !!bOwn);
  check("视角=数据主人时隐藏（默认）", bOwn && bOwn.style.display === "none",
        "实际 display=" + (bOwn || {}).style.display);
  check("window.hdStartOwner 已导出（视角栏 / 弹层共用）",
        typeof b.window.hdStartOwner === "function");
  const vp = JSON.parse(b.window.eval("JSON.stringify((DATA.view_players||[])[0]||null)"));
  check("有可切换的视角候选（view_players 非空）", !!vp);
  if (vp) {
    b.window.eval("setView(" + JSON.stringify(vp.pu) + "," + JSON.stringify(vp.n) + ")");
    check("切到别人后按钮显示出来（靠 hd-view-changed 事件刷新，不用刷页面）",
          bOwn.style.display !== "none", "实际 display=" + bOwn.style.display);
    check("按钮文案带上那个人的名字", bOwn.textContent.indexOf(vp.n) >= 0, bOwn.textContent);
    // 第一次点只出警告（不启动任务），第二次才调 start_owner —— 两次点击确认
    let called = 0, args = null;
    b.window.pywebview.api.start_owner = function (pu, nm, deep) {
      called++; args = [pu, nm, deep];
      return Promise.resolve({ ok: true, job: 9, step_total: 6 });
    };
    bOwn.click();
    const warn = db.getElementById("hdOut").textContent;
    check("第一次点只出确认警告、不启动任务", called === 0, "called=" + called);
    check("警告里写清「换成谁 / 旧对局不会丢 / 没截图就自动给分」",
          warn.indexOf(vp.n) >= 0 && /不会丢/.test(warn) && /自动给分/.test(warn),
          warn.slice(0, 90));
    bOwn.click();
    check("再点一次才真的启动（start_owner 被调用）", called === 1, "called=" + called);
    check("带上 puuid / 名字 / deep 三个参数",
          !!args && args[0] === vp.pu && args[1] === vp.n && typeof args[2] === "boolean",
          JSON.stringify(args));
  }

  console.log("\n结果: " + pass + " 通过 / " + fail + " 失败");
  process.exit(fail ? 1 : 0);
})();
