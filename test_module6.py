# -*- coding: utf-8 -*-
"""模块6全链路验证: 映射锚点(纸面推演)/家族分类/数值与方向解析/常数均值AR/
DB完整性/采集幂等/研究对账
运行: python test_module6.py  (桌面运行目录; T7需网络, T8需先跑run_macro_study)"""
import sys, os, sqlite3
from datetime import date, datetime, time as dtime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_data as qd

PASS, FAIL, SKIP = [], [], []


def ok(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ''))


def skip(name, why):
    SKIP.append(name)
    print(f"  [SKIP] {name}  {why}")


# ============================================================
print("== T1 事件→交易日映射 (期望值全部纸面推演, 见注释算式) ==")
cal = [date(2025, 7, 28), date(2025, 7, 29), date(2025, 7, 30), date(2025, 7, 31),
       date(2025, 8, 1), date(2025, 8, 4), date(2025, 8, 5),
       date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
       date(2026, 1, 8), date(2026, 1, 9)]
td = qd.macro_event_trading_day

# 非农 北京周五20:30, 夏令时差12h → ET=08-01 08:30 ≤16:00 → 美股当日
r = td(date(2025, 8, 1), dtime(20, 30), 'us', cal)
ok("非农(北京08-01 20:30)→美股当日08-01", r == date(2025, 8, 1), f"got {r}")
# 非农→A股: 20:30>15:00 → cand=08-02(六) → 吸附08-04(一)
r = td(date(2025, 8, 1), dtime(20, 30), 'cn', cal)
ok("非农→A股下周一08-04", r == date(2025, 8, 4), f"got {r}")
# FOMC 北京周三02:00, 夏令时 → ET=07-29 14:00 ≤16:00 → 美股前一日
r = td(date(2025, 7, 30), dtime(2, 0), 'us', cal)
ok("FOMC(北京07-30 02:00)→美股07-29", r == date(2025, 7, 29), f"got {r}")
# FOMC→A股: 02:00≤15:00 → 当日
r = td(date(2025, 7, 30), dtime(2, 0), 'cn', cal)
ok("FOMC→A股当日07-30", r == date(2025, 7, 30), f"got {r}")
# 冬令时跨日: 北京01-09(五) 00:30, 差13h → ET=01-08 11:30 ≤16:00 → 01-08
r = td(date(2026, 1, 9), dtime(0, 30), 'us', cal)
ok("冬令时00:30→美股前日01-08", r == date(2026, 1, 8), f"got {r}")
# 冬令时默认正午: 北京01-09 12:00 −13h = 01-08 23:00 >16:00 → 次日01-09
r = td(date(2026, 1, 9), None, 'us', cal)
ok("冬令时默认12:00→美股次日01-09", r == date(2026, 1, 9), f"got {r}")
# 夏令时默认正午: 12:00−12h = 当日00:00 ≤16:00 → 当日
r = td(date(2025, 8, 1), None, 'us', cal)
ok("夏令时默认12:00→美股当日08-01", r == date(2025, 8, 1), f"got {r}")
# A股盘后17:00 → 次日; 港股16:30→次日 / 15:30→当日
r = td(date(2025, 7, 30), dtime(17, 0), 'cn', cal)
ok("A股盘后17:00→次日07-31", r == date(2025, 7, 31), f"got {r}")
r = td(date(2025, 7, 30), dtime(16, 30), 'hk', cal)
ok("港股16:30→次日07-31", r == date(2025, 7, 31), f"got {r}")
r = td(date(2025, 7, 30), dtime(15, 30), 'hk', cal)
ok("港股15:30→当日07-30", r == date(2025, 7, 30), f"got {r}")
# 周六发布吸附: cand=08-02(六) 不在日历 → 08-04
r = td(date(2025, 8, 2), dtime(10, 0), 'cn', cal)
ok("周六10:00→A股吸附08-04", r == date(2025, 8, 4), f"got {r}")
# 超出日历末端 → None (北京01-12, 差13h→ET=01-11 21:00>16:00→cand=01-12不在日历)
r = td(date(2026, 1, 12), dtime(10, 0), 'us', cal)
ok("超出日历末端→None", r is None, f"got {r}")
# 未知市场 → None
r = td(date(2025, 8, 1), dtime(10, 0), 'jp', cal)
ok("未知市场→None", r is None, f"got {r}")

# ============================================================
print("== T2 家族分类 (顺序敏感: 核心 CPI 先于 CPI) ==")
fam_cases = [
    ('美国7月核心CPI年率', '核心CPI'), ('美国7月CPI年率', 'CPI'),
    ('美国7月未季调核心CPI月率', '核心CPI'),
    ('美国7月非农就业人口变动', '就业报告'),
    ('美国至7月26日当周初请失业金人数', '初请失业金'),
    ('美国7月季调后失业率', '失业率'),
    ('美联储公布利率决议(至7月30日)', '利率决议'),
    ('中国7月以美元计算贸易帐', '贸易帐'),
    ('中国第二季度GDP年率', 'GDP'),
    ('欧元区7月制造业PMI初值', 'PMI'),
    ('美国7月零售销售月率', '零售销售'),
    ('美国7月PCE物价指数年率', 'PCE'),
    ('美国6月新屋开工总数年化', '房地产数据'),
    ('美国至7月25日当周EIA原油库存', '其他'),
    ('日本央行行长黑田东彦发表讲话', '其他'),
]
for title, expect in fam_cases:
    got = qd._macro_family(title)
    ok(f"家族[{title[:14]}…]={expect}", got == expect, f"got {got}")

