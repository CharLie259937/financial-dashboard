# -*- coding: utf-8 -*-
"""模块12 P1 测试: 财报日历采集层 E1/E2
覆盖: 分类配置(简繁/顺序敏感) / EDGAR timing(自实现DST) / dedup幂等与同日多公告 /
建表幂等 / 读路径无DDL / 保护矩阵等级 / C3起点登记幂等与恢复 / 采集后live检查(宽容跳过)
live 部分不绑定首跑状态(模块11问题日志#1教训): 未采集时打印 SKIP 而非失败"""
import sys
import json
import inspect
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, r"C:\Users\lenovo\Desktop\financial_dashboard")
import quant_data as qd

ok, fail = 0, []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail.append(name)
        print(f"  ✗ {name} {detail}")


print("=" * 66)
print("[1] 分类配置: E1 报告期归类(顺序敏感) + E2 关键词(简繁)")
print("=" * 66)
check("E1_SUBTYPE_RULES 顺序: 半年度先于年度",
      [k for k, _ in qd.E1_SUBTYPE_RULES].index('半年度') < [k for k, _ in qd.E1_SUBTYPE_RULES].index('年度'))
check("E2_CLASSIFY_RULES 为配置型列表(非硬编码分支)",
      isinstance(qd.E2_CLASSIFY_RULES, list) and all(len(r) == 2 for r in qd.E2_CLASSIFY_RULES))
for title, want in [
        ('宁德时代:2025年年度报告', 'annual'),
        ('宁德时代:2026年半年度报告', 'interim'),
        ('宁德时代:2026年一季度报告', 'q1'),
        ('宁德时代:2026年第三季度报告', 'q3'),
        ('截至二零二五年十二月三十一日止年度全年业绩公布', 'annual'),
        ('截至二零二六年六月三十日止三个月及六个月业绩公布', 'interim'),
        ('截至二零二六年三月三十一日止三个月业绩公布', 'q1'),
        ('2026第三季度業績公佈', 'q3')]:
    check(f"E1归类 {title[:18]}... → {want}", qd._e1_subtype(title) == want,
          f"实际 {qd._e1_subtype(title)}")
for title, want in [('配售新H股', '配售'), ('要約收購事項', '收购'), ('回購計劃', '回购'),
                    ('限售股解禁', '解禁'), ('澄清公告', '澄清'), ('股東增持', '增减持'),
                    ('董事會會議通告', 'unclassified')]:
    check(f"E2分类 {title} → {want}", qd._e2_classify(title) == want,
          f"实际 {qd._e2_classify(title)}")

print()
print("=" * 66)
print("[2] EDGAR timing: 自实现 DST(夏令 3月第2周日~11月第1周日)")
print("=" * 66)
et = qd._to_et(datetime(2026, 7, 30, 20, 30))
check("夏令时 2026-07-30 20:30Z → ET 16:30", (et.hour, et.minute) == (16, 30), f"{et}")
et = qd._to_et(datetime(2026, 1, 29, 21, 30))
check("冬令时 2026-01-29 21:30Z → ET 16:30", (et.hour, et.minute) == (16, 30), f"{et}")
check("DST 切换日 2026-03-08(第2个周日) 05:00Z → 00:00 EST(-5)",
      (qd._to_et(datetime(2026, 3, 8, 5, 0)).hour) == 0)
check("DST 切换日 2026-03-08 07:00Z → 03:00 EDT(-4)",
      (qd._to_et(datetime(2026, 3, 8, 7, 0)).hour, ) == (3,))
for ts, want in [('2026-07-30T20:30:28.000Z', '盘后'), ('2026-05-01T10:01:00.000Z', '盘前'),
                 ('2026-07-30T15:30:00.000Z', '盘中'), (None, '未知'), ('garbage', '未知')]:
    check(f"timing {ts} → {want}", qd._edgar_timing(ts) == want,
          f"实际 {qd._edgar_timing(ts)}")

