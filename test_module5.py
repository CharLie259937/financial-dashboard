# -*- coding: utf-8 -*-
"""模块5 D2验证: 批量回测入库 → 回读对账 → 双栏对比 → 协议回读
执行链: run_d2_backtests(建表 + 全量run入库) → 对账检查 → 双栏总表 → 主动事件状态修复重算"""
import sqlite3
import quant_data as qd

CHECKS = []


def check(name, ok, detail=''):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ''))


print("=" * 70)
print(f"[1] D2 批量回测执行入库 ({len(qd.BT_STRATEGIES)}策略×2段×3费率 + 双基准×2段; "
      "S3财报策略已于2026-09-02删除, 触发源active_events财报数据不合格)")
summary = qd.run_d2_backtests()
n_strat = len(qd.BT_STRATEGIES)
expect_runs = n_strat * len(qd.BT_FEE_SENSITIVITY) * 2 + 4
print(f"  入库: runs={summary['runs']} equity行={summary['equity_rows']} "
      f"trade行={summary['trade_rows']}")

conn = sqlite3.connect(qd.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 模块8起存在多批次(2026-09-03冻结批 vs 扩窗批), 全部对账限定最新批次;
# 同日重跑由引擎先清同日旧批(幂等), 因此最新 run_date 恰好一批
RD = cur.execute("SELECT MAX(run_date) FROM backtest_runs").fetchone()[0]
n_batches = cur.execute(
    "SELECT COUNT(DISTINCT run_date) FROM backtest_runs").fetchone()[0]
print(f"  最新批次 run_date={RD} (库内共 {n_batches} 个批次, 对账仅看最新)")

print()
print("[2] 表结构与行数对账")
tabs = sorted(r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'backtest_%'"
).fetchall())
check('4张回测表存在',
      tabs == ['backtest_equity', 'backtest_meta', 'backtest_runs', 'backtest_trades'],
      str(tabs))
n_runs = cur.execute(
    "SELECT COUNT(*) FROM backtest_runs WHERE run_date=?", (RD,)).fetchone()[0]
check('最新批次runs行数 = 策略×费率×2段 + 双基准×2段', n_runs == expect_runs,
      f'{n_runs} vs {expect_runs}')
n_s3 = sum(cur.execute(
    f"SELECT COUNT(*) FROM {t} WHERE strategy_id='S3'").fetchone()[0]
    for t in ['backtest_runs', 'backtest_trades'])
check('S3已彻底清除(2026-09-02删除, runs/trades零残留)', n_s3 == 0, f'残留{n_s3}行')
seg_days = dict(cur.execute(
    "SELECT segment, n_days FROM backtest_runs "
    "WHERE strategy_id='B1' AND fee=0 AND run_date=?", (RD,)).fetchall())
runs_per_seg = n_strat * len(qd.BT_FEE_SENSITIVITY) + 2
n_eq = cur.execute(
    "SELECT COUNT(*) FROM backtest_equity e JOIN backtest_runs r ON e.run_id=r.run_id "
    "WHERE r.run_date=?", (RD,)).fetchone()[0]
expect_eq = (seg_days['full'] + seg_days['oos']) * runs_per_seg
check('最新批次equity行数 = Σ(段日历天数 × 该段run数)', n_eq == expect_eq,
      f'{n_eq} vs {expect_eq} (full {seg_days["full"]}日/oos {seg_days["oos"]}日)')
n_tr = cur.execute(
    "SELECT COUNT(*) FROM backtest_trades t JOIN backtest_runs r ON t.run_id=r.run_id "
    "WHERE r.run_date=?", (RD,)).fetchone()[0]
check('交易明细已入库', n_tr > 0, f'{n_tr}行')

print()
print("[3] oos 段完整性: 策略首日净值=1 / 行数=n_days / 首日>知识截止日")
bad_first, bad_len = [], []
for r in cur.execute(
        "SELECT run_id, strategy_id, kind, n_days FROM backtest_runs "
        "WHERE segment='oos' AND run_date=?", (RD,)).fetchall():
    rows = cur.execute(
        "SELECT trade_date, strategy_value FROM backtest_equity "
        "WHERE run_id=? ORDER BY trade_date", (r['run_id'],)).fetchall()
    if len(rows) != r['n_days']:
        bad_len.append(f"{r['strategy_id']}({len(rows)}≠{r['n_days']})")
    if r['kind'] == 'strategy' and abs(rows[0]['strategy_value'] - 1.0) > 1e-9:
        bad_first.append(r['strategy_id'])
