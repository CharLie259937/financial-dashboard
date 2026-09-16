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
check("原值恢复后卡片回落未登记态", '待采集层' in qd.get_s3_prereg_cards()[0]['verdict'])

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
print(f"模块12 P1 测试汇总: {ok} 通过 / {len(fail)} 失败")
if fail:
    for f_ in fail:
        print(f"  ✗ {f_}")
    sys.exit(1)
