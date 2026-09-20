# -*- coding: utf-8 -*-
"""看板信息密度简化 · 批次1: T6 信号效果 + T8 宏观日历
1) T8: ④⑤⑥ 宏观事件研究(模块6)收纳为单一档案 expander
2) T8: ⑦ FDR 块从 ④ 的 else: 内部提级到顶层(修复"研究为空则⑦不渲染"的潜伏嵌套)
3) T8: 封锁日台账从三层嵌套 expander 提级为 ⑦ 内一级直接展示(用户可发现性)
4) T6: O1 分市场分层从 if sig_rows: 内部提级为独立区块(昨日交付, 保持可见)
5) T6: 明细表+胜率/超额图 / 按股票分组+差异分析 / 主动事件+发布时点 三组收纳为档案 expander
原则: 只折叠不删除, 档案层 expander 默认收起; 零数据层改动。
"""
import io, sys
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


# ============ T8 ============
# --- 步骤1: 剪出 ⑦ FDR 块 (indent 16, 在 ④ 的 else: 内) ---
s7_start = '                st.write("**⑦ FDR 多重检验校正与宏观 Overlay (模块9)**")'
s7_end = '                                       "配置冻结后宏观研究重跑不自动更新 overlay(需新的预注册决策)")'
i7 = find_one(src, s7_start, '⑦块起点')
j7 = find_one(src, s7_end, '⑦块终点') + len(s7_end)
block7 = src[i7:j7]
assert 'BH 存活组明细' in block7 and '封锁日台账' in block7
# 从原位置删除(连同其后空行保留原样, 不动)
src = src[:i7] + src[j7:]

# --- 步骤2: ⑦ 块内部提级封锁日台账: expander(20) → 直接展示, 并移到 BH expander 之前 ---
ledg_start = '                        with st.expander("封锁日台账 (macro_overlay_days, 追加式永不改写):")'
ledg_end = '                            st.caption("台账刷新由每日管道执行(守卫=完整性截断日, 已入库净值日期永不回补封锁); "\n' \
           '                                       "配置冻结后宏观研究重跑不自动更新 overlay(需新的预注册决策)")'
b7 = block7
il = find_one(b7, ledg_start, '台账起点')
jl = find_one(b7, ledg_end, '台账终点') + len(ledg_end)
ledger_inner = b7[il + len(ledg_start):jl]  # expander 体 (indent 24)
# 去掉 expander 行, 内容 28→24 (与 else: 体同级直接渲染), 标题行加在前面(24)
ledg_direct = (
    '                        st.write("**封锁日台账**（macro_overlay_days · 追加式永不改写 · '
    '当前因宏观事件封锁的股票与天数）")\n'
    + indent_block(ledger_inner.strip('\n'), -4)
)
b7 = b7[:il] + b7[jl:]  # 先移除
# 插到 BH expander 之前
bh_anchor = '                        with st.expander("BH 存活组明细 (survive=1):"):'
ib = find_one(b7, bh_anchor, 'BH锚点')
b7 = b7[:ib] + ledg_direct + '\n\n' + b7[ib:]
# 整个 ⑦ 块 16→12
block7_new = indent_block(b7, -4)

# --- 步骤3: ④⑤⑥ 收纳 expander (indent 12) ---
s4 = '            st.write("**④ 宏观事件研究结果 (高重要性事件 × 指数 AR/CAR)**")'
s6_end = '                        st.dataframe(show_ev, use_container_width=True, hide_index=True)'
i4 = find_one(src, s4, '④起点')
j6 = find_one(src, s6_end, '⑥终点') + len(s6_end)
block456 = src[i4:j6]
assert '⑤ 意外方向拆分' in block456 and '⑥ 事件→交易日映射明细' in block456
exp456 = ('            # --- ④⑤⑥ 宏观事件研究档案(模块6, 结论已冻结 2026-09-06; 2026-09-19 批次1收纳) ---\n'
          '            with st.expander("📁 ④⑤⑥ 宏观事件研究档案（模块6 · 结果/意外方向拆分/映射明细）"):\n'
          + indent_block(block456, 4))
src = src[:i4] + exp456 + '\n\n' + block7_new + src[j6:]

# ============ T6 ============
# --- 步骤4: 提级 O1 分市场分层 (16 → 12, 独立区块) ---
o1_start = '                # --- O1: 分市场分层(跨市场报告批判性学习采纳项 2026-09-18) ---'
o1_end = '                except Exception as e:\n                    st.error(f"分市场分层读取失败: {e}")'
io1 = find_one(src, o1_start, 'O1起点')
jo1 = find_one(src, o1_end, 'O1终点') + len(o1_end)
o1_block = indent_block(src[io1:jo1], -4)
src = src[:io1] + src[jo1:]

# --- 步骤5: 明细+图表 收纳 (12) ---
d_start = '            st.write("**被动信号统计明细（按信号类型×方向×持有周期）**")'
d_end = '                           "正值表示看空信号触发后跌幅大于随机水平(预警有效)")'
id0 = find_one(src, d_start, '明细起点')
jd0 = find_one(src, d_end, '明细终点') + len(d_end)
block_detail = src[id0:jd0]
assert '各信号胜率 vs 随机基准' in block_detail and '各信号超额收益' in block_detail
exp_detail = ('            with st.expander("📁 被动信号统计明细与胜率/超额图表（研究档案）"):\n'
              + indent_block(block_detail, 4))
src = src[:id0] + exp_detail + '\n\n' + o1_block + src[jd0:]

# --- 步骤6: 按股票分组 + 差异分析 收纳 (12; 差异分析在 if per_stock: 内, 合并一框) ---
g_start = '            st.write("**按股票分组统计（同类信号在不同股票上的表现差异）**")'
g_end = '                if conclusions:\n                    st.write("③ 自动结论")\n                    for cc in conclusions:\n                        st.markdown(f"- {cc}")'
ig0 = find_one(src, g_start, '分组起点')
jg0 = find_one(src, g_end, '分组终点') + len(g_end)
block_g = src[ig0:jg0]
assert '股票表现差异分析' in block_g
exp_g = ('            with st.expander("📁 按股票分组统计与股票表现差异分析（研究档案）"):\n'
         + indent_block(block_g, 4))
src = src[:ig0] + exp_g + src[jg0:]

# --- 步骤7: 主动事件 + 发布时点 收纳 (12) ---
e_start = '            st.write("**主动事件统计（类型×方向×影响等级）**")'
e_end = '                st.info("暂无事件时点数据")'
ie0 = find_one(src, e_start, '主动事件起点')
je0 = find_one(src, e_end, '主动事件终点') + len(e_end)
block_e = src[ie0:je0]
assert '事件发布时点效果' in block_e
exp_e = ('            with st.expander("📁 主动事件统计与发布时点效果（研究档案）"):\n'
         + indent_block(block_e, 4))
src = src[:ie0] + exp_e + src[je0:]

open(P, 'w', encoding='utf-8', newline='').write(src)
print(f"批次1补丁完成: {orig_len} -> {len(src)} chars ({len(src)-orig_len:+d})")
print("档案 expander x5: ④⑤⑥宏观 / 被动明细图表 / 按股票分组差异 / 主动事件时点 / (BH存活组原样)")
print("提级: ⑦FDR(嵌套修复) / 封锁日台账(直接展示) / O1分市场分层(独立)")
