
## 教训：.env 文件值带双引号（2026-03-24）

**问题**：`.env` 文件内容为：
```
export LB_API_KEY="lb_c5d7beae8177a770..."
export LB_API_BASE="https://stock.quicktiny.cn/api/openclaw"
```
值被双引号包裹。直接 `split('=', 1)` 后得到 `v = '"https://..."'`，引号成为字符串一部分。
API Key 变成 `"lb_c5d7..."`（带引号），URL 变成 `"https://..."/endpoint`（多了一层引号）。

**症状**：`requests.get(url)` 报错 `InvalidSchema: No connection adapters were found for '"https://..."'`。

**修复**：读取 `.env` 后 `v = v.strip().strip('"').strip("'")`。

**预防**：所有 `.env` 解析统一加 strip 保护。
