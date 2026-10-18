# -*- coding: utf-8 -*-
"""港股事件研究 Demo · 数据拉取 (2026-10-19)
- 复用模块12内部函数 _fetch_em_notices / _e1_subtype (东财港股东悉, E1同源)
- 对象: 研究宇宙港股10只 + 池内 00700/02513 (双覆盖无害, 幂等去重)
- staging 表 demo_hk_earnings, 不碰生产表 earnings_calendar"""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data as qd

CODES = [('00005', '汇丰控股'), ('00388', '港交所'), ('00939', '建设银行'),
         ('00941', '中国移动'), ('01299', '友邦保险'), ('03690', '美团-W'),
         ('09988', '阿里巴巴-W'), ('01024', '快手-W'), ('00002', '中电控股'),
         ('00688', '中国海外发展'), ('00700', '腾讯控股'), ('02513', '智谱')]

conn = qd.get_db()
conn.execute("""CREATE TABLE IF NOT EXISTS demo_hk_earnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, name TEXT, event_date TEXT, subtype TEXT, title TEXT, UNIQUE(code, event_date, subtype))""")
conn.execute('DELETE FROM demo_hk_earnings')
trig = qd.E1_TRIG['港股']
for code, name in CODES:
    n_new = 0
    try:
        for d, t in qd._fetch_em_notices(code, 'H', log=lambda m: None):
            if any(p in t for p in trig):
                sub = qd._e1_subtype(t)
                cur = conn.execute(
                    "SELECT 1 FROM demo_hk_earnings WHERE code=? AND event_date=? AND subtype=?",
                    (code, d, sub)).fetchone()
                if not cur:
                    conn.execute("INSERT INTO demo_hk_earnings (code, name, event_date, subtype, title) VALUES (?,?,?,?,?)",
                                 (code, name, d, sub, t))
                    n_new += 1
        print(f'  {code} {name}: +{n_new}')
    except Exception as e:
        print(f'  {code} {name}: ✗ {type(e).__name__} {str(e)[:80]}')
    time.sleep(0.7)
conn.commit()
from collections import Counter
print('\n按类型:', dict(conn.execute("SELECT subtype, COUNT(*) FROM demo_hk_earnings GROUP BY subtype")))
print('按股票:', dict(conn.execute("SELECT code, COUNT(*) FROM demo_hk_earnings GROUP BY code")))
r = conn.execute("SELECT MIN(event_date), MAX(event_date), COUNT(DISTINCT event_date) FROM demo_hk_earnings").fetchone()
print('日期范围:', r[0], '~', r[1], '| 去重事件日:', r[2])
conn.close()
