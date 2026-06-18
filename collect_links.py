#!/usr/bin/env python3
"""China Telehealth Policy Collector v1.0.

Collect Chinese telehealth and digital health policy documents from official
government websites. Version 1 only retrieves, extracts, de-duplicates, and
organizes policy documents. It does not score, code, or run LLM analysis.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import datetime as dt
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit
import fitz  # PyMuPDF
import trafilatura
from urllib3.exceptions import InsecureRequestWarning

try:
    from playwright.sync_api import sync_playwright
except Exception:  # Playwright is optional at import time; requirements.txt lists it.
    sync_playwright = None


requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


NATIONAL_TARGET_DOMAINS = [
    "gov.cn",
    "nhc.gov.cn",
    "nhsa.gov.cn",
    "miit.gov.cn",
    "satcm.gov.cn",
]

NATIONAL_REPOSITORY_PAGES = [
    {
        "name": "gov.cn policy database",
        "url": "https://www.gov.cn/zhengce/zhengceku/",
        "agency_hint": "国务院",
        "policy_level_hint": "国家级",
        "pagination_first_url": "https://www.gov.cn/zhengce/zhengceku/index.htm",
        "pagination_pattern": "https://www.gov.cn/zhengce/zhengceku/index_{page}.htm",
        "pagination_start": 2,
        "pagination_end": 100,
    },
    {
        "name": "NHC normative documents",
        "url": "https://www.nhc.gov.cn/wjw/gfxwj/list.shtml",
        "agency_hint": "国家卫生健康委",
        "policy_level_hint": "国家级",
        "pagination_pattern": "https://www.nhc.gov.cn/wjw/gfxwj/list_{page}.shtml",
        "pagination_start": 2,
        "pagination_end": 80,
    },
    {
        "name": "NHSA policy regulations",
        "url": "https://www.nhsa.gov.cn/col/col104/index.html",
        "agency_hint": "国家医保局",
        "policy_level_hint": "国家级",
        "pagination_pattern": "https://www.nhsa.gov.cn/col/col104/index_{page}.html",
        "pagination_start": 2,
        "pagination_end": 80,
    },
    {
        "name": "MIIT policy documents",
        "url": "https://www.miit.gov.cn/zwgk/zcwj/index.html",
        "agency_hint": "工业和信息化部",
        "policy_level_hint": "国家级",
        "pagination_pattern": "https://www.miit.gov.cn/zwgk/zcwj/index_{page}.html",
        "pagination_start": 2,
        "pagination_end": 80,
    },
    {
        "name": "SATCM policy documents",
        "url": "https://www.satcm.gov.cn/zhengcewenjian/",
        "agency_hint": "国家中医药管理局",
        "policy_level_hint": "国家级",
        "pagination_first_url": "https://www.satcm.gov.cn/zhengcewenjian/index.html",
        "pagination_pattern": "https://www.satcm.gov.cn/zhengcewenjian/index_{page}.html",
        "pagination_start": 2,
        "pagination_end": 80,
    },
]

PROVINCIAL_ENTRY_POINTS = [
    ("北京", "北京市", "https://www.beijing.gov.cn/zhengce/", "https://wjw.beijing.gov.cn/", "https://ybj.beijing.gov.cn/", "https://zyj.beijing.gov.cn/"),
    ("天津", "天津市", "https://www.tj.gov.cn/zwgk/", "https://wsjk.tj.gov.cn/", "https://ylbz.tj.gov.cn/", "https://zyj.tj.gov.cn/"),
    ("河北", "河北省", "https://www.hebei.gov.cn/columns/20/", "https://wsjkw.hebei.gov.cn/", "https://ylbzj.hebei.gov.cn/", "https://zyj.hebei.gov.cn/"),
    ("山西", "山西省", "https://www.shanxi.gov.cn/zfxxgk/zfxxgkzl/fdzdgknr/lzyj/szfwj/", "https://wjw.shanxi.gov.cn/", "https://ybj.shanxi.gov.cn/", "https://sxtcm.shanxi.gov.cn/"),
    ("内蒙古", "内蒙古自治区", "https://www.nmg.gov.cn/zwgk/zfxxgk/fdzdgknr/zcwj/", "https://wjw.nmg.gov.cn/", "https://ylbzj.nmg.gov.cn/", "https://wjw.nmg.gov.cn/"),
    ("辽宁", "辽宁省", "https://www.ln.gov.cn/web/zwgkx/zfwj/", "https://wsjk.ln.gov.cn/", "https://ybj.ln.gov.cn/", "https://zyj.ln.gov.cn/"),
    ("吉林", "吉林省", "https://www.jl.gov.cn/zwgk/zc/", "https://wsjkw.jl.gov.cn/", "https://ybj.jl.gov.cn/", "https://jltcm.jl.gov.cn/"),
    ("黑龙江", "黑龙江省", "https://www.hlj.gov.cn/hlj/c108373/common_zfxxgk.shtml", "https://wsjkw.hlj.gov.cn/", "https://ybj.hlj.gov.cn/", "https://zyj.hlj.gov.cn/"),
    ("上海", "上海市", "https://www.shanghai.gov.cn/nw12344/index.html", "https://wsjkw.sh.gov.cn/", "https://ybj.sh.gov.cn/", "https://szyyj.sh.gov.cn/"),
    ("江苏", "江苏省", "https://www.jiangsu.gov.cn/col/col46143/index.html", "https://wjw.jiangsu.gov.cn/", "https://ybj.jiangsu.gov.cn/", "https://wjw.jiangsu.gov.cn/"),
    ("浙江", "浙江省", "https://www.zj.gov.cn/col/col1229019364/index.html", "https://wsjkw.zj.gov.cn/", "https://ybj.zj.gov.cn/", "https://zjtcm.zj.gov.cn/"),
    ("安徽", "安徽省", "https://www.ah.gov.cn/public/column/1681?type=4&action=list", "https://wjw.ah.gov.cn/", "https://ybj.ah.gov.cn/", "https://zyj.ah.gov.cn/"),
    ("福建", "福建省", "https://www.fujian.gov.cn/zwgk/zxwj/", "https://wjw.fujian.gov.cn/", "https://ybj.fujian.gov.cn/", "https://zyj.fujian.gov.cn/"),
    ("江西", "江西省", "https://www.jiangxi.gov.cn/col/col4969/index.html", "https://hc.jiangxi.gov.cn/", "https://ybj.jiangxi.gov.cn/", "https://zyj.jiangxi.gov.cn/"),
    ("山东", "山东省", "https://www.shandong.gov.cn/col/col107851/index.html", "https://wsjkw.shandong.gov.cn/", "https://ybj.shandong.gov.cn/", "https://zyj.shandong.gov.cn/"),
    ("河南", "河南省", "https://www.henan.gov.cn/zwgk/system/list/4.html", "https://wsjkw.henan.gov.cn/", "https://ylbz.henan.gov.cn/", "https://tcm.henan.gov.cn/"),
    ("湖北", "湖北省", "https://www.hubei.gov.cn/zfwj/", "https://wjw.hubei.gov.cn/", "https://ybj.hubei.gov.cn/", "https://zyj.hubei.gov.cn/"),
    ("湖南", "湖南省", "https://www.hunan.gov.cn/hnszf/xxgk/wjk/szfwj/", "https://wjw.hunan.gov.cn/", "https://ybj.hunan.gov.cn/", "https://tcm.hunan.gov.cn/"),
    ("广东", "广东省", "https://www.gd.gov.cn/zwgk/wjk/", "https://wsjkw.gd.gov.cn/", "https://hsa.gd.gov.cn/", "https://szyyj.gd.gov.cn/"),
    ("广西", "广西壮族自治区", "https://www.gxzf.gov.cn/zfwj/", "http://wsjkw.gxzf.gov.cn/", "http://ybj.gxzf.gov.cn/", "http://zyyj.gxzf.gov.cn/"),
    ("海南", "海南省", "https://www.hainan.gov.cn/hainan/5309/list3.shtml", "https://wst.hainan.gov.cn/", "https://ybj.hainan.gov.cn/", "https://wst.hainan.gov.cn/"),
    ("重庆", "重庆市", "https://www.cq.gov.cn/zwgk/zfxxgkml/szfwj/", "https://wsjkw.cq.gov.cn/", "https://ylbzj.cq.gov.cn/", "https://wsjkw.cq.gov.cn/"),
    ("四川", "四川省", "https://www.sc.gov.cn/10462/zfwjts/zfwj.shtml", "https://wsjkw.sc.gov.cn/", "https://ylbzj.sc.gov.cn/", "https://sctcm.sc.gov.cn/"),
    ("贵州", "贵州省", "https://www.guizhou.gov.cn/zwgk/zcwj/", "https://wjw.guizhou.gov.cn/", "https://ylbzj.guizhou.gov.cn/", "https://atcm.guizhou.gov.cn/"),
    ("云南", "云南省", "https://www.yn.gov.cn/zwgk/zcwj/", "https://ynswsjkw.yn.gov.cn/", "https://ylbz.yn.gov.cn/", "https://ynszyyglj.yn.gov.cn/"),
    ("西藏", "西藏自治区", "https://www.xizang.gov.cn/zwgk/xxfb/zbwj/", "https://wjw.xizang.gov.cn/", "https://ylbzj.xizang.gov.cn/", "https://wjw.xizang.gov.cn/"),
    ("陕西", "陕西省", "https://www.shaanxi.gov.cn/zfxxgk/fdzdgknr/zcwj/", "https://sxwjw.shaanxi.gov.cn/", "https://ybj.shaanxi.gov.cn/", "https://atcm.shaanxi.gov.cn/"),
    ("甘肃", "甘肃省", "https://www.gansu.gov.cn/gsszf/c100054/zfxxgk_zdgk.shtml", "https://wsjk.gansu.gov.cn/", "https://ylbz.gansu.gov.cn/", "https://gszyy.gansu.gov.cn/"),
    ("青海", "青海省", "https://www.qinghai.gov.cn/xxgk/xxgk/fd/zfwj/", "https://wsjkw.qinghai.gov.cn/", "https://ybj.qinghai.gov.cn/", "https://zyj.qinghai.gov.cn/"),
    ("宁夏", "宁夏回族自治区", "https://www.nx.gov.cn/zwgk/qzfwj/", "https://wsjkw.nx.gov.cn/", "https://ylbz.nx.gov.cn/", "https://wsjkw.nx.gov.cn/"),
    ("新疆", "新疆维吾尔自治区", "https://www.xinjiang.gov.cn/xinjiang/fgwjx/fgwj.shtml", "https://wjw.xinjiang.gov.cn/", "https://ylbzj.xinjiang.gov.cn/", "https://wjw.xinjiang.gov.cn/"),
]


def early_normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = re.sub(r"/+$", "", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def early_domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def build_repository_pages() -> List[Dict[str, Any]]:
    pages = list(NATIONAL_REPOSITORY_PAGES)
    seen_urls = {early_normalize_url(page["url"]) for page in pages}
    entry_types = [
        ("省级政府", 2),
        ("省级卫健委", 3),
        ("省级医保局", 4),
        ("省级中医药局", 5),
    ]
    for short_name, full_name, *urls in PROVINCIAL_ENTRY_POINTS:
        for label, index in entry_types:
            url = early_normalize_url(urls[index - 2])
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            agency_suffix = {
                "省级政府": "人民政府",
                "省级卫健委": "卫生健康委",
                "省级医保局": "医疗保障局",
                "省级中医药局": "中医药管理局",
            }[label]
            pages.append(
                {
                    "name": f"{full_name}-{label}",
                    "url": url,
                    "agency_hint": f"{full_name}{agency_suffix}",
                    "policy_level_hint": "省级",
                    "province_hint": short_name,
                    "entry_type": label,
                }
            )
    return pages


REPOSITORY_PAGES = build_repository_pages()
TARGET_DOMAINS = sorted(
    set(NATIONAL_TARGET_DOMAINS) | {early_domain_of(page["url"]) for page in REPOSITORY_PAGES if page.get("url")}
)

NATIONAL_AGENCIES = [
    "国务院",
    "国务院办公厅",
    "国家卫生健康委",
    "国家卫生健康委员会",
    "国家医保局",
    "国家医疗保障局",
    "工业和信息化部",
    "工信部",
    "国家中医药管理局",
    "教育部",
    "国家互联网信息办公室",
    "国家统计局",
]

PROVINCES = [
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
]

ARTICLE_SELECTORS = [
    ".TRS_Editor",
    "#zoom",
    "#UCAP-CONTENT",
    ".pages_content",
    ".article",
    ".article-content",
    ".article_con",
    ".content",
    ".content_main",
    ".main-content",
    ".Custom_UnionStyle",
    ".view",
    "article",
]

HEALTH_FIELD_TERMS = [
    "医疗",
    "卫生",
    "卫生健康",
    "医疗卫生",
    "健康中国",
    "人民健康",
    "健康服务",
    "健康管理",
    "医保",
    "医疗保障",
    "医院",
    "诊疗",
    "公共卫生",
    "中医药",
    "疾病",
    "慢性病",
    "基层卫生",
    "卫生健康",
]

RELEVANCE_KEYWORDS = {
    "high_relevance": [
        "互联网医院",
        "互联网诊疗",
        "远程医疗",
        "数字健康",
        "智慧医院",
        "电子健康档案",
    ],
    "medium_relevance": [
        "医疗信息化",
        "健康信息化",
        "医保电子凭证",
        "医联体",
        "分级诊疗",
    ],
    "low_relevance": [
        "医疗",
        "卫生",
        "医保",
        "健康服务",
    ],
}

POLICY_THEME_RULES = {
    "互联网医院": ["互联网医院"],
    "互联网诊疗": ["互联网诊疗", "在线诊疗", "在线复诊", "网络诊疗"],
    "远程医疗": ["远程医疗", "远程诊疗", "远程会诊", "远程监测"],
    "全民健康信息化": ["全民健康信息化", "健康信息化", "医疗信息化"],
    "电子健康档案": ["电子健康档案", "居民电子健康档案"],
    "医保支付": ["医保支付", "医疗服务价格", "线上医保支付", "医保电子凭证", "医保结算"],
    "智慧医院": ["智慧医院", "智慧医疗"],
    "分级诊疗": ["分级诊疗"],
    "医联体": ["医联体", "医疗联合体"],
    "医共体": ["医共体", "县域医共体"],
    "数字健康": ["数字健康", "数字医疗", "智慧健康", "数字卫生", "数字化健康服务"],
    "移动健康": ["移动医疗", "移动健康", "健康管理平台"],
}

POLICY_TITLE_CANDIDATE_TERMS = ["通知", "意见", "办法", "规范", "方案", "规划", "标准"]
POLICY_TITLE_SIGNALS = POLICY_TITLE_CANDIDATE_TERMS + ["关于", "印发", "发布", "公告"]
NAVIGATION_TITLES = {
    "法律法规",
    "政策法规",
    "通知公告",
    "政策解读",
    "征求意见",
    "政府信息公开",
    "首页",
    "更多",
    "更多>>",
    "查看更多",
    "机构职能",
    "内设机构",
    "公共服务",
}
BAD_STATUS_CODES = {404, 405, 412}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = re.sub(r"/+$", "", parsed.path or "/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        )
    )


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def domain_matches(domain: str, patterns: Iterable[str]) -> bool:
    domain = domain.lower().removeprefix("www.")
    for pattern in patterns:
        pattern = pattern.lower().strip().removeprefix("www.")
        if domain == pattern or domain.endswith("." + pattern):
            return True
    return False


def stable_name(url: str, suffix: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + suffix


def ensure_dirs(out_dir: Path) -> Dict[str, Path]:
    dirs = {
        "raw_html": out_dir / "raw_html",
        "raw_pdf": out_dir / "raw_pdf",
        "text": out_dir / "extracted_text",
        "logs": out_dir / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def load_rules(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        rules = json.load(f)
    rules["target_domains"] = merge_keep_order(rules.get("target_domains", []), TARGET_DOMAINS)
    rules.setdefault("national_agencies", NATIONAL_AGENCIES)
    rules["repository_pages"] = merge_repository_pages(rules.get("repository_pages", []), REPOSITORY_PAGES)
    rules.setdefault("max_repository_pages", 500)
    rules.setdefault("crawl_depth", 5)
    rules.setdefault("use_playwright", True)
    rules.setdefault("filter_year_start", 2018)
    rules.setdefault("filter_year_end", 2025)
    rules.setdefault("relevance_keywords", RELEVANCE_KEYWORDS)
    rules.setdefault("core_keywords", [])
    rules.setdefault("equity_keywords", [])
    rules.setdefault("document_type_terms", [])
    rules.setdefault("seed_urls", [])
    rules.setdefault("exclude_terms", [])
    rules.setdefault("max_summary_chars", 220)
    return rules


def merge_keep_order(primary: Iterable[str], defaults: Iterable[str]) -> List[str]:
    return dedupe_keep_order([*primary, *defaults])


def merge_repository_pages(
    primary: Iterable[Dict[str, Any]], defaults: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    seen_urls = set()
    for page in [*primary, *defaults]:
        url = normalize_url(str(page.get("url", "")))
        if not url or url in seen_urls:
            continue
        merged = dict(page)
        merged["url"] = url
        pages.append(merged)
        seen_urls.add(url)
    return pages


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
    )
    return session


def request_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with normal certificate checking, then local-CA fallback if needed."""

    try:
        return session.get(url, timeout=kwargs.pop("timeout", 25), **kwargs)
    except requests.exceptions.SSLError:
        return session.get(url, timeout=kwargs.pop("timeout", 25), verify=False, **kwargs)


