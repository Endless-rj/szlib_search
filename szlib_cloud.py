#!/usr/bin/env python3
"""
深圳图书馆搜索 - 云部署版（单文件，无外部依赖）

适用场景：PythonAnywhere、Render、Railway 等云平台

使用方法：
1. 安装依赖: pip install flask
2. 本地测试: python szlib_cloud.py
3. 浏览器打开: http://localhost:8080

云部署：此文件为完全自包含，无需 index.html 等额外文件
"""

import json
import os
import queue
import signal
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from flask import Flask, Response, request, render_template_string

app = Flask(__name__)

# ============================================================
#  常量
# ============================================================
BASE_URL = "https://www.szlib.org.cn"

# 搜索 API（直接返回 JSON，无需浏览器渲染）
SEARCH_API = (
    f"{BASE_URL}/api/opacservice/getQueryResult"
    "?library=all"
    "&v_tablearray=bibliosm,serbibm,apabibibm,mmbibm,"
    "&sortfield=ptitle&sorttype=desc&pageNum=10"
    "&v_page=1&v_secondquery="
    "&client_id=t1"
)

# 馆藏分布 API
HOLDING_API = f"{BASE_URL}/api/opacservice/getpreholding"

IGNORE_LIB_KEYWORD = "（暂不外借）"

