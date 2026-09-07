# -*- coding: utf-8 -*-
"""模块10测试: 股票池动态扩容与数据引导
全程在临时库副本上执行(sqlite3 backup 复制, 真实库只读):
迁移事件表 / 港股代码归一化 / onboard引导链(采集步打桩) / 资格区间过滤 /
B1首批成员冻结 / 离池快照零失配 / 停用再激活 / 管道孤儿股补课(9步) / 读路径无DDL
运行目录: Desktop\\financial_dashboard (依赖真实库的池与前向已入库数据)"""
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

import pandas as pd

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


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ============================================================
# [0] 临时库副本: 真实库 → tempfile (backup API, WAL安全)
# ============================================================
section("[0] 临时库副本隔离(真实库只读复制)")
_real_db = qd.DB_PATH
_tmpdir = tempfile.mkdtemp(prefix="m10_test_")
_tmp_db = os.path.join(_tmpdir, "quant_data.db")
_src = sqlite3.connect(_real_db)
_dst = sqlite3.connect(_tmp_db)
_src.backup(_dst)
_src.close()
_dst.close()
qd.DB_PATH = _tmp_db
qd.init_db()               # 副本补建 m10 三表(IF NOT EXISTS, 通常已随复制存在)
qd._m10_migrate_events()   # 副本补迁移(真实库迁移在 import 时已完成)
check("临时库就绪且非原库", os.path.exists(_tmp_db) and _tmp_db != _real_db)

conn = qd.get_db()
n_pool = conn.execute("SELECT COUNT(*) FROM stock_pool").fetchone()[0]
n_fwd_eq = conn.execute("SELECT COUNT(*) FROM forward_equity").fetchone()[0]
_b1_rows_base = conn.execute(
    "SELECT COUNT(*) FROM forward_equity WHERE strategy_id='B1'").fetchone()[0]
conn.close()
check("副本含池与前向已入库数据", n_pool >= 3 and n_fwd_eq >= 10,
      f"pool={n_pool}, forward_equity={n_fwd_eq}, B1={_b1_rows_base}")
if n_fwd_eq < 10:
    print("!! 前向已入库数据不足, 零失配断言无意义 — 请在 Desktop 运行目录执行本测试")
    sys.exit(1)


# ============================================================
# [1] 存量迁移: 事件表与资格区间
# ============================================================
section("[1] 存量迁移与资格区间")
conn = qd.get_db()
evs = [dict(r) for r in conn.execute(
    "SELECT code, event, eff_date, source FROM forward_pool_events ORDER BY code, id")]
conn.close()
joins = {e['code']: e['eff_date'] for e in evs if e['event'] == 'join'}
check("每只存量池股票有一条 join 事件", len(joins) >= n_pool, f"joins={joins}")
check("原始股(added_at≤2026-08-28) join_eff=FORWARD_START",
      all(v == qd.FORWARD_START for k, v in joins.items()
          if k in ('00700', 'AAPL', '300750')),
      str({k: v for k, v in joins.items() if k in ('00700', 'AAPL', '300750')}))
check("后加入股 join_eff=其实际入池日(历史信号不追溯)",
      joins.get('00100', '') == '2026-09-07' and joins.get('0100', '') >= '2026-09-07',
      f"00100 join={joins.get('00100')}, 0100 join={joins.get('0100')}")

mem = qd._forward_membership()
check("membership 区间结构正确(元组列表)",
      all(isinstance(ivs, list) and ivs and all(len(iv) == 2 for iv in ivs)
          for ivs in mem.values()))
check("_member_on 开放区间判定",
      qd._member_on(mem, '00700', '2026-09-04')
      and not qd._member_on(mem, '00700', '2026-08-27'))
check("_member_on 入池前历史日不判资格",
      not qd._member_on(mem, '00100', '2026-09-04')
      and qd._member_on(mem, '00100', '2026-09-07')
      and not qd._member_on(mem, '0100', '2026-09-07'))  # 换码离池: 同日join+leave=空区间

