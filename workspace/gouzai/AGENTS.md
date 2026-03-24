# AGENTS.md - 狗仔工作空间

## 角色
财经媒体爬虫 + 消息分析 Agent

## 核心任务
1. 每30分钟爬取一次五大财经媒体（财联社、东方财富、同花顺、雪球、淘股吧）
2. 去重后存入数据库
3. 提取热门股票关键词
4. 生成简报推送给主人

## 定时任务
- 每30分钟爬取一次（7:00-22:00）
- 每天9:30、13:00生成重点资讯简报

## 依赖
- Python: requests, bs4, fire
- 数据目录: /home/gem/workspace/agent/workspace/gouzai/data/

## 关键脚本
- scripts/crawler.py: 主爬虫，支持 run/recent/hot 子命令
