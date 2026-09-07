# -*- coding: utf-8 -*-
"""模块8测试: 回填覆盖/交界连续性/回放幂等/三口径/RSI24时间分割/扩窗回测"""
import os
import sqlite3
import quant_data as qd

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


print("=" * 62)
print("[1] 表结构: source 列 + caliber 列")
print("=" * 62)
conn = qd.get_db()
sig_cols = [r[1] for r in conn.execute("PRAGMA table_info(passive_signals)").fetchall()]
check("passive_signals.source 列存在", 'source' in sig_cols)
rep_cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_effect_report)").fetchall()]
check("signal_effect_report.caliber 列存在", 'caliber' in rep_cols)
conn.close()

print("\n" + "=" * 62)
print("[2] 行情回填覆盖(池内股票, 动态取池)")
print("=" * 62)
pool = qd.get_stock_pool(active_only=True)
codes = [s['code'] for s in pool]
conn = qd.get_db()
# 模块10起入池的新股(join_eff>2026-09-01): 数据源覆盖深度由 onboard 引导链保障
# (如00100自2026-01-09上市, 源端无更早数据), 不适用模块8的2020回填目标
join_eff = {r[0]: r[1] for r in conn.execute(
    "SELECT code, MIN(eff_date) FROM forward_pool_events WHERE event='join' GROUP BY code")}
for s in pool:
    r = conn.execute(
        "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_quotes WHERE code=?",
        (s['code'],)).fetchone()
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT code, trade_date FROM daily_quotes "
        "WHERE code=? GROUP BY code, trade_date HAVING COUNT(*)>1)", (s['code'],)).fetchone()[0]
    if join_eff.get(s['code'], '2020-01-01') > '2026-09-01':
        check(f"{s['code']} 新股(模块10入池)行情 {r[0]} 行 自 {r[1]} (源端覆盖, 引导链保障)",
              r[1] is not None and r[0] >= 100, f"{r[0]}行, {r[1]}~{r[2]}")
    else:
        check(f"{s['code']} 行情自 {r[1]} 起 (回填目标2020-01)",
              r[1] is not None and r[1] <= '2021-01-10', f"{r[0]}行, {r[1]}~{r[2]}")
    check(f"{s['code']} 无重复(code,trade_date)", dup == 0, f"重复{dup}")
conn.close()

print("\n" + "=" * 62)
print("[3] 交界月份连续性: 与回填前备份库比对(旧起点2025-02-25)")
print("=" * 62)
BK = os.path.join(os.path.dirname(os.path.abspath(qd.__file__)),
                  "quant_data_backup_20260904_pre_m8_backfill.db")
if os.path.exists(BK):
    bconn = sqlite3.connect(BK)
    bconn.row_factory = sqlite3.Row
    nconn = qd.get_db()
    for code in codes:
        brows = bconn.execute(
            "SELECT trade_date, close FROM daily_quotes WHERE code=? "
            "AND trade_date>='2025-02-25' AND trade_date<='2025-04-30' ORDER BY trade_date",
            (code,)).fetchall()
        nrow_map = {r['trade_date']: r['close'] for r in nconn.execute(
            "SELECT trade_date, close FROM daily_quotes WHERE code=? "
            "AND trade_date>='2025-02-25' AND trade_date<='2025-04-30'", (code,))}
        missing = [r['trade_date'] for r in brows if r['trade_date'] not in nrow_map]
        check(f"{code} 交界段旧日期全部保留", not missing,
              f"缺失{len(missing)}" + (f": {missing[:3]}" if missing else ""))
        # 前复权基准重写后, 相邻日收益率应与旧库一致(常数因子重标定不改变日收益)
        bmap = {r['trade_date']: r['close'] for r in brows}
        bdates = sorted(bmap)
        worst = 0.0
        for i in range(1, len(bdates)):
            if bdates[i - 1] in nrow_map and bdates[i] in nrow_map:
                old_r = bmap[bdates[i]] / bmap[bdates[i - 1]] - 1
                new_r = nrow_map[bdates[i]] / nrow_map[bdates[i - 1]] - 1
                worst = max(worst, abs(old_r - new_r))
        check(f"{code} 交界段日收益率连续(最大偏差<0.5%)", worst < 0.005, f"最大偏差{worst:.6f}")
    bconn.close()
    nconn.close()
else:
    print("  [!] 备份库不存在, 跳过交界比对")

print("\n" + "=" * 62)
print("[4] 回放幂等: 再跑一遍 replay_passive_signals")
print("=" * 62)
rep = qd.replay_passive_signals()
check("第二次回放零新增", rep['new_inserted'] == 0,
      f"新增{rep['new_inserted']}, replay总数{rep['total_replay']}")

print("\n" + "=" * 62)
print("[5] 信号来源分布与边界")
print("=" * 62)
conn = qd.get_db()
pool_f, pool_args = qd._pool_sql_filter(qd._pool_code_set())
src = {r['source']: r['n'] for r in conn.execute(
    f"SELECT COALESCE(source,'live') source, COUNT(*) n FROM passive_signals "
    f"WHERE {pool_f} GROUP BY 1", pool_args)}
print(f"  来源分布: {src}")
check("存在 live 实采信号", src.get('live', 0) > 0, f"live={src.get('live', 0)}")
check("存在 replay 回放信号", src.get('replay', 0) > 0, f"replay={src.get('replay', 0)}")
early = conn.execute(
    f"SELECT COUNT(*) FROM passive_signals WHERE source='replay' AND trade_date < ? AND {pool_f}",
    (qd.REPLAY_START,) + tuple(pool_args)).fetchone()[0]
