# -*- coding: utf-8 -*-
"""看板信息密度简化批次1 验证 (T6 信号效果 + T8 宏观日历)
惯例: 模块9 问题日志#7 — UI 改动 AppTest 全页 0 异常交付。
批次1 内容: ④⑤⑥宏观档案收纳 / ⑦嵌套修复 / 封锁台账提级 / O1提级 / T6 三组档案收纳。
两阶段: ①全新会话(仅无条件区块) ②注入 m3_result(验证 T6 会话态区块)。
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from streamlit.testing.v1 import AppTest

DASH = r"C:\Users\lenovo\Desktop\financial_dashboard\dashboard.py"
sys.path.insert(0, r"C:\Users\lenovo\Desktop\financial_dashboard")

fails = []

# ---------- 阶段1: 全新会话 ----------
at = AppTest.from_file(DASH, default_timeout=180)
at.run()
at.sidebar.radio[0].set_value("量化分析").run()
n_err = len(at.exception)
print(f"[阶段1 全新会话] 异常数: {n_err}")
for e in at.exception:
    print('--- exception ---'); print(e.value)
if n_err:
    fails.append('阶段1存在异常')

md = '\n'.join(m.value for m in at.markdown)
exp_labels = [e.label for e in at.expander]
checks1 = [
    ('④⑤⑥ 宏观档案 expander', any('④⑤⑥ 宏观事件研究档案' in (l or '') for l in exp_labels)),
    ('⑦ FDR 提级后仍渲染(md)', '⑦ FDR 多重检验校正' in md),
    ('封锁日台账 提级直接展示(md)', '封锁日台账' in md and '当前因宏观事件封锁的股票与天数' in md),
    ('BH 存活组明细 仍为 expander', any('BH 存活组明细' in (l or '') for l in exp_labels)),
    ('危机窗相关性 保留', '危机窗相关性' in md),
    ('AH股溢价 保留', 'AH股溢价' in md),
]
for name, ok in checks1:
    print(('  OK  ' if ok else '  MISS') + name)
    if not ok:
        fails.append(name)

# ---------- 阶段2: 注入 m3_result 验证 T6 会话态区块 ----------
import quant_data
at2 = AppTest.from_file(DASH, default_timeout=300)
at2.session_state["m3_result"] = quant_data.run_module3_analysis()
at2.run()
at2.sidebar.radio[0].set_value("量化分析").run()
n_err2 = len(at2.exception)
print(f"[阶段2 注入m3_result] 异常数: {n_err2}")
for e in at2.exception:
    print('--- exception ---'); print(e.value)
if n_err2:
    fails.append('阶段2存在异常')

md2 = '\n'.join(m.value for m in at2.markdown)
exp2 = [e.label for e in at2.expander]
checks2 = [
    ('被动明细图表 档案 expander', any('被动信号统计明细与胜率/超额图表' in (l or '') for l in exp2)),
    ('按股票分组差异 档案 expander', any('按股票分组统计与股票表现差异分析' in (l or '') for l in exp2)),
    ('主动事件时点 档案 expander', any('主动事件统计与发布时点效果' in (l or '') for l in exp2)),
    ('O1 分市场分层 提级后直接渲染(md)', '分市场分层' in md2 and 'keep-5 家族' in md2),
    ('核心有效信号池 保留直接展示', '核心有效信号池' in md2),
    ('RSI24 时间分割 保留', 'RSI24 时间分割验证' in md2),
]
for name, ok in checks2:
    print(('  OK  ' if ok else '  MISS') + name)
    if not ok:
        fails.append(name)

# 档案 expander 应默认收起(AppTest 无法直读展开态, 以渲染顺序存在性代替)
print()
if fails:
    print('FAIL:', fails); sys.exit(1)
print('批次1 AppTest 全部通过 PASS')