print()
print("=" * 66)
print("[3] dedup_key: 幂等 + 区分度")
print("=" * 66)
k1 = qd._ec_dedup_key('AAPL', '2026-07-30', 'sec_edgar', 'E1', 'earnings', '2026-06-27', 'T')
check("同输入同键", k1 == qd._ec_dedup_key('AAPL', '2026-07-30', 'sec_edgar', 'E1', 'earnings', '2026-06-27', 'T'))
check("异标题异键", k1 != qd._ec_dedup_key('AAPL', '2026-07-30', 'sec_edgar', 'E1', 'earnings', '2026-06-27', 'T2'))
check("异源异键", k1 != qd._ec_dedup_key('AAPL', '2026-07-30', 'baidu_report_time', 'E1', 'earnings', '2026-06-27', 'T'))

print()
print("=" * 66)
print("[4] 建表幂等 + 同日多公告共存(计划文档简化键的工程修正) + 重跑零新增")
print("=" * 66)
conn = qd.get_db()
qd.create_earnings_tables(conn)
qd.create_earnings_tables(conn)
check("建表幂等(CREATE IF NOT EXISTS ×2 无异常)", True)
TEST = '__M12TEST__'
conn.execute("DELETE FROM earnings_calendar WHERE code=?", (TEST,))
n1 = qd._insert_ec(conn, TEST, '港股', '测试', 'E2', '2026-01-01', '配售', None,
                   '配售新H股', '未知', 'eastmoney_notice', 't', 'd')
n2 = qd._insert_ec(conn, TEST, '港股', '测试', 'E2', '2026-01-01', '澄清', None,
                   '澄清公告', '未知', 'eastmoney_notice', 't', 'd')
n3 = qd._insert_ec(conn, TEST, '港股', '测试', 'E2', '2026-01-01', '配售', None,
                   '配售新H股', '未知', 'eastmoney_notice', 't', 'd')
check("同日同源两条不同公告共存(UNIQUE含标题)", n1 == 1 and n2 == 1 and n3 == 0,
      f"n={n1},{n2},{n3}")
rows = conn.execute("SELECT COUNT(*) FROM earnings_calendar WHERE code=?", (TEST,)).fetchone()[0]
check("测试行数=2(重复插入零新增)", rows == 2)
# 保护矩阵: 真实池股合成E1 → P2 📈(±3自然日包络)
REAL = conn.execute("SELECT code FROM stock_pool WHERE is_active=1 "
                    "AND market='港股' ORDER BY code LIMIT 1").fetchone()[0]
probe_d = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
try:
    qd._insert_ec(conn, REAL, '港股', '', 'E1', probe_d, '排期', None, '测试排期',
                  '预约', '__test__', 't', 'd')
    conn.commit()
    mx = qd.get_earnings_protection_matrix()
    row = next(r for r in mx['rows'] if r['code'] == REAL)
    hit = [d for d in mx['dates'] if '📈' in (row.get(d) or '') or 'P3' in (row.get(d) or '')]
    check(f"合成E1(+5日) → 矩阵该股出现 P2📈/P3 标记({len(hit)}天)", len(hit) >= 5,
          f"hit={len(hit)}")
finally:
    conn.execute("DELETE FROM earnings_calendar WHERE source='__test__' OR code=?",
                 (TEST,))
    conn.commit()
levels_ok = all((r[d] or '').split(' ')[0] in ('P0', 'P2', 'P3')
                for r in mx['rows'] for d in mx['dates'])
check("矩阵等级值域 ⊆ {P0,P2,P3}", levels_ok)
check("矩阵含活跃池全部股票", len(mx['rows']) == conn.execute(
    "SELECT COUNT(*) FROM stock_pool WHERE is_active=1").fetchone()[0])
conn.close()

print()
print("=" * 66)
print("[5] 读路径无DDL(坑25) + 采集函数结构")
print("=" * 66)
check("get_earnings_calendar_view 无DDL",
      'CREATE TABLE' not in inspect.getsource(qd.get_earnings_calendar_view))
