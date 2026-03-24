"""
astock/analyze
用历史数据优化预测和竞价策略
用法:
  python analyze.py compute_zt   # 从本地K线计算每日涨停，写入historical_zt
  python analyze.py compute_zb    # 从本地K线计算每日炸板
  python analyze.py predict_report # 预测效果报告
  python analyze.py auction_report # 竞价分析
  python analyze.py optimize       # 系数优化
"""
import sys, os, glob, re
sys.path.insert(0, os.path.dirname(__file__) or '.')

import sqlite3
import pandas as pd
import numpy as np
from config import DB_PATH


# ===================== 数据加载 =====================

def load_all_kline():
    """加载所有本地K线数据"""
    batch_dir = '/home/gem/workspace/agent/workspace/data/astock/kline/batches'
    files = sorted(glob.glob(f'{batch_dir}/batch_*.parquet'))
    if not files:
        print("无本地K线数据")
        return None
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values(['date', 'code']).reset_index(drop=True)


def load_predictions_with_outcome():
    """加载有结局的预测数据"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT p.date, p.code, p.name, p.lb, p.industry,
               p.trend, p.rsi, p.vr, p.macd_state, p.vol_status,
               p.prediction, p.outcome
        FROM predictions p
        WHERE p.outcome IS NOT NULL
    """, conn)
    conn.close()
    return df


# ===================== 涨停/炸板计算 =====================

def find_limitups(df, date):
    """从K线数据中找某日涨停股"""
    day_df = df[df['date'] == pd.to_datetime(date)].copy()
    if day_df.empty:
        return [], []

    dates = sorted(df['date'].unique())
    day_idx = list(dates).index(pd.to_datetime(date))
    if day_idx == 0:
        return [], []

    prev_date = dates[day_idx - 1]
    prev = df[df['date'] == prev_date].set_index('code')['close']

    zt_list, zb_list = [], []
    for _, row in day_df.iterrows():
        code = row['code']
        close, high = row['close'], row['high']
        open_p = row['open']

        if code not in prev.index:
            continue
        pc = prev.loc[code]
        if not (pc > 0 and close > 0):
            continue

        pct_chg = (close - pc) / pc * 100
        is_cyb = str(code).startswith(('300', '301', '688', '8'))
        zt_pct = 20.0 if is_cyb else 10.0
        zt_price = round(pc * (1 + zt_pct / 100), 2)

        is_zt = (high >= zt_price * 0.999 and pct_chg >= 9.5)
        is_zb = is_zt and close < zt_price * 0.999

        item = {
            'code': code, 'name': '',
            'close': close, 'high': high,
            'zt_price': zt_price, 'pct_chg': round(pct_chg, 2),
            'turn_rate': 0, 'reason': '', 'industry': '',
        }
        if is_zt:
            zt_list.append(item)
        if is_zb:
            zb_list.append(item)

    return zt_list, zb_list


def compute_all_zt():
    """
    从本地K线计算所有历史涨停，写入historical_zt表
    """
    from db import get_db, save_historical_zt

    df = load_all_kline()
    if df is None:
        return

    dates = sorted(df['date'].unique())
    print(f"共{len(dates)}个交易日: {dates[0].date()} ~ {dates[-1].date()}")

    conn = get_db()
    conn.execute('DELETE FROM historical_zt')
    conn.commit()
    conn.close()

    total_zt = 0
    day_stats = []

    for i, date in enumerate(dates):
        zt_list, zb_list = find_limitups(df, date)
        date_str = str(date.date())

        if zt_list:
            save_historical_zt(date_str, zt_list)
        total_zt += len(zt_list)

        if len(zt_list) > 0 or len(zb_list) > 0:
            day_stats.append({
                'date': date_str,
                'zt': len(zt_list),
                'zb': len(zb_list),
                'zb_rate': round(len(zb_list)/(len(zt_list)+len(zb_list))*100, 1)
                    if (len(zt_list)+len(zb_list)) > 0 else 0
            })

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(dates)} 完成，累计涨停{total_zt}只")

    print(f"\n总计: {total_zt}只涨停")
    return pd.DataFrame(day_stats)


