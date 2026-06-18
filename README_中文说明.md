# China Telehealth Policy Collector v1.1

这个文件夹用于从中国官方政府网站的政策库和政策列表页收集卫生健康、远程医疗、互联网医疗、数字健康和智慧医疗相关政策文件。v1.1 只做采集、正文抽取、元数据整理、相关性分级和导出，不做政策编码或 LLM 分析。

## 文件说明

- `collect_links.py`：主脚本。
- `keyword_rules.example.json`：政策库入口、目标域名、关键词、主题规则相关配置。
- `requirements.txt`：依赖包。
- `README_中文说明.md`：本说明文件。

## 安装依赖

```bash
cd /Users/harrison/Desktop/keyword_link_collector
python3 -m pip install --user -r requirements.txt
```

如果本机 Python 证书有问题：

```bash
python3 -m pip install --user --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
```

安装 Playwright 浏览器：

```bash
python3 -m playwright install chromium
```

如果网络慢导致浏览器下载失败，脚本仍可运行，但政策列表页会回退到 `requests`，部分动态列表可能抓不到。日志中会出现 `PLAYWRIGHT_UNAVAILABLE`。

## 当前版本的采集方式

本版本不使用 Bing、Google 或其他商业搜索引擎。脚本通过 `crawl_policy_repository()` 直接访问官方政策库和政策列表页，提取列表中的标题、日期和链接，再进入正文页面抓取全文。

当前配置优先爬取：

- 中国政府网政策文件库：`https://www.gov.cn/zhengce/zhengceku/`
- 国家卫生健康委规范性文件：`https://www.nhc.gov.cn/wjw/gfxwj/list.shtml`
- 国家医保局政策法规：`https://www.nhsa.gov.cn/col/col104/index.html`
- 工业和信息化部政策文件：`https://www.miit.gov.cn/zwgk/zcwj/index.html`
- 国家中医药管理局政策文件：`https://www.satcm.gov.cn/zhengcewenjian/`
- 全国 31 个省级行政区的省级政府入口。
- 全国 31 个省级行政区的省级卫健委入口。
- 全国 31 个省级行政区的省级医保局入口。
- 全国 31 个省级行政区的省级中医药局入口；少数地区中医药管理入口与卫健委合署或共用站点时，脚本会使用卫健委站点作为入口，并在 `agency_hint` 中保留中医药管理局提示。

同时支持 `seed_urls`，用于直接抓取已知核心政策链接。

`keyword_rules.example.json` 里保留了主要国家入口和少量历史入口；脚本运行时会自动合并内置的全国省级入口，不需要手工维护几百行 URL。

## 运行方式

小规模测试：

```bash
python3 collect_links.py \
  --rules keyword_rules.example.json \
  --out-dir outputs_test \
  --max-repository-pages 1 \
  --max-candidates 5 \
  --delay 0.5
```

正式运行：

```bash
python3 collect_links.py \
  --rules keyword_rules.example.json \
  --out-dir outputs \
  --max-repository-pages 500 \
  --max-candidates 3000 \
  --delay 1
```

说明：

- `--max-repository-pages` 控制最多访问多少个政策列表页或栏目页。
- `--max-candidates` 控制最多进入多少个候选政策正文页。
- `--delay` 控制访问间隔，建议保留，避免对政府网站造成压力。
- `--no-playwright` 可关闭动态浏览器，强制只用 `requests`。

## 输出结构

```text
outputs/
├── policies.csv
├── candidates_diagnostics.csv
├── raw_html/
├── raw_pdf/
├── extracted_text/
└── logs/
```

`policies.csv` 只包含最终保留的政策正文。v1.1 的正式保留逻辑是：先爬取候选网页并抽取全文，再做二次筛选；最终只保留官方域名范围内、发布日期在 2018-2025 年、且至少达到 low_relevance 的记录。

`candidates_diagnostics.csv` 包含所有发现到的链接及失败原因，用于调试栏目页误抓、坏状态、空文本和过滤问题。

`policies.csv` 每条政策记录包含：

- `policy_title`
- `publication_date`
- `issuing_agency`
- `policy_level`
- `document_type`
- `policy_theme`
- `relevance_level`
- `matched_keywords`
- `source_url`
- `domain`
- `short_summary`
- `content`
- `file_type`
- `retrieval_date`
- `status_category`
- `page_type`
- `redirected_url`
- `text_length`
- `text_path`
- `raw_path`
- `repository_source`
- `discovery_path`
- `fetch_status`