_today = datetime.now().strftime('%Y-%m-%d')
qd._m10_event('NOCODE', 'leave', 'm10_test')  # 不在池的无join序列
mem2 = qd._forward_membership()
check("leave 无 join 的极端序列 → 隐式区间 [FORWARD_START, leave) 不崩溃",
      mem2.get('NOCODE') == [(qd.FORWARD_START, _today)], f"NOCODE={mem2.get('NOCODE')}")


# ============================================================
# [2] 基线零失配: 引擎改造(快照注入+资格过滤+B1首批)必须逐日复现已入库净值
# ============================================================
section("[2] 基线零失配(引擎改造回归)")
rep0 = qd.run_forward_step(log=lambda m: None)
check("前向步进 ok", rep0.get('ok') is True, str(rep0.get('error', '')))
check("已入库 NAV 失配 = 0", rep0['mismatches'] == [],
      f"mismatches={rep0['mismatches'][:3]}")
check("无新增净值行(数据未更新, 截断日不变)", rep0['new_equity_rows'] == 0,
      f"new={rep0['new_equity_rows']}")
conn = qd.get_db()
proto = conn.execute("SELECT result_json FROM forward_meta "
                     "WHERE meta_key='pool_protocol'").fetchone()
conn.close()
_pp = json.loads(proto[0]) if proto else {}
check("pool_protocol 独立meta键已写入(4条规则)",
      all(k in _pp for k in ('membership_rule', 'b1_rule', 'leave_rule',
                             'adjudication_unchanged')))


# ============================================================
# [3] 港股代码归一化(add_to_pool)
# ============================================================
section("[3] 港股代码归一化")
ret = qd.add_to_pool('123', '港股')
check("add_to_pool('123','港股') → '00123'", ret == '00123', f"ret={ret}")
conn = qd.get_db()
in_pool = conn.execute("SELECT COUNT(*) FROM stock_pool WHERE code='00123'").fetchone()[0]
conn.close()
check("归一化代码入池", in_pool == 1)
removed = qd.remove_from_pool('00123')
check("无数据股移除正常(快照0行+leave事件)",
      removed.get('forward_snapshot', {}).get('px_rows') == 0,
      str(removed.get('forward_snapshot')))
conn = qd.get_db()
_n700a = conn.execute("SELECT COUNT(*) FROM forward_pool_events "
                      "WHERE code='00700' AND event='join'").fetchone()[0]
conn.close()
qd.add_to_pool('700', '港股')  # zfill → 00700, 已在池且活跃
conn = qd.get_db()
_n700b = conn.execute("SELECT COUNT(*) FROM forward_pool_events "
                      "WHERE code='00700' AND event='join'").fetchone()[0]
conn.close()
check("活跃股重复添加不追加 join 事件(防重复)",
      _n700a == _n700b == 1, f"{_n700a}→{_n700b}")


# ============================================================
# [4] 0100→00100 换码演练(部署步骤彩排)
# ============================================================
section("[4] 0100→00100 换码演练(4位池代码与5位行情代码不一致的修复路径)")
qd.remove_from_pool('0100')
ret = qd.add_to_pool('00100', '港股')
check("换码后入池代码为 00100(非 0100)", ret == '00100')
conn = qd.get_db()
codes = [r[0] for r in conn.execute("SELECT code FROM stock_pool WHERE is_active=1")]
ev0100 = [dict(r) for r in conn.execute(
    "SELECT event, eff_date, source FROM forward_pool_events "
    "WHERE code IN ('0100','00100') ORDER BY id")]
conn.close()
check("0100 事件留痕(join+leave)且 00100 在池",
      {e['event'] for e in ev0100} == {'join', 'leave'} and '00100' in codes
      and '0100' not in codes,
      f"events={[(e['event'], e['source']) for e in ev0100]}")
rep_sw = qd.run_forward_step(log=lambda m: None)
check("换码后前向重放仍零失配", rep_sw['mismatches'] == []
      and rep_sw['new_equity_rows'] == 0)
_ob = {s['code']: s for s in qd.get_pool_onboard_status()}
check("00100 已完成实库引导部署(行情/指标/信号/宽表齐备 → ready 非孤儿)",
      _ob.get('00100', {}).get('quotes', 0) > 0
      and _ob.get('00100', {}).get('wide', 0) > 0
      and _ob.get('00100', {}).get('needs_onboard') is False,
      f"00100={_ob.get('00100')}")


