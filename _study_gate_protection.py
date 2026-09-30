# -*- coding: utf-8 -*-
"""门控与限制的保护作用研究 (2026-09-30, 观察层)
对每类门控/限制, 计算"被挡掉的交易"的反事实 H 日统计口径收益(信号日收盘→T+H收盘):
  A. 宏观封锁 (S*M): 落在 macro_overlay_days 封锁日(按股票)的触发
  B. 波动域门 (SxV): σ20(T-1)年化 < 80% 的触发 (被滤掉的部分)
  C. 满仓拒绝 (S2): 引擎实际成交的触发之外的触发(FIFO 拒绝+末端放弃近似)
  D. T+1 执行延迟: forward_trades.gap_cost 直接读数
  E. 前向最大回撤对照 (变体 vs 母策略)
口径: 统计口径收益(与模块3一致), 无未来数据红线只约束特征输入——反事实收益是效果统计, 允许用未来H日价格。
窗口: full(2021-01-04起) / oos(2026-05-04起) / forward(2026-08-28起)
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data
import numpy as np

conn = quant_data.get_db()
POOL = tuple(r[0] for r in conn.execute("SELECT code FROM stock_pool WHERE is_active=1"))
ph = ','.join('?' * len(POOL))

# ---- 触发集(与引擎同口径: DISTINCT 日级) ----
FAM = {
    'S1a/S1b(大跌)': ("price_limit/大跌", 10),
    'S1e(RSI24超卖)': ("rsi24_oversold/超卖", 10),
    'S2(下轨∨大跌∨RSI14)': ("S2", 5),
}
def load_triggers(fam_key):
    if FAM[fam_key][0] == 'S2':
        rows = conn.execute(f"""SELECT DISTINCT code, trade_date, signal_type||'/'||signal_subtype f
            FROM passive_signals WHERE code IN ({ph})
            AND signal_type||'/'||signal_subtype IN ('boll_break/跌破下轨','price_limit/大跌','rsi_oversold/超卖')
            ORDER BY trade_date""", POOL).fetchall()
        return sorted(set((r[0], r[1]) for r in rows)), 5
    st, hold = FAM[fam_key]
    rows = conn.execute(f"""SELECT DISTINCT code, trade_date
        FROM passive_signals WHERE code IN ({ph})
        AND signal_type||'/'||signal_subtype=? ORDER BY trade_date""", POOL + (st,)).fetchall()
    return [(r[0], r[1]) for r in rows], hold

# ---- 行情 & σ20 ----
px = {}
for c in POOL:
    rows = conn.execute("SELECT trade_date, close FROM daily_quotes WHERE code=? ORDER BY trade_date", (c,)).fetchall()
    px[c] = rows
import pandas as pd
sig20 = {}
for c in POOL:
    s = pd.Series({d: v for d, v in px[c]}, dtype=float)
    ret = s.pct_change()
    sig20[c] = (ret.rolling(20).std() * np.sqrt(252)).shift(1)  # T-1 已知口径

CLOSE = {c: dict(px[c]) for c in POOL}
DATES = {c: [d for d, _ in px[c]] for c in POOL}

def stat_ret(code, d, hold):
    """信号日收盘 → T+hold 收盘 (模块3口径); 数据不足返回 None"""
    ds = DATES[code]
    if d not in CLOSE[code]:
        return None
    try:
        i = ds.index(d)
    except ValueError:
        return None
    if i + hold >= len(ds):
        return None
    return CLOSE[code][ds[i + hold]] / CLOSE[code][d] - 1

# ---- 封锁日集合 ----
blocked = set((r['code'], r['trade_date']) for r in conn.execute("SELECT code, trade_date FROM macro_overlay_days"))
print(f"封锁台账总行数: {len(blocked)} (股,日) 对")

WINDOWS = {'full': '2021-01-04', 'oos': '2026-05-04', 'forward': '2026-08-28'}

def study(fam_key):
    trigs, hold = load_triggers(fam_key)
    print(f"\n===== {fam_key}  (H={hold}, 触发 {len(trigs)}) =====")
    for wname, wstart in WINDOWS.items():
        wt = [(c, d) for c, d in trigs if d >= wstart]
        if not wt:
            continue
        rows_all = [(c, d, stat_ret(c, d, hold)) for c, d in wt]
        rows_ok = [r for r in rows_all if r[2] is not None]
        if not rows_ok:
            continue
        all_ret = np.array([r[2] * 100 for r in rows_ok])
        # A 宏观封锁挡掉的
        blk = [r for r in rows_ok if (r[0], r[1]) in blocked]
        # B 波动域滤掉的 (σ20 T-1 < 80%)
        low_vol = [r for r in rows_ok if not (sig20[r[0]].get(r[1]) is not None and sig20[r[0]][r[1]] == sig20[r[0]][r[1]] and sig20[r[0]][r[1]] >= 0.80)]
        hi_vol = [r for r in rows_ok if r not in low_vol]
        def agg(rs):
            if not rs: return ('n=0', '—', '—')
            a = np.array([r[2] * 100 for r in rs])
            return (f"n={len(rs)}", f"{a.mean():+.2f}%", f"胜率{(a>0).mean()*100:.0f}%")
        print(f"  [{wname}] 全触发 {agg(rows_ok)[0]} 均{agg(rows_ok)[1]} | "
              f"A封锁挡掉 {agg(blk)[0]} 均{agg(blk)[1]} | "
              f"B低波动域(被SxV滤) {agg(low_vol)[0]} 均{agg(low_vol)[1]} | "
              f"高波动域(保留) {agg(hi_vol)[0]} 均{agg(hi_vol)[1]}")
        if wname == 'forward' and blk:
            for c, d, r in blk:
                print(f"      前向封锁实例: {c} {d} → 反事实{r*100:+.2f}%")
        if wname == 'oos' and blk:
            losers = [r for r in blk if r[2] < 0]
            print(f"      oos封锁明细: {len(blk)}笔挡掉, 其中亏损{len(losers)}笔, 挡掉的均值见上")
    return trigs, hold

for fam in ('S1a/S1b(大跌)', 'S1e(RSI24超卖)', 'S2(下轨∨大跌∨RSI14)'):
    study(fam)

# ---- C: S2 前向 满仓拒绝反事实 ----
print("\n===== C. S2 前向: 实际成交 vs 全触发 =====")
trigs2, _ = load_triggers('S2(下轨∨大跌∨RSI14)')
taken = set((r['code'], r['trigger_date']) for r in conn.execute(
    "SELECT code, trigger_date FROM forward_trades WHERE strategy_id='S2'"))
fwd = [(c, d) for c, d in trigs2 if d >= '2026-08-28']
taken_ok = [r for r in (stat_ret(c, d, 5) for c, d in fwd if (c, d) in taken) if r is not None]
not_taken = [(c, d, stat_ret(c, d, 5)) for c, d in fwd if (c, d) not in taken]
nt_ok = [r for r in not_taken if r[2] is not None]
print(f"  前向触发 {len(fwd)}, S2 实际成交 {len(taken_ok)}笔 均{np.mean(taken_ok)*100:+.2f}%")
if nt_ok:
    a = np.array([r[2] * 100 for r in nt_ok])
    print(f"  未成交(满仓拒/末端弃) {len(nt_ok)}笔 均{a.mean():+.2f}% 胜率{(a>0).mean()*100:.0f}%  ← 拒绝的保护读数(负=拒得对)")
    for c, d, r in sorted(nt_ok, key=lambda x: x[1]):
        print(f"      拒绝实例: {c} {d} → 反事实{r*100:+.2f}%")

# ---- D: T+1 延迟 (gap_cost) ----
print("\n===== D. T+1 执行延迟读数 (forward_trades.gap_cost = 引擎收益-统计口径收益) =====")
for r in conn.execute("""SELECT strategy_id, COUNT(*) n, ROUND(AVG(gap_cost)*100,2) avg_gap
    FROM forward_trades GROUP BY strategy_id ORDER BY avg_gap"""):
    print(f"  {r[0]:6s} n={r[1]:<3d} 平均跳空成本 {r[2]:+.2f}%  (负=T+1开盘比信号日收盘买得更便宜=下跌市的保护)")

# ---- E: 前向最大回撤 ----
print("\n===== E. 前向最大回撤 (net值口径) =====")
def maxdd(vals):
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd * 100
for r in conn.execute("SELECT DISTINCT strategy_id FROM forward_equity"):
    sid = r[0]
    navs = [x[0] for x in conn.execute(
        "SELECT nav FROM forward_equity WHERE strategy_id=? ORDER BY trade_date", (sid,))]
    if len(navs) > 2:
        print(f"  {sid:6s} NAV末 {navs[-1]:.4f}  最大回撤 {maxdd(navs):+.2f}%")
conn.close()
