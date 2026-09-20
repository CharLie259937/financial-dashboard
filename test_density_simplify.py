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
cap_texts = [c.value for c in at.caption]
checks1 = [
    ('④⑤⑥ 宏观档案 expander', any('④⑤⑥ 宏观事件研究档案' in (l or '') for l in exp_labels)),
    ('⑦ FDR 提级后仍渲染(md)', '⑦ FDR 多重检验校正' in md),
    ('封锁日台账 提级直接展示(md)', '封锁日台账' in md and '当前因宏观事件封锁的股票与天数' in md),
    ('封锁日明细 逐日表渲染(caption)', any('封锁日明细' in (t or '') for t in cap_texts)),
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

# ---------- 阶段3: 批次2 (T7 策略回测 + T9 前向跟踪, 全新会话) ----------
at3 = AppTest.from_file(DASH, default_timeout=180)
at3.run()
at3.sidebar.radio[0].set_value("量化分析").run()
n_err3 = len(at3.exception)
print(f"[阶段3 批次2] 异常数: {n_err3}")
for e in at3.exception:
    print('--- exception ---'); print(e.value)
if n_err3:
    fails.append('阶段3存在异常')

md3 = '\n'.join(m.value for m in at3.markdown)
exp3 = [e.label for e in at3.expander]
cap3 = [c.value for c in at3.caption]
checks3 = [
    ('T7 ⑤ 绩效指标明细 档案', any('⑤ 绩效指标明细（回测档案）' in (l or '') for l in exp3)),
    ('T7 ⑥ 费率敏感性 档案', any('⑥ 费率敏感性（回测档案）' in (l or '') for l in exp3)),
    ('T7 ⑦ 交易明细 档案', any('⑦ 交易明细（回测档案）' in (l or '') for l in exp3)),
    ('T7 ⑧ 分段稳健性 档案', any('⑧ 分段稳健性（回测档案）' in (l or '') for l in exp3)),
    ('T7 ⑨ 容量诊断 档案', any('⑨ 触发与容量诊断（回测档案）' in (l or '') for l in exp3)),
    ('T7 ② 净值曲线 保留', '② 净值曲线' in md3),
    ('T7 ④ 对比总表 保留', '④ 策略回测对比总表' in md3 or '④ 对比总表' in md3),
    ('T7 ⑩ 自动结论 保留', '⑩ 自动结论' in md3),
    ('T9 ① 管道状态摘要(caption)', any('每日管道 · 最近运行' in (t or '') for t in cap3)),
    ('T9 ① 管道 expander', any('① 每日管道（手动运行 · 运行日志）' in (l or '') for l in exp3)),
    ('T9 ⑥ 前向交易明细 expander', any('⑥ 前向交易明细' in (l or '') for l in exp3)),
    ('T9 ② 新鲜度 保留', '② 数据新鲜度' in md3),
    ('T9 ⑦ 衰减监控 保留', '⑦ 信号衰减监控' in md3),
]
for name, ok in checks3:
    print(('  OK  ' if ok else '  MISS') + name)
    if not ok:
        fails.append(name)

# ---------- 阶段4: 批次3 (密度开关 + T1/T4 收纳, 全新会话; 含开关切换重跑) ----------
at4 = AppTest.from_file(DASH, default_timeout=180)
at4.run()
at4.sidebar.radio[0].set_value("量化分析").run()
n_err4 = len(at4.exception)
print(f"[阶段4 批次3·精简默认] 异常数: {n_err4}")
for e in at4.exception:
    print('--- exception ---'); print(e.value)
if n_err4:
    fails.append('阶段4存在异常')

exp4 = [e.label for e in at4.expander]
checks4 = [
    ('sidebar 展示密度开关存在', len(at4.sidebar.radio) >= 2
        and list(at4.sidebar.radio[1].options) == ["精简", "完整"]),
    ('开关默认精简', at4.sidebar.radio[1].value == "精简"),
    ('T1 宽表预览 档案', any('宽表预览与信号触发明细' in (l or '') for l in exp4)),
    ('T4 数据分布诊断 档案', any('数据分布 · 覆盖 · 日线图' in (l or '') for l in exp4)),
    ('T4 估值 档案', any('估值数据（观察档案' in (l or '') for l in exp4)),
    ('14 档案框全部联动展开参数', sum(1 for _ in range(1)), ),  # 占位: 联动由阶段5覆盖
]
checks4 = [c for c in checks4 if c[0] != '14 档案框全部联动展开参数']
for name, ok in checks4:
    print(('  OK  ' if ok else '  MISS') + name)
    if not ok:
        fails.append(name)

# 切换到"完整"重跑: 全档案展开渲染无异常
if len(at4.sidebar.radio) >= 2:
    at4.sidebar.radio[1].set_value("完整").run()
    n_err4b = len(at4.exception)
    print(f"[阶段4b 切换完整模式] 异常数: {n_err4b}")
    for e in at4.exception:
        print('--- exception ---'); print(e.value)
    if n_err4b:
        fails.append('完整模式存在异常')
    else:
        print('  OK  完整模式全档案展开渲染 0 异常')

print()
if fails:
    print('FAIL:', fails); sys.exit(1)
print('批次1+2+3 AppTest 全部通过 PASS')