# HTTP 请求头（模拟浏览器）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/opac/searchShow",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ============================================================
#  HTTP 工具
# ============================================================
def http_get(url, timeout=30):
    """发送 GET 请求，返回 JSON 或 None"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  HTTP 请求失败: {e}")
    return None


# ============================================================
#  搜索逻辑
# ============================================================
def search_books_api(keyword):
    """
    调用搜索 API，返回书籍列表

    API 返回结构（经验证）:
    {
        "data": [
            {
                "title": "红楼梦",
                "tablename": "bibliosm",
                "recordid": 12345,
                "isbn": "...",
                "author": "曹雪芹",
                "publisher": "...",
                "callno": "I242.4/...",
                ...
            }
        ],
        "totalPage": 5,
        "totalCount": 48
    }
    """
    url = SEARCH_API + "&v_index=title&v_value=" + urllib.parse.quote(keyword)
    result = http_get(url)
    if not result:
        return []

    # 兼容多种返回格式
    books = []
    if isinstance(result, dict):
        data = result.get("data", [])
        if isinstance(data, list):
            books = data
        elif isinstance(data, dict):
            books = data.get("list", data.get("books", data.get("result", [])))
            if isinstance(books, dict):
                books = books.get("list", [])

    return books if isinstance(books, list) else []


def fetch_holdings(tablename, recordid):
    """
    获取某本书的馆藏分布

    API: GET /api/opacservice/getpreholding?metaTable={}&metaId={}&library=all&client_id=t1

    返回结构:
    {
        "districtList": [{"name": "044007", "notes": "宝安区图书馆", ...}],
        "CanLoanBook": [
            {
                "notes": "可外借馆藏",
                "serviceaddrnotes": "宝安区图书馆",
                "serviceaddr": "044007",
                "recordList": [
                    {"barcode": "...", "callno": "I242.4/1488", "local": "阅览室"},
                    ...
                ]
            }
        ],
        "OnlyReadBook": [...],
        "borrowedBook": [...]
    }
    """
    url = f"{HOLDING_API}?metaTable={tablename}&metaId={recordid}&library=all&client_id=t1"
    api_data = http_get(url)
    if not api_data:
        return []

    return parse_holdings(api_data)


def parse_holdings(api_response):
    """解析馆藏分布 API 返回值"""
    results = []

    # 图书馆代码 → 名称映射
    district_map = {}
    for d in api_response.get("districtList", []):
        district_map[d.get("name", "")] = (
            d.get("serviceaddrnotes") or d.get("notes", "")
        )

    # 处理：可外借 / 仅阅览 / 已借出
    holding_sections = []
    for key in ("CanLoanBook", "OnlyReadBook", "borrowedBook"):
        section = api_response.get(key)
        if isinstance(section, list):
            holding_sections.extend(section)
        elif isinstance(section, dict):
            holding_sections.append(section)

    for section in holding_sections:
        library_name = section.get("serviceaddrnotes", "")
        if not library_name:
            addr_code = section.get("serviceaddr", "")
            if addr_code:
                code = addr_code.split()[0] if addr_code.split() else addr_code
                library_name = district_map.get(code, "")

        for record in section.get("recordList", []):
            results.append({
                "library": library_name,
                "call_number": record.get("callno", ""),
                "location": record.get("local", ""),
                "barcode": record.get("barcode", ""),
                "holding_type": section.get("notes", ""),
            })

    return results


def group_by_library(all_holdings):
    """按图书馆分组、去重并计数"""
    lib_books = {}

    for entry in all_holdings:
        book_title = entry["title"]
        for h in entry["holdings"]:
            lib_name = h.get("library", "")
            if IGNORE_LIB_KEYWORD in lib_name or not lib_name:
                continue
            if lib_name not in lib_books:
                lib_books[lib_name] = {}
            if book_title not in lib_books[lib_name]:
                lib_books[lib_name][book_title] = {
                    "title": book_title,
                    "call_number": h.get("call_number", ""),
                    "location": h.get("location", ""),
                    "count": 1,
                }
            else:
                lib_books[lib_name][book_title]["count"] += 1

    result = []
    for lib_name, books in lib_books.items():
        book_list = list(books.values())
        result.append({
            "library": lib_name,
            "total_types": len(book_list),
            "total_copies": sum(b["count"] for b in book_list),
            "books": book_list,
        })
    result.sort(key=lambda x: x["total_copies"], reverse=True)
    return result


# ============================================================
#  异步任务管理
# ============================================================
class SearchTask:
    def __init__(self, task_id, book_name):
        self.task_id = task_id
        self.book_name = book_name
        self.status = "running"
        self.progress_queue = queue.Queue()
        self.results = None
        self.error = None
        self.latest_progress = {"stage": "init", "message": "准备中...", "percent": 0}

    def send_progress(self, stage, message, percent=0):
        msg = {"type": "progress", "stage": stage, "message": message, "percent": percent}
        self.progress_queue.put(msg)
        self.latest_progress = msg

    def send_done(self, results):
        self.results = results
        self.status = "done"
        self.progress_queue.put({"type": "done", "results": results})
        self.latest_progress = {"stage": "done", "message": "搜索完成！", "percent": 100}

    def send_error(self, error):
        self.error = error
        self.status = "error"
        self.progress_queue.put({"type": "error", "error": error})
        self.latest_progress = {"stage": "error", "message": error, "percent": 0}


tasks = {}


def run_search(book_name, task):
    """搜索主流程（纯 API，无需浏览器）"""
    try:
        # 步骤1: 搜索书籍
        task.send_progress("search", f"正在搜索《{book_name}》...", 10)
        books = search_books_api(book_name)

        if not books:
            task.send_progress("done", "搜索完成，未找到结果", 100)
            task.send_done({"total": 0, "book_count": 0, "libraries": []})
            return

        total = len(books)
        task.send_progress("count", f"找到 {total} 条结果，正在获取馆藏...", 20)

        # 步骤2: 获取每本书的馆藏
        all_holdings = []
        for i, book in enumerate(books):
            title = book.get("title", book.get("u_title", f"第{i+1}项"))
            tablename = book.get("tablename", "bibliosm")
            recordid = book.get("recordid", 0)

            percent = 20 + int((i / total) * 70)
            task.send_progress("fetch", f"获取《{title}》馆藏 ({i+1}/{total})...", percent)

            holdings = []
            if tablename and recordid:
                holdings = fetch_holdings(tablename, recordid)

            all_holdings.append({"title": title, "holdings": holdings})

        # 步骤3: 按图书馆分组
        task.send_progress("group", "正在整理结果...", 95)
        grouped = group_by_library(all_holdings)

        task.send_progress("done", "搜索完成！", 100)
        task.send_done({
            "total": total,
            "book_count": total,
            "libraries": grouped,
        })

    except Exception as e:
        task.send_error(f"搜索出错: {str(e)}")


# ============================================================
#  内嵌 HTML 模板（微信绿主题，移动端优先）
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>深圳图书馆搜索</title>
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="深图搜索">
    <meta name="theme-color" content="#07C160">
    <style>
        :root {
            --primary: #07C160;
            --primary-light: #09DE6C;
            --primary-dark: #06AD56;
            --bg: #EDEDED;
            --card-bg: #FFFFFF;
            --text-primary: #333333;
            --text-secondary: #666666;
            --text-hint: #999999;
            --border: #E5E5E5;
            --danger: #FA5151;
            --shadow: 0 1px 4px rgba(0,0,0,0.06);
            --safe-bottom: env(safe-area-inset-bottom, 0px);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }

        html, body {
            overscroll-behavior-y: contain;
        }

        body {
            background: var(--bg);
            font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei',
                         'Helvetica Neue', Arial, sans-serif;
            color: var(--text-primary);
            line-height: 1.5;
            min-height: 100vh;
            min-height: 100dvh;
            position: relative;
        }

        /* ===== Header ===== */
        .header {
            background: var(--primary);
            color: white;
            padding: 36px 20px 18px;
            text-align: center;
            position: relative;
        }
        @supports (padding-top: env(safe-area-inset-top)) {
            .header {
                padding-top: calc(36px + env(safe-area-inset-top));
            }
        }
        .header h1 {
            font-size: 20px;
            font-weight: 600;
            letter-spacing: 1px;
        }
        .header p {
            font-size: 12px;
            opacity: 0.85;
            margin-top: 4px;
        }
        .exit-btn {
            position: absolute;
            top: calc(36px + env(safe-area-inset-top, 0px) + 6px);
            right: 12px;
            background: rgba(255,255,255,0.2);
            color: white;
            border: none;
            border-radius: 16px;
            padding: 5px 14px;
            font-size: 13px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .exit-btn:active {
            background: rgba(255,255,255,0.4);
        }

        /* ===== Search Bar ===== */
        .search-container {
            background: var(--card-bg);
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .search-bar {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .search-input-wrap {
            flex: 1;
            position: relative;
        }
        .search-input-wrap .icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-hint);
            font-size: 14px;
        }
        .search-input {
            width: 100%;
            padding: 10px 14px 10px 34px;
            border: 1px solid var(--border);
            border-radius: 22px;
            font-size: 16px;
            outline: none;
            background: #F7F7F7;
            transition: border-color 0.2s, background 0.2s;
        }
        .search-input:focus {
            border-color: var(--primary);
            background: white;
        }
        .search-input::placeholder {
            color: var(--text-hint);
        }
        .search-btn {
            padding: 10px 22px;
            background: var(--primary);
            color: white;
            border: none;
            border-radius: 22px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            white-space: nowrap;
        }
        .search-btn:active {
            background: var(--primary-dark);
            transform: scale(0.96);
        }
        .search-btn:disabled {
            background: #A0DFBB;
            cursor: not-allowed;
            transform: none;
        }

        /* ===== Progress ===== */
        .progress-section {
            padding: 12px 16px 8px;
            display: none;
        }
        .progress-section.active {
            display: block;
        }
        .progress-bar-wrap {
            height: 4px;
            background: #D9D9D9;
            border-radius: 2px;
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--primary), var(--primary-light));
            border-radius: 2px;
            transition: width 0.4s ease;
            width: 0%;
        }
        .progress-text {
            font-size: 12px;
            color: var(--text-hint);
            margin-top: 6px;
        }

        /* ===== Results ===== */
        .results {
            padding: 12px 12px 24px;
            padding-bottom: calc(24px + var(--safe-bottom));
        }

        .result-summary {
            font-size: 13px;
            color: var(--text-secondary);
            padding: 4px 4px 10px;
        }
        .result-summary strong {
            color: var(--primary);
            font-weight: 600;
        }

        /* Library Card */
        .library-card {
            background: var(--card-bg);
            border-radius: 10px;
            margin-bottom: 12px;
            overflow: hidden;
            box-shadow: var(--shadow);
        }

        .library-header {
            padding: 14px 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            user-select: none;
            -webkit-user-select: none;
            transition: background 0.15s;
        }
        .library-header:active {
            background: #F8F8F8;
        }

        .library-info {
            flex: 1;
        }
        .library-name {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
        }
        .library-count {
            font-size: 12px;
            color: var(--text-hint);
            margin-top: 2px;
        }
        .library-count em {
            font-style: normal;
            color: var(--primary);
            font-weight: 600;
        }

        .library-arrow {
            color: #C7C7CC;
            font-size: 12px;
            transition: transform 0.25s ease;
            margin-left: 8px;
        }
        .library-arrow.expanded {
            transform: rotate(90deg);
        }

        .library-books {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease;
        }
        .library-books.expanded {
            max-height: 5000px;
        }
        .library-books-inner {
            padding: 0 16px 12px;
        }

        /* Book Item */
        .book-item {
            padding: 12px 0;
            border-top: 1px solid #F0F0F0;
        }
        .book-item:first-child {
            border-top: none;
        }
        .book-title-row {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .book-title {
            font-size: 14px;
            color: var(--text-primary);
            font-weight: 500;
            flex: 1;
        }
        .book-dup {
            font-size: 12px;
            color: var(--primary);
            font-weight: 700;
            background: #E8F8EF;
            padding: 1px 7px;
            border-radius: 10px;
            white-space: nowrap;
        }
        .book-detail {
            font-size: 12px;
            color: var(--text-hint);
            margin-top: 4px;
            line-height: 1.7;
        }
        .book-detail span {
            color: var(--text-secondary);
        }

        /* ===== Empty State ===== */
        .empty-state {
            text-align: center;
            padding: 80px 20px 60px;
        }
        .empty-icon {
            font-size: 56px;
            margin-bottom: 16px;
            filter: grayscale(20%);
        }
        .empty-title {
            font-size: 16px;
            color: var(--text-secondary);
            margin-bottom: 6px;
        }
        .empty-desc {
            font-size: 13px;
            color: var(--text-hint);
        }

        /* ===== Error ===== */
        .error-state {
            text-align: center;
            padding: 60px 20px 40px;
        }
        .error-icon {
            font-size: 48px;
            margin-bottom: 12px;
        }
        .error-msg {
            font-size: 14px;
            color: var(--danger);
            line-height: 1.6;
        }
        .error-retry {
            margin-top: 16px;
            padding: 8px 24px;
            background: var(--primary);
            color: white;
            border: none;
            border-radius: 20px;
            font-size: 14px;
            cursor: pointer;
        }

        /* ===== Loading Dots ===== */
        .loading-dots {
            display: inline-flex;
            gap: 4px;
            margin-left: 4px;
        }
        .loading-dots span {
            width: 4px;
            height: 4px;
            background: var(--primary);
            border-radius: 50%;
            animation: bounce 1.4s infinite ease-in-out both;
        }
        .loading-dots span:nth-child(1) { animation-delay: -0.32s; }
        .loading-dots span:nth-child(2) { animation-delay: -0.16s; }

        @keyframes bounce {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }

        /* ===== Animations ===== */
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .fade-in-up {
            animation: fadeInUp 0.3s ease forwards;
        }

        /* ===== Scrollbar ===== */
        ::-webkit-scrollbar { width: 0; }

        /* ===== Cloud Badge ===== */
        .cloud-badge {
            display: inline-block;
            font-size: 10px;
            background: rgba(255,255,255,0.25);
            padding: 2px 8px;
            border-radius: 8px;
            margin-top: 6px;
            letter-spacing: 0.5px;
        }
    </style>
</head>
<body>

    <div class="header">
        <button class="exit-btn" onclick="confirmExit()">退出</button>
        <h1>📚 深圳图书馆搜索</h1>
        <p>查询图书馆藏分布</p>
        <span class="cloud-badge">☁️ 云端版</span>
    </div>

    <div class="search-container">
        <div class="search-bar">
            <div class="search-input-wrap">
                <span class="icon">🔍</span>
                <input type="text" class="search-input" id="searchInput"
                       placeholder="输入书名搜索..." autocomplete="off" />
            </div>
            <button class="search-btn" id="searchBtn">搜索</button>
        </div>
    </div>

    <div class="progress-section" id="progressSection">
        <div class="progress-bar-wrap">
            <div class="progress-bar-fill" id="progressFill"></div>
        </div>
        <div class="progress-text" id="progressText">准备中...</div>
    </div>

    <div class="results" id="results">
        <div class="empty-state">
            <div class="empty-icon">📖</div>
            <div class="empty-title">输入书名开始搜索</div>
            <div class="empty-desc">搜索深圳图书馆的馆藏分布信息</div>
        </div>
    </div>

    <script>
        const $ = (sel) => document.querySelector(sel);

        const searchInput = $('#searchInput');
        const searchBtn = $('#searchBtn');
        const progressSection = $('#progressSection');
        const progressFill = $('#progressFill');
        const progressText = $('#progressText');
        const resultsEl = $('#results');

        let isSearching = false;
        let currentEventSource = null;

        // ===== 搜索逻辑 =====
        searchBtn.addEventListener('click', doSearch);
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') doSearch();
        });

        async function doSearch() {
            const query = searchInput.value.trim();
            if (!query || isSearching) return;

            isSearching = true;
            searchBtn.disabled = true;
            searchBtn.textContent = '搜索中';
            progressSection.classList.add('active');
            progressFill.style.width = '0%';
            progressText.textContent = '正在启动搜索...';
            resultsEl.innerHTML = '';

            try {
                const resp = await fetch('/search?q=' + encodeURIComponent(query));
                const data = await resp.json();

                if (data.error) {
                    showError(data.error);
                    return;
                }

                const taskId = data.task_id;

                if (currentEventSource) {
                    currentEventSource.close();
                }

                currentEventSource = new EventSource('/stream/' + taskId);

                currentEventSource.onmessage = (event) => {
                    const msg = JSON.parse(event.data);

                    if (msg.type === 'progress') {
                        progressFill.style.width = msg.percent + '%';
                        progressText.innerHTML = msg.message +
                            '<div class="loading-dots"><span></span><span></span><span></span></div>';
                        if (msg.percent >= 100) {
                            progressText.innerHTML = msg.message;
                        }
                    } else if (msg.type === 'done') {
                        currentEventSource.close();
                        currentEventSource = null;
                        showResults(msg.results);
                        resetSearch();
                    } else if (msg.type === 'error') {
                        currentEventSource.close();
                        currentEventSource = null;
                        showError(msg.error);
                        resetSearch();
                    }
                };

                currentEventSource.onerror = () => {
                    currentEventSource.close();
                    currentEventSource = null;
                    showError('连接中断，请重试');
                    resetSearch();
                };

            } catch (err) {
                showError('请求失败: ' + err.message);
                resetSearch();
            }
        }

        function resetSearch() {
            isSearching = false;
            searchBtn.disabled = false;
            searchBtn.textContent = '搜索';
            setTimeout(() => { progressSection.classList.remove('active'); }, 1500);
        }

        function showResults(data) {
            if (!data || !data.libraries || data.libraries.length === 0) {
                resultsEl.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon">📭</div>
                        <div class="empty-title">没有找到相关馆藏</div>
                        <div class="empty-desc">换个关键词试试</div>
                    </div>`;
                return;
            }

            let html = `<div class="result-summary">
                共 <strong>${data.total}</strong> 条搜索结果，
                <strong>${data.book_count}</strong> 本图书，
                分布在 <strong>${data.libraries.length}</strong> 个图书馆
            </div>`;

            data.libraries.forEach((lib, idx) => {
                html += `
                <div class="library-card fade-in-up" style="animation-delay: ${idx * 0.05}s">
                    <div class="library-header" onclick="toggleLibrary(this)">
                        <div class="library-info">
                            <div class="library-name">🏛️ ${escapeHtml(lib.library)}</div>
                            <div class="library-count"><em>${lib.total_types}</em>种 / <em>${lib.total_copies}</em>册</div>
                        </div>
                        <span class="library-arrow">▶</span>
                    </div>
                    <div class="library-books">
                        <div class="library-books-inner">`;

                lib.books.forEach((book) => {
                    const dupTag = book.count > 1
                        ? `<span class="book-dup">×${book.count}</span>`
                        : '';
                    html += `
                        <div class="book-item">
                            <div class="book-title-row">
                                <span class="book-title">${escapeHtml(book.title)}</span>
                                ${dupTag}
                            </div>
                            <div class="book-detail">
                                ${book.call_number ? '索书号: <span>' + escapeHtml(book.call_number) + '</span>' : ''}
                                ${book.call_number && book.location ? ' &nbsp;|&nbsp; ' : ''}
                                ${book.location ? '位置: <span>' + escapeHtml(book.location) + '</span>' : ''}
                            </div>
                        </div>`;
                });

                html += `
                        </div>
                    </div>
                </div>`;
            });

            resultsEl.innerHTML = html;
        }

        function showError(msg) {
            resultsEl.innerHTML = `
                <div class="error-state">
                    <div class="error-icon">⚠️</div>
                    <div class="error-msg">${escapeHtml(msg)}</div>
                    <button class="error-retry" onclick="searchInput.focus()">重新搜索</button>
                </div>`;
        }

        function toggleLibrary(header) {
            const books = header.nextElementSibling;
            const arrow = header.querySelector('.library-arrow');
            books.classList.toggle('expanded');
            arrow.classList.toggle('expanded');
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // ===== 退出按钮 =====
        function confirmExit() {
            if (confirm('确定要退出深图搜索吗？')) {
                fetch('/shutdown', { method: 'POST' })
                    .then(() => {
                        document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#999;font-size:16px;">已退出深图搜索</div>';
                    })
                    .catch(() => {
                        document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#999;font-size:16px;">已退出深图搜索</div>';
                    });
            }
        }

        // 启动
        searchInput.focus();
    </script>
</body>
</html>"""