# ============================================================
# [5] 引导链(采集步打桩): TESTHK 全链路补数
# ============================================================
section("[5] 入池引导链(采集打桩, 指标/信号/宽表走真实生产代码)")

def _fabricate_quotes(code, market, days_end='2026-09-10'):
    """伪造 ~500 交易日随机游走 OHLCV, 植入三个大跌日:
    2026-08-20(前向窗口前) / 2026-09-01(窗口内·入池前=不追溯) / 2026-09-08(≥入池日=计入)"""
    dates = pd.bdate_range(end=days_end, periods=500)
    rows = []
    prev_close = None
    for d in dates:
        ds = d.strftime('%Y-%m-%d')
        if ds in ('2026-08-20', '2026-09-01', '2026-09-08'):
            close = prev_close * 0.92 if prev_close else 100.0
        else:
            drift = 0.0005 * ((hash(ds) % 11) - 5)
            close = (prev_close if prev_close else 100.0) * (1 + drift)
        openp = prev_close if prev_close else close * 0.995
        chg = (close / prev_close - 1) * 100 if prev_close else 0.0
        rows.append({'date': d, 'open': round(openp, 4),
                     'high': round(max(openp, close) * 1.002, 4),
                     'low': round(min(openp, close) * 0.998, 4),
                     'close': round(close, 4),
                     'volume': 1_000_000, 'amount': round(close * 1_000_000, 2),
                     'change_pct': round(chg, 2),
                     'amplitude': round((max(openp, close) - min(openp, close))
                                        / openp * 100, 2)})
        prev_close = close
    df = pd.DataFrame(rows)
    return qd._save_daily_quotes(df, code, market, data_source="m10_test")

qd._onboard_backfill_quotes = lambda code, market: _fabricate_quotes(code, market)
added = qd.add_to_pool('TESTHK', '港股')
check("TESTHK 入池(字母代码 zfill 不变形)", added == 'TESTHK')
ob_rep = qd.onboard_pool_stock('TESTHK', log=lambda m: None)
check("引导链 ok", ob_rep['ok'] is True,
      f"steps={ {k: (v if not isinstance(v, dict) else v) for k, v in ob_rep['steps'].items()} }")
conn = qd.get_db()
cov = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE code='TESTHK'").fetchone()[0]
       for t in ('daily_quotes', 'daily_indicators', 'passive_signals', 'daily_feature_base')}
sig_dates = [r[0] for r in conn.execute(
    "SELECT DISTINCT trade_date FROM passive_signals WHERE code='TESTHK' "
    "AND signal_type='price_limit' AND signal_subtype='大跌' ORDER BY trade_date")]
conn.close()
check("四表数据齐备(行情/指标/信号/宽表)", all(v > 0 for v in cov.values()), str(cov))
check("大跌信号在三个目标日检出(±0.05%随机游走不误触发)",
      {'2026-08-20', '2026-09-01', '2026-09-08'} == set(sig_dates), f"sig_dates={sig_dates}")
_ob = {s['code']: s for s in qd.get_pool_onboard_status()}
check("onboard 状态就绪(无需补课)", _ob['TESTHK']['ready'] is True)


# ============================================================
# [6] 资格区间过滤: 历史信号不追溯 / join 日起计入 / B1 不变
# ============================================================
section("[6] 前向资格区间过滤与 B1 冻结")
fwd = qd._forward_replay_data()
mem = qd._forward_membership()
trig_s1a = {(c, d) for (c, d) in qd._collect_triggers(fwd, qd.BT_STRATEGIES['S1a'])
            if d >= qd.FORWARD_START}
check("TESTHK 回填信号进入原始触发集(09-01/09-08)",
      ('TESTHK', '2026-09-01') in trig_s1a and ('TESTHK', '2026-09-08') in trig_s1a)
