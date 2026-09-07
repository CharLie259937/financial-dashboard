"""模块7 Forward Test 全链路测试

覆盖: 三表建表幂等 / run_forward_step 确定性重放 / 追加幂等(零新行) /
协议冻结(单行) / 唯一键约束 / 裁决面板结构 / 新鲜度结构 / 看板视图 / 管道报告读取
运行: python test_module7.py  (在 financial_dashboard 目录下)
注意: 本测试会真实执行 run_forward_step(追加式, 幂等, 不破坏已有数据)
"""

import os
import sys
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import quant_data  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def t1_tables_idempotent():
    print("[T1] forward 三表建表幂等")
    conn = quant_data.get_db()
    quant_data.create_forward_tables(conn)
    quant_data.create_forward_tables(conn)  # 二次建表不得报错
    tabs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    check("三表存在", {'forward_trades', 'forward_equity', 'forward_meta'} <= tabs)


def t2_forward_step_runs():
    print("[T2] run_forward_step 确定性重放")
    rep = quant_data.run_forward_step(log=lambda m: None)
    check("返回 ok", rep.get('ok') is True, str(rep.get('error', '')))
    if rep.get('ok'):
        check("窗口起点=FORWARD_START", rep['window'][0] >= quant_data.FORWARD_START,
              f"{rep['window']}")
        check("含策略状态", len(rep['strategies']) == len(quant_data.BT_STRATEGIES))
        check("含守卫字段", 'mismatches' in rep and 'new_equity_rows' in rep)


def t3_idempotent_append():
    print("[T3] 追加幂等: 二次运行零新行")
    rep2 = quant_data.run_forward_step(log=lambda m: None)
    check("ok", rep2.get('ok') is True)
    if rep2.get('ok'):
        check("new_equity_rows == 0", rep2['new_equity_rows'] == 0,
              f"实际 {rep2['new_equity_rows']}")
        check("new_trade_rows == 0", rep2['new_trade_rows'] == 0,
              f"实际 {rep2['new_trade_rows']}")
        check("守卫无不一致", len(rep2['mismatches']) == 0,
              f"实际 {len(rep2['mismatches'])} 条")


def t4_protocol_frozen():
    print("[T4] 协议预注册且冻结(单行)")
    conn = quant_data.get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM forward_meta WHERE meta_key='protocol'").fetchone()[0]
    conn.close()
    check("protocol 恰好 1 行", n == 1, f"实际 {n} 行")


def t5_unique_constraints():
    print("[T5] 唯一键约束(业务幂等键)")
    conn = quant_data.get_db()
    row = conn.execute(
        "SELECT strategy_id, code, trigger_date FROM forward_trades LIMIT 1").fetchone()
    if row is None:
        conn.close()
        print("  [SKIP] 前向暂无交易, 跳过(约束由建表语句保证)")
        return
    try:
        conn.execute(
            "INSERT INTO forward_trades(strategy_id, code, trigger_date) VALUES(?,?,?)",
            (row['strategy_id'], row['code'], row['trigger_date']))
        conn.rollback()
        conn.close()
        check("重复 trade 插入被拒", False, "IntegrityError 未抛出")
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        check("重复 trade 插入被拒", True)
    except Exception as e:
        conn.close()
        check("重复 trade 插入被拒", False, f"异常类型不符: {e}")


def t6_equity_unique():
    print("[T6] forward_equity 唯一键")
    conn = quant_data.get_db()
    row = conn.execute(
        "SELECT strategy_id, trade_date FROM forward_equity LIMIT 1").fetchone()
    if row is None:
        conn.close()
        print("  [SKIP] 前向净值暂无行")
        return
    try:
        conn.execute(
            "INSERT INTO forward_equity(strategy_id, trade_date, nav) VALUES(?,?,?)",
            (row['strategy_id'], row['trade_date'], 999.0))
        conn.rollback()
        conn.close()
        check("重复 equity 插入被拒", False, "IntegrityError 未抛出")
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        check("重复 equity 插入被拒", True)
    except Exception as e:
        conn.close()
        check("重复 equity 插入被拒", False, f"异常类型不符: {e}")


def t7_watchlist():
    print("[T7] 裁决面板")
    wl = quant_data.evaluate_watchlist()
    check("含 S1d/S1e", {w['strategy_id'] for w in wl} == {'S1d', 'S1e'})
    for w in wl:
        check(f"{w['strategy_id']} 有裁决字段",
              all(k in w for k in ('verdict', 'progress', 'n_trades', 'win_rate',
                                   'excess_vs_b1', 'note')))
        check(f"{w['strategy_id']} 样本不足时 verdict=积累中",
              (w['n_trades'] >= w['min_trades']) or (w['verdict'] == '⏳ 样本积累中'),
              f"n={w['n_trades']}, verdict={w['verdict']}")


def t8_freshness():
    print("[T8] 数据新鲜度")
    fr = quant_data.get_data_freshness()
    pool = quant_data.get_stock_pool(active_only=True)
    check("覆盖全部活跃池股票", len(fr['stocks']) == len(pool),
          f"{len(fr['stocks'])}/{len(pool)}")
    for s in fr['stocks']:
        check(f"{s['code']} lag 为非负整数",
              isinstance(s['lag_trading_days'], int) and s['lag_trading_days'] >= 0,
              f"lag={s['lag_trading_days']}")
        check(f"{s['code']} 告警逻辑自洽",
              s['alert'] == (s['lag_trading_days'] > fr['threshold']))
    check("三市场指数日期", set(fr['benchmark']) == {'A股', '港股', '美股'},
          str(fr['benchmark']))
    check("alerts 为告警子集", all(s['alert'] for s in fr['alerts']))


def t9_forward_view():
    print("[T9] 看板前向视图")
    v = quant_data.get_forward_view()
    n_eq = len(v['equity'])
    check("净值序列含策略+B1/B2", n_eq >= len(quant_data.BT_STRATEGIES) + 2,
          f"实际 {n_eq} 条序列")
    for sid, e in v['equity'].items():
        check(f"{sid} 序列非空且等长",
              len(e['dates']) > 0 and len(e['dates']) == len(e['nav']))
    for t in v['trades']:
        check("交易明细字段完整",
              all(k in t for k in ('strategy_id', 'code', 'trigger_date', 'entry_date',
                                   'exit_date', 'return_pct')))
        break
    check("状态快照存在", isinstance(v['status'], dict))


def t10_pipeline_last_run():
    print("[T10] 管道最近运行报告")
    r = quant_data.get_pipeline_last_run()
    if r is None:
        print("  [SKIP] 尚未运行过管道(可接受, 看板会提示)")
        return
    # 模块10起管道为9步(首步onboard_check); 旧报告(6/8步)属历史运行, 不判失败
    if 'onboard_check' in r.get('steps', {}):
        check("报告含 steps 且为模块10后9步", len(r['steps']) == 9,
              str(list(r.get('steps', {}).keys())))
    else:
        print(f"  [SKIP] 旧管道报告({len(r.get('steps', {}))}步, 模块10前), 部署后重跑即9步")
    check("报告含新鲜度", 'freshness' in r)


def main():
    print("=" * 60)
    print("模块7 Forward Test 测试开始")
    print("=" * 60)
    t1_tables_idempotent()
    t2_forward_step_runs()
    t3_idempotent_append()
    t4_protocol_frozen()
    t5_unique_constraints()
    t6_equity_unique()
    t7_watchlist()
    t8_freshness()
    t9_forward_view()
    t10_pipeline_last_run()
    print("-" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
