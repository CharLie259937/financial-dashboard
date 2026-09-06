# -*- coding: utf-8 -*-
"""模块9测试: BH分层FDR数学/裁决入库一致性/overlay配置冻结/台账幂等守卫/
引擎封锁行为/策略规格/回测批次事实/前向确定性重放/读路径无DDL(防锁回归)"""
import inspect
import json
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
print("[1] BH step-up 数学正确性(手工案例)")
print("=" * 62)
# 案例1: 全部存活(每个p都≤其rank临界值)
sv, qv = qd._bh_stratum([0.001, 0.002, 0.003, 0.004, 0.005], 0.05)
check("案例1 全存活", all(sv) and len(sv) == 5, f"survive={sv}")
# 案例2: 仅最小p存活(其余远超临界)
sv2, _ = qd._bh_stratum([0.001, 0.2, 0.21, 0.22, 0.23], 0.05)
check("案例2 仅首个存活", sv2 == [True, False, False, False, False], f"survive={sv2}")
# 案例3: step-up 本质 — 前两 rank 不达标但 rank3 达标 ⇒ 前三全部存活
sv3, qv3 = qd._bh_stratum([0.04, 0.041, 0.042], 0.05)
check("案例3 step-up拉活(rank3达标⇒1-3全存活)", sv3 == [True, True, True],
      f"survive={sv3}, qvals={[round(x, 4) for x in qv3]}")
# 案例3调整p: 全部= min 累积 = 0.042
check("案例3 调整p单调且=0.042",
      all(abs(x - 0.042) < 1e-9 for x in qv3), f"qvals={qv3}")
# 案例4: 单p不达标
sv4, qv4 = qd._bh_stratum([0.06], 0.05)
check("案例4 单p>q不存活", sv4 == [False] and abs(qv4[0] - 0.06) < 1e-12)
# 案例5: 空族
sv5, qv5 = qd._bh_stratum([], 0.05)
check("案例5 空族", sv5 == [] and qv5 == [])
# 案例6: 调整p单调不减性(按p升序)
ps = [0.30, 0.01, 0.07, 0.003, 0.5, 0.09]
_, qv6 = qd._bh_stratum(ps, 0.05)
order = sorted(range(len(ps)), key=lambda i: ps[i])
seq = [qv6[i] for i in order]
check("案例6 调整p按p升序单调不减", all(seq[i] <= seq[i + 1] + 1e-12 for i in range(5)),
      f"sorted_qv={[round(x, 4) for x in seq]}")

print("\n" + "=" * 62)
print("[2] FDR 裁决入库一致性(1602组 × 6窗口族)")
print("=" * 62)
conn = qd.get_db()
n_study = conn.execute("SELECT COUNT(*) FROM macro_study_results").fetchone()[0]
n_fdr = conn.execute("SELECT COUNT(*) FROM macro_fdr_results").fetchone()[0]
check("macro_fdr_results 行数 = macro_study_results 行数",
      n_fdr == n_study and n_fdr > 0, f"fdr={n_fdr}, study={n_study}")
fdr_rows = conn.execute(
    "SELECT window, p_rank, m_family, bh_critical, p_value, q_value, survive "
    "FROM macro_fdr_results").fetchall()
by_win = {}
for r in fdr_rows:
    by_win.setdefault(r['window'], []).append(r)
check("6 个窗口族", len(by_win) == 6, f"族={sorted(by_win)}")
ok_rank, ok_crit, ok_surv = True, True, True
for w, rows in by_win.items():
    testable = [r for r in rows if r['p_rank'] is not None]
    ranks = sorted(r['p_rank'] for r in testable)
    ms = {r['m_family'] for r in testable}
    if ranks != list(range(1, len(testable) + 1)) or len(ms) != 1:
        ok_rank = False
    for r in testable:
        if abs(r['bh_critical'] - round(0.05 * r['p_rank'] / r['m_family'], 6)) > 1e-9:
            ok_crit = False
        # BH 等价判据: 存活 ⇔ 调整p ≤ q (step-up下原始p可大于自身rank临界值, 属正常)
        if bool(r['survive']) != (r['q_value'] is not None and r['q_value'] <= 0.05 + 1e-12):
            ok_surv = False
    for r in rows:  # p值缺失的组不可检验, 不得存活
        if r['p_rank'] is None and r['survive']:
            ok_surv = False
check("各层可检验行 p_rank 连续 1..m 且 m_family 唯一", ok_rank)
check("bh_critical = q·rank/m (逐行)", ok_crit)
check("存活 ⇔ q_value≤q (BH等价判据); p缺失行不存活", ok_surv)
lr = json.loads(conn.execute(
    "SELECT value FROM macro_fdr_meta WHERE key='last_run'").fetchone()[0])