_fwd_trig = {(c, d) for (c, d) in trig_s1a if qd._member_on(mem, c, d)}
check("资格过滤后: 入池前(09-01)剔除 / 入池日起(09-08)保留",
      ('TESTHK', '2026-09-01') not in _fwd_trig and ('TESTHK', '2026-09-08') in _fwd_trig)
rep1 = qd.run_forward_step(log=lambda m: None)
check("TESTHK 就位后前向重放零失配(历史不追溯⇒已入库净值不变)",
      rep1['mismatches'] == [], f"mismatches={rep1['mismatches'][:3]}")
conn = qd.get_db()
b1_n = conn.execute("SELECT COUNT(*) FROM forward_equity "
                    "WHERE strategy_id='B1'").fetchone()[0]
conn.close()
check("B1 净值行数不变(新股永不进入 B1)", b1_n == _b1_rows_base,
      f"B1 {b1_n} vs base {_b1_rows_base}")


# ============================================================
# [7] 离池快照冻结: 移除参与过前向的原始股 → 零失配
# ============================================================
section("[7] 离池快照冻结(移除原始股 300750)")
conn = qd.get_db()
_b1_before = {r[0]: r[1] for r in conn.execute(
    "SELECT trade_date, nav FROM forward_equity WHERE strategy_id='B1'")}
_eq_before = conn.execute("SELECT COUNT(*) FROM forward_equity").fetchone()[0]
conn.close()
removed = qd.remove_from_pool('300750')
snap = removed.pop('forward_snapshot', {})
check("移除前快照非空(px ≥ FORWARD_START)", snap.get('px_rows', 0) > 0, str(snap))
check("级联删除生效(行情>0行)", removed.get('daily_quotes', 0) > 0,
      f"deleted={removed}")
conn = qd.get_db()
_in_active = conn.execute(
    "SELECT COUNT(*) FROM stock_pool WHERE code='300750'").fetchone()[0]
_snap_px = conn.execute(
    "SELECT COUNT(*) FROM forward_stock_px WHERE code='300750'").fetchone()[0]
conn.close()
check("300750 已出池且快照表留存", _in_active == 0 and _snap_px == snap.get('px_rows'),
      f"snap_px={_snap_px}")
rep2 = qd.run_forward_step(log=lambda m: None)
check("移除后前向重放零失配(快照复现已入库净值) ★核心验收",
      rep2['mismatches'] == [], f"mismatches={rep2['mismatches'][:3]}")
conn = qd.get_db()
_b1_after = {r[0]: r[1] for r in conn.execute(
    "SELECT trade_date, nav FROM forward_equity WHERE strategy_id='B1'")}
_eq_after = conn.execute("SELECT COUNT(*) FROM forward_equity").fetchone()[0]
conn.close()
check("B1 已入库净值逐日不变(sleeve 冻结在最后值)",
      _b1_before == _b1_after,
      f"diff={[d for d in _b1_before if _b1_before[d] != _b1_after.get(d)][:3]}")
check("净值总行数不变(无新增无丢失)", _eq_before == _eq_after,
      f"{_eq_before}→{_eq_after}")
mem = qd._forward_membership()
check("300750 资格区间已关闭", all(l is not None for (_, l) in mem.get('300750', [])))
mv = qd.get_pool_membership_view()
check("membership_view: 300750 不在开放成员但仍在 B1 首批名单",
      '300750' not in [m['code'] for m in mv['members']]
      and '300750' in mv['first_batch'])
check("membership_view: TESTHK/00100 不在 B1 首批(后加入)",
      'TESTHK' not in mv['first_batch'] and '00100' not in mv['first_batch'])
check("membership_view: 池协议可读", (mv.get('pool_protocol') or {}).get('b1_rule') is not None)


# ============================================================
# [8] 停用/再激活
# ============================================================
section("[8] 停用与再激活(AAPL)")
qd.set_pool_active('AAPL', 0)
conn = qd.get_db()
_aapl_state = conn.execute("SELECT is_active FROM stock_pool WHERE code='AAPL'").fetchone()[0]
_aapl_q = conn.execute("SELECT COUNT(*) FROM daily_quotes WHERE code='AAPL'").fetchone()[0]
_aapl_leave = conn.execute(
    "SELECT COUNT(*) FROM forward_pool_events WHERE code='AAPL' AND event='leave'").fetchone()[0]