check('策略run首日净值=1(oos空仓起步)', not bad_first,
      ','.join(sorted(set(bad_first))) or '全部=1')
check('每run净值行数=n_days', not bad_len, ','.join(sorted(set(bad_len))) or '全部一致')
oos_start = cur.execute(
    "SELECT MIN(e.trade_date) FROM backtest_equity e "
    "JOIN backtest_runs r ON e.run_id=r.run_id "
    "WHERE r.segment='oos' AND r.run_date=?", (RD,)).fetchone()[0]
check('oos首日 > 知识截止日 2026-05-01', oos_start > qd.BT_KNOWLEDGE_CUTOFF, oos_start)

print()
print("[4] 费率单调性: 同段同策略, 费率↑ → 总收益不升")
viol = []
for seg in ('full', 'oos'):
    for sid in qd.BT_STRATEGIES:
        rets = {r['fee']: r['total_return'] for r in cur.execute(
            "SELECT fee, total_return FROM backtest_runs "
            "WHERE segment=? AND strategy_id=? AND kind='strategy' AND run_date=?",
            (seg, sid, RD)).fetchall()}
        if (len(rets) == 3
                and not (rets[0.0] >= rets[0.001] - 1e-12
                         and rets[0.001] >= rets[0.003] - 1e-12)):
            viol.append(f'{seg}/{sid}')
check(f'{n_strat}策略×2段费率档全单调', not viol, ','.join(viol) or '全部通过')

print()
print("[5] 触发数独立对账 (回测 n_triggers vs 信号/事件表 SQL 计数)")
print("    口径对齐: 触发日须有当日行情(宽表 open/close 非空)——引擎同款条件;"
    " 模块8回放后宽表覆盖全窗口(2018-06起), 早期信号计入对账")
HAS_Q = (" AND EXISTS (SELECT 1 FROM daily_feature_base f "
         "WHERE f.code=t.code AND f.trade_date=t.trade_date "
         "AND f.open IS NOT NULL AND f.close IS NOT NULL)")
for seg in ('full', 'oos'):
    date_cond = " AND t.trade_date > ?" if seg == 'oos' else ""
    date_args = [qd.BT_KNOWLEDGE_CUTOFF] if seg == 'oos' else []
    for sid, spec in qd.BT_STRATEGIES.items():
        if 'match' in spec:
            ms = spec['match']
            conds = ' OR '.join('(t.signal_type=? AND t.signal_subtype=?)' for _ in ms)
            params = [x for m in ms for x in m]
            sql_n = cur.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT t.code, t.trade_date "
                "FROM passive_signals t WHERE (" + conds + ")"
                + date_cond + HAS_Q + ")", params + date_args).fetchone()[0]
        else:
            sql_n = cur.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT t.code, t.trade_date "
                "FROM active_events t WHERE t.event_type=?"
                + date_cond + HAS_Q + ")",
                [spec['event_type']] + date_args).fetchone()[0]
        bt_n = cur.execute(
            "SELECT n_triggers FROM backtest_runs "
            "WHERE segment=? AND strategy_id=? AND fee=0 AND run_date=?",
            (seg, sid, RD)).fetchone()[0]
        check(f'{sid} {seg}段触发数', bt_n == sql_n, f'回测{bt_n} vs SQL{sql_n}')

print()
print("[6] 双栏对比总表 (费率0档)")
print(f"{'对象':<26}{'FULL收益':>9}{'OOS收益':>9}{'OOS年化':>9}"
      f"{'OOS夏普':>8}{'超额vsB1':>10}{'超额vsB2':>10}{'OOS笔':>6}")