check("get_earnings_protection_matrix 无DDL",
      'CREATE TABLE' not in inspect.getsource(qd.get_earnings_protection_matrix))
src = inspect.getsource(qd.collect_earnings_calendar)
check("采集含单股失败不阻断(try/except + FAIL日志)", 'FAIL' in src and 'continue' in src)
check("采集含百度交叉验证与未来排期开关",
      'do_agreement' in src and 'do_future' in src)

print()
print("=" * 66)
print("[6] C3 前向计数起点登记: 幂等 + 卡片联动 + 原值恢复")
print("=" * 66)
conn = qd.get_db()
qd.create_forward_tables(conn)
orig = conn.execute("SELECT result_json FROM forward_meta WHERE "
                    "meta_key='s3_event_protocol'").fetchone()
try:
    p1 = qd.register_s3_forward_start('2026-09-16', log=lambda m: None)
    qd.register_s3_forward_start('2026-09-16', log=lambda m: None)
    n = conn.execute("SELECT COUNT(*) FROM forward_meta WHERE "
                     "meta_key='s3_event_protocol'").fetchone()[0]
    check("登记幂等(重复登记单行, DELETE+INSERT)", n == 1)
    reg = json.loads(conn.execute("SELECT result_json FROM forward_meta WHERE "
                                  "meta_key='s3_event_protocol'").fetchone()[0])
    check("payload 含冻结裁决规则与计数起点",
          reg['forward_count_start'] == '2026-09-16' and 'min_trades' in reg['adjudication'])
    card = qd.get_s3_prereg_cards()[0]
    check("S1aE/S2E 卡片联动显示采集层就绪", '采集层就绪' in card['verdict'], card['verdict'])
finally:
    conn.execute("DELETE FROM forward_meta WHERE meta_key='s3_event_protocol'")
    if orig:
        conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                     "VALUES('s3_event_protocol',?,?,?)", (orig[0], '', ''))
    conn.commit()
    conn.close()
check("原值恢复后卡片回到登记前原状态",
      ('待采集层' in qd.get_s3_prereg_cards()[0]['verdict']) if orig is None
      else ('采集层就绪' in qd.get_s3_prereg_cards()[0]['verdict']))

print()
print("=" * 66)
print("[7] live 检查(采集后; 未采集则 SKIP 不失败)")
print("=" * 66)
conn = qd.get_db()
n_ec = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
if n_ec == 0:
    print("  [SKIP] earnings_calendar 为空——先运行 collect_earnings_calendar 再跑 live 段")
else:
    today = datetime.now().strftime('%Y-%m-%d')
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM earnings_calendar WHERE event_class='E1' "
        "AND event_subtype != '排期'")]
    pool = [r[0] for r in conn.execute(
        "SELECT code FROM stock_pool WHERE is_active=1")]
    check(f"全部活跃池股有主源E1历史({len(codes)}/{len(pool)})", set(pool) <= set(codes),
          f"缺: {set(pool) - set(codes)}")
    anchors = conn.execute(
        "SELECT COUNT(*) FROM earnings_calendar WHERE code='02513' AND event_date='2026-03-31' "
        "AND event_class='E1'").fetchone()[0]
    check("锚点: 02513 智谱 2026-03-31 年度业绩(模块12研究案例)", anchors >= 1)
    a300 = conn.execute(
        "SELECT COUNT(*) FROM earnings_calendar WHERE code='300750' AND event_date='2026-03-10' "
        "AND event_class='E1'").fetchone()[0]
    check("锚点: 300750 宁德 2026-03-10 年报(经济日历审计锚点)", a300 >= 1)
    view = qd.get_earnings_calendar_view()
    ag = view.get('agreement') or {}
    check("双源一致率已记录(P1验收闸门)", ag.get('rate') is not None, str(ag)[:120])
    if ag.get('rate') is not None:
        check(f"双源一致率 ≥95% (实际 {ag['rate']:.1%})", ag['rate'] >= 0.95)
    nxt = [r for r in view['rows'] if r['next_e1']]
    print(f"  [info] 未来排期当前 {len(nxt)} 股有下次财报日"
          f"(A股三季报预约/港美排期源端发布后自动补充, 机制已由[4]合成排期验证)")
    hl = view.get('health') or {}
    check("健康度三源齐备", set(hl) == {'eastmoney_notice', 'sec_edgar', 'baidu_report_time'})
