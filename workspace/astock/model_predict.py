"""
astock.model_predict
XGBoost 模型推理接口

predict(code, lb, kl, ...) → ml_prob
返回模型预测的晋级/续涨概率，与规则模型融合
"""
import os, pickle, json
import numpy as np

MODEL_DIR = "/home/gem/workspace/agent/workspace/data/astock/model"
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_model.pkl")
META_PATH = os.path.join(MODEL_DIR, "model_metadata.json")

_model = None
_meta = None


def _load_model():
    global _model, _meta
    if _model is None:
        if os.path.exists(MODEL_PATH):
            with open(MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
        if os.path.exists(META_PATH):
            with open(META_PATH) as f:
                _meta = json.load(f)
    return _model, _meta


def predict_ml(code, lb, kl):
    """
    用 XGBoost 模型预测晋级/续涨概率。

    参数：
        code: 股票代码
        lb: 连板数
        kl: K线特征字典（rsi, vr, trend, price_vs_ma20, macd_state）

    返回：
        float: 晋级/续涨概率（0-1），失败返回 None
    """
    model, meta = _load_model()
    if model is None:
        return None

    try:
        # 特征工程（与训练时一致）
        rsi = kl.get("rsi")
        vr = kl.get("vr")
        jb_prob = kl.get("jb_prob", 0.10)  # 规则模型输出

        if rsi is None or vr is None:
            return None

        trend_up = 1 if kl.get("trend") == "上升通道" else 0
        above_ma20 = 1 if kl.get("price_vs_ma20") == "MA20上方" else 0
        log_vr = np.log1p(vr)
        rsi_high = 1 if rsi > 75 else 0
        rsi_low = 1 if rsi < 40 else 0
        lb_high = 1 if lb >= 4 else 0
        lb_mid = 1 if 2 <= lb < 4 else 0

        features = np.array([[
            rsi, log_vr, lb,
            trend_up, above_ma20,
            jb_prob,
            rsi_high, rsi_low,
            lb_high, lb_mid,
        ]])
        prob = model.predict_proba(features)[0, 1]
        return round(float(prob), 4)
    except Exception:
        return None


def ml_confidence_label(prob):
    """
    将模型概率转为置信度标签。
    """
    if prob is None:
        return "无模型"
    if prob >= 0.85:
        return "🟢高置信"
    if prob >= 0.65:
        return "🟡中等置信"
    if prob >= 0.50:
        return "🔴低置信"
    return "⚫回避"


def get_feature_importance():
    """返回特征重要性（供分析用）"""
    model, meta = _load_model()
    if model is None:
        return None
    cols = meta.get("feature_cols", [])
    imp = model.feature_importances_
    return dict(zip(cols, [float(v) for v in imp]))