def crawl_policy_repository(
    session: requests.Session,
    rules: Dict[str, Any],
    max_pages: int,
    max_candidates: int,
    delay: float,
    log_lines: List[str],
    stats: Dict[str, Any],
    diagnostics: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    """Crawl official policy repository/list pages and return candidate links."""

    candidates: Dict[str, Dict[str, str]] = {}
    repository_pages = rules.get("repository_pages", REPOSITORY_PAGES)
    max_depth = int(rules.get("crawl_depth", 5))
    use_playwright = bool(rules.get("use_playwright", True))
    visited_pages = set()

    playwright_manager = None
    browser = None
    page_obj = None
    if use_playwright and sync_playwright is not None:
        try:
            playwright_manager = sync_playwright().start()
            browser = playwright_manager.chromium.launch(headless=True)
            page_obj = browser.new_page(user_agent=USER_AGENT, locale="zh-CN")
            log_lines.append("PLAYWRIGHT_ENABLED\tchromium")
        except Exception as exc:
            log_lines.append(f"PLAYWRIGHT_UNAVAILABLE\t{type(exc).__name__}: {exc}")
            page_obj = None
    elif use_playwright:
        log_lines.append("PLAYWRIGHT_UNAVAILABLE\tpython package not importable")

    try:
        for repo in repository_pages:
            if stats["repository_pages_visited"] >= max_pages or len(candidates) >= max_candidates:
                break
            crawl_repository_site(
                session=session,
                repo=repo,
                rules=rules,
                max_pages=max_pages,
                max_candidates=max_candidates,
                delay=delay,
                log_lines=log_lines,
                stats=stats,
                diagnostics=diagnostics,
                candidates=candidates,
                visited_pages=visited_pages,
                max_depth=max_depth,
                page_obj=page_obj,
            )
    finally:
        if browser is not None:
            browser.close()
        if playwright_manager is not None:
            playwright_manager.stop()

    return list(candidates.values())


def crawl_repository_site(
    session: requests.Session,
    repo: Dict[str, Any],
    rules: Dict[str, Any],
    max_pages: int,
    max_candidates: int,
    delay: float,
    log_lines: List[str],
    stats: Dict[str, Any],
    diagnostics: Dict[str, Dict[str, str]],
    candidates: Dict[str, Dict[str, str]],
    visited_pages: set,
    max_depth: int,
    page_obj,
) -> None:
    source_name = repo.get("name", repo["url"])
    queue: List[Dict[str, Any]] = [
        {
            "url": repo["url"],
            "depth": 0,
            "page_no": None,
            "source_page": source_name,
            "discovery_path": source_name,
            "agency_hint": repo.get("agency_hint", ""),
            "policy_level_hint": repo.get("policy_level_hint", ""),
        }
    ]
    if repo.get("pagination_first_url"):
        queue.append(
            {
                "url": repo["pagination_first_url"],
                "depth": 0,
                "page_no": 1,
                "source_page": f"{source_name} page 1",
                "discovery_path": f"{source_name} -> page 1",
                "agency_hint": repo.get("agency_hint", ""),
                "policy_level_hint": repo.get("policy_level_hint", ""),
            }
        )

    bad_status_streak = 0
    no_new_candidate_streak = 0
    same_hash_streak = 0
    same_redirect_streak = 0
    previous_hash = ""
    seen_redirected_urls = set()
    pattern = repo.get("pagination_pattern")
    next_pattern_page = int(repo.get("pagination_start", 2))
    pattern_end = int(repo.get("pagination_end", 100))

    while queue and stats["repository_pages_visited"] < max_pages and len(candidates) < max_candidates:
        page = queue.pop(0)
        page_url = normalize_url(page["url"])
        if not page_url or page_url in visited_pages:
            continue
        visited_pages.add(page_url)
        stats["repository_pages_visited"] += 1
        print(f"[crawl {stats['repository_pages_visited']}/{max_pages}] {page_url}")

        html_text, status, redirected_url, status_code = fetch_repository_html(
            session, page_url, page_obj, log_lines
        )
        if redirected_url and normalize_url(redirected_url) != page_url:
            log_lines.append(f"REPOSITORY_REDIRECT\tREQUESTED={page_url}\tREDIRECTED={redirected_url}")
        redirected_key = normalize_url(redirected_url or page_url)
        if redirected_key in seen_redirected_urls:
            same_redirect_streak += 1
        else:
            same_redirect_streak = 0
            seen_redirected_urls.add(redirected_key)

        if status_code in {403, 404, 405, 412}:
            bad_status_streak += 1
            log_lines.append(f"REPOSITORY_ERROR\t{page_url}\t{status}\tBAD_STATUS_STREAK={bad_status_streak}")
            if bad_status_streak >= 5:
                log_lines.append(f"STOP_SITE_BAD_STATUS\t{source_name}\tSTREAK={bad_status_streak}")
                break
            maybe_add_pattern_page(queue, repo, next_pattern_page, pattern_end, source_name)
            next_pattern_page += 1
            time.sleep(delay)
            continue
        bad_status_streak = 0

        if not html_text:
            log_lines.append(f"REPOSITORY_ERROR\t{page_url}\t{status}")
            time.sleep(delay)
            continue

        html_hash = hashlib.sha1(html_text.encode("utf-8", errors="ignore")).hexdigest()
        if html_hash == previous_hash:
            same_hash_streak += 1
        else:
            same_hash_streak = 0
            previous_hash = html_hash
        if same_hash_streak >= 5:
            log_lines.append(f"STOP_SITE_SAME_HTML_HASH\t{source_name}\tSTREAK={same_hash_streak}")
            break
        if same_redirect_streak >= 5:
            log_lines.append(f"STOP_SITE_SAME_REDIRECTED_URL\t{source_name}\tSTREAK={same_redirect_streak}")
            break

        soup = BeautifulSoup(html_text, "html.parser")
        repository_links = extract_repository_links(soup, redirected_url or page_url, page, rules)
        stats["total_repository_links_found"] += len(repository_links)

        new_candidates = 0
        for item in repository_links:
            url = item["url"]
            context = item.get("snippet", "")
            page_type = classify_page_type(url, item.get("title", ""), context)
            article = is_article_url(url, item.get("title", ""), context)
            upsert_diagnostic(
                diagnostics,
                url,
                title=item.get("title", ""),
                domain=domain_of(url),
                repository_source=item.get("query", ""),
                discovery_path=item.get("discovery_path", ""),
                page_type=page_type,
                status_category="ok" if article else "filtered",
                filter_reason="" if article else "not_article_url",
                redirected_url="",
                text_length="0",
                matched_keywords="",
            )
            if article:
                if url not in candidates:
                    candidates[url] = item
                    stats["total_candidate_links"] += 1
                    new_candidates += 1
                if len(candidates) >= max_candidates:
                    break
            elif int(page.get("depth", 0)) < max_depth and is_repository_like_url(url):
                queue.append(
                    {
                        **page,
                        "url": url,
                        "depth": int(page.get("depth", 0)) + 1,
                        "discovery_path": f"{page.get('discovery_path', source_name)} -> {item.get('title', url)}",
                    }
                )

        if new_candidates == 0:
            no_new_candidate_streak += 1
        else:
            no_new_candidate_streak = 0
        log_lines.append(
            f"REPOSITORY_PAGE\t{page_url}\tREDIRECTED={redirected_url}\t"
            f"LINKS_FOUND={len(repository_links)}\tNEW_CANDIDATES={new_candidates}\tSTATUS={status}"
        )
        if no_new_candidate_streak >= 5:
            log_lines.append(f"STOP_SITE_NO_NEW_CANDIDATE\t{source_name}\tSTREAK={no_new_candidate_streak}")
            break

        next_url = find_next_page_url(soup, redirected_url or page_url, rules)
        if next_url and next_url not in visited_pages:
            queue.append(
                {
                    **page,
                    "url": next_url,
                    "discovery_path": f"{page.get('discovery_path', source_name)} -> 下一页",
                }
            )
        elif pattern and next_pattern_page <= pattern_end:
            maybe_add_pattern_page(queue, repo, next_pattern_page, pattern_end, source_name)
            next_pattern_page += 1
        time.sleep(delay)


def maybe_add_pattern_page(
    queue: List[Dict[str, Any]],
    repo: Dict[str, Any],
    page_no: int,
    pattern_end: int,
    source_name: str,
) -> None:
    pattern = repo.get("pagination_pattern")
    if not pattern or page_no > pattern_end:
        return
    queue.append(
        {
            "url": pattern.format(page=page_no),
            "depth": 0,
            "page_no": page_no,
            "source_page": f"{source_name} page {page_no}",
            "discovery_path": f"{source_name} -> page {page_no}",
            "agency_hint": repo.get("agency_hint", ""),
            "policy_level_hint": repo.get("policy_level_hint", ""),
        }
    )


def fetch_repository_html(
    session: requests.Session,
    url: str,
    page_obj,
    log_lines: List[str],
) -> Tuple[str, str, str, int]:
    if page_obj is not None:
        try:
            response = page_obj.goto(url, wait_until="networkidle", timeout=30000)
            html_text = page_obj.content()
            status_code = response.status if response is not None else 0
            if html_text and len(html_text) > 200:
                return html_text, f"PLAYWRIGHT {status_code}", page_obj.url, status_code
        except Exception as exc:
            log_lines.append(f"PLAYWRIGHT_PAGE_ERROR\t{url}\t{type(exc).__name__}: {exc}")

    try:
        resp = request_get(session, url, timeout=25)
        status = f"HTTP {resp.status_code}"
        if resp.status_code >= 400:
            return "", status, resp.url, resp.status_code
        return decode_html(resp.content, resp.encoding, resp.apparent_encoding), status, resp.url, resp.status_code
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}", "", 0


