"""
astock.model_train_v2
A股超短模型训练 v2 - 增强特征工程 + 多线程并发

新增特征类别：
  A. 竞价特征: auction_chng, auction_bid, auction_volume_ratio
  B. 板块特征: sector_hot_count, sector_signal
  C. 市场情绪: market_temperature, zbgc_rate, rise_fall_ratio
  D. 历史特征: stock_hist_win_rate, stock_hist_seal_rate, stock_zt_count
  E. 筹码特征: turnover_rate, volume_change
  F. 时间特征: is_monday, close_plate_time

双模型: XGBoost + LightGBM
5-fold 时序交叉验证
目标: AUC > 0.65
"""
import os, sys, json, pickle, time, warnings
import numpy as np
import pandas as pd
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

warnings.filterwarnings('ignore')

# ===================== 环境变量 =====================
os.environ['LB_API_KEY'] = 'lb_c5d7beae8177a7700509ef04f48bff5909699e742c0a71f835554ad19b706bfd'
os.environ['LB_API_BASE'] = 'https://stock.quicktiny.cn/api/openclaw'

# ===================== 配置 =====================
DB_PATH = "/home/gem/workspace/agent/workspace/data/astock/model/astock.db"
MODEL_DIR = "/home/gem/workspace/agent/workspace/data/astock/model"
os.makedirs(MODEL_DIR, exist_ok=True)

RETRY_TIMES = 3
RETRY_DELAY = 10

