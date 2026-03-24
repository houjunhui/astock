"""
ML晋级概率模型 v1（数据积累后训练）

特征工程（可解释特征，避免过拟合）:
- 情绪特征: 竞价涨幅, 竞价金额, 封板率, 量比VR
- 技术特征: RSI, MACD, KDJ
- 板块特征: 板块竞价涨幅, 板块涨停家数
- 基本面特征: 流通市值, 换手率

模型: LightGBM 增量学习
训练数据: 2年+历史情绪周期数据（待积累）
验证: 样本外IC值≥0.05才能上线
"""

import os
import json
import numpy as np
from pathlib import Path
from datetime import datetime, date

MODEL_DIR = Path("/home/gem/workspace/agent/workspace/astock/pools")
MODEL_FILE = MODEL_DIR / "晋级模型_v1.json"
TRAINING_DATA_FILE = MODEL_DIR / "training_data.json"

# ── 特征定义 ───────────────────────────────────────────────────
FEATURE_COLS = [
    "auction_chg",      # 竞价涨幅(%)
    "auction_amount",   # 竞价金额(元)
    "limit_up_suc_rate",# 封板率
    "vr",               # 量比
    "turnover",         # 换手率
    "lb",               # 连板数
    "phase_temp",       # 市场温度
    "sector_chg",       # 板块竞价涨幅
    "rsi",              # RSI
]

LABEL_COL = "next_up"  # 次日是否晋级（二分类）


def extract_features(stock, market_data, date_str):
    """
    从股票数据和市场数据中提取ML特征
    stock: 个股ladder数据
    market_data: 市场阶段/温度数据
    """
    features = {}
    
    # 情绪特征
    features["auction_chg"] = stock.get("auction_chg", 0)
    features["auction_amount"] = stock.get("auction_amount", 0) / 1e8  # 缩放到亿
    features["limit_up_suc_rate"] = stock.get("limit_up_suc_rate", 0.5)
    features["vr"] = stock.get("vr", 1.0)
    features["turnover"] = stock.get("turnover", 0)
    features["lb"] = stock.get("lb", 1)
    
    # 市场特征
    phase, temp = market_data
    features["phase_temp"] = temp
    features["phase_encoded"] = {"主升": 4, "发酵": 3, "分歧": 2, "退潮": 1, "冰点": 0}.get(phase, 2)
    
    # 板块特征
    features["sector_chg"] = stock.get("sector_chg", 0)
    
    # 技术特征（RSI来自ladder或kline，暂用默认值）
    features["rsi"] = stock.get("rsi", 50)
    
    return features


def compute_晋级概率(stock, market_data):
    """
    计算晋级概率（简化版，无真实模型时用逻辑回归近似）
    
    真实ML模型训练好后，替换此函数
    目前用基于规则的近似评分
    """
    # 基于特征的加权评分（模拟ML输出）
    score = 0.0
    
    # 竞价涨幅: 0-5%最佳
    chg = stock.get("auction_chg", 0)
    if 0 <= chg <= 3:
        score += 0.3 * (chg / 3)
    elif 3 < chg <= 5:
        score += 0.3
    elif 5 < chg <= 7:
        score += 0.3 * (1 - (chg - 5) / 2)
    else:
        score += 0
    
    # 封板率
    seal = stock.get("limit_up_suc_rate", 0.5)
    score += seal * 0.25
    
    # VR量比
    vr = stock.get("vr", 1.0)
    score += min(vr / 5, 1) * 0.15
    
    # 连板加成
    lb = stock.get("lb", 1)
    score += min(lb * 0.05, 0.15)
    
    # 市场温度
    phase, temp = market_data
    phase_boost = {"主升": 0.15, "发酵": 0.10, "分歧": 0.05, "退潮": 0, "冰点": -0.1}.get(phase, 0)
    score += phase_boost
    
    # 换手率
    turnover = stock.get("turnover", 0)
    score += min(turnover / 5, 1) * 0.10
    
    return max(0, min(score, 1))


def add_training_sample(stock, market_data, label, date_str):
    """
    每日收盘后追加一条训练样本
    label: 1=次日晋级, 0=未晋级
    """
    features = extract_features(stock, market_data, date_str)
    features[LABEL_COL] = label
    features["date"] = date_str
    
    data = []
    if TRAINING_DATA_FILE.exists():
        with open(TRAINING_DATA_FILE) as f:
            data = json.load(f)
    
    data.append(features)
    
    with open(TRAINING_DATA_FILE, "w") as f:
        json.dump(data[-1000:], f, ensure_ascii=False)  # 保留最近1000条


def get_training_stats():
    """获取训练数据统计"""
    if not TRAINING_DATA_FILE.exists():
        return {"count": 0, "message": "暂无训练数据"}
    
    with open(TRAINING_DATA_FILE) as f:
        data = json.load(f)
    
    if not data:
        return {"count": 0}
    
    labels = [d.get(LABEL_COL, 0) for d in data]
    return {
        "count": len(data),
        "positive": sum(labels),
        "negative": len(labels) - sum(labels),
        "positive_rate": round(sum(labels) / len(labels) * 100, 1),
        "oldest": min(d.get("date", "") for d in data),
        "newest": max(d.get("date", "") for d in data),
    }


if __name__ == "__main__":
    # 训练数据统计
    stats = get_training_stats()
    print(f"【ML模型训练数据】")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    
    if stats["count"] >= 500:
        print(f"\n✅ 数据量{stats['count']}已足够，可开始训练模型")
    else:
        print(f"\n⏳ 当前{stats['count']}条，还需{500 - stats['count']}条才能训练")
