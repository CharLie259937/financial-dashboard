# -*- coding: utf-8 -*-
"""港股事件研究 Demo HTML 报告生成 (读 demo-hk-result.json)"""
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open(r'event_study\demo\demo-hk-result.json', encoding='utf-8'))
meta, groups, events = data['meta'], data['groups'], data['events']
GNAMES = {'annual': '年报 annual', 'interim': '中报 interim', 'quarterly': '季报及其他 quarterly'}
GCOLORS = {'annual': '#C0392B', 'interim': '#0969DA', 'quarterly': '#27AE60'}

def win_rows(g):
    g = groups.get(g)
    if not g:
        return '<tr><td colspan="6">无事件</td></tr>'
    return ''.join(f"<tr><td>{w['window']}</td><td>{w['n']}</td><td>{w['car_mean']:+.2f}</td>"
                   f"<td>{w['t_stat'] if w['t_stat'] is not None else '—'}</td>"
                   f"<td>{w['p_value'] if w['p_value'] is not None else '—'}</td><td>{w['sig']}</td></tr>"
                   for w in g['windows'])

ev_rows = ''.join(f"<tr><td>{e['date']}</td><td>{e['code']}</td><td>{e['type']}</td><td>{GNAMES.get(e['group'], e['group'])}</td></tr>"
                  for e in events[-60:])

series = []
for g in ('annual', 'interim', 'quarterly'):
    if g in groups:
        c = groups[g]['curve']
        vals = ','.join(str(v) if v is not None else 'null' for v in c['caar'])
        series.append(f"{{name:'{GNAMES[g]}',type:'line',data:[{vals}],symbolSize:5,"
                      f"lineStyle:{{width:2.4,color:'{GCOLORS[g]}'}},itemStyle:{{color:'{GCOLORS[g]}'}}}}")
series_js = ','.join(series)
kpi = ''.join(f"<div><b style='color:{GCOLORS[g]}'>{meta['groups'][g]}</b>{GNAMES[g]} 事件</div>"
              for g in ('annual', 'interim', 'quarterly') if g in meta['groups'])

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>事件研究 Demo · 港股财报</title>
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
</style></head><body>
<h1>事件研究 Demo · 港股财报披露</h1>
<div class="meta">{meta['universe']} · 事件源 demo_hk_earnings(staging, 东财公告 E1 同源) · {meta['engine']} · 生成 {meta['generated']}</div>
<div class="kpi"><div><b>{meta['n_events']}</b>有效事件(2020-06后)</div>{kpi}</div>
<h2>CAAR 曲线（事件窗 [-5,+10]，Day 0 = 公告日后首个交易日）</h2>
<div id="chart"></div>
<h2>窗口统计（CAR %，普通 t 检验）</h2>
<table><tr><th>组</th><th>窗口</th><th>n</th><th>CAR%</th><th>t</th><th>p</th><th>显著</th></tr>""" + ''.join(
    f"<tr><td rowspan='6'><b>{GNAMES[g]}</b></td></tr>" if i == 0 else ''
    for i, g in enumerate([])) + ''.join(
    f"<tr><td><b>{GNAMES[g]}</b></td></tr>" + win_rows(g).replace('<tr>', '<tr><td></td>', 0) if False else ''
    for g in []) + ''.join(
    ''.join(f"<tr><td><b>{GNAMES[g]}</b></td><td>{w['window']}</td><td>{w['n']}</td><td>{w['car_mean']:+.2f}</td>"
            f"<td>{w['t_stat'] if w['t_stat'] is not None else '—'}</td>"
            f"<td>{w['p_value'] if w['p_value'] is not None else '—'}</td><td>{w['sig']}</td></tr>"
            for w in groups[g]['windows']) for g in ('annual', 'interim', 'quarterly') if g in groups) + f"""
</table>
<h2>事件清单（最近 60 条）</h2>
<table><tr><th>公告日</th><th>代码</th><th>类型</th><th>组</th></tr>{ev_rows}</table>
<div class="note">
<b>Demo 口径声明（必读）</b>：<br>
① 本页为<b>管线演示</b>，不构成任何"发现"声明；结论须经 E1 检验工具箱（BMP/KP/GRANK+功效预算）与 E2 复现验收；<br>
② 财报事件<b>无意外度条件化</b>（港股缺一致预期基准，SUE 需另建）——无方向条件的财报日 AR 均值趋近零属预期，读数的意义在展示管线与分组结构，而非预测力；<br>
③ 未做 Kolari-Pynnönen 聚类修正（港股财报季 3/8 月高度聚类）与 BMP 方差修正，t 值大概率虚高；<br>
④ 数据缺口已记：汇丰 00005 / 友邦 01299 东财港股东悉无覆盖（0 事件）；02513 智谱上市短仅 2 事件；估计窗不足跳过 4 条；<br>
⑤ Day 0 = 公告日后首个交易日（港股财报多在午间/盘后披露，无时点字段下取保守映射）；基准为恒生指数。
</div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var rel = {groups['annual']['curve']['rel_days'] if 'annual' in groups else []};
chart.setOption({{
  animation:false,
  grid:{{left:50,right:20,top:40,bottom:36}},
  xAxis:{{type:'category',data:rel,name:'相对交易日',nameLocation:'middle',nameGap:26}},
  yAxis:{{type:'value',name:'CAAR %',axisLabel:{{formatter:'{{value}}%'}}}},
  series:[{series_js}],
  legend:{{top:6}},
  tooltip:{{trigger:'axis',valueFormatter:function(v){{return (v===null?'—':v.toFixed(2)+'%');}}}}
}});
</script>
</body></html>"""

open(r'event_study\demo\event-study-demo-hk.html', 'w', encoding='utf-8').write(html)
print('→ event_study/demo/event-study-demo-hk.html')
