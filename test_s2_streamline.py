# -*- coding: utf-8 -*-
"""S2/数据减负新函数只读验证(指向生产库, 不写库)"""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\lenovo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a8453a88dd6b53cecf90b41\financial_dashboard")
import quant_data as qd
# 工作区代码 + 生产库数据(问题日志#2: 工作区 quant_data.db 为过期小库)
qd.DB_PATH = r"C:\Users\lenovo\Desktop\financial_dashboard\quant_data.db"

print('=== 1. detect_signals 停采过滤(live 路径, 干跑不入库) ===')
# 直接验证过滤逻辑: live 应剔除停采子类
import pandas as pd
test_sigs = [
    {'signal_type': 'macd_cross', 'signal_subtype': '金叉'},
    {'signal_type': 'price_limit', 'signal_subtype': '大跌'},
    {'signal_type': 'rsi_overbought', 'signal_subtype': '超买'},
    {'signal_type': 'rsi24_oversold', 'signal_subtype': '超卖'},
    {'signal_type': 'kdj_cross', 'signal_subtype': '金叉'},
]
stopped = qd._STOPPED_SIGNALS
kept = [s for s in test_sigs if (s['signal_type'], s['signal_subtype']) not in stopped]
print('  输入5笔(含3笔停采类):', [(s['signal_type'], s['signal_subtype']) for s in test_sigs])
print('  live过滤后保留:', [(s['signal_type'], s['signal_subtype']) for s in kept])
assert len(kept) == 3, '过滤数量不符'
assert all((s['signal_type'], s['signal_subtype']) not in stopped for s in kept)

print()
print('=== 2. get_signal_streamline_view ===')
t0 = time.time()
sv = qd.get_signal_streamline_view()
print(f'  耗时 {time.time()-t0:.2f}s · {len(sv["rows"])} 个方向子类')
print(f'  keep={sv["summary"]["keep"]} stopped={sv["summary"]["stopped"]} '
      f'phase2={sv["summary"]["phase2"]} 生效日后live新停采行={sv["summary"]["post_stop_live_rows"]}')
for r in sv['rows']:
    print(f'  {r["status"]:18s} {r["signal_type"]:16s} {r["signal_subtype"]:5s} '
          f'{r["direction"]:8s} 总量={r["n_total"]:5d} live={r["n_live"]:4d} '
          f'最近={r["last_trigger"]} 消费者={r["consumers"]}')

print()
print('=== 3. get_signal_decay_view ===')
t0 = time.time()
dv = qd.get_signal_decay_view()
print(f'  耗时 {time.time()-t0:.2f}s · 日历 {dv["meta"]["n_days"]} 天 ({dv["meta"]["date_range"]})')
for sid, f in dv['families'].items():
    s = f['series']
    if not s:
        print(f'  {sid}: 无有效窗口')
        continue
    last = s[-1]
    valid = [x for x in s if x['diff_pp'] is not None]
    d0 = valid[0]['diff_pp'] if valid else None
    print(f'  {sid} ({f["name"]}, hold={f["hold"]}): {len(s)} 窗口, 最新 {last["date"]} '
          f'n={last["n"]} wr={last["win_rate"]:.1%} base={last["baseline"]:.1%} '
          f'diff={last["diff_pp"] if last["diff_pp"] is None else round(last["diff_pp"],1)}pp '
          f'首窗diff={d0 if d0 is None else round(d0,1)}pp')
c = dv['crowding']
if c:
    print(f'  拥挤度: 最新 {c[-1]["date"]} 多股同日占比={c[-1]["pct_multi"]:.1%} '
          f'日均触发股数={c[-1]["avg_codes"]:.2f}')
print()
print('验证完成')