n_sv = sum(1 for r in fdr_rows if r['survive'])
check("存活行数 = meta.last_run.n_survive_rows",
      n_sv == lr['n_survive_rows'], f"{n_sv} vs {lr['n_survive_rows']}")
ok_strata = all(
    lr['strata'][w]['m'] == sum(1 for r in by_win[w] if r['p_rank'] is not None)
    for w in by_win)
check("分层 m 与表内行数一致", ok_strata,
      "strata=" + ", ".join(f"{w}:m={s['m']}" for w, s in sorted(lr['strata'].items())))

print("\n" + "=" * 62)
print("[3] overlay 配置冻结(macro_overlay_meta)")
print("=" * 62)
cfg = json.loads(conn.execute(
    "SELECT value FROM macro_overlay_meta WHERE key='config'").fetchone()[0])
adj = json.loads(conn.execute(
    "SELECT value FROM macro_overlay_meta WHERE key='adjudication'").fetchone()[0])
check("config.frozen_at 存在", bool(cfg.get('frozen_at')))
check("入选组数 = meta.n_eligible_groups",
      len(cfg['groups']) == lr['n_eligible_groups'] and len(cfg['groups']) > 0,
      f"{len(cfg['groups'])} 组")
four_ok = True
for g in cfg['groups']:
    if not (g['window_days'] >= 1 and g['n'] >= qd.FDR_MIN_N
            and g['car_mean'] < 0
            and f"[0,+{g['window_days']}]" in qd.OVERLAY_BLOCKABLE_WINDOWS
            and max(g['coverage'].values() or [0]) <= qd.OVERLAY_MAX_COV):
        four_ok = False
check("每组满足四条件+护栏(N≥1, n≥30, CAR<0, 窗口可封锁, 覆盖率≤50%)", four_ok,
      "; ".join(f"{g['group_id']} N={g['window_days']} n={g['n']} "
                f"car={g['car_mean']:.2f}" for g in cfg['groups']))
check("adjudication 预注册字段齐全",
      adj.get('min_trades') == 20 and bool(adj.get('rules')) and bool(adj.get('pre_registered')))

print("\n" + "=" * 62)
print("[4] 封锁日台账(幂等/守卫/可追溯)")
print("=" * 62)
r1 = qd.refresh_overlay_days(min_date=None, log=lambda *a, **k: None)
check("全历史种子幂等(重跑零新增)", r1['inserted'] == 0,
      f"ledger_total={r1['ledger_total']}")
r2 = qd.refresh_overlay_days(min_date='2999-01-01', log=lambda *a, **k: None)
check("min_date 守卫: 未来日期不产生任何插入", r2['inserted'] == 0)
led = conn.execute(
    "SELECT code, trade_date, group_id, window_days FROM macro_overlay_days").fetchall()
gids = {g['group_id'] for g in cfg['groups']}
pool = qd.get_stock_pool(active_only=True)
pool_codes = {s['code']: s['market'] for s in pool}
check("台账非空且每行 group_id ∈ 入选组", len(led) > 0 and all(
    r['group_id'] in gids for r in led), f"{len(led)} 行")
mkt_map = {'HSI': '港股', '.INX': '美股', 'sh000300': 'A股'}
trace_ok = all(r['code'] in pool_codes for r in led)
gid_mkt = {g['group_id']: mkt_map[g['index_code']] for g in cfg['groups']}
check("台账股票均在池内", trace_ok,
      f"股票={sorted({r['code'] for r in led})}")
check("台账股票市场与组指数匹配",
      all(pool_codes[r['code']] == gid_mkt[r['group_id']] for r in led if r['code'] in pool_codes))
dup = conn.execute(
    "SELECT COUNT(*) FROM (SELECT code, trade_date, group_id FROM macro_overlay_days "
    "GROUP BY code, trade_date, group_id HAVING COUNT(*)>1)").fetchone()[0]
check("无重复(code,trade_date,group_id)", dup == 0, f"重复{dup}")
wd_ok = all(r['window_days'] == next(
    g['window_days'] for g in cfg['groups'] if g['group_id'] == r['group_id'])
    for r in led)
check("台账 window_days 与冻结配置一致", wd_ok)

print("\n" + "=" * 62)
print("[5] D1 引擎封锁行为(真实数据, 不写库)")
print("=" * 62)
data = qd._load_backtest_data()
blocked = qd._load_overlay_blocked(data)
check("封锁日集非空且仅含池内股票", bool(blocked) and set(blocked) <= set(pool_codes),
      f"{ {c: len(d) for c, d in blocked.items()} }")
