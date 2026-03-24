"""
astock.config
配置常量 - 所有阈值、路径、模型参数集中管理
"""
import os

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"
MODEL_DIR = f"{DATA_DIR}/model"
os.makedirs(MODEL_DIR, exist_ok=True)

DB_PATH = f"{MODEL_DIR}/astock.db"

# ===================== 预测模型参数 =====================
# 修正后的基准晋级概率（基于26228个历史样本的实际晋级率）
# 1板: 22.6% / 2板: 22.3% / 3板: 21.5%（3板模型最准）
# 原预测系统性低估约10~16pp，已整体上调
BASE_PROBS = {1: 0.22, 2: 0.22, 3: 0.20, 4: 0.12, 5: 0.35}

DEFAULT_COEF = {
    '上升通道': 1.4, '下降通道': 0.4, '底部金叉': 1.3, '顶部死叉': 0.5,
    'vr_<0.5': 1.5, 'vr_<0.8': 1.2, 'vr_<1.0': 1.0, 'vr_<1.5': 0.8,
    'vr_>=1.5': 0.5,
    'rsi_<40': 1.4, 'rsi_<50': 1.1, 'rsi_<60': 1.0, 'rsi_<70': 0.8,
    'rsi_>=70': 0.5,
    'macd多头': 1.2, 'macd空头': 0.5,
    'ma20上方': 1.1, 'ma20下方': 0.7,
    '极度缩量': 1.3, '温和放量': 1.0,
    '首板': 1.0, '2板': 1.1, '3板': 1.0, '4板': 0.7, '5板+': 0.4,
}

PHASE_BASE_DISCOUNT = {'退潮': 0.40, '冰点': 0.55, '启动': 0.85, '发酵': 1.00}

# ===================== 卖出规则 =====================
EXIT_RULES = {
    'max_hold_days': 3,
    'stop_loss_pct': -5.0,
    'take_profit_pct': 15.0,
    'force_sell_if_no_zt': True,
}

# ===================== 竞价条件 =====================
# 3板以上
AUCTION_HIGH_BOARD = {'ok_min': 1, 'ok_max': 6, 'vol': '缩量/平量', 'warn': '⚠️高板位：竞价>+6%砸盘，<1%情绪弱'}
# 晋级>20%，1板
AUCTION_STD = {'ok_min': 3, 'ok_max': 7, 'vol': '成交>昨日20%', 'warn': '✅标准条件'}
# 晋级<20%
AUCTION_LOW = {'ok_min': 4, 'ok_max': 7, 'vol': '明显放量', 'warn': '⚠️晋级概率偏低，需竞价强劲'}
# 板块≥3只联动
AUCTION_SECTOR_HOT = {'ok_min': 3, 'ok_max': 8, 'vol': '温和放量', 'warn': '✅联动板块，情绪支持'}

# ===================== 标定 =====================
CALIBRATION_MIN_SAMPLES = 8
CALIBRATION_BUCKETS = [0, 10, 20, 30, 40, 100]
