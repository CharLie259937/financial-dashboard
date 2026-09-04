# -*- coding: utf-8 -*-
"""模块3测试: 胜率统计全流程 + 独立校验"""
import quant_data as qd

print("=" * 62)
print("[1] 运行模块3完整分析")
print("=" * 62)
m3 = qd.run_module3_analysis()
print("分析完成")

print("\n[2] 随机基准")
for n, b in m3['baselines'].items():
    print(f"  {n}日: 触发{b['triggers']} 胜率{b['win_rate']}% "
          f"均收{b['avg_return']}% 盈亏比{b['profit_loss_ratio']}")

print("\n[3] 被动信号统计 (每组×4周期)")
for r in m3['passive']:
    line = f"  {r['signal_type']}|{r['signal_subtype']}|{r['direction']} (样本{r['total_signals']}): "
    parts = []
    for n, st in sorted(r['stats'].items()):
        parts.append(f"{n}日:胜率{st['win_rate']}%超额{st.get('excess_win_rate','—')}pp "
                     f"均收{st['avg_return']}%超额{st.get('excess_return','—')}%")
    print(line)
    for p in parts:
        print(f"      {p}")

print("\n[4] 有效信号池")
for p in m3['pool']:
    extra = (f"优势{p.get('best_period','—')} 胜率{p.get('best_win_rate','—')}% "
             f"盈亏比{p.get('best_pl_ratio','—')} 超额胜率{p.get('excess_win_rate','—')}pp"
             if p['status'] == '有效' else '')
    print(f"  [{p['status']}] {p['signal_type']}|{p['signal_subtype']}|{p['direction']} "
          f"样本{p['total_signals']} {extra}")

print("\n[5] 主动事件统计")
for r in m3['events']['by_dimension']:
    print(f"  {r['key']} 样本{r['total_events']}:")
    for n, st in sorted(r['stats'].items()):
        print(f"      {n}日: 胜率{st['win_rate']}% 超额{st.get('excess_win_rate','—')}pp "
              f"均收{st['avg_return']}% 超额{st.get('excess_return','—')}%")
print("  by_time:")
for r in m3['events']['by_time']:
    print(f"  {r['key']} 样本{r['total_events']}")

print("\n[6] signal_effect_report 入库验证")
conn = qd.get_db()
rows = conn.execute(
    "SELECT category, COUNT(*) FROM signal_effect_report GROUP BY category").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}行")
sample = conn.execute(
    "SELECT group_key, hold_days, win_rate, excess_win_rate, status "
    "FROM signal_effect_report WHERE category='passive' LIMIT 5").fetchall()
for s in sample:
    print(f"  样例: {s[0]} {s[1]}日 胜率{s[2]}% 超额{s[3]}pp 状态[{s[4]}]")
conn.close()

print("\n" + "=" * 62)
print("[7] 独立校验: 胜率计算正确性 (rsi_oversold 1日)")
print("=" * 62)
import sqlite3
conn = qd.get_db()
sig = conn.execute(
    "SELECT code, trade_date FROM passive_signals "
    "WHERE signal_type='rsi_oversold' AND signal_subtype='超卖' AND direction='bullish'"
).fetchall()
quotes = conn.execute(
    "SELECT code, trade_date, close FROM daily_quotes ORDER BY code, trade_date").fetchall()
conn.close()

pm, dm = qd._load_price_map()
rets = []
skipped = 0
for s in sig:
    r = qd._future_return(pm, dm, s['code'], s['trade_date'], 1)
    if r is None:
        skipped += 1
    else:
        rets.append(r)

n = len(rets)
wins = sum(1 for r in rets if r > 0)
wr = wins / n * 100 if n else 0
print(f"  独立重算: n={n} 胜率={wr:.2f}% 跳过(无未来价)={skipped}")
for r in m3['passive']:
    if r['signal_type'] == 'rsi_oversold' and r['direction'] == 'bullish':
        st1 = r['stats'].get(1)
        if st1:
            print(f"  模块3输出: n={st1['triggers']} 胜率={st1['win_rate']}%")
            ok = (st1['triggers'] == n and abs(st1['win_rate'] - round(wr, 2)) < 0.01)
            print(f"  一致性: {'✓ 通过' if ok else '✗ 不一致'}")

print("\n[8] 无未来函数抽验: 信号日次日收盘才是1日收益终点")
code, td = sig[0]['code'], sig[0]['trade_date']
dates = dm[code]
idx = dates.index(td)
base = pm[code][td]
future = pm[code][dates[idx + 1]]
manual = (future - base) / base * 100
via_func = qd._future_return(pm, dm, code, td, 1)
print(f"  {code} {td}: base={base} 次日={future} "
      f"手工={manual:.4f}% 函数={via_func:.4f}% "
      f"{'✓' if abs(manual - via_func) < 1e-9 else '✗'}")