res_s2 = qd.run_backtest('S2', data=data, blocked_days=blocked)
res_s2m = qd.run_backtest('S2M', data=data, blocked_days=blocked)
check("S2 显式传入封锁日也不封锁(非overlay策略忽略)",
      res_s2.get('macro_blocked', 0) == 0)
check("S2M macro_blocked > 0(封锁机制生效)",
      res_s2m.get('macro_blocked', 0) > 0, f"封锁{res_s2m['macro_blocked']}次")
viol = [t for t in res_s2m['trades'] if t['entry_date'] in blocked.get(t['code'], ())]
check("S2M 成交开仓日零命中封锁日(硬不变量)", not viol,
      f"违例{len(viol)}" + (f": {[(v['code'], v['entry_date']) for v in viol[:3]]}" if viol else ""))
res_s2m_auto = qd.run_backtest('S2M')   # 不传 data/blocked_days → 自动加载
check("S2M 自动加载台账结果与显式传入一致",
      res_s2m_auto['macro_blocked'] == res_s2m['macro_blocked']
      and len(res_s2m_auto['trades']) == len(res_s2m['trades']))
check("触发集与原版一致(只拒开仓不删信号)",
      res_s2m['n_triggers'] == res_s2['n_triggers'],
      f"{res_s2m['n_triggers']} vs {res_s2['n_triggers']}")

print("\n" + "=" * 62)
print("[6] BT_STRATEGIES 规格(overlay 变体只加封锁不改核心)")
print("=" * 62)
for m_id, p_id in (('S2M', 'S2'), ('S1aM', 'S1a')):
    m, p = qd.BT_STRATEGIES[m_id], qd.BT_STRATEGIES[p_id]
    check(f"{m_id} = {p_id} + overlay(触发/持有一致)",
          m['match'] == p['match'] and m['hold'] == p['hold'] and m.get('overlay') is True)
    check(f"{p_id} 无 overlay 标志(原版行为不变)", not p.get('overlay'))
frozen_holds = {'S1a': 10, 'S1b': 5, 'S1c': 5, 'S1d': 3, 'S1e': 10, 'S2': 5}
check("原6策略 hold 未被模块9改动",
      all(qd.BT_STRATEGIES[s]['hold'] == h for s, h in frozen_holds.items()))

print("\n" + "=" * 62)
print("[7] D2 回测批次事实(2026-09-06 批次)")
print("=" * 62)
bt = conn.execute(
    "SELECT run_id, strategy_id, segment, fee, n_trades, n_macro_blocked "
    "FROM backtest_runs WHERE run_date='2026-09-06'").fetchall()
check("批次总 runs=52(8策略×2段×3费率+双基准×2段)", len(bt) == 52, f"{len(bt)} runs")
for m_id in ('S2M', 'S1aM'):
    rows_m = [r for r in bt if r['strategy_id'] == m_id]
    check(f"{m_id} 2段×3费率共6行", len(rows_m) == 6)
s2m_full = next(r for r in bt if r['strategy_id'] == 'S2M'
                and r['segment'] == 'full' and r['fee'] == 0.0)
check("S2M full 封锁65次(入库事实)", s2m_full['n_macro_blocked'] == 65,
      f"n_macro_blocked={s2m_full['n_macro_blocked']}")
s1am_full = next(r for r in bt if r['strategy_id'] == 'S1aM'
                 and r['segment'] == 'full' and r['fee'] == 0.0)
check("S1aM full 封锁1次(入库事实)", s1am_full['n_macro_blocked'] == 1,
      f"n_macro_blocked={s1am_full['n_macro_blocked']}")
old_rows = [r for r in bt if r['strategy_id'] not in ('S2M', 'S1aM')]
check("原6策略+基准共40行(与旧批次结构一致)", len(old_rows) == 40, f"{len(old_rows)} 行")
# 硬不变量复验(以批次入库交易为准)
for m_id in ('S2M', 'S1aM'):
    run_ids = [r['run_id'] for r in bt if r['strategy_id'] == m_id and r['fee'] == 0.0]
    trs = []
    for rid in run_ids:
        trs += conn.execute(
            "SELECT code, entry_date FROM backtest_trades WHERE run_id=?",
            (rid,)).fetchall()
    v = [t for t in trs if t['entry_date'] in blocked.get(t['code'], ())]
    check(f"{m_id} 入库交易开仓日零命中封锁日", not v, f"{len(trs)}笔, 违例{len(v)}")