conn.close()

print()
print("=" * 66)
print("[8] 门控批次(模块12 P2 事件门控 + 波动域门控 P1, 2026-09-18)")
print("=" * 66)
# 8.1 注册表
check(f"策略集 13 个(含4门控+1槽位变体)", len(qd.BT_STRATEGIES) == 13)
for vid, pid in [('S1aE', 'S1a'), ('S2E', 'S2'), ('S1bV', 'S1b'), ('S2V', 'S2')]:
    v, p = qd.BT_STRATEGIES[vid], qd.BT_STRATEGIES[pid]
    check(f"{vid} 与原版 {pid} match/hold 完全一致(唯一差异=门控)",
          v['match'] == p['match'] and v['hold'] == p['hold']
          and (v.get('event_gate') or v.get('vol_gate')))
check("S1aE/S2E fwd_start = 2026-09-16(与 s3_event_protocol 登记)",
      qd.BT_STRATEGIES['S1aE']['fwd_start'] == '2026-09-16'
      and qd.BT_STRATEGIES['S2E']['fwd_start'] == '2026-09-16')
conn = qd.get_db()
reg_v = json.loads(conn.execute("SELECT result_json FROM forward_meta WHERE "
                                "meta_key='vol_gate_protocol'").fetchone()[0])
check("vol_gate_protocol 已登记且与 S1bV/S2V fwd_start 一致",
      reg_v['forward_count_start'] == qd.BT_STRATEGIES['S1bV']['fwd_start'] ==
      qd.BT_STRATEGIES['S2V']['fwd_start'])
# 8.2 门控单元(合成上下文)
ctx = {'vol_prev': {'X': {'2026-01-05': 0.79, '2026-01-06': 0.80, '2026-01-07': 1.2}},
       'rsi14': {'X': {'2026-01-06': 29.9, '2026-01-07': 30.0}},
       'e1': {'X': ['2026-01-25']}, 'evt': {'X': []}}
check("C-V 边界: 0.79 拒 0.80 过(≥)",
      not qd._vol_gate_ok('X', '2026-01-05', ctx) and qd._vol_gate_ok('X', '2026-01-06', ctx))
dates = ['2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05', '2026-01-06', '2026-01-07',
         '2026-01-08', '2026-01-09', '2026-01-10', '2026-01-11', '2026-01-12', '2026-01-13',
         '2026-01-14', '2026-01-15', '2026-01-16', '2026-01-17', '2026-01-18', '2026-01-19',
         '2026-01-20', '2026-01-21', '2026-01-22', '2026-01-23', '2026-01-24', '2026-01-25']
didx = {d: i for i, d in enumerate(dates)}
det = qd._event_gate_detail('X', '2026-01-06', ctx, dates, didx)
check("C2 边界: RSI14<30 (29.9 过 30.0 不过)",
      det['C2'] is True
      and qd._event_gate_detail('X', '2026-01-07', ctx, dates, didx)['C2'] is False)
check("C3: 下次E1距触发19交易日 ≥10 过", det['C3'] is True)
det2 = qd._event_gate_detail('X', '2026-01-16', ctx, dates, didx)
check("C3: 距下次E1 7交易日 <10 拒", det2['C3'] is False)
ctx_no_e1 = dict(ctx, e1={'X': []})
check("C3 保守拒绝: 无下次E1(排期缺位)",
      qd._event_gate_detail('X', '2026-01-06', ctx_no_e1, dates, didx)['C3'] is False)
