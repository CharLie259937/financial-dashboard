# -*- coding: utf-8 -*-
"""AppTest 全页渲染验证 (S2 衰减监控 + 数据减负 + D1 卡片)
惯例: 模块9 问题日志#7 — 读取冻结配置的 UI 必须先跑 AppTest 全页渲染, 0 异常交付"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from streamlit.testing.v1 import AppTest

DASH = r"C:\Users\lenovo\Desktop\financial_dashboard\dashboard.py"

at = AppTest.from_file(DASH, default_timeout=120)
at.run()
# 默认页为"实时查询", 切换到量化分析页再验证
at.sidebar.radio[0].set_value("量化分析").run()

print('异常数:', len(at.exception))
for e in at.exception:
    print('--- exception ---')
    print(e.value)
    print('stack:', e.stack_trace if hasattr(e, 'stack_trace') else '(无)')

# 定位量化看板页签区(Tab9 前向跟踪)
if not at.exception:
    # st.markdown 文本抓取: 验证新区块标题存在
    md_texts = [m.value for m in at.markdown]
    joined = '\n'.join(md_texts)
    exp_labels = [e.label for e in at.expander]
    cap_texts = [c.value for c in at.caption]
    # S1aE/S2E 卡片: progress 元素为 UnknownElement(text 不可读), 改由 caption 数量验证
    # (两张卡片 caption 均含 "S3预注册(模块12)", 数量=2 与 progress 7/7 双重确认)
    n_s3 = sum('S3预注册(模块12)' in (t or '') for t in cap_texts)
    checks = [
        ('⑦ 信号衰减监控', '⑦ 信号衰减监控' in joined),
        ('⑧ 信号采集瘦身', '⑧ 信号采集瘦身' in joined),
        ('⑨ 前向协议(expander)', any('⑨ 前向协议' in (l or '') for l in exp_labels)),
        ('悬置假设进度卡片', '悬置假设进度卡片' in joined),
        ('裁决规则回读(caption)', any('裁决规则回读' in (t or '') for t in cap_texts)),
        ('S1aE/S2E 预注册卡片 x2', n_s3 == 2),
    ]
    for name, ok in checks:
        print(('  OK  ' if ok else '  MISS') + name)
    # st.progress 数量(D1 卡片 7 个: S1d/S1e/S2M/S1aM/S2Q + S1aE/S2E 模块12 P0 预注册)
    n_prog = len(at.get('progress'))
    print('  progress 组件数:', n_prog, '(期望 7)' if n_prog == 7 else '(异常, 期望 7)')
    # st.warning (停采生效日后新增行提示)
    warn_texts = [w.value for w in at.warning]
    print('  warning:', len(warn_texts), warn_texts[:2])
    # st.error 应为 0
    err_texts = [e2.value for e2 in at.error]
    print('  error:', len(err_texts), err_texts[:3])

print()
print('AppTest 完成:', 'PASS' if not at.exception else 'FAIL')
