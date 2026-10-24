# -*- coding: utf-8 -*-
"""海斗 stats 抓取：单局详情取 stats + 队友 stats → 算 9 项指标（含治疗占比），并导出每局 10 人明细。"""
import base64, json, re, ssl, sys, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import (DATA_DIR, MIN_GAME_SECONDS, valid_game,  # noqa: E402
                    raise_if_cancelled, atomic_write_text)
from _lcu_creds2 import find_lcu  # noqa: E402

# ---------- LCU 凭据 ----------
# 统一走 _lcu_creds2（NtQueryInformationProcess 信息类 60，只要 QUERY_LIMITED 权限）。
# 这里原本自带一份 PowerShell + WMI 实现，理由是「沙箱不许跨进程读命令行」——
# 但反作弊保护下 WMI 拿到的 CommandLine 是空的，实测**永远**取不到凭据，
# 于是本脚本必定以「✗ 找不到 League 客户端」退出。2026-09-21 改为复用 _lcu_creds2。
PORT, TOKEN = find_lcu()
if not PORT:
    print("✗ 找不到 League 客户端进程（请先启动游戏客户端）")
    sys.exit(1)
print(f"✓ LCU 已连接 @127.0.0.1:{PORT}")
AUTH = base64.b64encode(f'riot:{TOKEN}'.encode()).decode()
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

# ---------- 加载玩家隐藏分表（fit_bt 算的 mmr） ----------
PE_FILE = DATA_DIR / 'player_elo.json'
try:
    PE = json.load(open(PE_FILE, encoding='utf-8'))
    PE_META = PE.pop('_meta', {})
    print(f"✓ 加载玩家隐藏分表 {len(PE)} 人 (锚点 {PE_META.get('anchor')} / 公式 {PE_META.get('formula')})")
except FileNotFoundError:
    print("⚠ 找不到 player_elo.json，先跑 _player_elo_build.py")
    PE = {}; PE_META = {}

def lcu_get(path):
    # PORT 是 int（_lcu_creds2.find_lcu 返回的就是 int），拼字符串必须显式 str()。
    r = urllib.request.Request('https://127.0.0.1:'+str(PORT)+path,
                               headers={'Authorization':'Basic '+AUTH,'Accept':'application/json'})
    with urllib.request.urlopen(r, context=CTX, timeout=25) as resp:
        t = resp.read().decode()
        return json.loads(t) if t else None

# ---------- 抓本人对局 stats ----------
hd = json.load(open(DATA_DIR/'hd_data.json', encoding='utf-8'))
me_puuid = hd['meta']['puuid']

# 5 分钟以内的对局视为无效（挂机/重开）—— 不抓、不进 my_stats / roster。
# 见 _paths.MIN_GAME_SECONDS。_hd_crawl.py 已经不会收，这里再挡一次防老数据。
_by_gid = {g['gameId']: g for g in hd['games']}
_valid_ids = [g for g in hd['my_game_ids'] if valid_game(_by_gid.get(g) or {})]
_n_short = len(hd['my_game_ids']) - len(_valid_ids)
if _n_short:
    print(f"⚠ 跳过 {_n_short} 局 <{MIN_GAME_SECONDS}s 的无效对局（挂机/重开）")

my_ids = sorted(_valid_ids,
                key=lambda g: next(x['ts'] for x in hd['games'] if x['gameId']==g))
print(f"待抓 {len(my_ids)} 局")

def fetch_one(gid):
    try:
        d = lcu_get('/lol-match-history/v1/games/'+str(gid))
        me = None
        for p in d['participants']:
            pi = next(i for i in d['participantIdentities'] if i['participantId']==p['participantId'])
            if pi['player']['puuid'] == me_puuid:
                me = p
                break
        if me is None:
            return {'gid': gid, 'ok': False, 'err': 'me not found'}
        team_id = me['teamId']
        teammates = [p for p in d['participants'] if p['teamId'] == team_id]
        return {'gid': gid, 'ok': True, 'det': d, 'me': me, 'team': teammates,
                'ts': d.get('gameCreation', 0)}
    except urllib.error.HTTPError as e:
        return {'gid': gid, 'ok': False, 'err': f'HTTP{e.code}'}
    except Exception as e:
        return {'gid': gid, 'ok': False, 'err': f'{type(e).__name__}: {str(e)[:80]}'}

results = {}
t0 = time.time()
# 分批提交（每批 96 局 = 4 × 24 线程），批与批之间检查停止标志。
# 一次性 submit 全部的话，中途停止要等所有请求跑完才返回（见 _hd_crawl.py 的同款说明）。
_CHUNK = 96
with ThreadPoolExecutor(max_workers=24) as ex:
    done = 0
    for _i in range(0, len(my_ids), _CHUNK):
        raise_if_cancelled("抓本人对局明细")
        part = my_ids[_i:_i + _CHUNK]
        for f in as_completed([ex.submit(fetch_one, gid) for gid in part]):
            r = f.result()
            results[r['gid']] = r
            done += 1
            if done % 10 == 0 or done == len(my_ids):
                print(f"  [Stats] {done}/{len(my_ids)} 局", flush=True)