print("\n" + "=" * 62)
print("[8] 前向集成(确定性重放/协议唯一键)")
print("=" * 62)
n_ov_protocol = conn.execute(
    "SELECT COUNT(*) FROM forward_meta WHERE meta_key='overlay_protocol'").fetchone()[0]
check("forward_meta.overlay_protocol 键唯一", n_ov_protocol == 1, f"{n_ov_protocol} 行")
for sid in ('S2M', 'S1aM'):
    r = conn.execute(
        "SELECT COUNT(*), MIN(trade_date) FROM forward_equity WHERE strategy_id=?",
        (sid,)).fetchone()
    check(f"{sid} 前向净值已记录且自 FORWARD_START 起",
          r[0] > 0 and r[1] == qd.FORWARD_START, f"{r[0]}行, 起点{r[1]}")
rep = qd.run_forward_step(log=lambda *a, **k: None)
check("前向确定性重放: 零新增/零交易/零mismatch",
      rep['ok'] and rep['new_equity_rows'] == 0 and rep['new_trade_rows'] == 0
      and not rep['mismatches'],
      f"eq={rep.get('new_equity_rows')}, tr={rep.get('new_trade_rows')}, "
      f"mism={len(rep.get('mismatches', []))}")
st = qd._forward_status()
ok_fwd = all(st['strategies'].get(s, {}).get('overlay') for s in ('S2M', 'S1aM'))
check("前向状态含 overlay 字段", ok_fwd)
ev = qd.evaluate_overlay()
check("evaluate_overlay 返回 S2M/S1aM 两条目", len(ev) == 2
      and {e['strategy_id'] for e in ev} == {'S2M', 'S1aM'})
check("裁决读数含进度/裁决字段",
      all('/' in e['progress'] and e['verdict'] for e in ev),
      "; ".join(f"{e['strategy_id']}: {e['progress']} {e['verdict']}" for e in ev))

print("\n" + "=" * 62)
print("[9] load_fdr_view 回读(看板路径)")
print("=" * 62)
fv = qd.load_fdr_view()
need_cols = {'window', 'region', 'family', 'index_code', 'direction', 'n',
             'car_mean', 'p_value', 'q_value', 'survive', 'overlay_eligible',
             'eligible_reason'}
check("results 含全部必需列", need_cols <= set(fv['results'].columns),
      f"{len(fv['results'])} 行")
check("fdr_meta 含 protocol/last_run/diagnostic_cutoff",
      {'protocol', 'last_run', 'diagnostic_cutoff'} <= set(fv['fdr_meta']))
check("overlay_meta 含 config/adjudication",
      {'config', 'adjudication'} <= set(fv['overlay_meta']))
check("ledger 统计非空", bool(fv['ledger']),
      "; ".join(f"{l['code']}:{l['n_days']}" for l in fv['ledger']))

print("\n" + "=" * 62)
print("[10] 每日管道集成(静态)")
print("=" * 62)
src_pipe = inspect.getsource(qd.run_daily_pipeline)
check("管道含宏观采集步骤", "'macro'" in src_pipe)
check("管道含封锁日台账步骤(守卫=完整性截断日)", "'overlay_days'" in src_pipe
      and '_stock_cap_date()' in src_pipe)

print("\n" + "=" * 62)
print("[11] 读路径无DDL(锁问题回归防护, 问题日志#1/#3)")
print("=" * 62)
check("_load_overlay_blocked 无DDL", 'create_m9_tables' not in
      inspect.getsource(qd._load_overlay_blocked))
check("load_fdr_view 无DDL", 'create_m9_tables' not in inspect.getsource(qd.load_fdr_view))
check("evaluate_overlay 无DDL", 'create_forward_tables' not in
      inspect.getsource(qd.evaluate_overlay))
src_d2 = inspect.getsource(qd.run_d2_backtests)
check("run_d2_backtests 封锁日集预载于首个连接之前",
      src_d2.index('_load_overlay_blocked') < src_d2.index('conn = get_db()'))
src_fwd = inspect.getsource(qd.run_forward_step)
i_pre = src_fwd.index('overlay_blocked = _load_overlay_blocked')
i_loop = src_fwd.index('for sid, spec in BT_STRATEGIES.items():')
check("run_forward_step 台账预载于策略循环之前", i_pre < i_loop)

conn.close()
print()
print("=" * 62)
print(f"模块9测试完成: {PASS} 通过 / {FAIL} 失败")
print("=" * 62)
if FAIL:
    raise SystemExit(1)