check(f"replay 信号不早于 REPLAY_START({qd.REPLAY_START})", early == 0, f"越界{early}")
# 业务键唯一性(全表, 防重复入库)
dupk = conn.execute(
    "SELECT COUNT(*) FROM (SELECT code, trade_date, signal_type, signal_subtype, direction "
    "FROM passive_signals GROUP BY 1,2,3,4,5 HAVING COUNT(*)>1)").fetchone()[0]
check("业务键(code+date+type+subtype+direction)无重复", dupk == 0, f"重复{dupk}")
conn.close()

print("\n" + "=" * 62)
print("[6] 三口径统计: run_module3_analysis + 入库分布")
print("=" * 62)
m3 = qd.run_module3_analysis()
by_cal = m3.get('passive_by_caliber', {})
check("三口径齐全", set(by_cal) >= {'合并', 'replay', 'live'}, f"实际: {sorted(by_cal)}")
for cal in ('合并', 'replay', 'live'):
    tot = sum(r['total_signals'] for r in by_cal.get(cal, []))
    print(f"  {cal} 口径信号组 {len(by_cal.get(cal, []))} 组, 总样本 {tot}")
# 合并 >= max(replay, live) 每组
cal_maps = {cal: {(r['signal_type'], r['signal_subtype'], r['direction']): r['total_signals']
                  for r in by_cal.get(cal, [])} for cal in ('合并', 'replay', 'live')}
ok_ge = all(cal_maps['合并'].get(k, 0) >= max(cal_maps['replay'].get(k, 0),
                                              cal_maps['live'].get(k, 0))
            for k in cal_maps['合并'])
check("合并口径每组样本 >= max(replay, live)", ok_ge)
conn = qd.get_db()
cals = {r[0]: r[1] for r in conn.execute(
    "SELECT caliber, COUNT(*) FROM signal_effect_report WHERE category='passive' GROUP BY 1")}
print(f"  signal_effect_report(passive) 口径分布: {cals}")
check("入库含三口径", set(cals) >= {'合并', 'replay', 'live'})
conn.close()

print("\n" + "=" * 62)
print("[7] RSI24 时间分割验证(预注册规则)")
print("=" * 62)
v = m3.get('rsi24_verdict', {})
check("规则文本已预注册", '预注册' in v.get('rule', ''), v.get('rule', '')[:60])
for sig, e in v.get('signals', {}).items():
    check(f"{sig} verdict 合法", e.get('verdict') in ('转正', '淘汰', '继续观察'),
          f"{e.get('verdict')} — {e.get('verdict_reason', '')}")
    if e.get('verdict') == '转正':
        a, b = e['seg_a'], e['seg_b']
        check(f"{sig} 转正条件成立(两段≥20且超额均正)",
              a['triggers'] >= 20 and b['triggers'] >= 20
              and a['excess_win_rate'] > 0 and b['excess_win_rate'] > 0
              and a['excess_return'] > 0 and b['excess_return'] > 0)
# 池状态与判定联动
pool_map = {(p['signal_type'], p['direction']): p['status'] for p in m3['pool']}
for sig, e in v.get('signals', {}).items():
    st = pool_map.get((sig, 'bullish' if sig.endswith('oversold') else 'bearish'))
    if e.get('verdict') == '转正' and st:
        check(f"{sig} 池状态=有效(时间分割通过)", st == '有效(时间分割通过)', st)
    if e.get('verdict') == '淘汰' and st:
        check(f"{sig} 池状态=淘汰(时间分割未过)", st == '淘汰(时间分割未过)', st)

print("\n" + "=" * 62)
print("[8] 宽表全窗口重建")
print("=" * 62)
conn = qd.get_db()
r = conn.execute(
    f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_feature_base WHERE {pool_f}",
    pool_args).fetchone()
check("宽表起点早于2021-02(回放信号+前向窗口)", r[1] is not None and r[1] <= '2021-02-15',
      f"{r[0]}行, {r[1]}~{r[2]}")
conn.close()

print("\n" + "=" * 62)
print("[9] 模块5扩窗重跑(冻结参数, 新批次)")
print("=" * 62)
conn = qd.get_db()
rows = conn.execute(
    "SELECT run_date, COUNT(*) FROM backtest_runs GROUP BY run_date ORDER BY run_date DESC LIMIT 3"
).fetchall()
print("  最近批次: " + "; ".join(f"{r[0]}×{r[1]}条" for r in rows))
latest = rows[0][0] if rows else None
if latest:
    fr = conn.execute(
        "SELECT COUNT(*), MIN(start_date) FROM backtest_runs WHERE run_date=?", (latest,)).fetchone()
    oos = conn.execute(
        "SELECT MIN(start_date) FROM backtest_runs WHERE run_date=? AND segment='oos'", (latest,)).fetchone()
    full = conn.execute(
        "SELECT MIN(start_date) FROM backtest_runs WHERE run_date=? AND segment='full'", (latest,)).fetchone()
    check("最新批次 full 段起点前移至2021前(扩窗)", full[0] is not None and full[0] <= '2021-01-15',
          f"full起点{full[0]}")
    check("最新批次 oos 段起点为知识截止(2026-05-01)后首个交易日",
          oos[0] > '2026-05-01', f"oos起点{oos[0]}")
    n_bk = conn.execute(
        "SELECT COUNT(DISTINCT run_date) FROM backtest_runs").fetchone()[0]
    print(f"  批次数: {n_bk} (多批次追加模式: 冻结批次与扩窗批次并存, 同日重跑只覆盖当日)")
conn.close()

print("\n" + "=" * 62)
print(f"结果: {PASS} 通过 / {FAIL} 失败")
print("=" * 62)
if FAIL:
    raise SystemExit(1)
