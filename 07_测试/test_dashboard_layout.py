# -*- coding: utf-8 -*-
"""看板**真实排版**回归测试 —— 用真 Chromium 实测量各盒子的 scrollWidth vs clientWidth。

为什么必须有它（2026-09-22 用户反馈「为什么又显示不全了，拖条很影响观看体验」）：
jsdom（`_verify_*.js` 用的）**没有排版引擎**，所有 `scrollWidth == clientWidth`，
所以「表格被撑破容器、右侧列被截断、出现横向滚动条」这类问题**一条都测不出来**。
必须用真浏览器量。

实测抓到的三个真问题（当时的报错链）：
  ① `.games td{white-space:nowrap}` 命中了明细行的 `colspan` 单元格，
     而 `white-space` 可继承 → `.det-foot` 那段长说明被压成单行 1558px → 撑破 `.det-wrap`
     → 顶出外层 `overflow:auto` 的横向滚动条；
  ② `.det-team table{min-width:560px}` 让内层表不能收缩，叠加 `.det-wrap` 轨道 `1fr`
     的隐含最小宽度（= 内容最小宽度）→ **grid blowout**（实测两列被算成 197px/1569px）；
  ③ `.det-team .nm` 是 `<span>`（行内元素），`max-width`/`overflow:hidden`/`text-overflow`
     对它**不生效**，长名字（加上 `#后缀`后更长）把列撑宽。

跑法：`python 07_测试\\test_dashboard_layout.py`
需要 Playwright 的 Chromium（`%LOCALAPPDATA%\\ms-playwright\\chromium_headless_shell-*`）；
找不到就**跳过**（打印提示、退出码 0），不会把没装浏览器的机器判成失败。
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "03_看板" / "海斗隐藏分_数据看板.html"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _check  # noqa: E402  断言计数器唯一真身（2026-10-03）

# 用户实际会遇到的宽度区间都要覆盖：
#   1000 最小窗宽 / 1085 用户截图那台 / 1281~1366 双列最窄的一段（最容易溢出）
#   / 1450 / 1920 宽屏
VIEWPORTS = [(1000, 700), (1085, 800), (1281, 900), (1290, 900),
             (1366, 900), (1450, 900), (1920, 1080)]

PROBE = """
<div id="__probe" style="display:none">pending</div>
<script>
(function(){
  function meas(){
    var d=document, o={vw:innerWidth, over:[], paren:0, hiKda:0, hiKdaOnKda:0};
    var scope=d.querySelector("tr.det-row>td");
    if(scope){
      scope.querySelectorAll("*").forEach(function(el){
        if(getComputedStyle(el).display==="none") return;
        var ov=el.scrollWidth-el.clientWidth;
        if(ov>2 && el.clientWidth>0) o.over.push({
          tag:el.tagName.toLowerCase(), cls:(typeof el.className==="string"?el.className:""),
          over:ov, cw:el.clientWidth, sw:el.scrollWidth});
      });
    }
    function chk(el){ if(!el) return null; return {ow:el.offsetWidth, sw:el.scrollWidth, over:el.scrollWidth-el.offsetWidth}; }
    var g=d.getElementById("gameTable");
    o.games=chk(g);
    o.outer=chk(g?g.parentElement:null);
    o.doc={sw:d.documentElement.scrollWidth, cw:d.documentElement.clientWidth};
    // KDA 后面的括号值必须已删除
    d.querySelectorAll(".det-team td.d-kda, #gameBody td.kd").forEach(function(e){
      if(/\\(\\d+\\.\\d+\\)/.test(e.textContent||"")) o.paren++;
    });
    // 「KDA 最高」的紫色高亮应落在 k/d/a 上
    d.querySelectorAll(".det-team .hi-kda").forEach(function(e){
      o.hiKda++;
      var inner=e.querySelectorAll("b").length;
      if(inner>=3) o.hiKdaOnKda++;
    });
    // 2026-09-22 用户要求：K/D/A 一律普通颜色，只有明细里 KDA 最高的那一项带色。
    // 配色是**计算样式**，jsdom 测不出来，只能真浏览器量。
    var normal=getComputedStyle(d.body).color;
    o.normal=normal;
    o.kdColors={};
    d.querySelectorAll("#gameBody td.kd").forEach(function(e){
      var c=getComputedStyle(e).color; o.kdColors[c]=(o.kdColors[c]||0)+1;
    });
    o.detKdaColors={}; o.kdaPlain=0; o.kdaColored=0;
    d.querySelectorAll(".det-team td.d-kda span").forEach(function(e){
      var c=getComputedStyle(e).color; o.detKdaColors[c]=(o.detKdaColors[c]||0)+1;
      if(c===normal) o.kdaPlain++; else o.kdaColored++;
    });
    var p=d.getElementById("__probe"); p.textContent="PROBE="+JSON.stringify(o);
  }
  function go(){
    var xb=document.querySelector("#gameBody tr .xbtn");
    if(xb) xb.click();
    setTimeout(meas, 500);
  }
  if(document.readyState==="complete") setTimeout(go,700);
  else window.addEventListener("load", function(){setTimeout(go,700)});
})();
</script>
"""


def find_browser():
    base = Path.home() / "AppData" / "Local" / "ms-playwright"
    if not base.is_dir():
        return None
    for p in sorted(base.glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe")):
        return p
    for p in sorted(base.glob("chromium-*/chrome-win64/chrome.exe")):
        return p
    return None


def main() -> int:
    if not HTML.is_file():
        print("✗ 找不到看板 %s" % HTML)
        return 1
    browser = find_browser()
    if not browser:
        print("⏭ 跳过：本机没有 Playwright Chromium（%LOCALAPPDATA%\\ms-playwright）。")
        print("   这条测试只在装了浏览器的机器上跑，不算失败。")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="hd_layout_"))
    pf = tmp / "probe.html"
    # ⚠️ 插在**最后一个**闭合 body 之前，别用第一个出现处 —— 页面 JS 里只要有一处
    #    注释/字符串提到这个标签，探针就会被插进注释中间、把整段脚本打断（2026-09-22 真踩过）。
    _src = HTML.read_text(encoding="utf-8")
    _k = _src.rfind("</body>")
    if _k < 0:
        print("✗ 看板 HTML 里找不到 </body>，无法注入探针")
        return 1
    pf.write_text(_src[:_k] + PROBE + _src[_k:], encoding="utf-8")

    # 计数器必须是**函数内局部变量**（本函数有多个 return，不能用模块全局），
    # 所以用 _check.Counter 的局部实例而不是模块级单例。
    _cnt = _check.Counter()
    ck = _cnt.check

    print("浏览器: %s" % browser)
    print("看板  : %s" % HTML)
    for w, h in VIEWPORTS:
        r = subprocess.run([str(browser), "--headless", "--disable-gpu", "--no-sandbox",
                            "--hide-scrollbars", "--force-device-scale-factor=1",
                            "--window-size=%d,%d" % (w, h), "--virtual-time-budget=9000",
                            "--dump-dom", pf.as_uri()],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=240)
        m = re.search(r"PROBE=(\{.*?\})</div>", r.stdout or "", re.S)
        print("\n== 视口 %dx%d ==" % (w, h))
        if not m:
            ck("%d: 探针跑通" % w, False, (r.stderr or "")[-160:])
            continue
        o = json.loads(m.group(1))
        ck("%d: 明细区内无元素溢出" % w, not o["over"], o["over"][:3])
        ck("%d: 战绩表不溢出" % w, (o["games"] or {}).get("over", 1) <= 2, o["games"])
        ck("%d: 战绩表外层滚动容器不溢出（无横向滚动条）" % w,
           (o["outer"] or {}).get("over", 1) <= 2, o["outer"])
        ck("%d: 整页不横向滚动" % w, o["doc"]["sw"] <= o["doc"]["cw"] + 1, o["doc"])
        ck("%d: K/D/A 后面没有残留的 (kda) 括号值" % w, o["paren"] == 0, "残留 %d 处" % o["paren"])
        ck("%d: KDA 最高的高亮落在 k/d/a 上" % w, o["hiKda"] >= 1 and o["hiKdaOnKda"] == o["hiKda"],
           "hi-kda=%d / 命中 k/d/a 的 %d" % (o["hiKda"], o["hiKdaOnKda"]))
        # 2026-09-22 用户要求「K/D/A 除了详情里高亮的那一项都用普通颜色」：
        #   原来有一套自动配色（0 死金 / (K+A)≥2D 绿 / 否则红），满屏花里胡哨。
        #   这里量的是**计算色**，jsdom 量不出来。
        ck("%d: 逐局战绩表 K/D/A 一律普通色" % w,
           set(o["kdColors"].keys()) == {o["normal"]}, o["kdColors"])
        ck("%d: 明细里 K/D/A 只有最高那一项带色、其余普通色" % w,
           o["kdaColored"] == o["hiKda"] and len(o["detKdaColors"]) <= 2,
           "带色 %d / hi-kda %d / 色值分布 %s" % (o["kdaColored"], o["hiKda"], o["detKdaColors"]))

    # 本文件用 return 而不是 sys.exit（外层 __main__ 决定退出码），
    # 所以调 Counter 的两个非退出方法。
    _cnt.print_summary()
    return _cnt.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