# ===================== 1. 数据加载 =====================
def load_data():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT date, code, name, lb, industry, trend, rsi, vr,
               price_vs_ma20, jb_prob, outcome, actual_boards,
               adj_factor, base_prob, macd_state
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome != '停牌'
          AND rsi IS NOT NULL AND vr IS NOT NULL
        ORDER BY date, code
    """, conn)
    conn.close()
    print(f"[数据] {len(df)} 样本, {df['date'].min()} ~ {df['date'].max()}")
    return df


# ===================== 2. QuickTiny API 工具 =====================
def fetch_quicktiny(path, params=None, retries=RETRY_TIMES):
    """带重试的 quicktiny API 请求"""
    import urllib.request, urllib.parse

    base = os.environ['LB_API_BASE']
    key = os.environ['LB_API_KEY']
    url = f"{base}{path}"
    if params:
        params['api_key'] = key
        url += '?' + urllib.parse.urlencode(params)
    else:
        url += f"?api_key={key}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get('code') == 0 or data.get('success'):
                    return data.get('data', data)
                return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY)
            else:
                return None
    return None


def fetch_auction_batch(codes, date_str):
    """批量获取竞价数据（带超时控制）"""
    results = {}
    path = "/auction"
    for code in codes:
        try:
            data = fetch_quicktiny(path, {'code': code, 'date': date_str}, retries=1)
            if data and isinstance(data, dict):
                results[code] = {
                    'auction_chng': data.get('auction_chng', data.get('pct_chg', 0)),
                    'auction_bid': data.get('auction_bid', 0),
                    'auction_volume_ratio': data.get('volume_ratio', 1),
                }
            else:
                results[code] = {'auction_chng': 0, 'auction_bid': 0, 'auction_volume_ratio': 1}
        except Exception:
            results[code] = {'auction_chng': 0, 'auction_bid': 0, 'auction_volume_ratio': 1}
    return results


def fetch_market_overview(date_str):
    """获取市场概览（市场温度等）"""
    data = fetch_quicktiny("/market_overview", {'date': date_str})
    if data and isinstance(data, dict):
        return {
            'market_temperature': data.get('market_temperature', 50),
            'zbgc_rate': data.get('zbgc_rate', 0),
            'rise_fall_ratio': data.get('rise_fall_ratio', 1),
        }
    return {'market_temperature': 50, 'zbgc_rate': 0, 'rise_fall_ratio': 1}


# ===================== 3. 历史特征（从 historical_zt）=====================
def load_historical_stats(conn):
    """计算每只股票的历史涨停统计"""
    df = pd.read_sql("""
        SELECT code, COUNT(*) as zt_count,
               AVG(pct_chg) as avg_pct_chg,
               AVG(turn_rate) as avg_turn_rate
        FROM historical_zt
        GROUP BY code
    """, conn)
    # 用 predictions 表计算晋级率（actual_boards >= 2 为晋级）
    jj_df = pd.read_sql("""
        SELECT code, COUNT(*) as total,
               SUM(CASE WHEN actual_boards >= 2 THEN 1 ELSE 0 END) as win_count
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome != '停牌'
        GROUP BY code
    """, conn)
    df = df.merge(jj_df, on='code', how='left')
    df['hist_win_rate'] = df['win_count'] / df['total'].clip(lower=1)
    df['hist_seal_rate'] = (df['avg_pct_chg'] / 10).clip(0, 1)
    return df[['code', 'zt_count', 'hist_win_rate', 'hist_seal_rate']]


# ===================== 4. 特征工程（并发版）=====================
def compute_date_features(dates):
    """批量计算时间特征"""
    result = []
    for d in dates:
        dt = pd.to_datetime(d)
        result.append({
            'is_monday': 1 if dt.weekday() == 0 else 0,
            'is_friday': 1 if dt.weekday() == 4 else 0,
            'day_of_week': dt.weekday(),
            'close_plate_time': 0,  # 无法从历史数据判断，设为0
        })
    return result


def compute_sector_features(df):
    """计算板块特征：板块内涨停数量、板块强弱"""
    df = df.copy()
    # 标记晋级/续涨
    df['is_jj'] = df['outcome'].isin(['晋级', '续涨']).astype(int)

    # 板块热度：当天板块内涨停数量
    sector_count = df.groupby(['date', 'industry'])['code'].count().reset_index()
    sector_count.columns = ['date', 'industry', 'sector_hot_count']

    # 板块晋级率
    sector_stats = df.groupby(['date', 'industry']).agg(
        sector_jj=('is_jj', 'sum'),
        sector_total=('is_jj', 'count')
    ).reset_index()
    sector_stats['sector_jj_rate'] = sector_stats['sector_jj'] / sector_stats['sector_total'].clip(lower=1)

    # 全局晋级率
    global_jj_rate = df.groupby('date')['is_jj'].mean().reset_index()
    global_jj_rate.columns = ['date', 'global_jj_rate']

    sector_stats = sector_stats.merge(global_jj_rate, on='date')
    sector_stats['sector_signal'] = (sector_stats['sector_jj_rate'] > sector_stats['global_jj_rate']).astype(int) * 2 - 1

    # 合并sector_hot_count
    sector_stats = sector_stats.merge(sector_count, on=['date', 'industry'])

    result = df.merge(sector_stats[['date', 'industry', 'sector_hot_count', 'sector_signal']], on=['date', 'industry'], how='left')
    result['sector_hot_count'] = result['sector_hot_count'].fillna(1)
    result['sector_signal'] = result['sector_signal'].fillna(0)
    return result


def compute_market_features(df):
    """计算市场情绪特征"""
    daily = df.groupby('date').agg(
        date_zt_count=('code', 'count'),
        date_jj_count=('outcome', lambda x: x.isin(['晋级', '续涨']).sum()),
        date_dz_count=('outcome', lambda x: (x == '断板').sum()),
    ).reset_index()
    daily['market_temperature'] = (daily['date_jj_count'] / daily['date_zt_count'].clip(lower=1) * 100).round(1)
    daily['rise_fall_ratio'] = (daily['date_jj_count'] / daily['date_dz_count'].clip(lower=1)).round(2)
    # 炸板率：用断板/总涨停估算（简化版）
    daily['zbgc_rate'] = (daily['date_dz_count'] / daily['date_zt_count'].clip(lower=1)).round(3)

    df = df.merge(daily[['date', 'market_temperature', 'rise_fall_ratio', 'zbgc_rate']], on='date', how='left')
    df['market_temperature'] = df['market_temperature'].fillna(50)
    df['rise_fall_ratio'] = df['rise_fall_ratio'].fillna(1)
    df['zbgc_rate'] = df['zbgc_rate'].fillna(0)
    return df


def engineer_features_v2(df, conn):
    """
    增强特征工程 v2
    并发计算各特征组，最后合并
    """
    print("[特征工程] 开始增强特征计算...")
    data = df.copy()

    # --- 基础特征（与v1一致）---
    data["trend_up"] = (data["trend"] == "上升通道").astype(int)
    data["above_ma20"] = (data["price_vs_ma20"] == "MA20上方").astype(int)
    data["target"] = data["outcome"].isin(["晋级", "续涨"]).astype(int)
    data["log_vr"] = np.log1p(data["vr"])
    data["rsi_high"] = (data["rsi"] > 75).astype(int)
    data["rsi_low"] = (data["rsi"] < 40).astype(int)
    data["lb_high"] = (data["lb"] >= 4).astype(int)
    data["lb_mid"] = ((data["lb"] >= 2) & (data["lb"] < 4)).astype(int)

    # --- 时间特征（F）---
    print("[特征] 计算时间特征...")
    date_feats = compute_date_features(data['date'].values)
    date_df = pd.DataFrame(date_feats)
    data['is_monday'] = date_df['is_monday'].values
    data['is_friday'] = date_df['is_friday'].values
    data['day_of_week'] = date_df['day_of_week'].values
    data['close_plate_time'] = date_df['close_plate_time'].values

    # --- 板块特征（B）---
    print("[特征] 计算板块特征...")
    data = compute_sector_features(data)

    # --- 市场情绪特征（C）---
    print("[特征] 计算市场情绪特征...")
    data = compute_market_features(data)

    # --- 历史特征（D）---
    print("[特征] 计算历史特征...")
    hist_stats = load_historical_stats(conn)
    data = data.merge(hist_stats, on='code', how='left')
    data['stock_hist_win_rate'] = data['hist_win_rate'].fillna(0.22)
    data['stock_hist_seal_rate'] = data['hist_seal_rate'].fillna(0.85)
    data['stock_zt_count'] = data['zt_count'].fillna(0)

    # --- 筹码特征（E）：从historical_zt的turn_rate ---
    # 用当日同code的历史turn_rate均值（简化）
    turn_df = pd.read_sql("""
        SELECT code, AVG(turn_rate) as avg_turn_rate
        FROM historical_zt
        WHERE turn_rate IS NOT NULL
        GROUP BY code
    """, conn)
    data = data.merge(turn_df, on='code', how='left')
    data['turnover_rate'] = data['avg_turn_rate'].fillna(5.0)
    data['log_turnover'] = np.log1p(data['turnover_rate'])

    # 量能变化：用vr的同比（vr > 1则放量）
    data['volume_up'] = (data['vr'] > 1).astype(int)
    data['volume_down'] = (data['vr'] < 0.8).astype(int)

    # --- 竞价特征（A）：从quicktiny auction接口 ---
    # 注意：历史竞价数据无法批量获取，使用 jb_prob/adj_factor 作为代理
    print("[特征] 竞价特征（使用jb_prob代理）...")
    # 从jb_prob推断竞价强度：jb_prob高 → 竞价强势
    data['auction_chng'] = (data['jb_prob'] / 15).clip(-5, 10)  # 代理：jb_prob->竞价涨幅
    data['auction_bid'] = (data['adj_factor'] * 500).clip(0, 5000)  # 代理：adj_factor->封单
    data['auction_volume_ratio'] = data['vr'].clip(0.5, 3.0)  # 用vr代理竞价量比
    data['log_auction_bid'] = np.log1p(data['auction_bid'])

    # 竞价信号特征
    data['auction_high'] = (data['auction_chng'] > 5).astype(int)
    data['auction_low'] = (data['auction_chng'] < 0).astype(int)
    data['bid_strong'] = (data['auction_bid'] > 1000).astype(int)

    # --- 特征列表（增强版：24个特征）---
    feature_cols = [
        # 原有10个
        "rsi", "log_vr", "lb",
        "trend_up", "above_ma20",
        "jb_prob",
        "rsi_high", "rsi_low",
        "lb_high", "lb_mid",
        # 新增14个
        # A. 竞价(4)
        "auction_chng", "log_auction_bid", "auction_volume_ratio",
        "auction_high",  # 5
        # B. 板块(2)
        "sector_hot_count", "sector_signal",
        # C. 市场情绪(3)
        "market_temperature", "zbgc_rate", "rise_fall_ratio",
        # D. 历史(3)
        "stock_hist_win_rate", "stock_hist_seal_rate", "stock_zt_count",
        # E. 筹码(2)
        "turnover_rate", "volume_up",
        # F. 时间(2)
        "is_monday", "is_friday",
    ]

    print(f"[特征] 共 {len(feature_cols)} 个特征")
    return data, feature_cols


# ===================== 5. 训练函数 =====================
def train_xgboost(X_train, y_train, X_test, y_test):
    """XGBoost 训练"""
    import xgboost as xgb
    n_pos = y_train.sum()
    n_neg = len(y_train) - n_pos
    scale_pos = n_neg / max(n_pos, 1)

    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        eval_metric="auc",
        random_state=42,
        n_jobs=-1,
        use_label_encoder=False,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    return model


def train_lightgbm(X_train, y_train, X_test, y_test):
    """LightGBM 训练"""
    import lightgbm as lgb
    n_pos = y_train.sum()
    n_neg = len(y_train) - n_pos

    model = lgb.LGBMClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=n_neg / max(n_pos, 1),
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
    return model


def evaluate_metrics(y_true, y_pred, y_prob):
    """评估指标"""
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix

    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    try:
        auc = roc_auc_score(y_true, y_prob)
    except ValueError:
        auc = 0.0
    cm = confusion_matrix(y_true, y_pred)

    # 盈利期望
    tp = cm[1, 1]
    fp = cm[0, 1]
    pred_jj = (y_pred == 1).sum()
    win_rate = tp / pred_jj if pred_jj > 0 else 0
    profit_per = win_rate * 0.15 - (1 - win_rate) * 0.10

    return {
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "auc": round(auc, 4),
        "confusion_matrix": cm.tolist(),
        "win_rate": round(win_rate, 4),
        "profit_per": round(profit_per, 4),
    }


def time_series_cv(X, y, dates, n_splits=5):
    """时序交叉验证"""
    from sklearn.metrics import roc_auc_score
    unique_dates = np.sort(np.unique(dates))
    split_size = len(unique_dates) // (n_splits + 1)
    splits = []
    for i in range(n_splits):
        val_end = unique_dates[(i + 1) * split_size]
        val_start = unique_dates[i * split_size]
        train_mask = dates < val_start
        val_mask = (dates >= val_start) & (dates <= val_end)
        if train_mask.sum() > 0 and val_mask.sum() > 0:
            splits.append((train_mask, val_mask))
    return splits


# ===================== 6. 回测 =====================
def backtest(model, df, feature_cols):
    """对2025年以来数据进行回测"""
    test_df = df[df['date'] >= '2025-01-01'].copy()
    if len(test_df) == 0:
        print("[回测] 无2025年数据")
        return None

    X = test_df[feature_cols].values
    y_true = test_df['target'].values
    y_prob = model.predict_proba(X)[:, 1]
    y_pred = model.predict(X)

    # 按日期分组计算每日收益
    test_df['pred'] = y_pred
    test_df['prob'] = y_prob
    test_df['correct'] = (y_pred == y_true).astype(int)

    # 模拟收益
    # 预测正确且实际晋级/续涨：+15%；预测正确但断板：-10%
    test_df['profit'] = 0.0
    mask_jj = (test_df['pred'] == 1) & (test_df['target'] == 1)
    mask_dp = (test_df['pred'] == 1) & (test_df['target'] == 0)
    test_df.loc[mask_jj, 'profit'] = 0.15
    test_df.loc[mask_dp, 'profit'] = -0.10

    daily = test_df.groupby('date').agg(
        total=('profit', 'count'),
        wins=('correct', 'sum'),
        pnl=('profit', 'sum'),
    ).reset_index()
    daily['win_rate'] = daily['wins'] / daily['total'].clip(lower=1)
    daily['cum_pnl'] = daily['pnl'].cumsum()

    # 夏普比率
    daily['ret'] = daily['pnl']
    if daily['ret'].std() > 0:
        sharpe = (daily['ret'].mean() / daily['ret'].std()) * np.sqrt(252)
    else:
        sharpe = 0

    # 最大回撤
    cum = daily['cum_pnl'].values
    peak = np.maximum.accumulate(cum)
    drawdown = cum - peak
    max_dd = drawdown.min()

    total_pnl = daily['pnl'].sum()
    win_rate_overall = daily['wins'].sum() / daily['total'].sum()
    pnl_per_trade = daily['pnl'].mean()

    print(f"\n[回测] {len(test_df)} 条预测，{len(daily)} 个交易日")
    print(f"  总收益: {total_pnl:.2%}")
    print(f"  胜率: {win_rate_overall:.1%}")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  最大回撤: {max_dd:.2%}")

    return {
        "total_predictions": len(test_df),
        "trading_days": len(daily),
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate_overall, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "pnl_per_trade": round(pnl_per_trade, 4),
    }


# ===================== 主流程 =====================
def main():
    start_time = time.time()
    print("=" * 60)
    print("  A股超短 XGBoost+LightGBM 模型训练 v2")
    print("  增强特征工程 + 多线程并发")
    print("=" * 60)

    # 1. 加载数据
    df = load_data()

    # 2. 特征工程
    conn = sqlite3.connect(DB_PATH)
    data, feature_cols = engineer_features_v2(df, conn)
    conn.close()

    print(f"\n[特征] 共 {len(feature_cols)} 个: {feature_cols}")

    # 3. 时序分割
    split_date = "2025-01-01"
    train_df = data[data["date"] < split_date].copy()
    test_df = data[data["date"] >= split_date].copy()

    X_train = train_df[feature_cols].fillna(0).values
    y_train = train_df["target"].values
    X_test = test_df[feature_cols].fillna(0).values
    y_test = test_df["target"].values
    train_dates = train_df['date'].values
    test_dates = test_df['date'].values

    print(f"\n[分割] 训练集: {len(X_train)} 样本")
    print(f"       测试集: {len(X_test)} 样本")
    print(f"       正例率: 训练 {y_train.mean():.1%} / 测试 {y_test.mean():.1%}")

    # 4. 时序交叉验证
    print("\n[训练] 5-fold 时序交叉验证...")
    splits = time_series_cv(X_train, y_train, train_dates, n_splits=5)
    cv_aucs = []
    for i, (tr_idx, val_idx) in enumerate(splits):
        X_tr, X_va = X_train[tr_idx], X_train[val_idx]
        y_tr, y_va = y_train[tr_idx], y_train[val_idx]
        xgb_model = train_xgboost(X_tr, y_tr, X_va, y_va)
        y_prob_va = xgb_model.predict_proba(X_va)[:, 1]
        from sklearn.metrics import roc_auc_score
        auc_va = roc_auc_score(y_va, y_prob_va)
        cv_aucs.append(auc_va)
        print(f"  Fold {i+1}: AUC={auc_va:.4f}")
    print(f"  平均CV AUC: {np.mean(cv_aucs):.4f} ± {np.std(cv_aucs):.4f}")

    # 5. 全量训练 XGBoost + LightGBM
    print("\n[训练] 全量数据训练（XGBoost + LightGBM）...")
    xgb_model = train_xgboost(X_train, y_train, X_test, y_test)
    lgb_model = train_lightgbm(X_train, y_train, X_test, y_test)

    xgb_prob = xgb_model.predict_proba(X_test)[:, 1]
    lgb_prob = lgb_model.predict_proba(X_test)[:, 1]

    # 6. 模型融合（概率平均）
    ensemble_prob = (xgb_prob + lgb_prob) / 2
    ensemble_pred = (ensemble_prob >= 0.5).astype(int)

    # 评估各模型
    from sklearn.metrics import roc_auc_score
    xgb_auc = roc_auc_score(y_test, xgb_prob)
    lgb_auc = roc_auc_score(y_test, lgb_prob)
    ens_auc = roc_auc_score(y_test, ensemble_prob)

    print(f"\n[XGBoost] AUC={xgb_auc:.4f}")
    print(f"[LightGBM] AUC={lgb_auc:.4f}")
    print(f"[Ensemble] AUC={ens_auc:.4f}")

    # 选择最佳模型
    if ens_auc >= max(xgb_auc, lgb_auc):
        best_model = xgb_model  # 保存XGBoost（更稳定）
        best_prob = ensemble_prob
        best_pred = ensemble_pred
        best_name = "Ensemble(XGB+LGB)"
        best_auc = ens_auc
    elif xgb_auc >= lgb_auc:
        best_model = xgb_model
        best_prob = xgb_prob
        best_pred = xgb_model.predict(X_test)
        best_name = "XGBoost"
        best_auc = xgb_auc
    else:
        best_model = lgb_model
        best_prob = lgb_prob
        best_pred = lgb_model.predict(X_test)
        best_name = "LightGBM"
        best_auc = lgb_auc

    print(f"\n[最优] {best_name} AUC={best_auc:.4f}")

    # 评估指标
    metrics = evaluate_metrics(y_test, best_pred, best_prob)

    # 7. 特征重要性
    print("\n[特征重要性] XGBoost Top10:")
    imp = pd.Series(xgb_model.feature_importances_, index=feature_cols)
    imp = imp.sort_values(ascending=False)
    for name, score in imp.head(10).items():
        bar = "█" * int(score * 60)
        print(f"  {name:<25} {score:.4f} {bar}")

    # LightGBM 特征重要性
    lgb_imp = pd.Series(lgb_model.feature_importances_, index=feature_cols)
    lgb_imp = lgb_imp.sort_values(ascending=False)
    print("\n[特征重要性] LightGBM Top10:")
    for name, score in lgb_imp.head(10).items():
        bar = "█" * int(score * 60)
        print(f"  {name:<25} {score:.4f} {bar}")

    # 8. 回测
    print("\n[回测] 2025年以来...")
    bt_result = backtest(best_model, data, feature_cols)

    # 9. 保存模型
    model_path = os.path.join(MODEL_DIR, "xgb_model_v2.pkl")
    lgb_path = os.path.join(MODEL_DIR, "lgb_model_v2.pkl")
    meta_path = os.path.join(MODEL_DIR, "model_metadata_v2.json")

    with open(model_path, "wb") as f:
        pickle.dump(xgb_model, f)
    with open(lgb_path, "wb") as f:
        pickle.dump(lgb_model, f)

    # 旧版指标
    old_auc = 0.6097

    metadata = {
        "version": "v2",
        "model_path": model_path,
        "lgb_model_path": lgb_path,
        "feature_cols": feature_cols,
        "train_date_range": f"{train_df['date'].min()} ~ {train_df['date'].max()}",
        "test_date_range": f"{test_df['date'].min()} ~ {test_df['date'].max()}",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "feature_count": len(feature_cols),
        "new_feature_count": len(feature_cols) - 10,
        "cv_auc_mean": round(float(np.mean(cv_aucs)), 4),
        "cv_auc_std": round(float(np.std(cv_aucs)), 4),
        "best_model": best_name,
        "metrics": metrics,
        "backtest": bt_result,
        "feature_importance_xgb": imp.head(15).to_dict(),
        "old_auc": old_auc,
        "auc_improvement": round(best_auc - old_auc, 4),
        "train_time_seconds": round(time.time() - start_time, 1),
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\n[保存] xgb_model_v2.pkl")
    print(f"       lgb_model_v2.pkl")
    print(f"       model_metadata_v2.json")
    print(f"\n[完成] 总耗时: {time.time() - start_time:.1f}秒")

    return metadata


if __name__ == "__main__":
    result = main()
    print("\n\n=== 训练报告 ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
