# -*- coding: utf-8 -*-
"""模块11测试: 分钟级数据(观察口径)
全程在临时库副本上执行(sqlite3 backup 复制, 真实库只读):
表结构与UNIQUE约束 / Yahoo分钟取数(live冒烟+纽约时区归属+源窗口截断) /
落库幂等与REPLACE自愈与NaN→NULL / 采集单股入口 / 引导链⑧全窗口种子 /
覆盖视图 / 分析入口 / 管道步骤注册 / 读路径无DDL
运行目录: Desktop\\financial_dashboard (live 冒烟需网络)"""
import inspect
import os
import shutil
import sqlite3
import sys
import tempfile
import time as _time

import pandas as pd

import quant_data as qd

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ============================================================
# [0] 临时库副本: 真实库 → tempfile (backup API, WAL安全)
# ============================================================
section("[0] 临时库副本隔离(真实库只读复制)")
_real_db = qd.DB_PATH
_tmpdir = tempfile.mkdtemp(prefix="m11_test_")
_tmp_db = os.path.join(_tmpdir, "quant_data.db")
_src = sqlite3.connect(_real_db)
_dst = sqlite3.connect(_tmp_db)
_src.backup(_dst)
_src.close()
_dst.close()
qd.DB_PATH = _tmp_db
qd.init_db()
check("临时库就绪且非原库", os.path.exists(_tmp_db) and _tmp_db != _real_db)