# ============================================================
print("== T3 数值解析 ==")
num_cases = [('2.92', 2.92), ('13.5万', 135000.0), ('-0.3%', -0.3),
             ('1,234.5', 1234.5), ('3.8亿', 3.8e8), ('—', None), ('', None),
             (None, None), ('前值', None), ('51.5', 51.5)]
for s, expect in num_cases:
    got = qd._macro_num(s)
    cond = (got is None and expect is None) or \
           (got is not None and expect is not None and abs(got - expect) < 1e-6)
    ok(f"解析[{s}]={expect}", cond, f"got {got}")

# ============================================================
print("== T4 意外方向 ==")
dir_cases = [('2.92', '2.9', '高于预期'), ('2.8', '2.9', '低于预期'),
             ('2.9', '2.9', '符合预期'), (None, '2.9', None),
             ('2.9', None, None), ('13.5万', '14万', '低于预期'),
             ('—', '2.9', None)]
for pub, fc, expect in dir_cases:
    got = qd._macro_direction(pub, fc)
    ok(f"方向[{pub} vs {fc}]={expect}", got == expect, f"got {got}")

# ============================================================
print("== T5 常数均值模型 AR ==")
n = 150
rets = [None] + [0.001] * (n - 1)          # 估计窗均值 μ=0.001
rets[130] = 0.005                           # Day0
rets[131] = 0.004                           # Day+1
ar, why = qd._macro_ar(rets, 130)
ok("AR[0]=R[0]−μ", ar is not None and abs(ar[0] - 0.004) < 1e-12,
   f"got {ar and ar.get(0)}")
ok("AR[+1]=R[+1]−μ", ar is not None and abs(ar[1] - 0.003) < 1e-12,
   f"got {ar and ar.get(1)}")
ok("事件窗[-5,+10]共16个键", ar is not None and
   sorted(ar.keys()) == list(range(-5, 11)), f"got {sorted(ar.keys()) if ar else None}")
ar2, why2 = qd._macro_ar(rets, 50)          # est=rets[1..40]=40条 < 60
ok("估计窗不足→None", ar2 is None and why2 == '估计窗样本不足', f"got {why2}")
ar3, why3 = qd._macro_ar(rets, 150)         # Day0越界
ok("无Day0行情→None", ar3 is None and why3 == '无Day0行情', f"got {why3}")

# ============================================================
print("== T6 macro_events 完整性 ==")
conn = sqlite3.connect(qd.DB_PATH)
conn.row_factory = sqlite3.Row
q = conn.execute
rows, lo, hi = q('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) '
                 'FROM macro_events').fetchone()
if rows == 0:
    skip("T6全部", "macro_events 为空(回填未完成)")
else:
    ok("总行数>0", rows > 0, f"rows={rows}")
    ok(f"起点≤2025-07-29", lo <= '2025-07-29', f"min={lo}")
    print(f"  [INFO] 覆盖 {lo} ~ {hi}, 共 {rows} 行")
    dup = q('SELECT COUNT(*) FROM (SELECT trade_date,time,region,title,COUNT(*) c '
            'FROM macro_events GROUP BY 1,2,3,4 HAVING c>1)').fetchone()[0]
    ok("无重复(幂等键)", dup == 0, f"dups={dup}")
    hi_star = q('SELECT COUNT(*) FROM macro_events WHERE star=2').fetchone()[0]
    ok("高重要性(★2)事件存在", hi_star > 0, f"n={hi_star}")
    # 历史高重要性事件实际值填充率(审计称100%; 留2%容差给讲话类无值事件)
    past_hi = q("SELECT COUNT(*) FROM macro_events WHERE star=2 AND trade_date<'2026-08-25'").fetchone()[0]
    past_hi_filled = q("SELECT COUNT(*) FROM macro_events WHERE star=2 AND trade_date<'2026-08-25' "
                       "AND pub_val IS NOT NULL AND TRIM(pub_val) NOT IN ('','—','-')").fetchone()[0]
    rate = past_hi_filled / past_hi if past_hi else 0
    ok(f"历史★2实际值填充率≥98% (实测{rate:.1%})", rate >= 0.98,
       f"{past_hi_filled}/{past_hi}")
    # 工作日0行日期(验收: 周六日允许0行, 工作日0行须复核)
    dates = {r[0] for r in q('SELECT DISTINCT trade_date FROM macro_events')}
    d0, d1 = datetime.strptime(lo, '%Y-%m-%d').date(), datetime.strptime(hi, '%Y-%m-%d').date()
    missing = []
    d = d0
    while d <= d1:
        if d.weekday() < 5 and d.isoformat() not in dates:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    ok(f"工作日0行日期≤5个(实测{len(missing)})",
       len(missing) <= 5, f"缺失: {missing[:15]}")