# ===================== 预测策略分析 =====================

def parse_prediction(s):
    """从prediction字段解析晋级概率"""
    if pd.isna(s) or not s:
        return {}
    s = str(s)
    result = {}
    patterns = [
        ('jb_prob', r'jb_prob[=:\s]+([0-9.]+)'),
        ('dz_prob', r'dz_prob[=:\s]+([0-9.]+)'),
        ('signal', r'signal[=:\s]+([^,\)]+)'),
        ('distribution', r'distribution[=:\s]+(\{[^)]+\})'),
    ]
    for k, pat in patterns:
        m = re.search(pat, s)
        if m:
            result[k] = m.group(1).strip()
    return result


def predict_report():
    """分析预测准确度"""
    pred = load_predictions_with_outcome()
    if pred.empty:
        print("无标注数据")
        return

    def extract_outcome_is_jj(s):
        return 1 if isinstance(s, str) and '晋级' in s else 0

    pred['is_jj'] = pred['outcome'].apply(extract_outcome_is_jj)
    pred['pred_parsed'] = pred['prediction'].apply(parse_prediction)
    pred['pred_jb'] = pred['pred_parsed'].apply(
        lambda x: float(x.get('jb_prob', 0)) if x.get('jb_prob') else 0
    )

    print(f"=== 预测准确度报告 (n={len(pred)}) ===")
    if len(pred) == 0:
        return

    overall_actual = pred['is_jj'].mean() * 100
    overall_pred = pred['pred_jb'].mean()
    mae = (pred['pred_jb'] - pred['is_jj']*100).abs().mean()

    print(f"样本: {len(pred)}条 | 实际晋级率: {overall_actual:.1f}% | 模型预测均值: {overall_pred:.1f}% | MAE: {mae:.1f}")

    print("\n按板位分析:")
    for lb in sorted(pred['lb'].dropna().unique())[:6]:
        sub = pred[pred['lb'] == lb]
        if len(sub) < 1:
            continue
        actual = sub['is_jj'].mean() * 100
        pred_avg = sub['pred_jb'].mean()
        print(f"  {int(lb)}板: n={len(sub)} 实际{actual:.1f}% 预测{pred_avg:.1f}% | "
              f"偏差{actual-pred_avg:+.1f}%")

    print("\n按市场趋势:")
    for trend in pred['trend'].dropna().unique():
        sub = pred[pred['trend'] == trend]
        if len(sub) < 3:
            continue
        actual = sub['is_jj'].mean() * 100
        print(f"  {trend}: n={len(sub)} 实际{actual:.1f}%")

    print("\n晋级率矩阵 (板位 × MACD状态):")
    for macd in ['MACD多头', 'MACD空头']:
        for lb in sorted(pred['lb'].dropna().unique())[:5]:
            sub = pred[(pred['lb'] == lb) & (pred['macd_state'] == macd)]
            if len(sub) < 2:
                continue
            actual = sub['is_jj'].mean() * 100
            print(f"  {macd}+{int(lb)}板: n={len(sub)} 实际{actual:.1f}%")


# ===================== 竞价策略分析 =====================

