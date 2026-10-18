# -*- coding: utf-8 -*-
"""D0.2: 业绩预告一次性拉取 → staging 表 demo_forecast_events (2026-10-18)
- stock_yjyg_em × 2021-2026 各报告期(24次调用, 限速间隔0.8s)
- 过滤: 研究宇宙 A股10只 + 池内 300750
- 幂等: 表按 (code, period, 指标) 查重; 重跑覆盖式重建(staging语义)
demo 口径: 不进生产事件表(active_events/earnings_calendar), 不影响任何策略面"""
import io, sys, time, warnings
warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data
import akshare as ak

UNIVERSE = {'600519', '601318', '600036', '000333', '600030', '601899',
            '002415', '600900', '688981', '000725', '300750'}
PERIODS = [f'{y}{md}' for y in range(2021, 2027) for md in ('0331', '0630', '0930', '1231')]

rows = []
for i, p in enumerate(PERIODS):
    try:
        df = ak.stock_yjyg_em(date=p)
        hit = df[df['股票代码'].isin(UNIVERSE)]
        rows.extend(hit.to_dict('records'))
        print(f'  {p}: 全市场{len(df)}行, 宇宙内{len(hit)}行')
    except Exception as e:
        print(f'  {p}: ✗ {type(e).__name__} {str(e)[:80]}')
    time.sleep(0.8)

print(f'\n合计命中 {len(rows)} 条')
if not rows:
    sys.exit(1)

conn = quant_data.get_db()
conn.execute("""CREATE TABLE IF NOT EXISTS demo_forecast_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, name TEXT, period TEXT, announce_date TEXT,
    forecast_type TEXT, metric TEXT, change_pct REAL, pred_value REAL, prev_value REAL)""")
conn.execute('DELETE FROM demo_forecast_events')  # staging: 重建式
n_ins = 0
for r in rows:
    ann = str(r.get('公告日期', ''))[:10]
    if not ann or ann < '2021-01-01':
        continue
    conn.execute("""INSERT INTO demo_forecast_events
        (code, name, period, announce_date, forecast_type, metric, change_pct, pred_value, prev_value)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (r['股票代码'], r.get('股票简称', ''), r.get('预测指标', ''), ann,
         r.get('预告类型', ''), r.get('预测指标', ''),
         r.get('业绩变动幅度'), r.get('预测数值'), r.get('上年同期值')))
    n_ins += 1
conn.commit()
from collections import Counter
cnt = Counter(dict(conn.execute(
    "SELECT forecast_type, COUNT(*) FROM demo_forecast_events GROUP BY forecast_type"))for _ in [0]) if False else \
    dict(conn.execute("SELECT forecast_type, COUNT(*) FROM demo_forecast_events GROUP BY forecast_type"))
per_code = dict(conn.execute("SELECT code, COUNT(*) FROM demo_forecast_events GROUP BY code"))
conn.close()
print(f'入库 {n_ins} 条')
print('预告类型分布:', cnt)
print('按股票:', per_code)
