# -*- coding: utf-8 -*-
"""把桌面版操作工具栏注入看板 HTML。

用法
----
    python 06_桌面版\\apply_toolbar.py              # 注入
    python 06_桌面版\\apply_toolbar.py --restore    # 移除工具栏
    python 06_桌面版\\apply_toolbar.py --dry-run    # 只看会改什么

设计说明
--------
- 注入位置是 </body> 之前，只在 <body> 里加一段 DOM + 一段 JS。
- 工具栏默认 display:none，只有检测到 window.pywebview 才显示。
  所以用浏览器直接打开看板时它不出现，jsdom 断言也完全不受影响。
- 幂等：已注入过就跳过，不会重复叠加。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "03_看板" / "海斗隐藏分_数据看板.html"
MARK = "<!--HD-TOOLBAR-->"

TOOLBAR_HTML = MARK + """
<div id="hdBar">
  <div id="hdBarPanel">
    <h3>数据操作</h3>
    <div class="hd-btns">
      <button class="hd-btn primary" id="hdBtnUpdate">抓新局并重算</button>
      <!-- 换数据主人：默认隐藏，只在「看板视角 ≠ 数据主人」时出现（见 refreshOwnerBtn） -->
      <button class="hd-btn" id="hdBtnOwner" style="display:none"></button>
    </div>
    <div class="hd-btns hd-btns-sm">
      <button class="hd-btn" id="hdBtnCrawl" title="把整个对手网络重新爬一遍，慢（十几分钟），但覆盖更全">深度抓取</button>
      <button class="hd-btn" id="hdBtnFolder">数据目录</button>
      <button class="hd-btn" id="hdBtnSaves">管理存档</button>
    </div>
    <div id="hdMeta"></div>
    <div id="hdProg">
      <div class="hd-prog-head">
        <span id="hdProgStep">准备中</span>
        <span id="hdProgPct">0%</span>
      </div>
      <div class="hd-prog-track"><div id="hdProgFill"></div></div>
      <div class="hd-prog-time-row">
        <span id="hdProgTime">—</span>
        <button class="hd-btn danger" id="hdBtnStop">■ 停止</button>
      </div>
    </div>
    <div id="hdOut"></div>
    <!-- 管理存档（2026-09-22）：一个账号一份，切换秒级；删除走软删 -->
    <div id="hdSaves">
      <div class="hd-saves-head">
        <span>数据主人存档</span>
        <span class="sub" id="hdSavesSum"></span>
      </div>
      <div id="hdSavesList"></div>
      <div class="hd-saves-foot">
        <button class="xbtn" id="hdSavesDir">存档目录</button>
        <button class="xbtn" id="hdSavesReload">刷新</button>
      </div>
    </div>
  </div>
  <button id="hdToggle" title="收起 / 展开">操作</button>
</div>
<!-- 「请先登录游戏」提示弹层：页面内覆盖层（不是原生弹窗，见 theme.css 说明）。
     放在 #hdBar **外面** —— 面板收起时它也得能弹出来。 -->
<div id="hdPopup">
  <div id="hdPopBox">
    <div id="hdPopTitle"></div>
    <div id="hdPopMsg"></div>
    <button id="hdPopOk">知道了</button>
  </div>