try:
    # ========================================================
    section("[1] 表结构: init_db 建表 + 唯一键 + 索引")
    conn = qd.get_db()
    has_t = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='intraday_quotes'").fetchone()
    has_i = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                         "AND name='idx_intraday_lookup'").fetchone()
    check("intraday_quotes 表已建", bool(has_t))
    check("查询索引 idx_intraday_lookup 已建", bool(has_i))
    conn.execute("INSERT INTO intraday_quotes(code, market, interval, ts, dt_local, "
                 "trade_date, close) VALUES('T','美股','5m',1000,'2026-01-01 10:00',"
                 "'2026-01-01',1.0)")
    conn.commit()
    try:
        conn.execute("INSERT INTO intraday_quotes(code, market, interval, ts, dt_local, "
                     "trade_date, close) VALUES('T','美股','5m',1000,'2026-01-01 10:00',"
                     "'2026-01-01',2.0)")
        conn.commit()
        dup_rejected = False
    except sqlite3.IntegrityError:
        conn.rollback()
        dup_rejected = True
    check("UNIQUE(code,interval,ts) 拒绝重复插入", dup_rejected)
    check("同ts不同粒度可共存(键含interval)",
          conn.execute("INSERT INTO intraday_quotes(code, market, interval, ts, "
                       "dt_local, trade_date, close) VALUES('T','美股','60m',1000,"
                       "'2026-01-01 10:00','2026-01-01',1.0)") is not None)
    conn.commit()
    conn.close()

    # ========================================================
    section("[2] Yahoo 分钟取数 live 冒烟(AAPL 60m 30天)")
    aapl = qd.fetch_yahoo_intraday("AAPL", "美股", "60m", 30, log=lambda m: None)
    check("AAPL 60m 30天返回K线", len(aapl) > 50, f"{len(aapl)}根")
    need_cols = {"ts", "dt_local", "trade_date", "open", "high", "low", "close", "volume"}
    check("返回列完整", need_cols.issubset(set(aapl.columns)))
    check("close 无空值(dropna)", aapl["close"].notna().all())
    check("dt_local 与 trade_date 日期一致",
          (aapl["dt_local"].str[:10] == aapl["trade_date"]).all())
    check("trade_date 无未来日期(纽约口径)",
          aapl["trade_date"].max() <= pd.Timestamp.now(
              tz="America/New_York").strftime("%Y-%m-%d"),
          f"max={aapl['trade_date'].max()}")
    # 时区判别: 正确纽约时区下, 每个≥5根的交易日 首根≤10点 末根≥15点(盘后不超20)
    _bad = 0
    _tot = 0
    for _d, _g in aapl.groupby("trade_date"):
        if len(_g) < 5:
            continue
        _tot += 1
        _h0 = int(_g.iloc[0]["dt_local"][11:13])
        _h1 = int(_g.iloc[-1]["dt_local"][11:13])
        if not (_h0 <= 10 and _h1 >= 15 and _h1 <= 20):
            _bad += 1
    check("会话时段归属纽约时区(≥80%日期首末根合理)",
          _tot > 0 and _bad <= 0.2 * _tot, f"{_tot}天中{_bad}天异常")
    sz5 = qd.fetch_yahoo_intraday("300750", "A股", "5m", 7, log=lambda m: None)
    check("A股 300750 5m 7天返回K线", len(sz5) > 100, f"{len(sz5)}根")
    check("A股粒度≈48根/日(5m)",
          30 <= len(sz5) / max(sz5['trade_date'].nunique(), 1) <= 60)

    # ========================================================
    section("[3] 源窗口截断与非法粒度")
    _logs = []
    f5 = qd.fetch_yahoo_intraday("AAPL", "美股", "5m", 90, log=_logs.append)
    _span = (f5["ts"].max() - f5["ts"].min()) / 86400 if len(f5) else 999
    check("请求90天截断到5m源窗口60天", _span <= 61, f"span={_span:.1f}天")
    check("截断行为有日志", any("截断" in str(x) for x in _logs))
    _logs2 = []
    f3 = qd.fetch_yahoo_intraday("AAPL", "美股", "3m", 7, log=_logs2.append)
    check("非法粒度返回空DataFrame", len(f3) == 0)
    check("非法粒度有日志", any("非法粒度" in str(x) for x in _logs2))
    fh = qd.fetch_yahoo_intraday("TESTHK", "港股", "5m", 60, log=lambda m: None)
    check("非数字港股代码无映射→空(引导链安全)", len(fh) == 0)

    # ========================================================
    section("[4] 落库: 幂等 / REPLACE自愈 / NaN→NULL")
    _now = int(_time.time())
    _df = pd.DataFrame({
        "ts": [_now, _now + 300, _now + 600],
        "dt_local": ["2026-09-10 10:00", "2026-09-10 10:05", "2026-09-10 10:10"],
        "trade_date": ["2026-09-10"] * 3,
        "open": [1.0, float("nan"), 3.0],
        "high": [1.5, 2.5, 3.5], "low": [0.9, 1.9, 2.9],
        "close": [1.2, 2.2, 3.2], "volume": [100.0, 200.0, 300.0]})
    r1 = qd.save_intraday_quotes(_df, "SYNTH", "美股", "5m")
    check("首次写入3根全部新增", r1 == {'written': 3, 'new': 3}, str(r1))
    r2 = qd.save_intraday_quotes(_df, "SYNTH", "美股", "5m")
    check("重复写入幂等(new=0)", r2['new'] == 0 and r2['written'] == 3, str(r2))
    conn = qd.get_db()
    n_after2 = conn.execute("SELECT COUNT(*) FROM intraday_quotes "
                            "WHERE code='SYNTH'").fetchone()[0]
    check("重跑后行数不变", n_after2 == 3, f"{n_after2}行")
    null_open = conn.execute("SELECT open FROM intraday_quotes WHERE code='SYNTH' "
                             "AND ts=?", (_now + 300,)).fetchone()[0]
    check("NaN open 落库为 NULL", null_open is None)
    conn.close()
    _df2 = _df.copy()
    _df2.loc[1, "close"] = 9.9
    r3 = qd.save_intraday_quotes(_df2, "SYNTH", "美股", "5m")
    conn = qd.get_db()
    c_upd = conn.execute("SELECT close FROM intraday_quotes WHERE code='SYNTH' "
                         "AND ts=?", (_now + 300,)).fetchone()[0]
    conn.close()
    check("REPLACE自愈: 同ts收盘被覆盖", r3['new'] == 0 and abs(c_upd - 9.9) < 1e-9,
          f"close={c_upd}")

    # ========================================================
    section("[5] 采集入口(单股 live, AAPL 近10天, 两粒度)")
    _rep = qd.collect_intraday_quotes(code="AAPL", market="美股", days=10,
                                      log=lambda m: None)
    check("单股采集返回两粒度", set(_rep['rows'].keys()) == {"AAPL:5m", "AAPL:60m"})
    check("AAPL 两粒度均有写入",
          all(_rep['rows'][k]['written'] > 0 for k in _rep['rows']),
          str({k: v['written'] for k, v in _rep['rows'].items()}))
    _rep2 = qd.collect_intraday_quotes(code="AAPL", market="美股", days=10,
                                       log=lambda m: None)
    check("采集级幂等: 重跑零新增",
          all(v['new'] == 0 for v in _rep2['rows'].values()))

    # ========================================================
    section("[6] 引导链⑧种子(300750 全窗口 live)")
    _seed = qd._onboard_seed_intraday("300750", "A股")
    _n60 = _seed.get("300750:60m", {}).get('new', 0)
    _n5 = _seed.get("300750:5m", {}).get('new', 0)
    check("60m 全窗口种子入库(>500根)", _n60 > 500, f"new={_n60}")
    check("5m 全窗口种子入库(>500根)", _n5 > 500, f"new={_n5}")

    # ========================================================
    section("[7] 覆盖视图与分析入口")
    _st = qd.get_intraday_status()
    check("视图含口径说明", '未复权' in _st['caliber'])
    _by = {(r['code'], r['interval']): r for r in _st['rows']}
    check("AAPL 两粒度在覆盖视图",
          _by[("AAPL", "5m")]['rows'] > 0 and _by[("AAPL", "60m")]['rows'] > 0)
    check("300750 60m 覆盖近2年",
          _by[("300750", "60m")]['start'] < "2025-06-01",
          f"start={_by[('300750', '60m')]['start']}")
    _last_d = _by[("300750", "60m")]['end']
    _bars = qd.get_intraday_bars("300750", _last_d, interval="60m")
    check("分析入口返回当日K线(按ts升序)",
          len(_bars) > 0 and all(_bars[i]['ts'] <= _bars[i + 1]['ts']
                                 for i in range(len(_bars) - 1)),
          f"{len(_bars)}根 @ {_last_d}")
    check("分析入口字段完整", {"ts", "dt_local", "open", "high", "low",
                              "close", "volume"}.issubset(_bars[0].keys()))

    # ========================================================
    section("[8] 管道与引导链注册 + 读路径无DDL")
    _src_p = inspect.getsource(qd.run_daily_pipeline)
    check("管道注册 intraday 步骤", "step('intraday'" in _src_p)
    check("intraday 步骤在 quotes 后(行情族相邻)",
          _src_p.index("step('intraday'") > _src_p.index("step('quotes'"))
    check("管道增量窗口=7天", qd.INTRADAY_REFRESH_DAYS == 7)
    _src_o = inspect.getsource(qd.onboard_pool_stock)
    check("引导链注册 intraday_seed(在 overlay_seed 与 join_event 之间)",
          _src_o.index("step('intraday_seed'") > _src_o.index("step('overlay_seed'")
          and _src_o.index("step('intraday_seed'") < _src_o.index("step('join_event'"))
    _src_rs = inspect.getsource(qd.get_intraday_status)
    _src_rb = inspect.getsource(qd.get_intraday_bars)
    check("读路径无DDL(模块9约定: 不含 CREATE TABLE)",
          "CREATE TABLE" not in _src_rs and "CREATE TABLE" not in _src_rb)

finally:
    # ========================================================
    section("[9] 清理临时库")
    qd.DB_PATH = _real_db
    shutil.rmtree(_tmpdir, ignore_errors=True)
    check("临时目录已清理", not os.path.exists(_tmpdir))

print("\n" + "=" * 62)
print(f"模块11测试完成: PASS={PASS} FAIL={FAIL}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