conn.close()
check("停用生效+leave事件写入+数据保留(不级联删)",
      _aapl_state == 0 and _aapl_leave == 1 and _aapl_q > 0)
rep3 = qd.run_forward_step(log=lambda m: None)
check("停用后前向重放零失配(关闭区间只用快照)",
      rep3['mismatches'] == [], f"mismatches={rep3['mismatches'][:3]}")
qd.set_pool_active('AAPL', 1)
conn = qd.get_db()
_aapl_joins = conn.execute(
    "SELECT COUNT(*) FROM forward_pool_events WHERE code='AAPL' AND event='join'").fetchone()[0]
conn.close()
check("再激活写入新 join(旧区间快照冻结, 互不污染)", _aapl_joins == 2)
rep4 = qd.run_forward_step(log=lambda m: None)
check("再激活后前向重放零失配(开放区间用实时表)",
      rep4['mismatches'] == [], f"mismatches={rep4['mismatches'][:3]}")


# ============================================================
# [9] 管道孤儿股补课(9步) + 静态集成
# ============================================================
section("[9] 管道孤儿股自动补课")
qd.add_to_pool('ORPHAN', '港股')
_oc = qd._pipeline_onboard_check(log=lambda m: None)
check("孤儿股被检测并自动引导(ORPHAN; 00100 已实库部署就绪无需补课)",
      _oc['checked'] == 1 and _oc['onboarded'] == ['ORPHAN'],
      f"checked={_oc['checked']}, onboarded={_oc['onboarded']}")
_ob = {s['code']: s for s in qd.get_pool_onboard_status()}
check("补课后两股均就绪", _ob['00100']['ready'] is True and _ob['ORPHAN']['ready'] is True)
_oc2 = qd._pipeline_onboard_check(log=lambda m: None)
check("再次补课检查: 全部就绪无需补课", _oc2['checked'] == 0 and 'note' in _oc2)
src = inspect.getsource(qd.run_daily_pipeline)
_n_steps = src.count("step('")
check("管道 9 步", _n_steps == 9, f"steps={_n_steps}")
check("首步为 onboard_check(先补数后采集)",
      src.index("step('onboard_check'") < src.index("step('quotes'"))


# ============================================================
# [10] 读路径无DDL + 关键顺序(模块9问题日志#1/#4 回归防护)
# ============================================================
section("[10] 读路径无DDL与关键顺序")
for fn in (qd._forward_membership, qd._forward_replay_data, qd.get_pool_onboard_status,
           qd.get_pool_membership_view, qd._pipeline_onboard_check):
    check(f"{fn.__name__} 无DDL", 'CREATE TABLE' not in inspect.getsource(fn))
check("_snapshot_forward_stock INSERT OR REPLACE 幂等",
      'INSERT OR REPLACE' in inspect.getsource(qd._snapshot_forward_stock))
_src_rm = inspect.getsource(qd.remove_from_pool)
check("remove_from_pool 快照先于级联删除",
      _src_rm.index('_snapshot_forward_stock') < _src_rm.index('DELETE FROM'))
_src_fwd = inspect.getsource(qd.run_forward_step)
check("run_forward_step 触发过滤含资格区间",
      '_member_on(membership' in _src_fwd and 'first_batch' in _src_fwd)
check("pool_protocol DELETE+INSERT 幂等(问题日志#4)",
      "DELETE FROM forward_meta WHERE meta_key='pool_protocol'" in _src_fwd)


# ============================================================
# [11] 收尾清理(临时库删除, DB_PATH 还原)
# ============================================================
section("[11] 收尾清理")
qd.DB_PATH = _real_db
shutil.rmtree(_tmpdir, ignore_errors=True)
check("临时库已清理, DB_PATH 还原",
      qd.DB_PATH == _real_db and not os.path.exists(_tmp_db))

print()
print("=" * 62)
print(f"模块10测试结果: {PASS} 通过, {FAIL} 失败")
print("=" * 62)
sys.exit(1 if FAIL else 0)