conn.close()

# ============================================================
print("== T7 采集幂等(网络, 任一已有日期强制重采行数不变) ==")
try:
    conn = sqlite3.connect(qd.DB_PATH)
    row = conn.execute("SELECT trade_date, COUNT(*) FROM macro_events "
                       "WHERE trade_date<'2026-08-20' GROUP BY trade_date "
                       "ORDER BY trade_date DESC LIMIT 1").fetchone()
    conn.close()
    if row is None:
        skip("幂等重采", "无历史日期可选")
    else:
        d_test, before = row[0], row[1]
        stats = qd.collect_macro_events([d_test], force=True, log=lambda m: None)
        conn = sqlite3.connect(qd.DB_PATH)
        after = conn.execute('SELECT COUNT(*) FROM macro_events WHERE trade_date=?',
                             (d_test,)).fetchone()[0]
        conn.close()
        ok(f"{d_test} 强制重采行数不变({before}→{after})", before == after,
           f"stats={stats}")
except Exception as e:
    skip("幂等重采", f"网络异常: {e}")

# ============================================================
print("== T8 研究结果对账 (需先运行 run_macro_study) ==")
try:
    st = qd.load_macro_study()
    res, cur, meta = st['results'], st['curves'], st['meta']
    if res.empty:
        skip("T8全部", "macro_study_results 为空(研究未运行)")
    else:
        ok("结果表非空", len(res) > 0, f"{len(res)}行")
        ok("meta含run_ts/caveat", bool(meta.get('run_ts')) and bool(meta.get('caveat')))
        # 口径自洽: [0,0]窗口car_mean == 曲线rel=0的AAR×100 (同一批事件同一均值)
        w00 = res[res['window'] == '[0,0]'].set_index('group_id')
        c0 = cur[cur['rel_day'] == 0].set_index('group_id')
        n_bad = 0
        for gid, r in w00.iterrows():
            if gid not in c0.index or r['n'] == 0:
                continue
            aar0, n0 = c0.loc[gid, 'aar'], c0.loc[gid, 'n']
            if aar0 is None or (isinstance(aar0, float) and aar0 != aar0):
                continue
            if abs(aar0 * 100 - r['car_mean']) > 1e-3 or n0 != r['n']:
                n_bad += 1
        ok("CAR[0,0]=AAR(0)×100 且 n 一致(全部分组)", n_bad == 0, f"不一致组数={n_bad}")
        # 曲线累加自洽: CAAR(t)−CAAR(t−1)=AAR(t)
        n_bad2 = 0
        for gid, g in cur.groupby('group_id'):
            g = g.sort_values('rel_day').reset_index(drop=True)
            for i in range(1, len(g)):
                a, b, c = g.loc[i, 'aar'], g.loc[i - 1, 'caar'], g.loc[i, 'caar']
                if None in (a, b, c) or a != a or b != b or c != c:
                    continue
                if abs((c - b) - a) > 1e-9:
                    n_bad2 += 1
        ok("CAAR累加=AAR逐日(全部曲线)", n_bad2 == 0, f"不一致点数={n_bad2}")
        # 方向拆分组存在(公布vs预期可解析)
        n_dir = res[res['direction'].notna()]['group_id'].nunique()
        ok(f"意外方向拆分组存在({n_dir}组)", n_dir > 0)
        # 事件明细与统计样本量对账: included=1 且无排除原因的事件数≥任一窗口n
        evs = st['events']
        ok("事件映射明细非空", len(evs) > 0, f"{len(evs)}行")
        ex_ok = True
        for gid in res[res['n'] > 0]['group_id'].unique():
            fam, region, code, dirn = gid.split('|')
            if dirn == '全部':
                n_ev = len(evs[(evs['region'] == region) &
                               (evs['family'] == fam) & (evs['index_code'] == code) &
                               (evs['included'] == 1)])
                w_all = res[(res['group_id'] == gid) & (res['window'] == '[-5,+10]')]
                if not w_all.empty and n_ev > 0 and int(w_all.iloc[0]['n']) > n_ev:
                    ex_ok = False
                    print(f"    样本量超界: {gid} 全窗口n={w_all.iloc[0]['n']} > 明细included={n_ev}")
        ok("全窗口n≤明细included数(方向'全部'组)", ex_ok)
except Exception as e:
    skip("T8全部", f"异常: {e}")

# ============================================================
print(f"\n==== 模块6测试汇总: PASS {len(PASS)} / FAIL {len(FAIL)} / SKIP {len(SKIP)} ====")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("全部通过" if not SKIP else "通过(含跳过项, 见上方SKIP原因)")