def dedupe_keep_order(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        value = clean_text(value)
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def extract_repository_links(
    soup: BeautifulSoup,
    base_url: str,
    page: Dict[str, str],
    rules: Dict[str, Any],
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        if not title or len(title) < 4:
            continue
        url = normalize_url(urljoin(base_url, a["href"]))
        if not url or url in seen or not is_official_url(url, rules):
            continue
        seen.add(url)

        context_text = clean_text(parent_context_text(a))
        list_date = extract_date_from_text(context_text)
        items.append(
            {
                "title": title,
                "url": url,
                "snippet": context_text[:240],
                "list_date": list_date,
                "query": page.get("source_page", base_url),
                "discovery_path": page.get("discovery_path", page.get("source_page", base_url)),
                "search_source": "policy_repository",
                "agency_hint": page.get("agency_hint", ""),
                "policy_level_hint": page.get("policy_level_hint", ""),
            }
        )

    return items


def parent_context_text(node) -> str:
    parent = node
    for _ in range(3):
        if parent.parent is None:
            break
        parent = parent.parent
        text = clean_text(parent.get_text(" ", strip=True))
        if len(text) >= 12:
            return text
    return clean_text(node.get_text(" ", strip=True))


def extract_date_from_text(text: str) -> str:
    patterns = [
        r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def has_date_like_article_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(
        re.search(r"(/content/|/content_|/art/|/t20|/20(?:20|21|22|23|24|25|26))", path)
        or url.lower().endswith(".pdf")
    )


def title_has_policy_signal(title: str) -> bool:
    return len(clean_text(title)) > 8 and contains_any(title, POLICY_TITLE_SIGNALS)


def context_has_publication_date(context: str) -> bool:
    return bool(extract_date_from_text(context))


def is_navigation_title(title: str) -> bool:
    title = clean_text(title)
    if not title or title.startswith("--") or len(title) < 8:
        return True
    return title in NAVIGATION_TITLES


def is_list_like_url_without_date(url: str) -> bool:
    path = urlparse(url).path.lower()
    list_markers = ["/col/col", "/index.html", "/index.htm", "/list", "/node", "/zhengce/", "/zwgk/"]
    return any(marker in path for marker in list_markers) and not has_date_like_article_path(url)


def is_article_url(url: str, title: str, context: str) -> bool:
    title = clean_text(title)
    if is_navigation_title(title):
        return False
    if is_list_like_url_without_date(url):
        return False
    return (
        has_date_like_article_path(url)
        or title_has_policy_signal(title)
        or context_has_publication_date(context)
    )


def classify_page_type(url: str, title: str, context: str) -> str:
    if is_article_url(url, title, context):
        return "article"
    path = urlparse(url).path.lower()
    if "index" in path or clean_text(title) in {"首页", "更多", "查看更多"}:
        return "index"
    if any(marker in path for marker in ["list", "node", "col", "zhengce", "zwgk"]):
        return "list"
    return "unknown"


def find_next_page_url(soup: BeautifulSoup, base_url: str, rules: Dict[str, Any]) -> str:
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ", strip=True))
        rel = " ".join(a.get("rel", [])) if isinstance(a.get("rel"), list) else str(a.get("rel") or "")
        if text in {"下一页", "下页", "下一頁", ">", "›"} or "next" in rel.lower():
            url = normalize_url(urljoin(base_url, a["href"]))
            if url and is_official_url(url, rules):
                return url
    return ""


def upsert_diagnostic(diagnostics: Dict[str, Dict[str, str]], url: str, **values: str) -> None:
    url = normalize_url(url)
    if not url:
        return
    row = diagnostics.setdefault(
        url,
        {
            "source_url": url,
            "title": "",
            "domain": domain_of(url),
            "repository_source": "",
            "discovery_path": "",
            "page_type": "unknown",
            "status_category": "ok",
            "filter_reason": "",
            "fetch_status": "",
            "redirected_url": "",
            "text_length": "0",
            "policy_title": "",
            "publication_date": "",
            "issuing_agency": "",
            "policy_theme": "",
            "relevance_level": "",
            "matched_keywords": "",
        },
    )
    for key, value in values.items():
        if value is not None:
            row[key] = str(value)


def is_repository_like_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if re.search(r"(index_\d+|node_\d+|list_\d+|content_\d+)", path):
        return True
    markers = ["list", "index", "zhengce", "col", "gfxwj", "zcwj", "policy"]
    if any(marker in path for marker in markers):
        return True
    return path.endswith("/")


def is_probable_policy_url(url: str, title: str, rules: Dict[str, Any]) -> bool:
    path = urlparse(url).path.lower()
    if any(url.lower().endswith(ext) for ext in [".jpg", ".png", ".gif", ".mp4", ".zip"]):
        return False
    if any(marker in path for marker in ["/content_", "/art/", "/zhengce/content", ".pdf"]):
        return True
    if contains_any(title, POLICY_TITLE_CANDIDATE_TERMS):
        return True
    if contains_any(title, rules.get("document_type_terms", [])):
        return True
    if contains_any(title, HEALTH_FIELD_TERMS):
        return True
    return False


def is_official_url(url: str, rules: Dict[str, Any]) -> bool:
    domain = domain_of(url)
    return domain_matches(domain, rules.get("target_domains", TARGET_DOMAINS))


def fetch_document(
    session: requests.Session,
    url: str,
    dirs: Dict[str, Path],
) -> Tuple[str, str, str, Optional[Path], int, str]:
    """Return file_type, extracted_text, status, raw_file_path, status_code, redirected_url."""

    resp = request_get(session, url)
    status = f"HTTP {resp.status_code}"
    if resp.status_code >= 400:
        return "UNKNOWN", "", status, None, resp.status_code, resp.url

    content_type = resp.headers.get("Content-Type", "").lower()
    url_lower = url.lower()
    is_pdf = "pdf" in content_type or url_lower.endswith(".pdf")

    if is_pdf:
        raw_path = dirs["raw_pdf"] / stable_name(url, ".pdf")
        raw_path.write_bytes(resp.content)
        text = extract_pdf_text(resp.content)
        return "PDF", text, status, raw_path, resp.status_code, resp.url

    raw_path = dirs["raw_html"] / stable_name(url, ".html")
    raw_path.write_bytes(resp.content)
    html_text = decode_html(resp.content, resp.encoding, resp.apparent_encoding)
    text = extract_html_text(html_text, url)
    return "HTML", text, status, raw_path, resp.status_code, resp.url


def decode_html(content: bytes, response_encoding: Optional[str], apparent_encoding: Optional[str]) -> str:
    candidates = []
    for encoding in [apparent_encoding, response_encoding, "utf-8", "gb18030"]:
        if encoding and encoding not in candidates:
            candidates.append(encoding)

    for encoding in candidates:
        try:
            decoded = content.decode(encoding, errors="replace")
        except LookupError:
            continue
        if not looks_mojibake(decoded):
            return decoded

    dammit = UnicodeDammit(content, ["utf-8", "gb18030", "gbk"])
    return dammit.unicode_markup or content.decode("utf-8", errors="replace")


def looks_mojibake(text: str) -> bool:
    sample = text[:2000]
    bad_markers = sample.count("ç") + sample.count("æ") + sample.count("é") + sample.count("�")
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", sample))
    return bad_markers > 10 and cjk_chars < 20


def extract_html_text(html_text: str, url: str) -> str:
    extracted = trafilatura.extract(
        html_text,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    if extracted and len(extracted) >= 80:
        return clean_text(extracted)

    soup = BeautifulSoup(html_text, "html.parser")
    for selector in ARTICLE_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) >= 80:
                return text

    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True))


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ""
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    return clean_text("\n".join(pages))


def extract_policy_metadata(
    title: str,
    url: str,
    text: str,
    rules: Dict[str, Any],
) -> Dict[str, str]:
    sample = clean_text(" ".join([title, text[:4000]]))
    issuing_agency = extract_issuing_agency(sample, url, rules)
    publication_date = extract_publication_date(sample, url)
    policy_level = classify_policy_level(issuing_agency, url, sample)
    document_type = classify_document_type(title or sample[:120])

    return {
        "policy_title": clean_title(title, sample),
        "publication_date": publication_date,
        "issuing_agency": issuing_agency,
        "policy_level": policy_level,
        "document_type": document_type,
    }


def clean_title(title: str, sample: str) -> str:
    title = clean_text(title)
    if title and len(title) >= 6 and not looks_like_generic_title(title):
        return title[:200]

    patterns = [
        r"(国务院办公厅关于[^。]{6,120}?的(?:通知|意见))",
        r"((?:国家卫生健康委|国家医保局|工业和信息化部|国家中医药管理局)[^。]{4,140}?(?:通知|意见|办法|规范|规划|方案|标准))",
        r"(《[^》]{4,120}》)",
        r"([^。]{6,120}?(?:通知|意见|办法|规范|规划|方案|标准|行动计划|工作方案))",
    ]
    for pattern in patterns:
        m = re.search(pattern, sample)
        if m:
            return clean_text(m.group(1))[:200]
    return title[:200] or "未识别标题"


def looks_like_generic_title(title: str) -> bool:
    generic_terms = ["首页", "政策", "政务公开", "通知公告", "政策文件", "政府信息公开"]
    return title in generic_terms or len(title) < 6


def extract_publication_date(sample: str, url: str) -> str:
    patterns = [
        r"(?:发布时间|发布日期|发文日期|日期|时间)[:：]?\s*(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
        r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    joined = f"{sample} {url}"
    for pattern in patterns:
        m = re.search(pattern, joined)
        if m:
            y, month, day = m.group(1), m.group(2), m.group(3)
            return f"{int(y):04d}-{int(month):02d}-{int(day):02d}"
    return ""


def publication_year(publication_date: str) -> Optional[int]:
    m = re.match(r"^(20\d{2})", publication_date or "")
    if not m:
        return None
    return int(m.group(1))


def year_in_scope(publication_date: str, rules: Dict[str, Any]) -> bool:
    year = publication_year(publication_date)
    if year is None:
        return False
    return int(rules.get("filter_year_start", 2018)) <= year <= int(rules.get("filter_year_end", 2025))


def extract_issuing_agency(sample: str, url: str, rules: Dict[str, Any]) -> str:
    agencies = rules.get("national_agencies", NATIONAL_AGENCIES)
    for agency in agencies:
        if agency in sample:
            return agency

    agency_patterns = [
        r"([\u4e00-\u9fff]{2,12}(?:省|自治区|市|县)(?:卫生健康委员会|卫生健康委|人民政府办公厅|人民政府|医疗保障局|工业和信息化厅|中医药管理局))",
        r"([\u4e00-\u9fff]{2,12}(?:卫生健康委员会|卫生健康委|医疗保障局|工信厅|工业和信息化厅))",
    ]
    for pattern in agency_patterns:
        m = re.search(pattern, sample)
        if m:
            return clean_text(m.group(1))

    domain = domain_of(url)
    domain_map = {
        "nhc.gov.cn": "国家卫生健康委",
        "nhsa.gov.cn": "国家医保局",
        "miit.gov.cn": "工业和信息化部",
        "satcm.gov.cn": "国家中医药管理局",
        "moe.gov.cn": "教育部",
        "cac.gov.cn": "国家互联网信息办公室",
        "stats.gov.cn": "国家统计局",
        "gov.cn": "中国政府网",
    }
    for key, value in domain_map.items():
        if domain == key or domain.endswith("." + key):
            return value
    return ""


def classify_policy_level(agency: str, url: str, sample: str) -> str:
    domain = domain_of(url)
    if any(a in agency for a in NATIONAL_AGENCIES) or domain in {
        "gov.cn",
        "www.gov.cn",
        "nhc.gov.cn",
        "nhsa.gov.cn",
        "miit.gov.cn",
        "satcm.gov.cn",
        "moe.gov.cn",
        "cac.gov.cn",
        "stats.gov.cn",
    }:
        return "国家级"

    if "县" in agency:
        return "县级"
    if re.search(r"(市人民政府|市卫生健康|市医保|市工业和信息化)", agency):
        return "市级"
    if any(p in agency or p in sample[:300] for p in PROVINCES):
        return "省级"
    if domain.endswith(".gov.cn"):
        return "地方政府"
    return ""


def classify_document_type(title: str) -> str:
    mapping = [
        ("法律", ["法"]),
        ("行政法规", ["条例"]),
        ("通知", ["通知"]),
        ("指导意见", ["指导意见", "意见"]),
        ("实施方案", ["实施方案", "方案"]),
        ("行动计划", ["行动计划"]),
        ("发展规划", ["发展规划", "规划"]),
        ("管理办法", ["管理办法", "办法"]),
        ("规范", ["规范"]),
        ("标准", ["标准"]),
        ("工作方案", ["工作方案"]),
        ("政府报告", ["报告"]),
    ]
    for label, terms in mapping:
        if any(term in title for term in terms):
            return label
    return "其他政策文件"


def is_relevant_policy(
    title: str,
    text: str,
    rules: Dict[str, Any],
    agency: str,
    publication_date: str = "",
) -> bool:
    return filter_diagnostics(title, text, rules, agency, publication_date)["keep"]


def filter_diagnostics(
    title: str,
    text: str,
    rules: Dict[str, Any],
    agency: str,
    publication_date: str = "",
) -> Dict[str, Any]:
    combined = clean_text(" ".join([title, text[:8000]]))
    exclude_hits = contains_any(combined, rules.get("exclude_terms", []))
    core_hits = contains_any(combined, rules.get("core_keywords", []))
    health_hits = contains_any(combined, HEALTH_FIELD_TERMS)
    health_agency = any(term in agency for term in ["卫生", "医保", "中医药", "医疗保障"])
    relevance_level, relevance_hits = classify_relevance_level(title, text, rules)

    if exclude_hits:
        keep = False
        reason = "exclude_terms"
    elif not publication_date:
        keep = False
        reason = "missing_publication_date"
    elif not year_in_scope(publication_date, rules):
        keep = False
        reason = "year_out_of_scope"
    elif relevance_level:
        keep = True
        reason = relevance_level
    else:
        keep = False
        reason = "no_relevance_signal"

    return {
        "keep": keep,
        "reason": reason,
        "core_hits": core_hits,
        "health_hits": health_hits,
        "exclude_hits": exclude_hits,
        "health_agency": health_agency,
        "relevance_level": relevance_level,
        "relevance_hits": relevance_hits,
    }


def classify_relevance_level(title: str, text: str, rules: Dict[str, Any]) -> Tuple[str, List[str]]:
    combined = clean_text(" ".join([title, text[:12000]]))
    relevance_keywords = rules.get("relevance_keywords", RELEVANCE_KEYWORDS)
    for level in ["high_relevance", "medium_relevance", "low_relevance"]:
        hits = contains_any(combined, relevance_keywords.get(level, []))
        if hits:
            return level, hits
    return "", []


def detect_policy_theme(title: str, text: str) -> str:
    combined = clean_text(" ".join([title, text[:12000]]))
    themes = []
    for theme, keywords in POLICY_THEME_RULES.items():
        if contains_any(combined, keywords):
            themes.append(theme)
    return "；".join(themes) if themes else "卫生健康政策（fallback）"


def collect_matched_keywords(title: str, text: str, rules: Dict[str, Any]) -> Dict[str, List[str]]:
    combined = clean_text(" ".join([title, text[:12000]]))
    return {
        "high_relevance": contains_any(combined, rules.get("relevance_keywords", RELEVANCE_KEYWORDS).get("high_relevance", [])),
        "medium_relevance": contains_any(combined, rules.get("relevance_keywords", RELEVANCE_KEYWORDS).get("medium_relevance", [])),
        "low_relevance": contains_any(combined, rules.get("relevance_keywords", RELEVANCE_KEYWORDS).get("low_relevance", [])),
        "core_keywords": contains_any(combined, rules.get("core_keywords", [])),
        "health_keywords": contains_any(combined, HEALTH_FIELD_TERMS),
        "document_type_terms": contains_any(combined, rules.get("document_type_terms", [])),
    }


def format_matched_keywords(matches: Dict[str, List[str]]) -> str:
    parts = []
    for key in [
        "high_relevance",
        "medium_relevance",
        "low_relevance",
        "core_keywords",
        "health_keywords",
        "document_type_terms",
    ]:
        values = matches.get(key, [])
        parts.append(f"{key}={';'.join(values)}")
    return " | ".join(parts)


def contains_any(text: str, terms: Iterable[str]) -> List[str]:
    text = text.lower()
    hits = []
    for term in terms:
        term = str(term).strip()
        if not term:
            continue
        if re.search(re.escape(term.lower()), text):
            hits.append(term)
    return hits


def short_summary(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedupe_records(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    unique: List[Dict[str, str]] = []
    seen_urls = set()
    seen_titles: List[str] = []

    for record in records:
        url = normalize_url(record["source_url"])
        title = clean_text(record["policy_title"])
        if not url or url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if title and any(title_similarity(title, old) > 0.90 for old in seen_titles):
            continue
        seen_urls.add(url)
        if title:
            seen_titles.append(title)
        unique.append(record)
    return unique


def write_text_file(dirs: Dict[str, Path], url: str, text: str) -> Path:
    path = dirs["text"] / stable_name(url, ".txt")
    path.write_text(text, encoding="utf-8")
    return path


def export_records(records: List[Dict[str, str]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "policy_title",
        "publication_date",
        "issuing_agency",
        "policy_level",
        "document_type",
        "policy_theme",
        "relevance_level",
        "matched_keywords",
        "source_url",
        "domain",
        "short_summary",
        "content",
        "file_type",
        "retrieval_date",
        "status_category",
        "page_type",
        "redirected_url",
        "text_length",
        "text_path",
        "raw_path",
        "repository_source",
        "discovery_path",
        "fetch_status",
    ]

    csv_path = out_dir / "policies.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def export_diagnostics(diagnostics: Dict[str, Dict[str, str]], out_dir: Path) -> None:
    fields = [
        "source_url",
        "title",
        "policy_title",
        "publication_date",
        "domain",
        "issuing_agency",
        "policy_theme",
        "relevance_level",
        "matched_keywords",
        "repository_source",
        "discovery_path",
        "page_type",
        "status_category",
        "filter_reason",
        "fetch_status",
        "redirected_url",
        "text_length",
    ]
    path = out_dir / "candidates_diagnostics.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in diagnostics.values():
            writer.writerow(row)


def write_collector_log(dirs: Dict[str, Path], log_lines: List[str], stats: Dict[str, Any]) -> None:
    summary_lines = [
        "SUMMARY",
        f"TOTAL_REPOSITORY_PAGES_VISITED={stats['repository_pages_visited']}",
        f"TOTAL_REPOSITORY_LINKS_FOUND={stats['total_repository_links_found']}",
        f"TOTAL_CANDIDATE_LINKS={stats['total_candidate_links']}",
        f"TOTAL_FETCHED={stats['total_fetched']}",
        f"TOTAL_FILTERED={stats['total_filtered']}",
        f"TOTAL_BAD_STATUS={stats['total_bad_status']}",
        f"TOTAL_EMPTY_TEXT={stats['total_empty_text']}",
        f"TOTAL_FETCH_ERRORS={stats['total_fetch_errors']}",
        f"TOTAL_KEPT_AFTER_DEDUPE={stats['total_kept_after_dedupe']}",
        "FILTER_REASONS="
        + json.dumps(dict(stats["filter_reasons"]), ensure_ascii=False, sort_keys=True),
        "TOP_DOMAINS=" + json.dumps(stats.get("top_domains", {}), ensure_ascii=False),
        "TOP_AGENCIES=" + json.dumps(stats.get("top_agencies", {}), ensure_ascii=False),
        "TOP_THEMES=" + json.dumps(stats.get("top_themes", {}), ensure_ascii=False),
        "TOP_RELEVANCE_LEVELS=" + json.dumps(stats.get("top_relevance_levels", {}), ensure_ascii=False),
        "",
        "DETAILS",
    ]
    (dirs["logs"] / "collector.log").write_text(
        "\n".join(summary_lines + log_lines),
        encoding="utf-8",
    )


def print_collection_stats(stats: Dict[str, Any]) -> None:
    print("Collection diagnostics:")
    print(f"  TOTAL_REPOSITORY_LINKS_FOUND={stats['total_repository_links_found']}")
    print(f"  TOTAL_CANDIDATE_LINKS={stats['total_candidate_links']}")
    print(f"  TOTAL_FETCHED={stats['total_fetched']}")
    print(f"  TOTAL_FILTERED={stats['total_filtered']}")
    print(f"  TOTAL_BAD_STATUS={stats['total_bad_status']}")
    print(f"  FILTER_REASONS={dict(stats['filter_reasons'])}")
    print(f"  TOP_DOMAINS={stats.get('top_domains', {})}")
    print(f"  TOP_AGENCIES={stats.get('top_agencies', {})}")
    print(f"  TOP_THEMES={stats.get('top_themes', {})}")
    print(f"  TOP_RELEVANCE_LEVELS={stats.get('top_relevance_levels', {})}")


def update_coverage_stats(stats: Dict[str, Any], records: List[Dict[str, str]]) -> None:
    domain_counts = Counter()
    agency_counts = Counter()
    theme_counts = Counter()
    relevance_counts = Counter()

    for record in records:
        domain_counts[record.get("domain", "") or "UNKNOWN"] += 1
        agency_counts[record.get("issuing_agency", "") or "UNKNOWN"] += 1
        themes = [t for t in record.get("policy_theme", "").split("；") if t]
        if not themes:
            themes = ["UNKNOWN"]
        for theme in themes:
            theme_counts[theme] += 1
        relevance_counts[record.get("relevance_level", "") or "UNKNOWN"] += 1

    stats["top_domains"] = dict(domain_counts.most_common(20))
    stats["top_agencies"] = dict(agency_counts.most_common(20))
    stats["top_themes"] = dict(theme_counts.most_common(20))
    stats["top_relevance_levels"] = dict(relevance_counts.most_common(20))


def collect_policies(
    rules: Dict[str, Any],
    out_dir: Path,
    max_repository_pages: int,
    max_candidates: int,
    delay: float,
) -> List[Dict[str, str]]:
    dirs = ensure_dirs(out_dir)
    session = make_session()
    retrieval_date = dt.date.today().isoformat()
    candidates: Dict[str, Dict[str, str]] = {}
    log_lines: List[str] = []
    stats: Dict[str, Any] = {
        "repository_pages_visited": 0,
        "total_repository_links_found": 0,
        "total_candidate_links": 0,
        "total_fetched": 0,
        "total_filtered": 0,
        "total_bad_status": 0,
        "total_empty_text": 0,
        "total_fetch_errors": 0,
        "filter_reasons": Counter(),
    }
    diagnostics: Dict[str, Dict[str, str]] = {}

    for url in rules.get("seed_urls", []):
        url = normalize_url(url)
        if url and is_official_url(url, rules):
            upsert_diagnostic(
                diagnostics,
                url,
                title="",
                domain=domain_of(url),
                repository_source="seed_url",
                discovery_path="seed_url",
                page_type="article",
                status_category="ok",
            )
            candidates.setdefault(
                url,
                {
                    "title": "",
                    "url": url,
                    "snippet": "",
                    "query": "seed_url",
                    "discovery_path": "seed_url",
                    "search_source": "seed_url",
                    "list_date": "",
                    "agency_hint": "",
                    "policy_level_hint": "",
                },
            )

    repository_candidates = crawl_policy_repository(
        session=session,
        rules=rules,
        max_pages=max_repository_pages,
        max_candidates=max_candidates,
        delay=delay,
        log_lines=log_lines,
        stats=stats,
        diagnostics=diagnostics,
    )
    for row in repository_candidates:
        url = normalize_url(row.get("url", ""))
        if url and is_official_url(url, rules):
            row["url"] = url
            candidates.setdefault(url, row)

    records: List[Dict[str, str]] = []
    for idx, candidate in enumerate(candidates.values(), 1):
        url = candidate["url"]
        print(f"[fetch {idx}/{len(candidates)}] {url}")
        stats["total_fetched"] += 1
        try:
            file_type, text, fetch_status, raw_path, status_code, redirected_url = fetch_document(session, url, dirs)
        except Exception as exc:
            stats["total_fetch_errors"] += 1
            upsert_diagnostic(
                diagnostics,
                url,
                status_category="bad_status",
                filter_reason=f"fetch_error:{type(exc).__name__}",
                fetch_status=str(exc),
            )
            log_lines.append(f"FETCH_ERROR\t{url}\t{type(exc).__name__}: {exc}")
            continue
        if status_code in BAD_STATUS_CODES or status_code >= 400:
            stats["total_bad_status"] += 1
            upsert_diagnostic(
                diagnostics,
                url,
                status_category="bad_status",
                filter_reason=f"bad_status:{status_code}",
                fetch_status=fetch_status,
                redirected_url=redirected_url,
                text_length="0",
            )
            log_lines.append(f"BAD_STATUS\t{url}\t{fetch_status}\tREDIRECTED={redirected_url}")
            continue
        if not text:
            stats["total_empty_text"] += 1
            upsert_diagnostic(
                diagnostics,
                url,
                status_category="empty_text",
                filter_reason="empty_text",
                fetch_status=fetch_status,
                redirected_url=redirected_url,
                text_length="0",
            )
            log_lines.append(f"EMPTY_TEXT\t{url}\t{fetch_status}")
            continue

        metadata = extract_policy_metadata(candidate.get("title", ""), url, text, rules)
        if not metadata["publication_date"]:
            metadata["publication_date"] = candidate.get("list_date", "")
        if not metadata["issuing_agency"]:
            metadata["issuing_agency"] = candidate.get("agency_hint", "")
        if not metadata["policy_level"]:
            metadata["policy_level"] = candidate.get("policy_level_hint", "")
        filter_result = filter_diagnostics(
            metadata["policy_title"],
            text,
            rules,
            metadata["issuing_agency"],
            metadata["publication_date"],
        )
        matched_keywords = collect_matched_keywords(metadata["policy_title"], text, rules)
        matched_keywords_text = format_matched_keywords(matched_keywords)
        policy_theme = detect_policy_theme(metadata["policy_title"], text)
        relevance_level = filter_result["relevance_level"]
        if not filter_result["keep"]:
            stats["total_filtered"] += 1
            stats["filter_reasons"][filter_result["reason"]] += 1
            upsert_diagnostic(
                diagnostics,
                url,
                title=candidate.get("title", ""),
                policy_title=metadata["policy_title"],
                publication_date=metadata["publication_date"],
                issuing_agency=metadata["issuing_agency"],
                policy_theme=policy_theme,
                relevance_level=relevance_level,
                matched_keywords=matched_keywords_text,
                status_category="filtered",
                filter_reason=filter_result["reason"],
                fetch_status=fetch_status,
                redirected_url=redirected_url,
                text_length=str(len(text)),
            )
            log_lines.append(
                "FILTERED_REASON\t"
                f"URL={url}\t"
                f"TITLE={metadata['policy_title']}\t"
                f"REASON={filter_result['reason']}\t"
                f"CORE_HITS={';'.join(filter_result['core_hits'])}\t"
                f"HEALTH_HITS={';'.join(filter_result['health_hits'])}\t"
                f"RELEVANCE_LEVEL={relevance_level}\t"
                f"RELEVANCE_HITS={';'.join(filter_result['relevance_hits'])}\t"
                f"EXCLUDE_HITS={';'.join(filter_result['exclude_hits'])}\t"
                f"PUBLICATION_DATE={metadata['publication_date']}\t"
                f"AGENCY={metadata['issuing_agency']}"
            )
            continue

        text_path = write_text_file(dirs, url, text)
        record = {
            **metadata,
            "policy_theme": policy_theme,
            "relevance_level": relevance_level,
            "matched_keywords": matched_keywords_text,
            "source_url": url,
            "domain": domain_of(url),
            "short_summary": short_summary(text, int(rules.get("max_summary_chars", 220))),
            "content": text,
            "file_type": file_type,
            "retrieval_date": retrieval_date,
            "status_category": "kept",
            "page_type": "article",
            "redirected_url": redirected_url,
            "text_length": str(len(text)),
            "text_path": str(text_path),
            "raw_path": str(raw_path) if raw_path else "",
            "repository_source": candidate.get("query", ""),
            "discovery_path": candidate.get("discovery_path", candidate.get("query", "")),
            "fetch_status": fetch_status,
        }
        records.append(record)
        upsert_diagnostic(
            diagnostics,
            url,
            title=candidate.get("title", ""),
            policy_title=metadata["policy_title"],
            publication_date=metadata["publication_date"],
            issuing_agency=metadata["issuing_agency"],
            policy_theme=policy_theme,
            relevance_level=relevance_level,
            matched_keywords=matched_keywords_text,
            status_category="kept",
            filter_reason="kept",
            fetch_status=fetch_status,
            redirected_url=redirected_url,
            text_length=str(len(text)),
            page_type="article",
        )
        time.sleep(delay)

    records = dedupe_records(records)
    update_coverage_stats(stats, records)
    export_records(records, out_dir)
    export_diagnostics(diagnostics, out_dir)
    stats["total_kept_after_dedupe"] = len(records)
    write_collector_log(dirs, log_lines, stats)
    print_collection_stats(stats)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Chinese telehealth policy documents.")
    parser.add_argument("--rules", type=Path, default=Path("keyword_rules.example.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--max-repository-pages", type=int, default=500)
    parser.add_argument("--max-candidates", type=int, default=3000)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--no-playwright", action="store_true", help="Disable Playwright for repository pages.")
    args = parser.parse_args()

    rules = load_rules(args.rules)
    if args.no_playwright:
        rules["use_playwright"] = False
    records = collect_policies(
        rules=rules,
        out_dir=args.out_dir,
        max_repository_pages=args.max_repository_pages,
        max_candidates=args.max_candidates,
        delay=args.delay,
    )
    print(f"Done. Collected {len(records)} policy documents.")
    print(f"Outputs written to: {args.out_dir.resolve()}")


if __name__ == "__main__":
    main()
