"""
A股超短每日预测记录Schema
==============================
每日记录格式: data/astock/daily/{YYYYMMDD}_prediction.json
预测结果格式: data/astock/daily/{YYYYMMDD}_outcome.json
模型参数: data/astock/model/params.json
预测准确率: data/astock/model/accuracy.json
"""

PREDICTION_SCHEMA = {
    "date": "YYYYMMDD 预测日期",
    "model_version": "模型版本号",
    "market_sentiment": {
        "zt_count": "涨停股总数",
        "lianban_max": "最高连板数",
        "phase": "情绪周期阶段: bingdian/qidong/fajiao/tuichao"
    },
    "stocks": [
        {
            "code": "股票代码",
            "name": "股票名称",
            "lb": "当前连板数",
            "industry": "所属行业",
            "technicals": {
                "trend": "趋势: 上升通道/下降通道/底部金叉/顶部死叉/震荡",
                "rsi": "RSI(14)",
                "vr": "量比",
                "macd_state": "MACD状态: 多头/空头/中性",
                "price_vs_ma20": "价格vsMA20: MA20上方/MA20下方",
                "vol_status": "量能状态: 缩量/放量/正常量/爆量"
            },
            "prediction": {
                "jb_prob": "晋级概率(%)",
                "dz_prob": "断板概率(%)",
                "distribution": {3: 68.6, 4: 21.4, 5: 10.0},  # {板数: 概率%}
                "signal": "主要信号描述",
                "base_prob": "基准晋级率",
                "adj_factor": "调整系数"
            },
            "outcome": None,   # 晋级/duanban/zhaban, 待明晚填入
            "actual_boards": None,  # 实际达到的板数
            "verdict": None    # correct/wrong/partial
        }
    ],
    "summary": {
        "total": "预测股票总数",
        "correct": "完全正确数",
        "partial": "部分正确数",
        "wrong": "错误数",
        "accuracy": "准确率(%)"
    }
}