ctx_evt = dict(ctx, evt={'X': ['2026-01-04']})
check("C1: 前5交易日(含T)内有事件 → 拒",
      qd._event_gate_detail('X', '2026-01-06', ctx_evt, dates, didx)['C1'] is False)
ctx_evt2 = dict(ctx, evt={'X': ['2026-01-01']})
check("C1: 窗口外事件 → 过",
      qd._event_gate_detail('X', '2026-01-06', ctx_evt2, dates, didx)['C1'] is True)
conn.close()

# 8.3 机制对照批次硬不变量(09-18 批次, 读库验证)
conn = qd.get_db()
batch = conn.execute("SELECT MAX(run_date) FROM backtest_runs").fetchone()[0]
gate_ctx = qd._load_gate_context()
rows = list(conn.execute(
    "SELECT t.strategy_id, t.code, t.trigger_date FROM backtest_trades t "
    "JOIN backtest_runs r ON t.run_id=r.run_id WHERE r.run_date=? AND r.fee=0 "
    "AND t.strategy_id IN ('S1bV','S2V')", (batch,)))
viol = [r for r in rows if not qd._vol_gate_ok(r['code'], r['trigger_date'], gate_ctx)]
check(f"SxV 开仓触发日 100% 高波动域({len(rows)}笔全查)", not viol)
for vid, pid in [('S1aE', 'S1a'), ('S2E', 'S2'), ('S1bV', 'S1b'), ('S2V', 'S2')]:
    nv = conn.execute("SELECT COUNT(*) FROM backtest_trades t JOIN backtest_runs r ON "
                      "t.run_id=r.run_id WHERE r.run_date=? AND r.fee=0 AND t.strategy_id=?",
                      (batch, vid)).fetchone()[0]
    np_ = conn.execute("SELECT COUNT(*) FROM backtest_trades t JOIN backtest_runs r ON "
                       "t.run_id=r.run_id WHERE r.run_date=? AND r.fee=0 AND t.strategy_id=?",
                       (batch, pid)).fetchone()[0]
    check(f"{vid} 笔数({nv}) ≤ 原版 {pid}({np_})——门控只做减法", nv <= np_)
# 8.4 前向计数起点纪律: 门控变体无起点前交易
for vid in ('S1aE', 'S2E', 'S1bV', 'S2V'):
    floor = qd.BT_STRATEGIES[vid]['fwd_start']
    n_bad = conn.execute("SELECT COUNT(*) FROM forward_trades WHERE strategy_id=? AND "
                         "trigger_date < ?", (vid, floor)).fetchone()[0]
    check(f"{vid} 无 fwd_start({floor}) 前前向交易", n_bad == 0)
conn.close()
# 8.5 评估器与卡片
ev1, ev2 = qd.evaluate_s3_event(), qd.evaluate_vol_gate()
check("裁决读数各2变体", len(ev1['variants']) == 2 and len(ev2['variants']) == 2)
check("样本<20 笔时判定=积累中",
      all(v['verdict'] == '⏳ 样本积累中' for v in ev1['variants'] + ev2['variants']
          if v['n_trades'] < 20))
cards = qd.get_vol_gate_cards()
check("S1bV/S2V 卡片2张且引擎接入态", len(cards) == 2 and '门控就绪' in cards[0]['verdict'])
# 8.6 错杀候选视图
cand = qd.get_s3_candidates_view(days=45)
check("候选视图行含三条件布尔且 candidate=C1∧C2∧C3",
      all(isinstance(r['candidate'], bool)
          and r['candidate'] == (r['C1'] and r['C2'] and r['C3']) for r in cand['rows']))
# 8.7 读路径无DDL
for fn in (qd.get_s3_candidates_view, qd._load_gate_context, qd.evaluate_vol_gate):
    check(f"{fn.__name__} 无DDL", 'CREATE TABLE' not in inspect.getsource(fn))

print()
print("=" * 66)
print(f"模块12 测试汇总: {ok} 通过 / {len(fail)} 失败")
if fail:
    for f_ in fail:
        print(f"  ✗ {f_}")
    sys.exit(1)
