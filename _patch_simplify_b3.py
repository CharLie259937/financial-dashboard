# -*- coding: utf-8 -*-
"""看板信息密度简化 · 批次3: T1/T4 去重 + 密度开关
1) sidebar 密度开关: 量化分析页下「展示密度: 精简(默认)/完整」→ 所有 📁 档案 expander 联动展开
2) T1: 宽表预览收纳 expander(诊断档案)
3) T4: 按市场分布/信号统计/股票覆盖/日线行情图 收纳一档; 估值两区块收纳一档(已知缺口#5 PE/PB为零)
4) 全局: 所有 📁 档案 expander 注入 expanded=ARCHIVE_EXPANDED
原则: 只折叠不删除; 零数据层改动。
"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

P = r"C:\Users\lenovo\Desktop\financial_dashboard\dashboard.py"
src = open(P, encoding='utf-8').read()
orig_len = len(src)


def find_one(hay, needle, what):
    n = hay.count(needle)
    assert n == 1, f"[{what}] anchor 出现 {n} 次(期望1): {needle[:60]!r}"
    return hay.index(needle)


def indent_block(block, delta):
    out = []
    for line in block.split('\n'):
        if line.strip() == '':
            out.append(line)
        else:
            out.append((' ' * delta) + line if delta > 0 else line[(-delta):])
    return '\n'.join(out)


def wrap(hay, start_anchor, end_anchor, label, base_indent, what):
    i = find_one(hay, start_anchor, what + '.start')
    j = find_one(hay, end_anchor, what + '.end') + len(end_anchor)
    block = hay[i:j]
    pad = ' ' * base_indent
    exp = (pad + 'with st.expander("' + label + '"):\n'
           + indent_block(block, 4))
    return hay[:i] + exp + hay[j:]


# ============ 1) sidebar 密度开关 ============
nav = 'page = st.sidebar.radio("导航", ["实时查询", "长期追踪", "量化分析", "数据库浏览"])\n'
i = find_one(src, nav, '导航radio')
toggle = nav + (
    '\n'
    '# 展示密度(看板简化批次3 2026-09-19): 档案层 expander 统一联动; 默认精简=收起\n'
    'ARCHIVE_EXPANDED = False\n'
    'if page == "量化分析":\n'
    '    _dens = st.sidebar.radio(\n'
    '        "展示密度", ["精简", "完整"], horizontal=True, index=0,\n'
    '        help="精简=研究档案区块默认收起(裁决/操作层直接可见); 完整=全部展开供研究回查")\n'
    '    ARCHIVE_EXPANDED = (_dens == "完整")\n'
)
src = src[:i] + toggle + src[i + len(nav):]

# ============ 2) T1 宽表预览收纳 ============
src = wrap(src,
           '        # 宽表预览',
           '                st.warning(f"未找到 {wt_code.strip()} 的宽表数据，请先生成宽表。")',
           '📁 宽表预览与信号触发明细（诊断档案）', 8, 'T1宽表')

# ============ 3) T4 收纳 ============
src = wrap(src,
           '        # 按市场分布',
           '            st.info("暂无日线数据，请先在数据采集标签页中采集")',
           '📁 数据分布 · 覆盖 · 日线图（诊断档案）', 8, 'T4诊断')
src = wrap(src,
           '        # --- 估值数据可视化 ---',
           '            st.info("暂无估值数据，请先在「数据采集」标签页点击「采集股票池估值」")',
           '📁 估值数据（观察档案 · 已知缺口#5: PE/PB 回填段为零）', 8, 'T4估值')

# ============ 4) 全部 📁 档案 expander 注入 expanded 参数 ============
src, n_wire = re.subn(
    r'(with st\.expander\("📁 [^"]*")\):',
    r'\1, expanded=ARCHIVE_EXPANDED):',
    src)
assert n_wire >= 13, f"联动档案框数量异常: {n_wire}"

open(P, 'w', encoding='utf-8', newline='').write(src)
print(f"批次3补丁完成: {orig_len} -> {len(src)} chars ({len(src)-orig_len:+d})")
print(f"密度开关联动档案 expander: {n_wire} 个")
