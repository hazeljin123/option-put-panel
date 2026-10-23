# 期权 · 卖Put / 卖Call 持仓集中价位面板

一个**每日自动更新**的公开面板，覆盖 **CBOT 四大农产品**（大豆 ZS / 豆粕 ZM / 玉米 ZC / 豆油 ZL）
与 **国内八品种商品期权**（豆粕、豆油、棕榈油、菜油、菜粕、生猪、鸡蛋、豆二）。

**在线地址：https://hazeljin123.github.io/option-put-panel/**

---

## 两个板块的数据强度不同（务必区分）

### 板块一：国内商品期权 —— 真实逐档持仓量 ✅

| 列 | 含义 | 数据性质 |
|---|---|---|
| 标的期货价 | 交易所结算价 / 东财收盘价 | ✅ **真实** |
| 卖Put集中价位 | 价格下方 δ∈[0.10,0.25] 区间内 **OI 最大的行权价** | ✅ **真实持仓量** |
| 卖Call集中价位 | 价格上方 δ∈[0.10,0.25] 区间内 **OI 最大的行权价** | ✅ **真实持仓量** |

数据源（全部免费公开）：
- **郑商所官方每日行情表** `OptionDataDaily.txt` —— 自带 **持仓量 + DELTA + 隐含波动率**（交易所口径，无需自行推算）
- **东方财富 futsseapi** —— 大商所全品种逐档持仓量、收盘价、成交量（单页上限 6000，需分页拉全量）

⚠️ 持仓量是**总持仓**（多空合计），交易所免费数据**不区分买卖方向**。
所以"卖Put集中"是基于「虚值 + 高持仓」的**概率推断**，不是交易所披露的事实。

### 板块二：CBOT 农产品 —— 真实价 + 理论推算带 🟡

| 列 | 含义 | 数据性质 |
|---|---|---|
| 支撑线 · 真实期货价 | 新浪财经连续主力真实报价 | ✅ **真实** |
| 卖Put上沿（价格下方 Δ≈0.25） | 最靠近平值的 OTM Put 收租边界 | 🟡 **推算** |
| 卖Call下沿（价格上方 Δ≈0.25） | 最靠近平值的 OTM Call 收租边界 | 🟡 **推算** |

CBOT 板块的价位带是「真实价 + 假设IV 经 Black-76 推算」的**理论虚值带**，
**不含真实 OI**（真实 OI 需 QuikStrike/CME 逐档导出，免费源给不到买卖方向）。

---

## 方法学与可信度校验

| 环节 | 做法 | 校验结果 |
|---|---|---|
| 到期日 | 按交易所**官方规则**推算：DCE = 交割月前1月第12个交易日；CZCE = 前1月第15日历日前(含)倒数第3个交易日 | 与郑商所官方 DELTA 反解交叉验证，两者均指向 2026-12-11 ✅ |
| IV / Delta | Black-76；郑商所直接用交易所官方 IV/DELTA，大商所由真实收盘价反解 | 对郑商所官方 DELTA 回归 **RMSE = 0.0006（0.06%）** ✅ |
| 主力合约 | 按**期权持仓量**排名（而非期货持仓量） | 豆二 b2611 期货持仓最高但期权已交割清零，按期货排会选错月份 ✅ |
| 数据日期 | 郑商所当日文件约 **15:42** 上传；未发布则自动回溯最近可用交易日，**缓存按实际数据日期命名** | 杜绝「T-1 数据挂成今日」的假更新 ✅ |
| 发布前 | 机器校验：8 品种 / delta 非空 / OI>0 / expiry 格式 / 无未替换占位符 / index.html > 4000 字节 | 任一不过 → 拒绝推送，线上保留上次内容 ✅ |

---

## 目录结构

```
仓库根/                    <- GitHub Pages 发布源（legacy 模式 source=main, path=/）
  index.html               统一入口（CBOT + 国内）
  cn_panel.html            国内商品期权独立面板
  real_band.html           CBOT 三列理论带
  data/                    每日 JSON / Markdown 产物（历史留档）
  archive/                 每日 HTML 快照
  .nojekyll                禁用 Jekyll（否则 data/*.md 会被当页面处理致构建失败）
src/                       源码
  daily.py                 每日编排入口
  cn_fetch.py              真实数据抓取（郑商所官方 + 东财）
  cn_engine.py             Black-76 定价 / 集中价位识别
  cn_expiry.py             交易所到期日规则
  cn_panel.py              国内面板渲染
  real_band.py             CBOT 理论带渲染
  build_index.py           统一入口渲染
  fetch_sina.py            新浪期货价抓取
  publish_github.py        校验 → 布置产物 → commit → push
```

---

## 自动更新机制

**每日 16:10 北京时间**由 WorkBuddy 自动化任务执行 `src/daily.py`，流程：

```
real_band.py  →  cn_panel.py  →  fetch_barchart.py(best-effort)
              →  build_index.py  →  publish_github.py
```

`publish_github.py` 把产物拷到仓库根并 `git commit && git push` 到 main 分支，
**GitHub Pages 内置发布器**自动重建线上站点。

> **为什么用 Pages 内置发布器而非自定义 Actions 工作流？**
> 本机 GitHub 令牌的 OAuth scope 只有 `repo`、没有 `workflow`，
> GitHub 会直接拒绝任何创建/修改 `.github/workflows/*.yml` 的请求（已实测确认）。
> legacy Pages 在 main 分支内容变化时会自动重建，正好满足需求，且零额外依赖。

---

## 本地运行

```bash
cd cbot_put_dashboard/src
python daily.py          # 全流程：生成 → 校验 → 推送 GitHub
```

单独运行各环节：

```bash
python real_band.py      # CBOT 三列理论带
python cn_panel.py       # 国内真实持仓集中度
python build_index.py    # 统一入口
python publish_github.py # 仅校验 + 推送
```

推送需要 GitHub 凭据（脚本从 `git credential manager` 读取，**不写进任何文件**）。

---

## 数据来源与限制

| 源 | 用途 | 限制 |
|---|---|---|
| 郑商所官方每日行情表 | 持仓量 / DELTA / 隐含波动率 / 结算价 | 当日文件约 15:42 上传 |
| 东方财富 futsseapi | 大商所逐档持仓量、收盘价、成交量 | 单页 6000 条上限，需分页 |
| 新浪财经 `hq.sinajs.cn` | CBOT 真实连续主力价 | https 握手不稳（已加多主机+重试） |
| Barchart 免费期权页 | CBOT 逐档总 OI / Delta / IV（延迟、无方向） | 反爬严格，多数网络被挡 |
| QuikStrike / CME 导出 | CBOT 真实 OI-by-strike（含方向） | 付费/需账号，本仓库不内置 |

---

## 许可证

数据均来自公开免费源，仅供研究学习，**不构成任何投资建议**。