order = list(qd.BT_STRATEGIES) + ['B1', 'B2']
for sid in order:
    rows = {r['segment']: r for r in cur.execute(
        "SELECT * FROM backtest_runs WHERE strategy_id=? AND fee=0 AND run_date=?",
        (sid, RD)).fetchall()}
    f, o = rows['full'], rows['oos']
    print(f"{sid} {f['strategy_name'][:12]:<14}"
          f"{f['total_return']*100:>8.2f}%{o['total_return']*100:>8.2f}%"
          f"{o['annual_return']*100:>8.2f}%"
          f"{(o['sharpe'] if o['sharpe'] is not None else 0):>8.2f}"
          f"{o['excess_vs_bh']*100:>9.2f}pp{o['excess_vs_index']*100:>9.2f}pp"
          f"{(o['n_trades'] or 0):>6}")

print()
print("[7] 跳空成本与容量诊断 (费率0)")
for sid in qd.BT_STRATEGIES:
    for seg in ('full', 'oos'):
        r = cur.execute(
            "SELECT avg_gap_cost, n_trades, n_triggers, n_rejected, "
            "n_end_dropped, avg_fund_util FROM backtest_runs "
            "WHERE strategy_id=? AND segment=? AND fee=0", (sid, seg)).fetchone()
        if not r or not r['n_triggers']:
            print(f"  {sid} {seg}: 无触发")
            continue
        gap = (r['avg_gap_cost'] if r['avg_gap_cost'] is not None else 0) * 100
        print(f"  {sid} {seg}: 触发{r['n_triggers']} 成交{r['n_trades']} "
              f"满仓拒{r['n_rejected']} 末端弃{r['n_end_dropped']} "
              f"资金占用{(r['avg_fund_util'] or 0)*100:.0f}% 平均跳空{gap:+.2f}%")

print()
print("[8] 协议参数回读 (backtest_meta)")
meta = qd.load_backtest_meta()
check('meta三键齐备', set(meta) == {'d2_protocol', 'segment_full', 'segment_oos'},
      str(sorted(meta)))
proto = meta['d2_protocol']['json']
check('知识截止日/冻结日与引擎常量一致',
      proto['knowledge_cutoff'] == qd.BT_KNOWLEDGE_CUTOFF
      and proto['freeze_date'] == qd.BT_FREEZE_DATE,
      f"{proto['knowledge_cutoff']} / {proto['freeze_date']}")
print(f"  诚实性声明: {proto['honesty_note'][:56]}...")
seg_full = meta['segment_full']['json']
seg_oos = meta['segment_oos']['json']
check('段JSON含全部策略+双基准',
      set(seg_full['strategies']) == set(qd.BT_STRATEGIES)
      and set(seg_oos['baselines']) == {'B1', 'B2'},
      f"full {len(seg_full['strategies'])}策略 / oos {len(seg_oos['strategies'])}策略")

print()
print("[9] 重跑一致性抽查 (S2 oos fee=0: 现场重算 vs 入库值)")
res = qd.run_backtest('S2', qd.BT_MAX_POSITIONS, 0.0,
                      start_date=qd.BT_KNOWLEDGE_CUTOFF)
db = cur.execute(
    "SELECT total_return, n_trades FROM backtest_runs "
    "WHERE strategy_id='S2' AND segment='oos' AND fee=0 AND run_date=?",
    (RD,)).fetchone()
check('总收益一致(容差1e-9)',
      abs(res['metrics']['total_return'] - db['total_return']) < 1e-9,
      f"{res['metrics']['total_return']:.8f} vs {db['total_return']:.8f}")
check('成交笔数一致', res['metrics']['n_trades'] == db['n_trades'],
      f"{res['metrics']['n_trades']} vs {db['n_trades']}")

print()
print("[10] 模块3主动事件状态修复重算 (signal_effect_report)")
qd.run_module3_analysis()
empty_st = cur.execute(
    "SELECT COUNT(*) FROM signal_effect_report "
    "WHERE status IS NULL OR status=''").fetchone()[0]
check('无空状态行', empty_st == 0, f'剩余{empty_st}行')
for r in cur.execute(
        "SELECT DISTINCT group_key, status FROM signal_effect_report "
        "WHERE category='active' ORDER BY group_key").fetchall():
    print(f"  {r['group_key']} → {r['status']}")

print()
print("=" * 70)
n_ok = sum(1 for _, ok, _ in CHECKS if ok)
print(f"D2验证完成: {n_ok}/{len(CHECKS)} 项通过")
for name, ok, detail in CHECKS:
    if not ok:
        print(f"  ✗ 未通过: {name} — {detail}")
conn.close()
