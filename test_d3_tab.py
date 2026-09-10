# -*- coding: utf-8 -*-
"""D3 验证: 模拟 Tab 8 数据层与图表构建(不依赖 Streamlit 运行时)
对 Desktop 库执行 Tab 8 同款查询 + 净值/回撤/费率图数据构建 + 结论计算
期望值基准: 2026-09-09 五股池收敛批次(00100 已由用户 09-08 移除, 02513 智谱 09-08 入池,
SNDK 闪迪 09-09 16:26 入池补课后重生成)。oos: S2 最强 +93.15%/超额90.08pp;
S1a 大跌反弹 +40.26% — SNDK 触发竞争稀释了 4 股批次中 02513 极端波动主导的超额
(槽位竞争效应: 同日触发抢槽, 新增样本非简单叠加, 与模块9机制对照设计同类)
注意: test_module5 每次运行会重生成当日批次(run_d2_backtests 同日覆盖), 行情刷新后
B1(恒满仓)端点移动而空仓策略不动 → 超额期望值以"数据刷新后重生成"的批次为准"""
import sys
import json
import sqlite3
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, r"C:\Users\lenovo\Desktop\financial_dashboard")
import quant_data

ok, fail = 0, []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail.append(name)
        print(f"  ✗ {name} {detail}")


conn = quant_data.get_db()
# 模块8 多批次模式: 取最新批次(与看板默认视图一致), 跨日批次保留作对照
bt_batch = conn.execute("SELECT MAX(run_date) FROM backtest_runs").fetchone()[0]
n_batches = conn.execute(
    "SELECT COUNT(DISTINCT run_date) FROM backtest_runs").fetchone()[0]
bt_rows = conn.execute("""
    SELECT run_id, strategy_id, strategy_name, kind, segment, fee,
           start_date, end_date, n_days, total_return, annual_return,
           annual_vol, sharpe, max_drawdown, mdd_start, mdd_end,
           daily_win_rate, seg1_annual, seg2_annual, n_trades,
           avg_trade_ret, avg_stat_ret, avg_gap_cost, avg_fund_util,
           n_triggers, n_rejected, n_end_dropped, excess_vs_bh,
           excess_vs_index, run_date
    FROM backtest_runs WHERE run_date=?
    ORDER BY strategy_id, segment, fee""", (bt_batch,)).fetchall()
eq_rows = conn.execute("""
    SELECT e.run_id, e.trade_date, e.strategy_value
    FROM backtest_equity e JOIN backtest_runs r ON e.run_id = r.run_id
    WHERE r.fee = 0.0 AND r.run_date=?""", (bt_batch,)).fetchall()
tr_rows = conn.execute("""
    SELECT t.strategy_id, t.segment, t.fee, t.code, t.market, t.trigger_date,
           t.entry_date, t.entry_price, t.exit_date, t.exit_price, t.return_pct,
           t.stat_ret, t.gap_cost, t.holding_days
    FROM backtest_trades t JOIN backtest_runs r ON t.run_id = r.run_id
    WHERE r.run_date=? ORDER BY t.trigger_date, t.code""", (bt_batch,)).fetchall()
proto_row = conn.execute(
    "SELECT result_json FROM backtest_meta WHERE meta_key='d2_protocol' "
    "AND run_date=? ORDER BY id DESC LIMIT 1", (bt_batch,)).fetchone()
conn.close()
proto = json.loads(proto_row[0]) if proto_row else {}

print("[1] 数据装载")
n_strat = len(quant_data.BT_STRATEGIES)
exp_runs = n_strat * len(quant_data.BT_FEE_SENSITIVITY) * 2 + 4
check(f"runs {exp_runs} 行 (实际 {len(bt_rows)})", len(bt_rows) == exp_runs)
check(f"多批次并存 {n_batches} 个 (最新 {bt_batch}, 模块8跨日保留)",
      n_batches >= 2 or len(bt_rows) == exp_runs)
days = {r['segment']: r['n_days'] for r in bt_rows
        if r['strategy_id'] == 'B1' and r['fee'] == 0.0}
exp_eq = (n_strat + 2) * sum(days.values())
check(f"净值 fee0 行数 {exp_eq} (实际 {len(eq_rows)})", len(eq_rows) == exp_eq)
exp_tr = sum(r['n_trades'] or 0 for r in bt_rows)
check(f"交易明细 {exp_tr} 行 (与 runs 汇总一致, 实际 {len(tr_rows)})",
      len(tr_rows) == exp_tr)
check("meta 含 d2_protocol", bool(proto))
n_s3 = sum(1 for r in bt_rows if r['strategy_id'] == 'S3')
check("S3 已清除(2026-09-02删除, 触发源财报数据不合格)", n_s3 == 0)

print("[2] 索引与工具(与看板同款)")
def _bt_run(sid, seg, fee=0.0):
    return next((r for r in bt_rows if r['strategy_id'] == sid
                 and r['segment'] == seg and r['fee'] == fee), None)

