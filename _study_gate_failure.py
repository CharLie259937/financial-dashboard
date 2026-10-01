# -*- coding: utf-8 -*-
"""门控失效机理研究 (2026-10-01, 观察层 · 风控模型设计输入)
三个失效机制检验:
  F2 时间尺度错配: 宏观封锁挡掉的 S2 族触发, 在 H=5 vs H=10 下的反事实对比
  F3 域内二阶条件缺失: 高波动域(σ20≥80%)触发按前期20日趋势分拆(V型反弹 vs 趋势下跌)
  F5 限制≠限额: S1a/S2 前向并发持仓的同票重叠(槽数≠集中度)
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data
import numpy as np
import pandas as pd

conn = quant_data.get_db()
POOL = tuple(r[0] for r in conn.execute("SELECT code FROM stock_pool WHERE is_active=1"))
ph = ','.join('?' * len(POOL))

# S2 族触发(日级去重)
trigs = sorted(set((r[0], r[1]) for r in conn.execute(f"""
    SELECT DISTINCT code, trade_date FROM passive_signals WHERE code IN ({ph})
    AND signal_type||'/'||signal_subtype IN ('boll_break/跌破下轨','price_limit/大跌','rsi_oversold/超卖')
    ORDER BY trade_date""", POOL)))
blocked = set((r['code'], r['trade_date']) for r in conn.execute(
    "SELECT code, trade_date FROM macro_overlay_days"))

px, dates = {}, {}
for c in POOL:
    rows = conn.execute("SELECT trade_date, close FROM daily_quotes WHERE code=? ORDER BY trade_date", (c,)).fetchall()
    px[c] = dict(rows); dates[c] = [d for d, _ in rows]
CLOSE = px

def stat_ret(code, d, hold):
    ds = dates[code]
    if d not in CLOSE[code]: return None
    try: i = ds.index(d)
    except ValueError: return None
    if i + hold >= len(ds): return None
    return CLOSE[code][ds[i + hold]] / CLOSE[code][d] - 1

# σ20(T-1) 与 前期20日趋势(T-1 收盘 / T-21 收盘 - 1)
sig20, trend20 = {}, {}
for c in POOL:
    s = pd.Series(px[c], dtype=float)
    ret = s.pct_change()
    sig20[c] = (ret.rolling(20).std() * np.sqrt(252)).shift(1)
    trend20[c] = (s / s.shift(20) - 1).shift(1)

WINDOWS = {'full': '2021-01-04', 'oos': '2026-05-04', 'forward': '2026-08-28'}

# ---- F2: 封锁挡掉的 S2 触发, H=5 vs H=10 ----
print("===== F2 时间尺度错配: 宏观封锁挡掉的 S2 族触发, H=5 vs H=10 反事实 =====")
for w, wstart in WINDOWS.items():
    rows = []
    for c, d in trigs:
        if d < wstart or (c, d) not in blocked: continue
        r5, r10 = stat_ret(c, d, 5), stat_ret(c, d, 10)
        if r5 is not None and r10 is not None:
            rows.append((c, d, r5 * 100, r10 * 100))
    if rows:
        a5, a10 = np.array([r[2] for r in rows]), np.array([r[3] for r in rows])
        print(f"  [{w}] n={len(rows)}  H=5均值{a5.mean():+7.2f}% 胜率{(a5>0).mean()*100:.0f}%   "
              f"H=10均值{a10.mean():+7.2f}% 胜率{(a10>0).mean()*100:.0f}%  ← 同一批被挡交易, 持有期翻转结论")

# ---- F3: 高波动域内按 trend20 二阶分拆 ----
print("\n===== F3 域内二阶条件: 高波动域(σ20≥80%)触发 × 前期20日趋势 =====")
for w, wstart in WINDOWS.items():
    hv_dn, hv_up, hv_na = [], [], []
    for c, d in trigs:
        if d < wstart: continue
        s20, t20 = sig20[c].get(d), trend20[c].get(d)
        r5 = stat_ret(c, d, 5)
        if r5 is None or s20 is None or s20 != s20 or s20 < 0.80: continue
        if t20 is None or t20 != t20: continue
        (hv_dn if t20 < 0 else hv_up).append(r5 * 100)
    def agg(a): return f"n={len(a):4d} 均{np.mean(a):+7.2f}% 胜率{(np.array(a)>0).mean()*100:.0f}%" if a else "n=0"
    print(f"  [{w}] 高波动域∧趋势下跌: {agg(hv_dn)}")
    print(f"  [{w}] 高波动域∧趋势非跌: {agg(hv_up)}")
# 对照: 9月前向每票触发数量
print("  (9月前向触发按股票: ", end='')
from collections import Counter
cnt = Counter(c for c, d in trigs if d >= '2026-08-28')
print(dict(cnt), ")")

# ---- F5: 前向并发持仓同票重叠 ----
print("\n===== F5 限制≠限额: 前向并发持仓结构 =====")
for sid in ('S1a', 'S1b', 'S2'):
    trades = conn.execute("""SELECT code, trigger_date, entry_date, exit_date FROM forward_trades
        WHERE strategy_id=? ORDER BY entry_date""", (sid,)).fetchall()
    # 逐日重放在仓
    spans = [(t['code'], t['entry_date'], t['exit_date']) for t in trades]
    if not spans: continue
    alld = sorted(set(d for _, e, x in spans for d in (e, x)))
    worst = []
    for d in alld:
        open_pos = [c for c, e, x in spans if e <= d <= x]
        if len(open_pos) >= 2:
            from collections import Counter as Ct
            cc = Ct(open_pos)
            top = cc.most_common(1)[0]
            worst.append((d, len(open_pos), top[0], top[1]))
    if worst:
        max_same = max(w[3] for w in worst)
        days_multi = len(worst)
        print(f"  {sid}: 多仓日{days_multi}天, 单日最多并发{max(w[1] for w in worst)}仓, "
              f"同票并发最大{max_same}仓(K=3下同票占比可达{max_same}/3)")
        for d, n, c, k in worst[:6]:
            print(f"      {d}: 在仓{n}仓, 其中 {c} 占 {k} 仓")
    else:
        print(f"  {sid}: 无多仓并发日")
conn.close()
