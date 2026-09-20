# -*- coding: utf-8 -*-
"""D4 风险分解视图数据层插入: get_risk_decomposition_view (CRLF 文件用脚本插入)"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

P = r"C:\Users\lenovo\Desktop\financial_dashboard\quant_data.py"
src = open(P, encoding='utf-8', newline='').read()

anchor = ("                   '的分散保护在熊市系统性打折, A股~美股分散即使在2022熊市仍稳固')\r\n"
          "    return out\r\n")
assert src.count(anchor) == 1, 'anchor not unique'

func = '''

def get_risk_decomposition_view(window=60):
    """D4 风险分解视图(优化清单 P1, 2026-09-21 交付): 活跃池等权组合的百分比风险贡献
    PCR_i = w_i·(Σw)_i / σ_p² (Σ PCR = 1) + ATR14% 倒数对照权重并列。
    口径: 三市场交易日交集上的日收益样本协方差(样本内最近 window 日, 无未来数据);
    ATR = 14日真实波幅均值/最新收盘。**只研究不改仓**(冻结协议), 支撑 S2VW 预注册的
    方向假设与"等权非风险最优"的持续可视化。纯读路径, 无 DDL。"""
    import pandas as _pd
    import numpy as _np
    conn = get_db()
    pool = [dict(r) for r in conn.execute(
        "SELECT code, name, market FROM stock_pool WHERE is_active=1 ORDER BY code")]
    if len(pool) < 2:
        conn.close()
        return {'error': '活跃池不足 2 只, 无法做组合风险分解'}
    closes, tr_last = {}, {}
    for p in pool:
        rows2 = conn.execute(
            "SELECT trade_date, close, high, low FROM daily_quotes WHERE code=? "
            "ORDER BY trade_date", (p['code'],)).fetchall()
        if len(rows2) < 20:
            continue
        s = _pd.Series({r['trade_date']: r['close'] for r in rows2}, dtype=float)
        closes[p['code']] = s
        # ATR14(全历史滚动, 取最新值)——用最新快照, 与 PCR 的 window 窗口独立
        h = _pd.Series({r['trade_date']: r['high'] for r in rows2}, dtype=float)
        lo = _pd.Series({r['trade_date']: r['low'] for r in rows2}, dtype=float)
        prev_c = s.shift(1)
        tr = _pd.concat([(h - lo), (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14, min_periods=14).mean().iloc[-1]
        tr_last[p['code']] = (atr14 / s.iloc[-1]) if (atr14 == atr14 and s.iloc[-1] > 0) else None
    conn.close()
    px = _pd.DataFrame(closes).sort_index()
    rets = px.pct_change().dropna(how='any').tail(window)
    if len(rets) < window * 0.6:
        return {'error': f'三市场交集交易日不足(需~{int(window*0.6)}, 实际 {len(rets)} 日), '
                         '检查各市场行情完整性'}
    codes = list(rets.columns)
    cov = rets.cov().values  # 样本协方差 ddof=1
    w_eq = _np.full(len(codes), 1.0 / len(codes))
    def _port_vol(w):
        return float(_np.sqrt(w @ cov @ w) * (252 ** 0.5))
    sig_eq = _port_vol(w_eq)
    mrc = cov @ w_eq / (sig_eq / (252 ** 0.5))          # 日频边际风险贡献
    pcr = w_eq * mrc / (w_eq @ cov @ w_eq)              # 百分比贡献, 和=1
    atr = {c: tr_last.get(c) for c in codes}
    ok_atr = [c for c in codes if atr.get(c) and atr[c] > 0]
    w_atr = {c: (1.0 / atr[c]) / sum(1.0 / atr[x] for x in ok_atr) if c in ok_atr else None
             for c in codes}
    vol_atr = _port_vol(_np.array([w_atr.get(c) or 0.0 for c in codes])) if len(ok_atr) == len(codes) else None
    rows = []
    name_map = {p['code']: p for p in pool}
    for i, c in enumerate(codes):
        rows.append({
            'code': c, 'name': name_map[c]['name'], 'market': name_map[c]['market'],
            'ann_vol_pct': round(float(rets[c].std() * (252 ** 0.5) * 100), 2),
            'weight_eq': round(float(w_eq[i]), 4),
            'pcr_pct': round(float(pcr[i] * 100), 2),
            'atr14_pct': round(atr[c] * 100, 2) if atr.get(c) else None,
            'w_atr': round(w_atr[c], 4) if w_atr.get(c) else None,
            'w_diff_pp': round((w_atr[c] - w_eq[i]) * 100, 2) if w_atr.get(c) else None,
        })
    rows.sort(key=lambda r: -r['pcr_pct'])
    return {'rows': rows, 'window': window, 'n_days': len(rets),
            'sample': (str(rets.index[0]), str(rets.index[-1])),
            'port_vol_eq_pct': round(sig_eq * 100, 2),
            'port_vol_atr_pct': round(vol_atr * 100, 2) if vol_atr else None,
            'note': ('PCR=百分比风险贡献(等权下, Σ=100%): >权重者主导组合波动; ATR倒数权重'
                     '为对照建议非指令——改仓须走 S2VW 预注册(资金维度一次一个变体); '
                     '协方差窗口=样本内最近%d日三市场交集, 无未来数据' % window)}
'''

src = src.replace(anchor, anchor + func, 1)
open(P, 'w', encoding='utf-8', newline='').write(src)
print('inserted get_risk_decomposition_view')