def auction_report():
    """
    分析竞价成功率的特征组合。
    用历史数据找最优竞价条件。
    """
    pred = load_predictions_with_outcome()
    if pred.empty:
        print("无标注数据")
        return

    def extract_outcome(s):
        return str(s) if pd.notna(s) else ''

    def is_jj(s):
        return 1 if '晋级' in extract_outcome(s) else 0

    def is_zb(s):
        return 1 if '炸板' in extract_outcome(s) else 0

    pred['is_jj'] = pred['outcome'].apply(is_jj)
    pred['is_zb'] = pred['outcome'].apply(is_zb)

    print(f"=== 竞价策略分析 (n={len(pred)}) ===")
    print(f"晋级: {pred['is_jj'].sum()} | 炸板: {pred['is_zb'].sum()}")

    # 按板数分析
    print("\n板位与晋级率:")
    for lb in sorted(pred['lb'].dropna().unique())[:5]:
        sub = pred[pred['lb'] == lb]
        if len(sub) < 1:
            continue
        jb = sub['is_jj'].mean() * 100
        zb = sub['is_zb'].mean() * 100
        print(f"  {int(lb)}板: n={len(sub)} 晋级{jb:.1f}% 炸板{zb:.1f}%")

    # 按市场情绪分析（从market_sentiment字段）
    # 这个字段在historical_zt里有，我们可以结合来看

    # 理想竞价条件挖掘
    print("\n最优特征组合:")
    for macd in ['MACD多头', 'MACD空头', '']:
        for vol in ['缩量/平量', '温和放量', '']:
            key = (macd, vol)
            sub = pred[(pred['macd_state'] == macd) | (pred['vol_status'] == vol)]
            sub = sub if (macd or vol) else pred
            if macd:
                sub = pred[pred['macd_state'] == macd]
            if vol:
                sub = pred[pred['vol_status'] == vol]
            if len(sub) < 5:
                continue
            jb = sub['is_jj'].mean() * 100
            print(f"  {macd or '*'} + {vol or '*'}: n={len(sub)} 晋级{jb:.1f}%")


# ===================== 系数优化 =====================

def optimize():
    """
    暴力搜索最优系数组合。
    目标：最小化预测误差MAE。
    """
    pred = load_predictions_with_outcome()
    if len(pred) < 30:
        print(f"样本不足: {len(pred)}条，需要30+条")
        return

    def extract_jb(s):
        try:
            m = re.search(r'jb_prob[=:\s]+([0-9.]+)', str(s))
            return float(m.group(1)) if m else 0
        except:
            return 0

    pred['pred_jb'] = pred['prediction'].apply(extract_jb)
    pred['actual'] = pred['outcome'].apply(lambda x: 1.0 if '晋级' in str(x) else 0.0)

    print(f"=== 系数优化 (n={len(pred)}) ===")

    # 网格搜索
    best_mae = 999
    best = None

    # 测试板位系数
    for coef_lb in [0.3, 0.5, 0.7, 1.0, 1.5]:
        for coef_rsi in [0.1, 0.3, 0.5, 0.7]:
            for coef_vr in [0.1, 0.3, 0.5, 0.7]:
                for coef_macd in [0.3, 0.5, 0.7, 1.0]:
                    # 计算调整后预测分
                    lb_effect = pred['lb'].fillna(1).apply(lambda x: (x - 1) * coef_lb)
                    rsi_effect = pred['rsi'].fillna(50).apply(lambda x: (x - 50) / 100 * coef_rsi)
                    vr_effect = pred['vr'].fillna(1).apply(lambda x: (x - 1) * coef_vr)
                    macd_effect = pred['macd_state'].apply(
                        lambda x: coef_macd if '多头' in str(x) else -coef_macd*0.5
                    )
                    adj = pred['pred_jb'] * 0.5 + (lb_effect + rsi_effect + vr_effect + macd_effect) * 0.5
                    adj = adj.clip(0, 100)
                    mae = (adj - pred['actual'] * 100).abs().mean()

                    if mae < best_mae:
                        best_mae = mae
                        best = (coef_lb, coef_rsi, coef_vr, coef_macd, adj)

    print(f"最优系数:")
    print(f"  板位系数: {best[0]}")
    print(f"  RSI系数: {best[1]}")
    print(f"  VR系数: {best[2]}")
    print(f"  MACD系数: {best[3]}")
    print(f"  MAE: {best_mae:.2f}")

    # 给出系数调整建议
    print(f"\n建议调整:")
    print(f"  LB_COEF: 当前0.5 → 建议{best[0]}")
    print(f"  RSI_COEF: 当前0.3 → 建议{best[1]}")
    print(f"  VR_COEF: 当前0.3 → 建议{best[2]}")
    print(f"  MACD_COEF: 当前0.5 → 建议{best[3]}")


if __name__ == '__main__':
    import fire
    fire.Fire()
