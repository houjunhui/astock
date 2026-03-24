#!/bin/bash
# A股每日复盘数据抓取脚本

DATA_DIR="/home/gem/workspace/agent/workspace/data/astock"
mkdir -p "$DATA_DIR"

# 腾讯财经接口 - 主要指数
curl -s "https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_sh000016,s_sh000300" \
  -H "Referer: https://finance.qq.com" | iconv -f gbk -t utf8

echo "---INDEX_END---"

# 东财涨停股列表 (沪市)
curl -s "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:0+t:14,m:1+t:2,m:1+t:23&fields=f2,f3,f4,f8,f9,f12,f14,f15,f16,f17,f18&_=$(date +%s)000" \
  -H "Referer: https://data.eastmoney.com" -H "User-Agent: Mozilla/5.0" 2>/dev/null | head -c 5000

echo "---ZT_END---"