def _row_name(*rows):
    for r in rows:
        if r is not None and r['strategy_name']:
            return r['strategy_name']
    return ''

bt_strats = sorted({r['strategy_id'] for r in bt_rows if r['kind'] == 'strategy'})
run_meta = {(r['strategy_id'], r['segment']): r for r in bt_rows if r['fee'] == 0.0}
eq_by_run = {}
for e in eq_rows:
    eq_by_run.setdefault(e['run_id'], []).append((e['trade_date'], e['strategy_value']))
for k in eq_by_run:
    eq_by_run[k].sort()

def _bt_curve(sid, seg):
    r = run_meta.get((sid, seg))
    if not r:
        return [], []
    pts = eq_by_run.get(r['run_id'], [])
    return [p[0] for p in pts], [p[1] for p in pts]

check(f"策略集 {n_strat} 个 {bt_strats}",
      bt_strats == ['S1a', 'S1aM', 'S1b', 'S1c', 'S1d', 'S1e', 'S2', 'S2M'])
check(f"fee0 run 索引 {n_strat + 2} 组({n_strat}策略+双基准)×2段",
      len(run_meta) == (n_strat + 2) * 2, f"实际 {len(run_meta)}")

print("[3] 净值/回撤曲线构建 (两段)")
for seg in ['oos', 'full']:
    expect_days = days[seg]
    for sid in bt_strats + ['B1', 'B2']:
        xs, ys = _bt_curve(sid, seg)
        if not check(f"{seg} {sid} 曲线 {expect_days} 点 (实际 {len(xs)})", len(xs) == expect_days):
            break
    # 回撤水下曲线
    xs, ys = _bt_curve('S1a', seg)
    peak, dds = -1e9, []
    for v in ys:
        peak = max(peak, v)
        dds.append((v / peak - 1) * 100)
    check(f"{seg} S1a 回撤序列非正且首点=0",
          all(d <= 1e-9 for d in dds) and abs(dds[0]) < 1e-12)
    check(f"{seg} oos 首日净值=1" if seg == 'oos' else f"full 首日≈1",
          abs(ys[0] - 1.0) < 1e-9 if seg == 'oos' else abs(ys[0] - 1.0) < 0.01)

print("[4] Row 名称工具(修复点: sqlite3.Row 无 .get)")
nm_s1a = _row_name(_bt_run('S1a', 'oos'))
nm_b1 = _row_name(_bt_run('B1', 'oos'))
nm_none = _row_name(None, None)
check(f"S1a 名称非空 ({nm_s1a})", bool(nm_s1a))
check(f"B1 名称非空 ({nm_b1})", bool(nm_b1))
check("空 Row 返回空串", nm_none == '')

print("[5] 双段对比总表构建(费率0, 含 B1/B2)")
def _n(v, scale=100, nd=2):
    return None if v is None else round(v * scale, nd)

cmp_rows = []
for sid in bt_strats + ['B1', 'B2']:
    f_r, o_r = _bt_run(sid, 'full'), _bt_run(sid, 'oos')
    nm = _row_name(f_r, o_r) or sid
    cmp_rows.append({
        '对象': f"{sid} {nm}",
        'FULL收益%': _n(f_r and f_r['total_return']),
        'OOS收益%': _n(o_r and o_r['total_return']),
        'OOS年化%': _n(o_r and o_r['annual_return']),
        'OOS夏普': _n(o_r and o_r['sharpe'], scale=1),
        'OOS回撤%': _n(o_r and o_r['max_drawdown']),
        'OOS笔数': (o_r['n_trades'] if o_r else None),
        'OOS超额vsB1(pp)': _n(o_r and o_r['excess_vs_bh']),
        'OOS超额vsB2(pp)': _n(o_r and o_r['excess_vs_index']),
    })
cmp_df = pd.DataFrame(cmp_rows)
for c in ['FULL收益%', 'OOS收益%', 'OOS年化%', 'OOS夏普', 'OOS回撤%',
          'OOS笔数', 'OOS超额vsB1(pp)', 'OOS超额vsB2(pp)']:
    cmp_df[c] = pd.to_numeric(cmp_df[c], errors='coerce')
check(f"总表 {n_strat + 2} 行 (实际 {len(cmp_df)})", len(cmp_df) == n_strat + 2)
check("数值列 dtype 均为数值型 int64/float64 (无 object 混型, Arrow 安全)",
      all(str(cmp_df[c].dtype) in ('float64', 'int64')
          for c in cmp_df.columns if c != '对象'))
s1a = cmp_df[cmp_df['对象'].str.startswith('S1a ')].iloc[0]
check(f"S1a OOS收益 40.26 (实际 {s1a['OOS收益%']})", abs(s1a['OOS收益%'] - 40.26) < 0.01)
check(f"S1a OOS超额vsB1 36.89pp (实际 {s1a['OOS超额vsB1(pp)']})",
      abs(s1a['OOS超额vsB1(pp)'] - 36.89) < 0.01)