print(f"耗时 {time.time()-t0:.1f}s, 成功 {sum(1 for r in results.values() if r['ok'])}/{len(my_ids)}")
fails = {g: r.get('err') for g, r in results.items() if not r['ok']}
if fails:
    # 别把 161 条失败原因整片倒出来 —— 取前 3 条 + 总数就够定位了
    sample = list(fails.items())[:3]
    print(f"失败 {len(fails)} 局，示例: " + "; ".join(f"{g}: {e}" for g, e in sample))

# ---------- 算指标（含治疗量） + 每局 10 人 roster ----------
def safe_div(a, b): return (a / b) if b else 0.0
mine = []
roster = []
for gid in my_ids:
    r = results.get(gid)
    if not r or not r['ok']: continue
    det = r['det']
    s = r['me']['stats']; tm = r['team']
    team_kills = sum(t['stats']['kills'] for t in tm)
    team_dmg = sum(t['stats']['totalDamageDealtToChampions'] for t in tm)
    team_taken = sum(t['stats']['totalDamageTaken'] for t in tm)
    team_gold = sum(t['stats']['goldEarned'] for t in tm)
    team_heal = sum(t['stats'].get('totalHeal', 0) for t in tm)
    K, D, A = s['kills'], s['deaths'], s['assists']
    dmg = s['totalDamageDealtToChampions']
    taken = s['totalDamageTaken']
    gold = s['goldEarned']
    heal = s.get('totalHeal', 0)
    win = bool(s['win'])
    dur = det.get('gameDuration', 0)
    mine.append({
        'gid': gid, 'ts': r['ts'], 'duration': dur, 'win': win,
        'champ': r['me'].get('championId'),
        'k': K, 'd': D, 'a': A, 'kda': round((K+A)/max(D,1), 2),
        'dmg_share': round(safe_div(dmg, team_dmg)*100, 1),
        'taken_share': round(safe_div(taken, team_taken)*100, 1),
        'gold_share': round(safe_div(gold, team_gold)*100, 1),
        'heal_share': round(safe_div(heal, team_heal)*100, 1),
        'dmg_conv': round(safe_div(safe_div(dmg, team_dmg), safe_div(gold, team_gold)), 2),
        'kill_part': round(min(safe_div(K+A, max(team_kills,1))*100, 100.0), 1),
        'team_kills': team_kills,
    })
    # 当局 10 人明细（蓝/红两队），LCU 详情同一次抓取带出
    teams = {}
    for p in det['participants']:
        pi = next(i for i in det['participantIdentities'] if i['participantId'] == p['participantId'])
        ps = p['stats']
        puuid = pi['player'].get('puuid')
        pe = PE.get(puuid) or {}
        pl = {
            'n': pi['player'].get('summonerName') or pi['player'].get('gameName') or '?',
            'pu': puuid,
            'c': p.get('championId'), 'win': bool(ps['win']),
            'k': ps['kills'], 'd': ps['deaths'], 'a': ps['assists'],
            'dmg': ps['totalDamageDealtToChampions'], 'taken': ps['totalDamageTaken'],
            'gold': ps['goldEarned'], 'heal': ps.get('totalHeal', 0),
            'mmr': pe.get('mmr'),       # 来自 BT 全网拟合的隐藏分（无样本则 None）
            'mmr_n': pe.get('n_games', 0),
        }
        teams.setdefault(p['teamId'], []).append(pl)
    roster.append({'gid': gid, 'ts': r['ts'], 'dur': dur,
                   'teams': [{'t': t, 'win': any(p['win'] for p in pl),
                              'kills': sum(p['k'] for p in pl), 'players': pl}
                             for t, pl in sorted(teams.items())]})

mine.sort(key=lambda x: x['ts'])
roster.sort(key=lambda x: x['ts'])

# 一局都没成功就**不要写盘**。
# 这两个文件是看板的数据源，写空等于把看板打瞎；而失败原因（凭据不对、
# 客户端关了、URL 拼错）通常一眼可查，留旧数据反而更好排查。
if not mine and my_ids:
    print(f"✗ {len(my_ids)} 局全部抓取失败，拒绝覆盖 my_stats.json / 海斗roster.json"
          f"（保留旧数据，先查上面的失败原因）")
    sys.exit(1)

# 原子写（审查 H5）：先写 .tmp 再 os.replace，写一半不会留半个文件
atomic_write_text(DATA_DIR/'my_stats.json', json.dumps(mine, ensure_ascii=False, indent=1))
atomic_write_text(DATA_DIR/'海斗roster.json', json.dumps(roster, ensure_ascii=False))
# 落盘 PE_META 备份（dashboard 也会用到锚点信息）
atomic_write_text(DATA_DIR/'player_elo_meta.json', json.dumps(PE_META, ensure_ascii=False, indent=1))
print(f"✓ 输出 {len(mine)} 局 → my_stats.json（含治疗占比）")
print(f"✓ 输出 {len(roster)} 局 ×10 人 → 海斗roster.json（含 mmr 字段）")

# 打印最近5局
for m in mine[-5:]:
    print(' ', time.strftime("%m-%d %H:%M", time.localtime(m['ts']/1000)),
          f"胜{str(m['win']):5}",
          f"伤占{m['dmg_share']}% 承伤占{m['taken_share']}% 经济占{m['gold_share']}%",
          f"转化{m['dmg_conv']} 参团{m['kill_part']}% KDA{m['kda']}")