</div>
<script>
(function () {
  function init() {
    var api = window.pywebview && window.pywebview.api;
    var bar = document.getElementById('hdBar');
    if (!api || !bar) return;
    bar.classList.add('on');

    var panel = document.getElementById('hdBarPanel');
    var out = document.getElementById('hdOut');
    var meta = document.getElementById('hdMeta');
    var prog = document.getElementById('hdProg');
    var pStep = document.getElementById('hdProgStep');
    var pPct = document.getElementById('hdProgPct');
    var pFill = document.getElementById('hdProgFill');
    var pTime = document.getElementById('hdProgTime');
    var bUpdate = document.getElementById('hdBtnUpdate');
    var bCrawl = document.getElementById('hdBtnCrawl');
    var bFolder = document.getElementById('hdBtnFolder');
    var bOwner = document.getElementById('hdBtnOwner');
    var bStop = document.getElementById('hdBtnStop');
    var btns = [bUpdate, bCrawl, bFolder, bOwner];
    var timer = null;
    var smoothEta = null;
    // 锚点参数**不再展示给用户**（2026-09-21 用户要求面板只留关键内容）：
    // 它只是「截图里那个数」，界面上一堆解释反而干扰。
    // 这里从上次估算结果里静默读出来、原样透传给后端；后端按 gameId
    // 自动跟随到同一局，用户一个数都不用管。
    var anchorVal = null, agoVal = 0;

    // 日志框是 max-height:150px 的滚动容器（theme.css #hdOut），任务运行时
    // 每 0.5 秒刷新一次。不跟着滚的话永远停在最早那几行，新进度全在容器外面
    // （2026-09-22 主人反馈「能不能跟随最新的一条自动往下滑」）。
    // 用**粘底跟随**（stick-to-bottom）：本来就在底部附近才自动跟；
    // 用户往上翻看历史时不拽他回来 —— 否则每 0.5 秒一跳，根本没法看旧输出。
    function say(txt) {
      var stick = (out.scrollHeight - out.scrollTop - out.clientHeight) < 30;
      out.classList.add('on');
      out.textContent = txt;
      if (stick) out.scrollTop = out.scrollHeight;   // 跟到最新一行
    }
    // 页面内提示弹层：用于「必须先看提示才能继续」的场景（目前是游戏没登录）。
    // ⚠️ 刻意不用原生 alert/confirm —— pywebview 里不保证可用（铁律）。
    var pop = document.getElementById('hdPopup');
    var popTitle = document.getElementById('hdPopTitle');
    var popMsg = document.getElementById('hdPopMsg');
    var popOk = document.getElementById('hdPopOk');
    function popup(title, msg) {
      if (!pop) { say(msg || title || ''); return; }   // 兜底：元素缺失就退回日志区
      popTitle.textContent = title || '提示';
      popMsg.textContent = msg || '';
      pop.classList.add('on');
    }
    if (popOk) popOk.onclick = function () { pop.classList.remove('on'); };
    function busy(on) {
      btns.forEach(function (b) { if (b) b.disabled = on; });
      if (bStop) bStop.style.display = on ? '' : 'none';
      if (on) prog.classList.add('on');
    }

    function fmtSec(s) {
      if (s === null || s === undefined || !isFinite(s) || s < 0) return '—';
      s = Math.round(s);
      if (s < 60) return s + ' 秒';
      var m = Math.floor(s / 60), r = s % 60;
      if (m < 60) return m + ' 分' + (r ? ' ' + r + ' 秒' : '');
      return Math.floor(m / 60) + ' 时 ' + (m % 60) + ' 分';
    }

    function render(p) {
      var pct = Math.round((p.frac || 0) * 100);
      pFill.style.width = pct + '%';
      pPct.textContent = pct + '%';
      pStep.textContent = (p.owner ? '【' + p.owner + '】' : '') + (p.step_total
        ? '第 ' + p.step + '/' + p.step_total + ' 步 · ' + p.step_name
        : (p.step_name || '准备中'));
      if (p.running) {
        // 剩余时间做平滑：原始值每 0.5 秒抖一次，看着像坏了
        var e = (p.eta === null || p.eta === undefined) ? 0 : p.eta;
        smoothEta = (smoothEta === null) ? e : (smoothEta * 0.7 + e * 0.3);
        pTime.textContent = '已用 ' + fmtSec(p.elapsed) + ' · 剩余约 ' + fmtSec(smoothEta);
        if (p.stopping && bStop) {
          bStop.disabled = true;
          bStop.textContent = '停止中 …';
          pStep.textContent = '正在停止（等当前这一小步跑完）';
        }
      } else if (p.finished) {
        pTime.textContent = '总用时 ' + fmtSec(p.elapsed) +
                            (p.stopped ? ' · 已停止' : (p.ok ? ' · 完成' : ' · 未全部成功'));
      }
      if (p.log && p.log.length) say(p.log.join('\\n'));
    }

    function poll() {
      api.progress().then(function (p) {
        render(p);
        if (p.running) { timer = setTimeout(poll, 500); return; }
        timer = null;
        busy(false);
        if (bStop) { bStop.disabled = false; bStop.textContent = '■ 停止'; }
        if (p.stopped) {
          say('已按你的要求停止。\\n\\n本次抓取/重算的结果**不会保存**（避免写进半个文件），\\n' +
              '数据还是上一次的完整状态；想继续就再点一次按钮。\\n\\n' +
              (p.log || []).slice(-8).join('\\n'));
        } else if (p.finished && p.ok) {
          var lines = (p.steps || []).map(function (s) {
            return '【' + s.name + '】' + s.secs + 's  rc=' + s.rc;
          }).join('\\n');
          say('全部完成，正在刷新看板 ...\\n\\n' + lines);
          // 换主人后必须把记住的「视角」清掉：新主人的 roster 里不一定有旧视角那个人，
          // 留着会让整个看板切到一个查无此人的视角上（所有面板全空）。
          try {
            localStorage.removeItem('hd_view_pu');
            localStorage.removeItem('hd_view_nm');
          } catch (e) { /* 隐私模式 / file:// 下可能不可用，忽略 */ }
          setTimeout(function () { location.reload(); }, 1200);
        } else {
          say('没有全部成功。最后几行输出：\\n\\n' +
              (p.log || []).slice(-14).join('\\n') +
              (p.error ? '\\n\\n' + p.error : ''));
        }
      }).catch(function (e) {
        // 审查 M19（与看板注入版同步）：reject 时解锁 UI，别永久锁死
        timer = null; busy(false);
        if (bStop) { bStop.disabled = false; bStop.textContent = '■ 停止'; }
        say('进度查询失败：' + e);
      });
    }

    function start(kind) {
      if (timer) return;                       // 已经在跑，忽略重复点击
      smoothEta = null;
      busy(true);
      pFill.style.width = '0%';
      pPct.textContent = '0%';
      pStep.textContent = '正在启动 ...';
      pTime.textContent = '—';
      say('正在启动 ...');
      api.start(kind, anchorVal, agoVal, 300).then(function (r) {
        if (!r || !r.ok) {
          busy(false);
          prog.classList.remove('on');
          say((r && r.msg) || '启动失败');
          if (r && r.need_login) popup('需要先登录游戏', r.msg);
          return;
        }
        poll();
      }).catch(function (e) {
        busy(false); prog.classList.remove('on');
        say('启动接口调用失败：' + e);
      });
    }

    // ---------- 换数据主人（2026-09-22 用户要求「看板视角是谁，抓取/估算就算谁」）----------
    // 语义：把 hd_data 的「数据主人」换成某个玩家，以 TA 为种子重抓 + 重算整个看板。
    // 网络是**并集**（--seed 只增不减），所以换回去只要再点一次，旧对局不会丢。
    //
    // 确认方式刻意**不用 window.confirm**：pywebview 里原生弹窗不一定可用（会静默吞掉）。
    // 改成「同一个按钮点两次」——第一次只出警告并进入待确认，第二次才真跑。
    var pendingOwner = null;

    function viewInfo() {
      try {
        // VIEW / ME_PU / viewSameGames 是看板主脚本里的顶层绑定，
        // 本脚本挂在 body 末尾执行，此刻它们都已初始化；包 try 是防主脚本早退。
        // ⚠️ 注释/字符串里**不要**写出闭合 body 的字面量：apply_toolbar 和
        //   test_dashboard_layout 都是「在闭合 body 标签前插入」，一旦这里有它，
        //   注入点就会跑到注释中间、把整段 JS 打断（2026-09-22 真踩过）。
        if (typeof VIEW === 'undefined') return null;
        return {
          pu: VIEW.pu,
          name: VIEW.name,
          me: (typeof ME_PU === 'undefined') ? '' : ME_PU,
          games: (typeof viewSameGames === 'function') ? viewSameGames(VIEW.pu) : 0
        };
      } catch (e) { return null; }
    }

    function refreshOwnerBtn() {
      if (!bOwner) return;
      var v = viewInfo();
      if (!v || !v.pu || v.pu === v.me) { bOwner.style.display = 'none'; return; }
      bOwner.style.display = '';
      bOwner.textContent = '以「' + (v.name || 'TA') + '」为数据主人重抓重算';
      bOwner.title = '把数据主人换成 ' + (v.name || v.pu) + '（在你这 ' + v.games + ' 局里出现过）。' +
        '会以 TA 为种子做一次完整 BFS 重抓（约几分钟到十几分钟）——' +
        'TA 的隐藏分要靠 TA 周边网络的对手覆盖率撑着，只抓新局不够。' +
        '旧对局不会丢（网络是并集），之后可以再换回来。';
    }

    function startOwner(pu, name, gamesHint) {
      var v = viewInfo();
      var tgt = pu ? { pu: pu, name: name || '' } : (v ? { pu: v.pu, name: v.name } : null);
      if (!tgt || !tgt.pu) return;
      if (v && tgt.pu === v.me) {
        say('TA 已经是当前数据主人了 —— 直接点上面的「抓新局并重算」。');
        return;
      }
      if (timer) return;                       // 已经在跑任务
      var g = (gamesHint === undefined || gamesHint === null) ? 0 : gamesHint;
      // ⚠️ 2026-09-22 改成默认**不重抓**：切换优先走「存档秒切」——
      // 每个主人跑过一次管线都会自动存一份档（app.py 的 _archive_owner），
      // 再切回来直接换档，秒级完成，不抓不重算。见 app.py 的 start_owner。
      // 只有 TA **还没存过档** 时才需要抓一次；那条路也只抓窗口新局 + 补抓，
      // 想完整 BFS 用操作栏的「深度抓取」（十几分钟、网络更全）。
      var deep = false;
      var label = tgt.name || (tgt.pu.slice(0, 8) + '…');
      if (pendingOwner !== tgt.pu) {
        pendingOwner = tgt.pu;
        say('⚠ 要把**数据主人**换成「' + label + '」吗？\\n\\n' +
            '· TA 有存档 → **直接切换**（几秒，不重抓不重算）\\n' +
            '· TA 没存过档 → 这次要抓一次（只抓窗口新局 + 补抓，较快）\\n' +
            '· 逐局战绩 / 指标 / 隐藏分全部换成 TA 的，不再是你自己的\\n' +
            '· 旧对局不会丢（网络是并集），想换回来再走一次即可\\n' +
            '· TA 没有分数截图 → 先用数据库自动给分，之后有截图填一次就覆盖成精确值\\n\\n' +
            '确认无误 → **再点一次同一个按钮**就开始。');
        return;
      }
      pendingOwner = null;
      smoothEta = null;
      busy(true);
      pFill.style.width = '0%';
      pPct.textContent = '0%';
      pStep.textContent = '正在切换数据主人 ...';
      pTime.textContent = '—';
      say('正在把数据主人换成「' + label + '」...');
      api.start_owner(tgt.pu, tgt.name || '', deep).then(function (r) {
        if (!r || !r.ok) {
          busy(false);
          prog.classList.remove('on');
          say((r && r.msg) || '切换失败');
          if (r && r.need_login) popup('需要先登录游戏', r.msg);
          return;
        }
        poll();
      }).catch(function (e) {
        busy(false); prog.classList.remove('on');
        say('切换接口调用失败：' + e);
      });
    }
    // 看板「视角栏」和「玩家弹层」的按钮都调这个（两处共用一份确认/启动逻辑）
    window.hdStartOwner = startOwner;
    if (bOwner) bOwner.onclick = function () { startOwner(null, null, null); };
    // 看板切视角时广播 hd-view-changed（见 setView），这里跟着刷新按钮的显隐与文案 ——
    // 不然切到别人之后按钮不会自己冒出来，得刷新页面才行。
    window.addEventListener('hd-view-changed', refreshOwnerBtn);

    document.getElementById('hdToggle').onclick = function () {
      panel.style.display = (panel.style.display === 'none') ? '' : 'none';
    };

    // 面板上唯一保留的「状态信息」：数据什么时候抓的、上次估算什么时候算的。
    // 锚点那两行解释被用户要求删掉了（2026-09-21），只留这两个真正有用的时间。
    api.last_anchor().then(function (r) {
      if (!r || !r.ok) {
        meta.textContent = '还没有估算结果 —— 点「抓新局并重算」跑第一次。';
        meta.classList.add('warn');
        return;
      }
      if (r.anchor !== null && r.anchor !== undefined) anchorVal = r.anchor;
      agoVal = (r.games_ago === null || r.games_ago === undefined) ? 0 : r.games_ago;
      var t = [];
      if (r.anchor_time) t.push('锚点局 ' + r.anchor_time);
      if (r.estimate !== null && r.estimate !== undefined) t.push('隐藏分 ' + Math.round(r.estimate));
      meta.textContent = t.length ? t.join(' · ') : '';
      meta.classList.add('on');
    }).catch(function () { /* 桥抖动：状态行保持默认 */ });
    refreshOwnerBtn();

    // 页面刷新后如果后台还有任务在跑，自动接回进度条
    api.progress().then(function (p) {
      if (p && p.running) { busy(true); poll(); }
    }).catch(function () { /* 桥未就绪：忽略 */ });

    busy(false);   // 初始态：停止按钮先藏起来（它只在任务跑的时候才有意义）
    bUpdate.onclick = function () { start('update'); };
    bCrawl.onclick = function () { start('crawl'); };
    bFolder.onclick = function () { api.open_data_dir(); };

    // ---------------- 管理存档（2026-09-22） ----------------
    // 一个数据主人一份档；切换 = 换档 + 重注入看板（秒级），不重抓不重算。
    // 删除走**软删**（app.py 里挪进 _stale\），且要「同一个按钮点两次」确认 ——
    // 不用原生 confirm（pywebview 里不保证可用）。
    var bSaves = document.getElementById('hdBtnSaves');
    var boxSaves = document.getElementById('hdSaves');
    var listSaves = document.getElementById('hdSavesList');
    var sumSaves = document.getElementById('hdSavesSum');
    function esc(s) {
      return String(s === null || s === undefined ? '' : s)
        .replace(/[&<>"']/g, function (c) {
          return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function renderSaves(r) {
      if (!listSaves) return;
      if (!r || !r.ok) { listSaves.innerHTML = '<div class="kv">读不到存档列表。</div>'; return; }
      var S = r.saves || [];
      if (sumSaves) sumSaves.textContent = S.length + ' 份 · 共享评分表 ' + (r.base_mb || 0) + ' MB';
      if (!S.length) {
        listSaves.innerHTML = '<div class="kv">还没有存档 —— 跑完一次抓取/重算会自动存一份。</div>';
        return;
      }
      listSaves.innerHTML = S.map(function (s) {
        var kv = [];
        if (s.n_my_games !== null && s.n_my_games !== undefined) kv.push(s.n_my_games + ' 局');
        if (s.estimate !== null && s.estimate !== undefined) {
          kv.push('隐藏分 ' + s.estimate + (s.anchor_source === 'auto' ? '（自动给分）' : '（截图锚定）'));
        }
        if (s.at) kv.push(s.at + ' 算的');
        if (s.size_kb) kv.push(s.size_kb + ' KB');
        return '<div class="hd-save' + (s.current ? ' cur' : '') + '">' +
          '<div class="nm">' + esc(s.name || '(未知)') +
            (s.current ? ' <i>当前</i>' : '') + '</div>' +
          '<div class="kv">' + esc(kv.join(' · ')) + '</div>' +
          (s.unsaved
            ? '<div class="ops"><span class="kv">还没存过档（跑一次就会自动存）</span></div>'
            : '<div class="ops">' +
                '<button class="xbtn" data-go="' + esc(s.puuid) + '">切换</button>' +
                '<button class="xbtn" data-del="' + esc(s.puuid) + '">删除</button>' +
              '</div>') +
          '</div>';
      }).join('');
    }
    function loadSaves() {
      if (!listSaves) return;
      listSaves.innerHTML = '<div class="kv">读取中 …</div>';
      api.owner_saves().then(renderSaves).catch(function () {
        listSaves.innerHTML = '<div class="kv">读取失败。</div>';
      });
    }
    if (bSaves) bSaves.onclick = function () {
      if (!boxSaves) return;
      var on = !boxSaves.classList.contains('on');
      if (on) { boxSaves.classList.add('on'); loadSaves(); }
      else { boxSaves.classList.remove('on'); }
    };
    var bSavesReload = document.getElementById('hdSavesReload');
    if (bSavesReload) bSavesReload.onclick = loadSaves;
    var bSavesDir = document.getElementById('hdSavesDir');
    if (bSavesDir) bSavesDir.onclick = function () { api.open_saves_dir(); };
    if (listSaves) listSaves.addEventListener('click', function (ev) {
      var go = ev.target.closest('[data-go]');
      if (go && !go.disabled) {
        var pu = go.getAttribute('data-go');
        go.disabled = true;
        go.textContent = '切换中 …';
        busy(true);
        prog.classList.add('on');
        pStep.textContent = '正在切换到该存档 …';
        api.apply_owner_save(pu).then(function (r) {
          if (!r || !r.ok) {
            go.disabled = false;
            go.textContent = '切换';
            busy(false);
            prog.classList.remove('on');
            say((r && r.msg) || '切换失败');
            return;
          }
          say(r.msg || '已切换存档');
          poll();                       // 只跑「重建看板」那一步，完了会自动刷新页面
        }).catch(function (e) {
          go.disabled = false; go.textContent = '切换';
          busy(false); prog.classList.remove('on');
          say('切换存档失败：' + e);
        });
        return;
      }
      var del = ev.target.closest('[data-del]');
      if (del && !del.disabled) {
        var pu2 = del.getAttribute('data-del');
        if (del.getAttribute('data-armed') !== '1') {
          del.setAttribute('data-armed', '1');
          del.textContent = '再点一次确认删除';
          setTimeout(function () {
            if (del) { del.removeAttribute('data-armed'); del.textContent = '删除'; }
          }, 5000);
          return;
        }
        del.disabled = true;
        del.textContent = '删除中 …';
        api.drop_owner_save(pu2).then(function (r) {
          say((r && r.msg) || '已删除');
          loadSaves();
        }).catch(function (e) {
          del.disabled = false; del.textContent = '删除';
          say('删除失败：' + e);
        });
      }
    });

    if (bStop) bStop.onclick = function () {
      bStop.disabled = true;
      bStop.textContent = '停止中 …';
      api.stop().then(function (r) {
        if (!r || !r.ok) {
          bStop.disabled = false;
          bStop.textContent = '■ 停止';
          say((r && r.msg) || '停止失败');
        }
      }).catch(function (e) {
        bStop.disabled = false; bStop.textContent = '■ 停止';
        say('停止接口调用失败：' + e);
      });
    };
  }

  if (window.pywebview && window.pywebview.api) init();
  else window.addEventListener('pywebviewready', init);
})();
</script>
"""


def _atomic_write(path: Path, text: str) -> None:
    """原子写（审查 H5）：tmp → 验尺寸 → os.replace；bytes 落盘不做换行转换。

    看板 HTML 是唯一产物，先截断再写的 write_text 在写盘窗口内被强杀
    就留下半个文件。read 端用 newline="" 保持字节原样，避免整份文件
    的换行符被 universal-newlines 来回改写。
    """
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

    with open(BOARD, encoding="utf-8", newline="") as _f:   # newline=""：字节保真
        html = _f.read()
    has = MARK in html

    if restore:
        if not has:
            print("没有检测到工具栏，无需移除。")
            return 0
        # MARK 出现多次 = 历史半次写入 / 手工编辑，状态未知，别猜
        n_mark = html.count(MARK)
        if n_mark != 1:
            raise SystemExit("检测到 %d 个 %s（应为 1），状态异常，已中止。"
                             "请先人工检查看板 HTML。" % (n_mark, MARK))
        i = html.index(MARK)
        j = html.index("</script>", i) + len("</script>")
        new_html = html[:i] + html[j:]
        if dry:
            print("--dry-run：将移除工具栏（%d -> %d 字符）" % (len(html), len(new_html)))
            return 0
        _atomic_write(BOARD, new_html)
        print("[OK] 已移除工具栏  %d -> %d 字符" % (len(html), len(new_html)))
        return 0

    if has:
        print("工具栏已存在，无需重复注入。")
        return 0

    if "</body>" not in html:
        raise SystemExit("HTML 里找不到 </body>，已中止。")

    # ⚠️ 必须插在**最后一个**闭合 body 之前：脚本里只要有一处注释/字符串提到这个标签，
    #    用第一个出现位置就会把工具栏插进注释中间，直接把页面 JS 打断（2026-09-22 踩过）。
    _k = html.rfind("</body>")
    new_html = html[:_k] + TOOLBAR_HTML + html[_k:]
    if dry:
        print("--dry-run：将注入工具栏（%d -> %d 字符）" % (len(html), len(new_html)))
        return 0

    if '<script id="dash-data"' not in new_html:
        raise SystemExit("注入后数据块丢失，已中止（未写盘）。")

    _atomic_write(BOARD, new_html)
    print("[OK] 已注入工具栏  %d -> %d 字符" % (len(html), len(new_html)))
    print("     移除：python 06_桌面版\\apply_toolbar.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