print("[6] 费率敏感性图构建")
fig_fee = make_subplots(rows=1, cols=2, subplot_titles=("full 段", "oos 段"))
fee_colors = {0.0: '#43A047', 0.001: '#FB8C00', 0.003: '#E53935'}
trace_cnt = 0
for col, seg in enumerate(['full', 'oos'], start=1):
    for fee in [0.0, 0.001, 0.003]:
        vals, labels = [], []
        for sid in bt_strats:
            r = _bt_run(sid, seg, fee)
            vals.append(_n(r and r['total_return']))
            labels.append(sid)
        fig_fee.add_trace(go.Bar(x=labels, y=vals, name=f"费率{fee*100:.1f}%",
                                 marker_color=fee_colors[fee], showlegend=(col == 1)),
                          row=1, col=col)
        trace_cnt += 1
fig_fee.update_layout(barmode='group', template="plotly_white", height=360)
check(f"费率图 trace 数 6 (实际 {trace_cnt})", trace_cnt == 6)
oos_s1a_fee3 = _bt_run('S1a', 'oos', 0.003)['total_return']
check(f"S1a oos fee0.3% = 35.13% (实际 {oos_s1a_fee3*100:.2f}%)",
      abs(oos_s1a_fee3 - 0.3513) < 0.001)

print("[7] 交易明细过滤(oos×fee0×S1a)")
trs = [t for t in tr_rows if t['strategy_id'] == 'S1a' and t['segment'] == 'oos'
       and abs(t['fee'] - 0.0) < 1e-9]
exp_s1a_tr = _bt_run('S1a', 'oos')['n_trades']
check(f"S1a oos fee0 交易 {exp_s1a_tr} 笔 (与runs汇总一致, 实际 {len(trs)})",
      len(trs) == exp_s1a_tr)
tr_df = pd.DataFrame([{
    '代码': t['code'], '市场': t['market'],
    '触发日': t['trigger_date'], '买入日': t['entry_date'],
    '买入价': t['entry_price'], '卖出日': t['exit_date'],
    '卖出价': t['exit_price'], '收益%': _n(t['return_pct']),
    '统计口径%': _n(t['stat_ret']), '跳空成本pp': _n(t['gap_cost']),
    '持有天数': t['holding_days'],
} for t in trs])
for c in ['买入价', '卖出价', '收益%', '统计口径%', '跳空成本pp', '持有天数']:
    tr_df[c] = pd.to_numeric(tr_df[c], errors='coerce')
check(f"交易表 {exp_s1a_tr} 行 × 11 列 (实际 {tr_df.shape})",
      tr_df.shape == (exp_s1a_tr, 11))
check(f"均收益可算 ({tr_df['收益%'].mean():.2f}%)", not pd.isna(tr_df['收益%'].mean()))

print("[8] 自动结论逻辑(看板同款)")
conclusions = []
oos_strats = [(_bt_run(sid, 'oos'), sid) for sid in bt_strats]
oos_valid = [(r, sid) for r, sid in oos_strats if r and r['excess_vs_bh'] is not None]
best_r, best_sid = max(oos_valid, key=lambda x: x[0]['excess_vs_bh'])
check(f"样本外最强 = S2 (5股池SNDK入池后S2超额90.08pp居首, 实际 {best_sid})", best_sid == 'S2')
oos_fee3 = [(_bt_run(sid, 'oos', 0.003), sid) for sid in bt_strats]
fee_fragile = [sid for r, sid in oos_fee3
               if r and r['total_return'] is not None and r['total_return'] <= 0]
check(f"费率脆弱名单 = 仅S1d (5股池oos费后仅S1d为负, 实际 {fee_fragile})",
      fee_fragile == ['S1d'])
zero_trig = [sid for r, sid in oos_strats if not r or not r['n_triggers']]
check(f"S3删除后全部策略 oos 段均有触发, 「无法样本外验证」结论不再触发 (实际零触发: {zero_trig})",
      not zero_trig)
s2_oos = _bt_run('S2', 'oos')
check(f"S2 oos 满仓拒率>30% 触发容量约束结论 (实际 {s2_oos['n_rejected']}/{s2_oos['n_triggers']})",
      s2_oos['n_rejected'] / s2_oos['n_triggers'] > 0.3)
s1e_oos = _bt_run('S1e', 'oos')
check(f"S1e oos 超额 +4.33pp (5股池批次; 冻结协议: 回测不改观察状态, 裁决=前向)",
      abs(s1e_oos['excess_vs_bh'] - 0.0433) < 0.005
      and quant_data.BT_STRATEGIES['S1e'].get('observation') is True)
check("协议字段齐备", all(k in proto for k in
      ['knowledge_cutoff', 'freeze_date', 'honesty_note', 'freeze_rule']))

print()
print("=" * 70)
print(f"D3 数据层验证完成: {ok}/{ok + len(fail)} 项通过")
if fail:
    print("失败明细:")
    for f in fail:
        print(f"  ✗ {f}")
    sys.exit(1)
