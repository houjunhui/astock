"""
astock.model_train
XGBoost 晋级/断板 二分类模型训练

数据：26,228 样本（2024-01-03 ~ 2026-03-19）
特征：RSI / VR / 连板数 / 趋势 / MA20位置 / 规则模型输出
标签：断板=0 / 续涨+晋级=1
时序分割：2024训练 / 2025-2026测试
"""
import os, sys, json, pickle
import numpy as np
import pandas as pd
import sqlite3
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, confusion_matrix, classification_report
)
from sklearn.model_selection import TimeSeriesSplit
import xgboost as xgb

# ===================== 配置 =====================
DB_PATH = "/home/gem/workspace/agent/workspace/data/astock/model/astock.db"
MODEL_DIR = "/home/gem/workspace/agent/workspace/data/astock/model"
os.makedirs(MODEL_DIR, exist_ok=True)

# ===================== 1. 加载数据 =====================
def load_data():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT date, code, lb, trend, rsi, vr,
               price_vs_ma20, jb_prob, outcome
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome != '停牌'
          AND rsi IS NOT NULL AND vr IS NOT NULL
        ORDER BY date
    """, conn)
    conn.close()
    print(f"[数据] {len(df)} 样本, {df['date'].min()} ~ {df['date'].max()}")
    return df

# ===================== 2. 特征工程 =====================
def engineer_features(df):
    """将原始字段转为模型特征向量"""
    data = df.copy()

    # 趋势：上升通道=1，其他=0
    data["trend_up"] = (data["trend"] == "上升通道").astype(int)

    # MA20：上方=1，下方=0
    data["above_ma20"] = (data["price_vs_ma20"] == "MA20上方").astype(int)

    # 目标：晋级或续涨=1（盈利），断板=0（亏损）
    data["target"] = data["outcome"].isin(["晋级", "续涨"]).astype(int)

    # 对数换手率（VR天然已是比例，但有极端值）
    data["log_vr"] = np.log1p(data["vr"])

    # RSI 分桶（非线性关系）
    data["rsi_high"] = (data["rsi"] > 75).astype(int)
    data["rsi_low"] = (data["rsi"] < 40).astype(int)

    # 板位分段
    data["lb_high"] = (data["lb"] >= 4).astype(int)
    data["lb_mid"] = ((data["lb"] >= 2) & (data["lb"] < 4)).astype(int)

    # 特征列表
    feature_cols = [
        "rsi", "log_vr", "lb",
        "trend_up", "above_ma20",
        "jb_prob",
        "rsi_high", "rsi_low",
        "lb_high", "lb_mid",
    ]
    return data, feature_cols

# ===================== 3. 训练 + 评估 =====================
def train_xgboost(X_train, y_train, X_test, y_test):
    """训练 XGBoost，返回模型 + 预测结果"""

    # 计算类别权重（断板是少数类）
    n_pos = y_train.sum()
    n_neg = len(y_train) - n_pos
    scale_pos = n_neg / n_pos  # ≈ 1.4

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        eval_metric="auc",
        random_state=42,
        n_jobs=-1,
        use_label_encoder=False,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # 预测
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    return model, y_pred, y_prob


def evaluate(y_true, y_pred, y_prob, label_names=["断板", "晋级/续涨"]):
    """打印完整评估报告"""
    print("\n" + "=" * 50)
    print("  模型评估报告")
    print("=" * 50)

    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)

    try:
        auc = roc_auc_score(y_true, y_prob)
    except ValueError:
        auc = 0.0

    print(f"\n准确率: {acc:.3f}")
    print(f"精确率: {prec:.3f} (预测为晋级的样本中，真正晋级的比例)")
    print(f"召回率: {rec:.3f} (实际晋级的样本中，模型正确识别的比例)")
    print(f"F1分数: {f1:.3f}")
    print(f"AUC-ROC: {auc:.3f}")

    print(f"\n混淆矩阵:")
    cm = confusion_matrix(y_true, y_pred)
    print(f"              预测断板  预测晋级/续涨")
    print(f"  实际断板      {cm[0,0]:>5}      {cm[0,1]:>5}")
    print(f"  实际晋级/续涨 {cm[1,0]:>5}      {cm[1,1]:>5}")

    print(f"\n详细报告:")
    print(classification_report(y_true, y_pred, target_names=label_names, zero_division=0))

    # 盈利期望分析
    total = len(y_true)
    n_jj = (y_true == 1).sum()
    n_dp = (y_true == 0).sum()
    pred_jj = (y_pred == 1).sum()
    # 按预测标签的胜率
    tp = cm[1, 1]  # 预测晋级，实际晋级
    fp = cm[0, 1]  # 预测晋级，实际断板
    if pred_jj > 0:
        win_rate = tp / pred_jj  # 预测晋级的标的中，实际晋级的比例
        print(f"预测晋级/续涨: {pred_jj} 只 ({pred_jj/total*100:.1f}%)")
        print(f"预测晋级的胜率: {win_rate:.1%} ({tp}胜/{fp}负)")
        print(f"若按模型预测操作，预期盈利: {win_rate*0.15 - (1-win_rate)*0.10:.3f}元/股")
        # 假设续涨/晋级平均收益+15%，断板平均亏损-10%

    return {
        "accuracy": acc, "precision": prec, "recall": rec,
        "f1": f1, "auc": auc, "confusion_matrix": cm.tolist(),
    }


# ===================== 4. 特征重要性 =====================
def print_feature_importance(model, feature_cols):
    imp = pd.Series(model.feature_importances_, index=feature_cols)
    imp = imp.sort_values(ascending=False)
    print("\n特征重要性排序:")
    for name, score in imp.items():
        bar = "█" * int(score * 50)
        print(f"  {name:<20} {score:.4f} {bar}")


# ===================== 5. 概率校准 =====================
def calibrate_prob(y_true, y_prob):
    """
    将模型输出的原始概率按分桶校准为真实胜率。
    与规则模型的校准表逻辑一致。
    """
    df = pd.DataFrame({"prob": y_prob, "actual": y_true})
    df["bucket"] = pd.cut(df["prob"], bins=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    cal = df.groupby("bucket", observed=True).agg(
        count=("actual", "count"),
        actual_rate=("actual", "mean")
    ).dropna()
    print("\n概率校准表（模型输出 → 真实胜率）:")
    print(f"  {'概率区间':<15} {'样本':>6}  {'真实胜率':>8}  {'vs 断板率':>8}")
    baseline = 1 - y_true.mean()  # 断板率基准
    for bucket, row in cal.iterrows():
        if row["count"] < 5:
            continue
        vs_baseline = row["actual_rate"] - (1 - baseline)
        print(f"  {str(bucket):<15} {row['count']:>6.0f}  {row['actual_rate']:>8.1%}  {vs_baseline:>+8.1%}")
    return cal


# ===================== 主流程 =====================
def main():
    print("=" * 50)
    print("  A股超短 XGBoost 模型训练")
    print("=" * 50)

    # 1. 加载
    df = load_data()

    # 2. 特征工程
    data, feature_cols = engineer_features(df)
    print(f"[特征] {feature_cols}")

    # 3. 时序分割（不用随机分割，保持时间顺序）
    split_date = "2025-01-01"
    train_df = data[data["date"] < split_date]
    test_df = data[data["date"] >= split_date]

    X_train = train_df[feature_cols].values
    y_train = train_df["target"].values
    X_test = test_df[feature_cols].values
    y_test = test_df["target"].values

    print(f"\n[分割] 训练集: {len(X_train)} 样本 ({train_df['date'].min()} ~ {train_df['date'].max()})")
    print(f"       测试集: {len(X_test)} 样本 ({test_df['date'].min()} ~ {test_df['date'].max()})")
    print(f"       训练集正例率: {y_train.mean():.1%}（晋级/续涨比例）")
    print(f"       测试集正例率: {y_test.mean():.1%}")

    # 4. 训练
    print("\n[训练] XGBoost...")
    model, y_pred, y_prob = train_xgboost(X_train, y_train, X_test, y_test)

    # 5. 评估
    metrics = evaluate(y_test, y_pred, y_prob)

    # 6. 特征重要性
    print_feature_importance(model, feature_cols)

    # 7. 概率校准
    cal = calibrate_prob(y_test, y_prob)

    # 8. 保存模型
    model_path = os.path.join(MODEL_DIR, "xgb_model.pkl")
    metadata = {
        "model_path": model_path,
        "feature_cols": feature_cols,
        "train_date_range": f"{train_df['date'].min()} ~ {train_df['date'].max()}",
        "test_date_range": f"{test_df['date'].min()} ~ {test_df['date'].max()}",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "metrics": {k: v if not isinstance(v, np.ndarray) else v.tolist()
                    for k, v in metrics.items()},
    }
    meta_path = os.path.join(MODEL_DIR, "model_metadata.json")

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\n[保存] 模型: {model_path}")
    print(f"       元数据: {meta_path}")

    # 9. 对比规则模型 vs XGBoost
    print("\n" + "=" * 50)
    print("  规则模型 vs XGBoost 对比")
    print("=" * 50)
    rule_jb = test_df["jb_prob"].values / 100.0  # jb_prob 是百分数
    rule_pred = (rule_jb >= 0.20).astype(int)   # 阈值20%
    rule_auc = roc_auc_score(y_test, rule_jb)
    xgb_auc = metrics["auc"]

    print(f"  规则模型 AUC: {rule_auc:.3f}")
    print(f"  XGBoost  AUC: {xgb_auc:.3f}")
    print(f"  提升: {(xgb_auc - rule_auc):.3f}")

    return model, metadata


if __name__ == "__main__":
    main()
