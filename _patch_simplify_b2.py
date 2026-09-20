# -*- coding: utf-8 -*-
"""看板信息密度简化 · 批次2: T7 策略回测 + T9 前向跟踪
1) T7: ⑤绩效明细/⑥费率敏感性/⑦交易明细/⑧分段稳健性/⑨容量诊断 五个单次运行深挖视图收纳为档案 expander
   (保留直接展示: ⚙️运行管理/①参数/②净值/③回撤/④总表/④b/④c机制对照/⑩自动结论)
2) T9: ①每日管道收纳 expander + 外置一行管道状态摘要(每日瞥一眼即可);
   ⑥前向交易明细收纳 expander
   (保留: ②新鲜度/③裁决家族/④净值/⑤快照/⑦衰减/⑧瘦身/⑨协议/⑩-⑬)
原则: 只折叠不删除; 零数据层改动。
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


def wrap(hay, start_anchor, end_anchor, label, base_indent, what):
    """把 [start_anchor起, end_anchor止] 包进同缩进 expander, 返回新源码"""
    i = find_one(hay, start_anchor, what + '.start')
    j = find_one(hay, end_anchor, what + '.end') + len(end_anchor)
    block = hay[i:j]
    pad = ' ' * base_indent
    exp = (pad + 'with st.expander("' + label + '"):\n'
           + indent_block(block, 4))
    return hay[:i] + exp + hay[j:]


# ============ T7 (indent 12) ============
IND = 12
src = wrap(src,
           '            st.write(f"**⑤ 绩效指标明细 · {bt_seg} 段 × 费率 {bt_fee * 100:.1f}%**")',
           '                       "(T+1开盘成交相对信号日收盘买入的执行损耗)")',
           '📁 ⑤ 绩效指标明细（回测档案）', IND, 'T7⑤')
src = wrap(src,
           '            st.write("**⑥ 费率敏感性 (总收益%, 单边 0 / 0.1% / 0.3%)**")',
           '                       "由正转负, 低频策略(大跌S1a/S1b)受费率影响最小")',
           '📁 ⑥ 费率敏感性（回测档案）', IND, 'T7⑥')
src = wrap(src,
           '            # ---- ⑦ 交易明细表 ----',
           '                st.info(f"{tr_sid} 在 {bt_seg} 段 × 费率{bt_fee * 100:.1f}% 下无成交交易")',
           '📁 ⑦ 交易明细（回测档案）', IND, 'T7⑦')
src = wrap(src,
           '            # ---- ⑧ 分段稳健性 ----',
           '                       "管选择偏差控制)是两种并存口径, 用途分开(见下方协议说明)")',
           '📁 ⑧ 分段稳健性（回测档案）', IND, 'T7⑧')
src = wrap(src,
           '            # ---- ⑨ 诊断信息 ----',
           '                       "(仅 S*M 列有值); 资金占用率 = 持仓槽市值占比均值")',
           '📁 ⑨ 触发与容量诊断（回测档案）', IND, 'T7⑨')

# ============ T9 (indent 8) ============
# --- ① 每日管道: 外置状态摘要 + 整块收纳 ---
p_start = '        # ---------- ① 每日管道 ----------'
p_end = '                st.info("尚未运行过每日管道（点击左侧按钮或运行 daily_pipeline.py）")'
i = find_one(src, p_start, 'T9①.start')
j = find_one(src, p_end, 'T9①.end') + len(p_end)
block_p = src[i + len(p_start):j]  # 不含注释行
status_line = (
    '        _pl_last = quant_data.get_pipeline_last_run()\n'
    '        if _pl_last:\n'
    '            _pl_ok = sum(1 for v in _pl_last.get("steps", {}).values() if v == "OK")\n'
    '            _pl_n = len(_pl_last.get("steps", {}))\n'
    '            st.caption(f"① 每日管道 · 最近运行 {_pl_last.get(\'generated_at\', \'—\')} · "\n'
    '                       f"成功 {_pl_ok}/{_pl_n} 步 · 展开可手动运行/查看失败步骤")\n'
    '        else:\n'
    '            st.caption("① 每日管道 · 尚未运行过 · 展开可手动运行")\n'
    '        with st.expander("📁 ① 每日管道（手动运行 · 运行日志）"):\n'
    + indent_block(block_p.strip('\n'), 4)
)
src = src[:i] + p_start + '\n' + status_line + src[j:]

# --- ⑥ 前向交易明细: 收纳 ---
src = wrap(src,
           '        # ⑥ 交易明细',
           '            st.info("前向窗口暂无已完成交易")',
           '📁 ⑥ 前向交易明细（最近 300 笔 · 追加式）', 8, 'T9⑥')

open(P, 'w', encoding='utf-8', newline='').write(src)
print(f"批次2补丁完成: {orig_len} -> {len(src)} chars ({len(src)-orig_len:+d})")
print("T7 档案 expander x5: ⑤绩效/⑥费率/⑦交易/⑧分段/⑨容量")
print("T9: ①管道(状态摘要外置+收纳) / ⑥前向交易明细收纳")
