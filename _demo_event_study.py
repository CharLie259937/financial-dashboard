# -*- coding: utf-8 -*-
"""D0.1+D0.3: 事件研究 demo —— 个股 AR 引擎重建 + 分组聚合 (2026-10-18)
AR 引擎口径(模块4 删除前口径重建, 常量沿用库内定义):
  - Day 0 = 公告日后首个交易日(盘后公告→次日映射, 保守)
  - 估计窗 [-120,-10] OLS: r_s = α + β·r_m (沪深300), MIN_EST_OBS=60 降级守卫
  - 事件窗 [-5,+10] 逐日 AR = r_s − (α̂+β̂·r_m)
  - 聚合复用 quant_data._agg_group (CAAR 曲线 + 六窗口 CAR t 检验)
分组: 利好{预增/略增/扭亏/续盈} vs 利空{预减/略减/首亏/续亏}; 事件=(code,公告日,预告类型)去重
输出: event_study/demo/demo-result.json (供 HTML 报告)"""
import io, sys, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data
import numpy as np

GOOD = {'预增', '略增', '扭亏', '续盈'}
BAD = {'预减', '略减', '首亏', '续亏'}

conn = quant_data.get_db()
evs_raw = [dict(r) for r in conn.execute(
    "SELECT DISTINCT code, announce_date, forecast_type FROM demo_forecast_events")]
mkt = {r[0]: r[1] for r in conn.execute(
    "SELECT trade_date, close FROM benchmark_index WHERE code='sh000300' ORDER BY trade_date")}
mkt_dates = sorted(mkt)
mkt_ret = {}
for i in range(1, len(mkt_dates)):
    d0, d1 = mkt_dates[i - 1], mkt_dates[i]
    mkt_ret[d1] = mkt[d1] / mkt[d0] - 1

stocks = {}
for code in sorted(set(e['code'] for e in evs_raw)):
    rows = conn.execute("SELECT trade_date, close FROM daily_quotes WHERE code=? ORDER BY trade_date",
                        (code,)).fetchall()
    stocks[code] = ([r[0] for r in rows], {r[0]: r[1] for r in rows})
conn.close()

EVENT_WIN = quant_data.EVENT_WIN          # [-5,+10]
EST_LO, EST_HI = quant_data.EST_WIN       # (-120,-10)
MIN_OBS = quant_data.MIN_EST_OBS          # 60

events, skipped = [], {'无次日行情': 0, '估计窗不足': 0, '窗口行情缺': 0}
for e in evs_raw:
    grp = '利好' if e['forecast_type'] in GOOD else ('利空' if e['forecast_type'] in BAD else None)
    if grp is None:
        continue
    dates, px = stocks.get(e['code'], ([], {}))
    i0 = next((i for i, d in enumerate(dates) if d > e['announce_date']), None)
    if i0 is None:
        skipped['无次日行情'] += 1
        continue
    lo, hi = i0 + EST_LO, i0 + EST_HI
    if lo < 0:
        lo = 0
    pairs = [(px[dates[j]] / px[dates[j - 1]] - 1, mkt_ret.get(dates[j]))
             for j in range(lo, hi + 1) if j >= 1 and dates[j] in mkt_ret and dates[j - 1] in px]
    if len(pairs) < MIN_OBS:
        skipped['估计窗不足'] += 1
        continue
    rs = np.array([p[0] for p in pairs]); rm = np.array([p[1] for p in pairs])
    beta, alpha = np.polyfit(rm, rs, 1)
    ar = {}
    for rel in EVENT_WIN:
        j = i0 + rel
        if 1 <= j < len(dates) and dates[j] in mkt_ret:
            r_s = px[dates[j]] / px[dates[j - 1]] - 1
            ar[rel] = r_s - (alpha + beta * mkt_ret[dates[j]])
    if len(ar) < 11:
        skipped['窗口行情缺'] += 1
        continue
    events.append({'code': e['code'], 'date': e['announce_date'], 'type': e['forecast_type'],
                   'group': grp, 'beta': round(float(beta), 3), 'ar': ar})

print(f"事件 {len(events)} 条 (跳过 {skipped})")
by_grp = {}
for gname in ('利好', '利空'):
    evs = [e for e in events if e['group'] == gname]
    if evs:
        by_grp[gname] = quant_data._agg_group((gname, '业绩预告'), evs, 'demo')
        print(f"  {gname}: n={len(evs)}, CAAR[+1,+10]={by_grp[gname]['windows'][4]['car_mean'] if len(by_grp[gname]['windows'])>4 else '—'}")

# 全窗口曲线紧凑化 + 元数据
out = {
    'meta': {
        'generated': '2026-10-18', 'source': 'demo_forecast_events(staging) + daily_quotes + 沪深300',
        'universe': '研究宇宙A股10只 + 300750', 'engine': '市场模型 估计窗[-120,-10] OLS, MIN_OBS=60',
        'n_events': len(events), 'skipped': skipped,
        'groups': {g: v['total_events'] for g, v in by_grp.items()},
    },
    'groups': {g: {'curve': v['curve'], 'windows': v['windows']} for g, v in by_grp.items()},
    'events': [{'code': e['code'], 'date': e['date'], 'type': e['type'], 'group': e['group']}
               for e in sorted(events, key=lambda x: x['date'])],
}
os.makedirs(r'event_study\demo', exist_ok=True)
with open(r'event_study\demo\demo-result.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('→ event_study/demo/demo-result.json')
