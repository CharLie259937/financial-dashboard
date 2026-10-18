# -*- coding: utf-8 -*-
"""D0.4+D0.5: 事件研究 demo HTML 报告生成 (读 demo-result.json → 独立HTML)"""
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open(r'event_study\demo\demo-result.json', encoding='utf-8'))
meta = data['meta']
groups = data['groups']
events = data['events']

def win_rows(gname):
    g = groups.get(gname)
    if not g:
        return '<tr><td colspan="6">无事件</td></tr>'
    return ''.join(f"<tr><td>{w['window']}</td><td>{w['n']}</td><td>{w['car_mean']:+.2f}</td>"
                   f"<td>{w['t_stat'] if w['t_stat'] is not None else '—'}</td>"
                   f"<td>{w['p_value'] if w['p_value'] is not None else '—'}</td><td>{w['sig']}</td></tr>"
                   for w in g['windows'])

ev_rows = ''.join(f"<tr><td>{e['date']}</td><td>{e['code']}</td><td>{e['type']}</td>"
                  f"<td>{'利好' if e['group']=='利好' else '利空'}</td></tr>"
                  for e in events[-60:])

curve_js = {}
for gname, g in groups.items():
    c = g['curve']
    pairs = ','.join(f"[{d},{v if v is not None else 'null'}]" for d, v in zip(c['rel_days'], c['caar']))
    curve_js[gname] = '[' + pairs + ']'
n_good = meta['groups'].get('利好', 0)
n_bad = meta['groups'].get('利空', 0)

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>事件研究 Demo · 业绩预告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
body{{font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1F2328;background:#fff}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px;border-left:3px solid #0969DA;padding-left:8px}}
.meta{{background:#F6F8FA;border:1px solid #D8DEE4;border-radius:8px;padding:10px 14px;font-size:13px;color:#424A53}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
th,td{{border:1px solid #D8DEE4;padding:5px 8px;text-align:center}}
th{{background:#EAF1FB}}
.kpi{{display:flex;gap:12px;margin:14px 0}}
.kpi div{{flex:1;background:#F6F8FA;border-radius:8px;padding:10px 12px;font-size:13px}}
.kpi b{{font-size:20px;display:block}}
.note{{font-size:12px;color:#6E7781;background:#FFF7E8;border-left:3px solid #D48806;padding:8px 12px;margin-top:20px;line-height:1.7;break-inside:avoid;page-break-inside:avoid}}
#chart{{width:100%;height:380px}}
.pos{{color:#C0392B}} .neg{{color:#27AE60}}
</style></head><body>
<h1>事件研究 Demo · A股业绩预告</h1>
<div class="meta">{meta['universe']} · 事件源 demo_forecast_events(staging) · {meta['engine']} · 生成 {meta['generated']}</div>
<div class="kpi">
  <div><b>{meta['n_events']}</b>有效事件(去重后)</div>
  <div><b class="pos">{n_good}</b>利好组(预增/略增/扭亏/续盈)</div>
  <div><b class="neg">{n_bad}</b>利空组(预减/略减/首亏/续亏)</div>
  <div><b>{meta['skipped']}</b>跳过明细</div>
</div>
<h2>CAAR 曲线（事件窗 [-5,+10]，Day 0 = 公告日后首个交易日）</h2>
<div id="chart"></div>
<h2>窗口统计（CAR %，普通 t 检验）</h2>
<h3 style="font-size:13px">利好组</h3><table><tr><th>窗口</th><th>n</th><th>CAR%</th><th>t</th><th>p</th><th>显著</th></tr>{win_rows('利好')}</table>
<h3 style="font-size:13px">利空组</h3><table><tr><th>窗口</th><th>n</th><th>CAR%</th><th>t</th><th>p</th><th>显著</th></tr>{win_rows('利空')}</table>
<h2>事件清单（最近 60 条）</h2>
<table><tr><th>公告日</th><th>代码</th><th>预告类型</th><th>组</th></tr>{ev_rows}</table>
<div class="note">
<b>Demo 口径声明（必读）</b>：<br>
① 本页为<b>管线演示</b>，不构成任何"发现"声明——事件因子结论须经 E1 检验工具箱（BMP/KP/GRANK 三重检验+功效预算）与 E2 复现验收；<br>
② 现用普通 t 检验：<b>未做 Kolari-Pynnönen 聚类修正</b>（预告季事件日高度聚类，t 值大概率虚高）、未做 BMP 事件诱导方差修正；<br>
③ A股业绩预告为<b>条件强制披露</b>（净利润为负/扭亏/±50% 变动等情形），大市值公司预告稀疏——样本是强选择偏差子集，不能外推到全市场；<br>
④ Day 0 = 公告日后首个交易日（盘后公告→次日映射的保守口径）；估计窗 [-120,-10] 市场模型（沪深300），最少 60 观测；<br>
⑤ 事件与行情均来自研究宇宙（research_universe，与交易池分离）；staging 表 demo_forecast_events，不影响任何生产表与策略面。
</div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var good = {curve_js.get('利好', '[]')};
var bad = {curve_js.get('利空', '[]')};
var rel = good.length ? good.map(function(p){{return p[0];}}) : bad.map(function(p){{return p[0];}});
chart.setOption({{
  animation:false,
  grid:{{left:50,right:20,top:40,bottom:36}},
  xAxis:{{type:'category',data:rel,name:'相对交易日',nameLocation:'middle',nameGap:26}},
  yAxis:{{type:'value',name:'CAAR %',axisLabel:{{formatter:'{{value}}%'}}}},
  series:[
    {{name:'利好(预增/略增/扭亏/续盈)',type:'line',data:good.map(function(p){{return p[1];}}),symbolSize:6,lineStyle:{{width:2.5,color:'#C0392B'}},itemStyle:{{color:'#C0392B'}},label:{{show:false}}}},
    {{name:'利空(预减/略减/首亏/续亏)',type:'line',data:bad.map(function(p){{return p[1];}}),symbolSize:6,lineStyle:{{width:2.5,color:'#27AE60'}},itemStyle:{{color:'#27AE60'}}}}
  ],
  legend:{{top:6}},
  tooltip:{{trigger:'axis',valueFormatter:function(v){{return (v===null?'—':v.toFixed(2)+'%');}}}}
}});
</script>
</body></html>"""

open(r'event_study\demo\event-study-demo.html', 'w', encoding='utf-8').write(html)
print('→ event_study/demo/event-study-demo.html')