## HTML 和 PDF 处理

HTML 正文抽取顺序：

1. `trafilatura`
2. 常见政府网站正文选择器，例如 `.TRS_Editor`、`#zoom`、`#UCAP-CONTENT`
3. BeautifulSoup fallback

PDF 正文抽取：

- 使用 `PyMuPDF`
- 保存原始 PDF 到 `raw_pdf/`
- 保存抽取全文到 `extracted_text/`

## 正式域名范围

正式采集时只保留以下来源范围：

- `gov.cn`
- `nhc.gov.cn`
- `nhsa.gov.cn`
- `miit.gov.cn`
- `satcm.gov.cn`
- 各地 `gov.cn` 政府站点
- 各地 `wjw` / `wsjkw` / `wsjk` 卫健委站点
- 各地 `ybj` / `ylbzj` / `nhsa` / `hsa` 医保局站点
- 各地中医药管理局站点

脚本会把内置省级入口的域名自动加入 `target_domains`。对于抓取中发现的外部商业站、新闻站、企业站、软件下载站等，即使页面文字命中关键词，也不会进入候选抓取。

## 二次筛选策略

v1.1 的策略是先采集候选全文，再二次筛选。最终保留条件包括：

- 发布日期必须识别出来，且年份在 2018-2025 之间。
- 不能命中明显无关噪声，例如招聘、采购公告、广告、软件下载、远程桌面等。
- 必须命中 `relevance_level` 三档之一。

`candidates_diagnostics.csv` 会保留被过滤的候选记录和原因，例如：

- `missing_publication_date`
- `year_out_of_scope`
- `exclude_terms`
- `no_relevance_signal`

## 相关性分级

脚本新增 `relevance_level` 字段，不再只依赖 `policy_theme`。自动分三级：

- `high_relevance`：命中互联网医院、互联网诊疗、远程医疗、数字健康、智慧医院、电子健康档案。
- `medium_relevance`：命中医疗信息化、健康信息化、医保电子凭证、医联体、分级诊疗。
- `low_relevance`：仅命中医疗、卫生、医保、健康服务。

## 主题识别

`policy_theme` 仍保留，用于描述主题，可多主题并列：

- 互联网医院
- 互联网诊疗
- 远程医疗
- 全民健康信息化
- 电子健康档案
- 医保支付
- 智慧医院
- 分级诊疗
- 医联体
- 医共体
- 数字健康
- 移动健康

如果未命中上述主题，但仍属于卫生健康领域，会标记为 `卫生健康政策`。

## 注意

部分政府网站会对脚本请求返回 `403` 或 `412`，或者使用前端异步加载列表。脚本会把这些情况写入 `outputs/logs/collector.log`，并继续抓取其他政策库或 `seed_urls`，不会因为一个页面失败而停止。

日志开头会输出诊断摘要：

```text
TOTAL_REPOSITORY_PAGES_VISITED
TOTAL_REPOSITORY_LINKS_FOUND
TOTAL_CANDIDATE_LINKS
TOTAL_FETCHED
TOTAL_FILTERED
TOTAL_EMPTY_TEXT
TOTAL_FETCH_ERRORS
FILTER_REASONS
TOP_DOMAINS
TOP_AGENCIES
TOP_THEMES
TOP_RELEVANCE_LEVELS
```

如果 `TOTAL_REPOSITORY_LINKS_FOUND` 很低，优先检查分页或动态页面问题；如果 `TOTAL_REPOSITORY_LINKS_FOUND` 很高但 `TOTAL_FILTERED` 很高，则优先检查过滤规则。每条过滤记录会写出 `FILTERED_REASON`，包括 URL、标题、`CORE_HITS`、`HEALTH_HITS` 和 `EXCLUDE_HITS`。

##

本研究没有使用 LLM 或商业搜索引擎随机收集材料，而是先由研究者确定官方政策来源和可复核的关键词范围。Python 脚本直接访问中国政府网、国家卫生健康委、国家医保局、工业和信息化部、国家中医药管理局，以及全国省级政府、卫健委、医保局和中医药局入口，提取政策标题、发布日期和链接，再进入正文页抓取 HTML 或 PDF 全文。脚本自动抽取政策标题、发布日期、发布机构、政策层级、文件类型、主题和相关性等级，并导出 `policies.csv` 与 `candidates_diagnostics.csv`。正式纳入范围限定为 2018-2025 年、官方域名、且至少达到 low_relevance 的记录；最终纳入仍由研究者人工核验。
