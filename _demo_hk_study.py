# -*- coding: utf-8 -*-
"""港股事件研究 Demo · AR引擎+聚合 (2026-10-19)
与A股demo同口径: Day0=公告日后首个交易日, 估计窗[-120,-10]市场模型(恒指), MIN_OBS=60
分组: annual(年报) / interim(中报) / quarterly(季报与其他)
样本自动限于行情覆盖(2020-06起), 早于此的事件被引擎跳过"""
import io, sys, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data
import numpy as np

GRP = {'annual': 'annual', 'interim': 'interim',
       'q1': 'quarterly', 'q3': 'quarterly', 'earnings': 'quarterly'}

conn = quant_data.get_db()
evs_raw = [dict(r) for r in conn.execute(
    "SELECT code, name, event_date, subtype FROM demo_hk_earnings")]
mkt = {r[0]: r[1] for r in conn.execute(
    "SELECT trade_date, close FROM benchmark_index WHERE code='HSI' ORDER BY trade_date")}
mkt_dates = sorted(mkt)
mkt_ret = {}
for i in range(1, len(mkt_dates)):
    mkt_ret[mkt_dates[i]] = mkt[mkt_dates[i]] / mkt[mkt_dates[i - 1]] - 1

stocks = {}
for code in sorted(set(e['code'] for e in evs_raw)):
    rows = conn.execute("SELECT trade_date, close FROM daily_quotes WHERE code=? ORDER BY trade_date",
                        (code,)).fetchall()
    stocks[code] = ([r[0] for r in rows], {r[0]: r[1] for r in rows})
conn.close()

EVENT_WIN = quant_data.EVENT_WIN
EST_LO, EST_HI = quant_data.EST_WIN
MIN_OBS = quant_data.MIN_EST_OBS

events, skipped = [], {'早于行情(2020-06前)': 0, '无次日行情': 0, '估计窗不足': 0, '窗口行情缺': 0}
qstart = None
for e in evs_raw:
    dates, px = stocks.get(e['code'], ([], {}))
    if not dates:
        skipped['早于行情(2020-06前)'] += 1
        continue
    if qstart is None:
        qstart = dates[0]
    if e['event_date'] < qstart:
        skipped['早于行情(2020-06前)'] += 1
        continue
    i0 = next((i for i, d in enumerate(dates) if d > e['event_date']), None)
    if i0 is None:
        skipped['无次日行情'] += 1
        continue
    lo = max(i0 + EST_LO, 1)
    hi = i0 + EST_HI
    pairs = [(px[dates[j]] / px[dates[j - 1]] - 1, mkt_ret[dates[j]])
             for j in range(lo, hi + 1) if dates[j] in mkt_ret and dates[j - 1] in px]
    if len(pairs) < MIN_OBS:
        skipped['估计窗不足'] += 1
        continue
    rs = np.array([p[0] for p in pairs]); rm = np.array([p[1] for p in pairs])
    beta, alpha = np.polyfit(rm, rs, 1)
    ar = {}
    for rel in EVENT_WIN:
        j = i0 + rel
        if 1 <= j < len(dates) and dates[j] in mkt_ret:
            ar[rel] = px[dates[j]] / px[dates[j - 1]] - 1 - (alpha + beta * mkt_ret[dates[j]])
    if len(ar) < 11:
        skipped['窗口行情缺'] += 1
        continue
    events.append({'code': e['code'], 'date': e['event_date'], 'subtype': e['subtype'],
                   'group': GRP.get(e['subtype'], 'quarterly'), 'ar': ar})

print(f"有效事件 {len(events)} (跳过 {skipped})")
from collections import Counter
print('分组:', dict(Counter(e['group'] for e in events)))
by_grp = {}
for gname in ('annual', 'interim', 'quarterly'):
    evs = [e for e in events if e['group'] == gname]
    if evs:
        by_grp[gname] = quant_data._agg_group((gname, '港股财报'), evs, 'demo')
        w = by_grp[gname]['windows']
        print(f"  {gname}: n={len(evs)}, CAR[0,0]={w[0]['car_mean']:+.2f}%, CAAR[+1,+10]={w[4]['car_mean']:+.2f}%")

out = {
    'meta': {
        'generated': '2026-10-19', 'market': '港股',
        'source': 'demo_hk_earnings(staging, 东财港股东悉E1同源) + daily_quotes + 恒生指数',
        'universe': '研究宇宙港股10只 + 00700/02513 (汇丰00005/友邦01299东财无公告覆盖, 缺口已记)',
        'engine': '市场模型 估计窗[-120,-10] OLS(恒指), MIN_OBS=60, Day0=公告日后首个交易日',
        'n_events': len(events), 'skipped': skipped,
        'groups': {g: v['total_events'] for g, v in by_grp.items()},
    },
    'groups': {g: {'curve': v['curve'], 'windows': v['windows']} for g, v in by_grp.items()},
    'events': [{'code': e['code'], 'date': e['date'], 'type': e['subtype'], 'group': e['group']}
               for e in sorted(events, key=lambda x: x['date'])],
}
os.makedirs(r'event_study\demo', exist_ok=True)
with open(r'event_study\demo\demo-hk-result.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('→ event_study/demo/demo-hk-result.json')