# ============================================================
#  Web 路由
# ============================================================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/search")
def search():
    """发起搜索"""
    book_name = request.args.get("q", "").strip()
    if not book_name:
        return json.dumps({"error": "请输入书名"}, ensure_ascii=False), 400

    import uuid
    task_id = str(uuid.uuid4())[:8]
    task = SearchTask(task_id, book_name)
    tasks[task_id] = task

    thread = threading.Thread(target=run_search, args=(book_name, task), daemon=True)
    thread.start()

    return json.dumps({"task_id": task_id}, ensure_ascii=False)


@app.route("/stream/<task_id>")
def stream(task_id):
    """SSE 进度流"""
    task = tasks.get(task_id)
    if not task:
        return json.dumps({"error": "任务不存在"}, ensure_ascii=False), 404

    def generate():
        while True:
            try:
                msg = task.progress_queue.get(timeout=30)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                if task.status in ("done", "error"):
                    break
                yield ": keepalive\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/shutdown", methods=["POST"])
def shutdown():
    """关闭服务器（云环境下仅本地有效）"""
    try:
        def do_shutdown():
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGTERM)
        threading.Thread(target=do_shutdown, daemon=True).start()
        return json.dumps({"status": "shutting_down"}, ensure_ascii=False)
    except Exception:
        return json.dumps({"status": "not_supported"}, ensure_ascii=False)


@app.route("/api/status/<task_id>")
def api_status(task_id):
    """轮询接口（小程序用）"""
    task = tasks.get(task_id)
    if not task:
        return json.dumps({"error": "任务不存在"}, ensure_ascii=False), 404

    resp = {"status": task.status, "progress": task.latest_progress}
    if task.status == "done" and task.results is not None:
        resp["results"] = task.results
    elif task.status == "error" and task.error:
        resp["error"] = task.error

    return json.dumps(resp, ensure_ascii=False), 200, {"Content-Type": "application/json"}


# ============================================================
#  启动
# ============================================================
if __name__ == "__main__":
    # 云平台通过环境变量指定端口，本地默认 8080
    port = int(os.environ.get("PORT", 8080))
    print()
    print("=" * 50)
    print("   📚 深圳图书馆搜索系统（云端版）")
    print("   单文件部署，无需额外静态资源")
    print(f"   访问地址: http://0.0.0.0:{port}")
    print("=" * 50)
    print()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
