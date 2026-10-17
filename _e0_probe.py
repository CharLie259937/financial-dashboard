# -*- coding: utf-8 -*-
"""E0 数据源审计: akshare 事件族接口实测 (2026-10-18)
每族: 接口可达性 / 字段 / 历史深度 / 样本行数。轻量调用+超时, 防限速。"""
import io, sys, warnings
warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import akshare as ak
import pandas as pd

def probe(name, fn):
    print(f'\n===== {name} =====')
    try:
        df = fn()
        if df is None or len(df) == 0:
            print('  可达但空表')
            return
        print(f'  行数 {len(df)}, 列 {list(df.columns)[:12]}')
        dcol = next((c for c in df.columns if '日期' in c or 'Date' in c or '时间' in c), None)
        if dcol:
            print(f'  日期范围 {df[dcol].min()} ~ {df[dcol].max()}')
        print(df.head(2).to_string(max_colwidth=18)[:500])
    except Exception as e:
        print(f'  ✗ 失败: {type(e).__name__}: {str(e)[:150]}')

# 1 业绩预告 (东财, 按报告期)
probe('业绩预告 stock_yjyg_em(20240331)', lambda: ak.stock_yjyg_em(date="20240331"))
# 2 业绩快报 (东财, 按报告期)
probe('业绩快报 stock_yjkb_em(20240331)', lambda: ak.stock_yjkb_em(date="20240331"))
# 3 解禁 (东财 汇总)
probe('解禁 stock_restricted_release_summary_em', lambda: ak.stock_restricted_release_summary_em())
# 4 高管增减持 (东财)
probe('高管增减持 stock_ggcg_em(全部)', lambda: ak.stock_ggcg_em(symbol="全部"))
# 5 回购 (东财)
probe('回购 stock_repurchase_em', lambda: ak.stock_repurchase_em())
# 6 大宗交易 每日统计
probe('大宗交易 stock_dzjy_mrtj(20260915)', lambda: ak.stock_dzjy_mrtj(start_date="20260901", end_date="20260915"))
# 7 定增 (东财 增发)
probe('定增 stock_qbzf_em?', lambda: ak.stock_zf_issuance())
