# RPS Dashboard

一个本地运行的 RPS 相对强弱研究台，用来每天更新数据、计算强弱排名，并在浏览器里查看美股、Crypto 和宏观大类资产的强弱状态。

这个项目不是交易系统，也不自动下单。它的定位是：先帮你从大池子里找出值得研究的强势标的，再辅助观察宏观资金偏好。

## 功能

- 美股 RPS：基于本地日线，计算 `RPS 30 / 50 / 120 / 250`，输出 Signals 和 Watchlist。
- Crypto RPS：基于 Binance 4H K 线，计算 `RPS 30 / 90 / 180`。
- Macro RPS：对比 `QQQ / SPY / IWM / GLD / DBC / TLT / HYG / UUP / EEM / BTCUSDT` 的大类资产强弱，计算 `RPS 20 / 60 / 120`。
- 口袋支点：美股和 Crypto 表格里保留 pocket pivot、volume signature、strong trend 等字段。
- 日期选择：可以查看历史已生成报告，不需要重新计算。
- 筛选：支持 Tier 多选、RPS 阈值、strong trend、core watchlist 和搜索。
- 数据健康：页面显示本地 CSV 数量、可排名数量、短历史数量和最新日期。
- 前端刷新按钮：数据回补和排名计算拆开，避免每次都重拉历史数据。

## 数据来源

- 美股：Polygon，默认使用 grouped daily 接口批量补全全市场普通股日线。
- Crypto：Binance 现货 USDT 交易对，默认保留 4H。
- Macro：ETF 使用 Polygon 日线，`BTCUSDT` 使用 Binance 日线。

本地数据默认写入：

```text
data/rps_pp/
```

报告默认写入：

```text
reports/rps_pp/YYYY-MM-DD/
```

这两个目录包含行情数据和生成结果，默认不会提交到 git。

## 安装

建议使用 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

配置 Polygon API key。默认脚本会读取：

```text
docs/strategies/.env
```

内容格式：

```bash
polygon_key=你的_polygon_api_key
```

也可以在运行脚本时用 `--env-file` 指定其他路径。

## 启动 Dashboard

```bash
python tools/rps_dashboard_server.py
```

然后打开：

```text
http://127.0.0.1:8765/
```

Dashboard 会用一个能 `import pandas` 的 Python 解释器来运行后台刷新任务。默认探测顺序包括项目 `.venv`、当前 Python、系统 Python 和 Codex bundled Python。也可以手动指定：

```bash
python tools/rps_dashboard_server.py --runner-python /path/to/python
```

或使用环境变量：

```bash
RPS_RUNNER_PYTHON=/path/to/python python tools/rps_dashboard_server.py
```

页面里的按钮含义：

- `补美股数据`：只补美股本地缺失日期，不重新计算排名。
- `补 Crypto 数据`：只补 Crypto 4H 缺失数据。
- `补宏观数据`：只补宏观资产日线。
- `补缺失 Symbol`：维护动作，补美股 universe 里缺 CSV 或历史不足 250 根的 symbol。
- `算美股排名`：只用本地美股数据重算 Signals / Watchlist。
- `算 Crypto 排名`：只用本地 Crypto 数据重算。
- `算宏观排名`：只用本地宏观数据重算 Macro RPS。
- `更新研究面板`：只在点击后生成行业/主题强度、领导股监控和宏观环境面板；不更新行情，不重算 RPS。

研究面板说明：

- 行业/主题强度：默认统计美股 `A+B+Core Watchlist`，输出每个主题的数量、A/B 数量、平均/中位 RPS 和代表标的。
- 领导股监控：自动列出现任领导股，并从历史报告和手动名单里追踪老领导股是否黄灯或破位。
- 宏观环境：基于本地 Macro RPS 判断风险偏好、风险警戒等状态。

可选本地配置：

- `data/rps_pp/metadata/us_theme_overrides.csv`：手动维护 `symbol,theme`，用于修正投资主题分类。
- `data/rps_pp/metadata/us_manual_leaders.csv`：手动维护 `symbol,note`，用于加入老领导股观察名单。

## 命令行用法

美股增量补数据：

```bash
python tools/rps_daily_runner.py \
  --markets us \
  --operation backfill \
  --us-universe polygon-common \
  --us-provider polygon-grouped
```

美股本地重算排名：

```bash
python tools/rps_daily_runner.py \
  --markets us \
  --operation scan-only
```

Crypto 4H 补数据：

```bash
python tools/rps_daily_runner.py \
  --markets crypto \
  --operation backfill \
  --crypto-timeframes 4h \
  --crypto-limit 200
```

宏观资产补数据并计算：

```bash
python tools/rps_daily_runner.py \
  --markets macro \
  --operation update-and-scan
```

同时更新并计算多个市场：

```bash
python tools/rps_daily_runner.py \
  --markets us,crypto,macro \
  --operation update-and-scan \
  --crypto-timeframes 4h \
  --us-universe polygon-common \
  --us-provider polygon-grouped
```

## 宏观 RPS 的处理逻辑

宏观池混合了 ETF 和 BTC。BTC 周末交易，ETF 周末不交易，所以 Macro RPS 会先对齐到所有宏观资产都有数据的最新共同交易日，再计算 RPS。这样不会出现 BTC 因为周末独有数据而单独排名的问题。

当前宏观池：

| Symbol | 含义 |
|---|---|
| QQQ | 纳指 100 / 科技成长 |
| SPY | 标普 500 / 美股大盘 |
| IWM | Russell 2000 / 小盘股 |
| GLD | 黄金 |
| DBC | 广义商品 |
| TLT | 20 年以上美国国债 |
| HYG | 高收益债 |
| UUP | 美元指数多头 |
| EEM | 新兴市场 |
| BTCUSDT | 比特币 |

## 测试

```bash
python -m unittest discover -s test -p 'test_*.py'
python -m py_compile tools/rps_daily_runner.py tools/rps_dashboard_server.py tools/rps_pocket_pivot_scanner.py
```

## 注意

- `.env`、本地行情数据和报告 CSV 不会提交。
- RPS 只是相对强弱观察工具，不等于买入建议。
- Signals / Watchlist 是研究入口，不是完整交易策略；仓位、止损、退出和执行规则需要单独设计。
