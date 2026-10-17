# -*- coding: utf-8 -*-
"""E0-3: 研究宇宙试点批次 (2026-10-18)
- 新表 research_universe (与 stock_pool 分离: 只做数据面, 不进任何策略触发过滤/前向成员)
- 试点 10只/市场 × 3市场, 行情回填复用 _onboard_backfill_quotes (QUOTE_BACKFILL_START 起)
- 产出: 每票回填行数/起止日期 → 审计表输入"""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'C:\Users\lenovo\Desktop\financial_dashboard')
import quant_data

PILOT = {
    'A股': [('600519', '贵州茅台'), ('601318', '中国平安'), ('600036', '招商银行'),
           ('000333', '美的集团'), ('600030', '中信证券'), ('601899', '紫金矿业'),
           ('002415', '海康威视'), ('600900', '长江电力'), ('688981', '中芯国际'),
           ('000725', '京东方A')],
    '港股': [('00005', '汇丰控股'), ('00388', '港交所'), ('00939', '建设银行'),
           ('00941', '中国移动'), ('01299', '友邦保险'), ('03690', '美团-W'),
           ('09988', '阿里巴巴-W'), ('01024', '快手-W'), ('00002', '中电控股'),
           ('00688', '中国海外发展')],
    '美股': [('MSFT', '微软'), ('NVDA', '英伟达'), ('GOOGL', '谷歌A'), ('AMZN', '亚马逊'),
           ('META', 'Meta'), ('TSLA', '特斯拉'), ('JPM', '摩根大通'), ('V', 'Visa'),
           ('UNH', '联合健康'), ('XOM', '埃克森美孚')],
}

conn = quant_data.get_db()
conn.execute("""CREATE TABLE IF NOT EXISTS research_universe (
    code TEXT NOT NULL, market TEXT NOT NULL, name TEXT, sector TEXT,
    purpose TEXT DEFAULT 'event_factor_research', batch TEXT DEFAULT 'e0_pilot_20261018',
    added_at TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (code, market))""")
for mkt, lst in PILOT.items():
    for code, name in lst:
        conn.execute("INSERT OR IGNORE INTO research_universe (code, market, name) VALUES (?,?,?)",
                     (code, mkt, name))
conn.commit()
n = conn.execute("SELECT COUNT(*) FROM research_universe").fetchone()[0]
conn.close()
print(f'research_universe 建表+登记 {n} 只 (试点批)')

ok, fail = [], []
for mkt, lst in PILOT.items():
    for code, name in lst:
        try:
            t0 = time.time()
            n = quant_data._onboard_backfill_quotes(code, mkt)
            ok.append((mkt, code, name, n, round(time.time() - t0, 1)))
            print(f'  ✓ {mkt} {code} {name}: {n} 行 ({time.time()-t0:.1f}s)')
        except Exception as e:
            fail.append((mkt, code, name, str(e)[:80]))
            print(f'  ✗ {mkt} {code} {name}: {type(e).__name__} {str(e)[:80]}')
        time.sleep(0.6)  # 防限速

print(f'\n回填完成: 成功 {len(ok)} / 失败 {len(fail)}')
if fail:
    for f in fail:
        print('  FAIL:', f)
