#!/usr/bin/env python3
"""
1NIWA 物件自動収集ツール
テンポスマート・Tempodasから物件を自動収集し、用途地域チェッカーで判定する。

使い方:
  python3 auto_search.py                        # 全サイトから収集+判定
  python3 auto_search.py --site temposmart      # テンポスマートのみ
  python3 auto_search.py --site tempodas        # Tempodasのみ
  python3 auto_search.py --no-zoning            # 用途地域判定なし（収集のみ）
  python3 auto_search.py --output result.csv    # CSV出力
  python3 auto_search.py --pages 3              # 最大3ページ取得
"""

import argparse
import csv
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# 用途地域チェッカーのインポート準備
# ローカル環境: ../zoning-checker/  クラウド環境: 同一ディレクトリ
ZONING_CHECKER_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "zoning-checker"
)
if os.path.isdir(ZONING_CHECKER_DIR):
    sys.path.insert(0, ZONING_CHECKER_DIR)
else:
    # 同一ディレクトリにある場合（クラウド版）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ===== 定数 =====

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}

# 東京23区リスト
TOKYO_23KU = [
    "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区",
    "江東区", "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区",
    "杉並区", "豊島区", "北区", "荒川区", "板橋区", "練馬区", "足立区",
    "葛飾区", "江戸川区",
]

# テンポスマート
TEMPOSMART_BASE = "https://www.temposmart.jp"
TEMPOSMART_LOGIN_URL = "https://www.temposmart.jp/login"
TEMPOSMART_LIST_URL = "https://www.temposmart.jp/estates/pref/13/newest"  # 東京都

# テンポダス
TEMPODAS_BASE = "https://tempodas.com"

# 検索済み物件キャッシュファイル
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "searched_cache.json")

REQUEST_INTERVAL = 1.5  # リクエスト間隔（秒）


# ===== キャッシュ管理 =====

def load_cache() -> set:
    """検索済み物件URLのキャッシュを読み込む（GitHub API → ローカルファイル）"""
    # まずGitHub APIから読み込み
    try:
        from github_storage import read_json
        data = read_json("data/searched_cache.json")
        if data:
            return set(data.get("searched_urls", []))
    except Exception:
        pass

    # ローカルファイルフォールバック
    if os.path.isfile(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("searched_urls", []))
        except Exception:
            pass
    return set()


def save_cache(cached_urls: set):
    """検索済み物件URLをキャッシュに保存（GitHub API + ローカルファイル）"""
    cache_data = {
        "searched_urls": list(cached_urls),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # GitHub APIに保存
    try:
        from github_storage import write_json
        write_json("data/searched_cache.json", cache_data, "auto: update search cache")
    except Exception:
        pass

    # ローカルファイルにも保存
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def filter_new_properties(properties: list[dict], cached_urls: set) -> tuple[list[dict], int]:
    """キャッシュ済みの物件を除外し、新着のみ返す"""
    new_props = []
    skipped = 0
    for p in properties:
        url = p.get("url", "").split("?")[0]  # クエリパラメータ除去
        if url and url in cached_urls:
            skipped += 1
        else:
            new_props.append(p)
    return new_props, skipped


# ===== HTTP取得 =====

def fetch_url(url: str, is_json: bool = False) -> Optional[str | dict]:
    """URLからコンテンツを取得する。"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8")
            if is_json:
                return json.loads(data)
            return data
    except urllib.error.HTTPError as e:
        print(f"  HTTPエラー {e.code}: {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  取得エラー: {e} ({url})", file=sys.stderr)
        return None


# ===== 認証情報取得 =====

def _get_credential(key: str) -> str:
    """認証情報を取得（Streamlit secrets → 環境変数）"""
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


# ===== テンポスマート スクレイパー =====

def _clean_address(addr: str) -> str:
    """住所文字列をクリーンアップする。"""
    addr = re.sub(r"[💰🔥🏠✨📍\s]+.*$", "", addr)
    addr = addr.rstrip("-－")
    return addr.strip()


def _create_temposmart_session() -> urllib.request.OpenerDirector:
    """テンポスマートにログインしてセッション付きOpenerを返す。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    email = _get_credential("TEMPOSMART_EMAIL")
    password = _get_credential("TEMPOSMART_PASSWORD")

    if not email or not password:
        print("  警告: テンポスマートの認証情報が設定されていません。", file=sys.stderr)
        print("  環境変数 TEMPOSMART_EMAIL / TEMPOSMART_PASSWORD を設定してください。", file=sys.stderr)
        return opener

    # ログインページを取得（CSRFトークン取得）
    try:
        req = urllib.request.Request(TEMPOSMART_LOGIN_URL, headers=HEADERS)
        with opener.open(req, timeout=20) as resp:
            login_html = resp.read().decode("utf-8")

        # CSRFトークンを抽出
        csrf_token = ""
        if BeautifulSoup:
            soup = BeautifulSoup(login_html, "html.parser")
            token_input = soup.find("input", {"name": "_token"})
            if token_input:
                csrf_token = token_input.get("value", "")
        if not csrf_token:
            match = re.search(r'name="_token"\s+value="([^"]+)"', login_html)
            if match:
                csrf_token = match.group(1)

        # ログインPOST
        login_data = urllib.parse.urlencode({
            "_token": csrf_token,
            "email": email,
            "password": password,
        }).encode("utf-8")

        req = urllib.request.Request(
            TEMPOSMART_LOGIN_URL,
            data=login_data,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": TEMPOSMART_LOGIN_URL},
        )
        with opener.open(req, timeout=20) as resp:
            resp.read()

        print("  テンポスマート: ログイン成功")
    except Exception as e:
        print(f"  テンポスマート: ログイン失敗 ({e})", file=sys.stderr)

    return opener


def _fetch_with_session(opener: urllib.request.OpenerDirector, url: str) -> Optional[str]:
    """セッション付きでURLを取得する。"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with opener.open(req, timeout=20) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  取得エラー: {e} ({url})", file=sys.stderr)
        return None


def _extract_address_from_temposmart_detail(opener: urllib.request.OpenerDirector, url: str) -> tuple[Optional[str], str]:
    """テンポスマートの物件詳細ページから住所と賃料を抽出する。"""
    time.sleep(REQUEST_INTERVAL)
    html = _fetch_with_session(opener, url)
    if not html:
        return None, ""

    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text()

    # 住所抽出
    address = None
    # 「所在地」「住所」ラベルから抽出
    addr_match = re.search(
        r"(?:所在地|住所|物件所在地)[：:\s]*(?:〒[\d-]+\s*)?"
        r"(東京都[\w]+区[\w\d丁目番号\-－]*)",
        body_text,
    )
    if addr_match:
        address = _clean_address(addr_match.group(1))
    else:
        # 「東京都○○区...」の一般的なパターン
        ku_pattern = "|".join(TOKYO_23KU)
        addr_match = re.search(
            r"(東京都(?:" + ku_pattern + r")[\w\d丁目番号\-－]*)",
            body_text,
        )
        if addr_match:
            address = _clean_address(addr_match.group(1))

    # 賃料抽出
    price = ""
    price_match = re.search(r"(?:賃料|月額|家賃)[：:\s]*([\d,]+(?:\.\d+)?万?円)", body_text)
    if price_match:
        price = price_match.group(1)
    else:
        price_match = re.search(r"([\d,]+(?:\.\d+)?万?円)", body_text)
        if price_match:
            price = price_match.group(1)

    return address, price


def search_temposmart(max_pages: int = 2, fetch_detail: bool = True) -> list[dict]:
    """
    テンポスマートから東京23区の物件を収集。

    Args:
        max_pages: 取得する最大ページ数
        fetch_detail: 詳細ページから住所・賃料を取得するか

    Returns:
        [{"source": ..., "title": ..., "address": ..., "price": ..., "url": ..., "area": ...}, ...]
    """
    if not BeautifulSoup:
        print("  エラー: BeautifulSoupが必要です。pip install beautifulsoup4", file=sys.stderr)
        return []

    # ログインセッション作成
    opener = _create_temposmart_session()
    all_properties = []

    for page in range(1, max_pages + 1):
        url = f"{TEMPOSMART_LIST_URL}?page={page}"
        print(f"    ページ {page}: {url}")
        time.sleep(REQUEST_INTERVAL)

        html = _fetch_with_session(opener, url)
        if not html:
            print(f"    ページ取得失敗。")
            break

        soup = BeautifulSoup(html, "html.parser")

        # 物件リンクを抽出（/estates/数字 パターン）
        pattern = re.compile(r"/estates/(\d+)")
        links = soup.find_all("a", href=pattern)

        url_to_info = {}
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            match = pattern.search(href)
            if not match:
                continue
            estate_id = match.group(1)
            full_url = f"{TEMPOSMART_BASE}/estates/{estate_id}"
            text = link.get_text(strip=True)
            if full_url not in url_to_info or (text and len(text) > len(url_to_info[full_url].get("title", ""))):
                url_to_info[full_url] = {"url": full_url, "title": text, "id": estate_id}

        if not url_to_info:
            print(f"    物件なし。")
            break

        for full_url, info in url_to_info.items():
            title = info.get("title", "") or f"物件ID: {info['id']}"
            if len(title) > 200:
                title = title[:200]

            # 区名抽出（タイトルから）
            area = ""
            for ku in TOKYO_23KU:
                if ku in title:
                    area = ku
                    break

            # 価格抽出（タイトルから）
            price = ""
            price_match = re.search(r"([\d,]+(?:\.\d+)?万?円)", title)
            if price_match:
                price = price_match.group(1)

            prop = {
                "source": "テンポスマート",
                "category": "店舗・テナント",
                "title": title[:100],
                "price": price,
                "area": area,
                "address": "",
                "url": full_url,
            }
            all_properties.append(prop)

        print(f"    {len(url_to_info)}件取得")

        # 次のページがあるか
        if f"?page={page + 1}" not in html:
            break

    # 詳細ページから住所・賃料取得
    if fetch_detail and all_properties:
        print(f"\n  詳細ページから住所・賃料を取得中...")
        for i, p in enumerate(all_properties):
            print(f"    [{i+1}/{len(all_properties)}] {p['title'][:30]}...")
            addr, price = _extract_address_from_temposmart_detail(opener, p["url"])
            if addr:
                p["address"] = addr
                for ku in TOKYO_23KU:
                    if ku in addr:
                        p["area"] = ku
                        break
                print(f"      -> 住所: {addr}")
            else:
                print(f"      -> 住所取得不可")
            if price and not p["price"]:
                p["price"] = price

    # 23区外を除外
    result = []
    for p in all_properties:
        if p["address"]:
            is_23ku = any(ku in p["address"] for ku in TOKYO_23KU)
            if not is_23ku:
                continue
        result.append(p)

    return result


# ===== テンポダス スクレイパー =====

def _create_tempodas_session() -> urllib.request.OpenerDirector:
    """テンポダスにログインしてセッション付きOpenerを返す。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPRedirectHandler(),
    )

    email = _get_credential("TEMPODAS_EMAIL")
    password = _get_credential("TEMPODAS_PASSWORD")

    if not email or not password:
        print("  警告: テンポダスの認証情報が設定されていません。", file=sys.stderr)
        print("  環境変数 TEMPODAS_EMAIL / TEMPODAS_PASSWORD を設定してください。", file=sys.stderr)
        return opener

    # ログインページを取得
    login_url = f"{TEMPODAS_BASE}/login"
    try:
        req = urllib.request.Request(login_url, headers=HEADERS)
        with opener.open(req, timeout=20) as resp:
            login_html = resp.read().decode("utf-8")

        # CSRFトークンを抽出
        csrf_token = ""
        token_match = re.search(r'name="[^"]*token[^"]*"\s+value="([^"]+)"', login_html, re.IGNORECASE)
        if token_match:
            csrf_token = token_match.group(1)
        else:
            token_match = re.search(r'value="([^"]+)"\s+name="[^"]*token[^"]*"', login_html, re.IGNORECASE)
            if token_match:
                csrf_token = token_match.group(1)

        # ログインPOST
        login_data = urllib.parse.urlencode({
            "_token": csrf_token,
            "email": email,
            "password": password,
        }).encode("utf-8")

        req = urllib.request.Request(
            login_url,
            data=login_data,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": login_url},
        )
        with opener.open(req, timeout=20) as resp:
            resp.read()

        print("  テンポダス: ログイン成功")
    except Exception as e:
        print(f"  テンポダス: ログイン失敗 ({e})", file=sys.stderr)
        print("  テンポダスはJavaScript SPAのため、自動収集が制限される場合があります。", file=sys.stderr)

    return opener


def search_tempodas(max_pages: int = 2) -> list[dict]:
    """
    テンポダスから東京23区の物件を収集。

    テンポダスはJavaScript SPAのため、サーバーサイドでレンダリングされた
    コンテンツが限られる場合があります。

    Args:
        max_pages: 取得する最大ページ数

    Returns:
        [{"source": ..., "title": ..., "address": ..., "price": ..., "url": ..., "area": ...}, ...]
    """
    if not BeautifulSoup:
        print("  エラー: BeautifulSoupが必要です。pip install beautifulsoup4", file=sys.stderr)
        return []

    opener = _create_tempodas_session()
    all_properties = []

    # テンポダスの物件ページURL候補を試行
    search_urls = [
        f"{TEMPODAS_BASE}/estates",
        f"{TEMPODAS_BASE}/estates/pref/13",
        f"{TEMPODAS_BASE}/search?prefecture=13",
        f"{TEMPODAS_BASE}/properties",
        f"{TEMPODAS_BASE}/properties?area=tokyo",
    ]

    html = None
    working_url = None
    for try_url in search_urls:
        time.sleep(REQUEST_INTERVAL)
        print(f"    試行: {try_url}")
        test_html = _fetch_with_session(opener, try_url)
        if test_html and len(test_html) > 1000:
            # 物件情報が含まれているか確認
            if any(ku in test_html for ku in TOKYO_23KU) or "物件" in test_html or "estate" in test_html.lower():
                html = test_html
                working_url = try_url
                print(f"    -> コンテンツ取得成功")
                break

    if not html:
        print("  テンポダス: 物件ページの自動取得ができませんでした。", file=sys.stderr)
        print("  テンポダスはJavaScript SPAのため、手動検索をご利用ください。", file=sys.stderr)
        print(f"  手動検索URL: {TEMPODAS_BASE}", file=sys.stderr)
        return []

    # HTMLから物件情報を抽出
    soup = BeautifulSoup(html, "html.parser")

    # 物件リンクを探す（一般的なパターン）
    link_patterns = [
        re.compile(r"/estates?/\d+"),
        re.compile(r"/properties?/\d+"),
        re.compile(r"/detail/\d+"),
    ]

    url_to_info = {}
    for pat in link_patterns:
        links = soup.find_all("a", href=pat)
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            full_url = href if href.startswith("http") else TEMPODAS_BASE + href
            base_url = full_url.split("?")[0]
            text = link.get_text(strip=True)
            if base_url not in url_to_info or (text and len(text) > len(url_to_info[base_url].get("title", ""))):
                url_to_info[base_url] = {"url": full_url, "title": text}

    for full_url, info in url_to_info.items():
        title = info.get("title", "") or full_url.split("/")[-1]
        if len(title) > 200:
            title = title[:200]

        area = ""
        for ku in TOKYO_23KU:
            if ku in title:
                area = ku
                break

        price = ""
        price_match = re.search(r"([\d,]+(?:\.\d+)?万?円)", title)
        if price_match:
            price = price_match.group(1)

        # 住所抽出（テキストから）
        address = ""
        ku_pattern = "|".join(TOKYO_23KU)
        addr_match = re.search(
            r"(東京都(?:" + ku_pattern + r")[\w\d丁目番号\-－]*)",
            title,
        )
        if addr_match:
            address = _clean_address(addr_match.group(1))

        prop = {
            "source": "テンポダス",
            "category": "店舗・テナント",
            "title": title[:100],
            "price": price,
            "area": area,
            "address": address,
            "url": full_url,
        }
        all_properties.append(prop)

    if all_properties:
        print(f"    {len(all_properties)}件取得")
    else:
        print("  テンポダス: 物件の自動抽出ができませんでした（JavaScript SPA）。", file=sys.stderr)
        print(f"  手動検索をご利用ください: {TEMPODAS_BASE}", file=sys.stderr)

    return all_properties


# ===== 用途地域チェック連携 =====

def check_zoning_batch(properties: list[dict]) -> list[dict]:
    """
    住所が取得できた物件を用途地域チェッカーで一括判定する。

    各物件dictに以下のキーを追加:
      - youto_chiiki: 用途地域名
      - ryokan_kahi: 旅館業可否（○△×）
      - sogo_hantei: 総合判定
      - sogo_detail: 詳細
      - school_warning: 学校距離警告
    """
    # 住所ありの物件を抽出
    props_with_addr = [p for p in properties if p.get("address")]

    if not props_with_addr:
        print("\n住所が取得できた物件がありません。用途地域判定をスキップします。")
        return properties

    print(f"\n{'=' * 60}")
    print(f"用途地域判定: {len(props_with_addr)}件")
    print(f"{'=' * 60}")

    try:
        from zoning_checker import (
            load_zoning_data,
            load_school_data,
            load_chiku_keikaku_data,
            load_tokubetsu_youto_data,
            check_zoning,
        )
    except ImportError:
        print("警告: 用途地域チェッカーが見つかりません。判定をスキップします。")
        print(f"  検索パス: {ZONING_CHECKER_DIR}")
        return properties

    print("GISデータ読み込み中...")
    gdf = load_zoning_data()
    school_gdf = load_school_data()
    chiku_gdf = load_chiku_keikaku_data()
    tokubetsu_gdf = load_tokubetsu_youto_data()

    for i, prop in enumerate(props_with_addr):
        addr = prop["address"]
        print(f"\n  [{i + 1}/{len(props_with_addr)}] {addr}")

        result = check_zoning(addr, gdf, school_gdf, chiku_gdf, tokubetsu_gdf)

        prop["youto_chiiki"] = result.youto_chiiki or ""
        prop["ryokan_kahi"] = result.ryokan_kahi or ""
        prop["sogo_hantei"] = result.sogo_hantei or ""
        prop["sogo_detail"] = result.sogo_detail or result.error or ""
        prop["school_warning"] = result.school_warning or ""
        prop["tokubetsu_youto"] = result.tokubetsu_youto or ""

        # 判定結果を表示
        kahi = result.ryokan_kahi or "?"
        youto = result.youto_chiiki or "不明"
        sogo = result.sogo_hantei or "?"
        print(f"    用途地域: {youto} | 旅館業: {kahi} | 総合: {sogo}")

        if i < len(props_with_addr) - 1:
            time.sleep(0.5)

    return properties


# ===== 結果表示・出力 =====

def print_results(properties: list[dict]):
    """結果をコンソールに表示する。"""
    print(f"\n{'=' * 70}")
    print(f"収集結果: 全{len(properties)}件")
    print(f"{'=' * 70}")

    # 住所あり・なしで分類
    with_addr = [p for p in properties if p.get("address")]
    without_addr = [p for p in properties if not p.get("address")]

    # 旅館業可能物件をハイライト
    ok_props = [p for p in with_addr if p.get("ryokan_kahi") in ("○",)]
    cond_props = [p for p in with_addr if p.get("ryokan_kahi") in ("△",)]
    ng_props = [p for p in with_addr if p.get("ryokan_kahi") in ("×",)]
    unknown_props = [p for p in with_addr if p.get("ryokan_kahi") not in ("○", "△", "×")]

    if ok_props:
        print(f"\n--- 旅館業 営業可能 ({len(ok_props)}件) ---")
        for p in ok_props:
            _print_property(p)

    if cond_props:
        print(f"\n--- 旅館業 条件付き可能 ({len(cond_props)}件) ---")
        for p in cond_props:
            _print_property(p)

    if ng_props:
        print(f"\n--- 旅館業 不可 ({len(ng_props)}件) ---")
        for p in ng_props:
            _print_property(p)

    if unknown_props:
        print(f"\n--- 判定未実施 ({len(unknown_props)}件) ---")
        for p in unknown_props:
            _print_property(p)

    if without_addr:
        print(f"\n--- 住所未取得（手動確認必要） ({len(without_addr)}件) ---")
        for p in without_addr:
            _print_property(p)

    print(f"\n{'=' * 70}")
    print(f"サマリー:")
    print(f"  全件数: {len(properties)}")
    print(f"  住所取得済み: {len(with_addr)}")
    print(f"  旅館業可能: {len(ok_props)}  条件付き: {len(cond_props)}  不可: {len(ng_props)}")
    print(f"  住所未取得: {len(without_addr)}（手動確認が必要）")
    print(f"{'=' * 70}")


def _print_property(p: dict):
    """1物件の情報を表示。"""
    print(f"\n  [{p.get('source', '')}] {p.get('title', '')[:60]}")
    if p.get("price"):
        print(f"    価格: {p['price']}")
    if p.get("address"):
        print(f"    住所: {p['address']}")
    elif p.get("area"):
        print(f"    エリア: {p['area']}")
    if p.get("youto_chiiki"):
        kahi = p.get("ryokan_kahi", "?")
        sogo = p.get("sogo_hantei", "?")
        print(f"    用途地域: {p['youto_chiiki']} | 旅館業: {kahi} | 総合: {sogo}")
    if p.get("sogo_detail"):
        print(f"    詳細: {p['sogo_detail'][:80]}")
    if p.get("school_warning"):
        print(f"    学校警告: {p['school_warning'][:80]}")
    print(f"    URL: {p.get('url', '')}")


def append_csv(properties: list[dict], output_path: str):
    """結果をCSVファイルに追記する（ヘッダーはファイルが新規の場合のみ）。GitHub APIにも保存。"""
    import io as _io

    csv_header = [
        "検索日", "ソース", "カテゴリ", "タイトル", "価格", "エリア", "住所",
        "用途地域", "旅館業可否", "総合判定", "詳細",
        "特別用途地区", "学校警告", "URL",
    ]

    # ローカルファイルに追記
    file_exists = os.path.isfile(output_path)
    with open(output_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(csv_header)
        for p in properties:
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d"),
                p.get("source", ""),
                p.get("category", ""),
                p.get("title", ""),
                p.get("price", ""),
                p.get("area", ""),
                p.get("address", ""),
                p.get("youto_chiiki", ""),
                p.get("ryokan_kahi", ""),
                p.get("sogo_hantei", ""),
                p.get("sogo_detail", ""),
                p.get("tokubetsu_youto", ""),
                p.get("school_warning", ""),
                p.get("url", ""),
            ])
    print(f"  → {len(properties)}件を追記: {output_path}")

    # GitHub APIにも保存
    try:
        from github_storage import read_file, write_file
        existing_csv = read_file("data/bukken_history.csv") or ""
        buf = _io.StringIO()
        writer = csv.writer(buf)
        if not existing_csv:
            writer.writerow(csv_header)
        for p in properties:
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d"),
                p.get("source", ""),
                p.get("category", ""),
                p.get("title", ""),
                p.get("price", ""),
                p.get("area", ""),
                p.get("address", ""),
                p.get("youto_chiiki", ""),
                p.get("ryokan_kahi", ""),
                p.get("sogo_hantei", ""),
                p.get("sogo_detail", ""),
                p.get("tokubetsu_youto", ""),
                p.get("school_warning", ""),
                p.get("url", ""),
            ])
        content = existing_csv.rstrip("\n") + "\n" + buf.getvalue() if existing_csv else buf.getvalue()
        write_file("data/bukken_history.csv", content, f"auto: append {len(properties)} properties {datetime.now().strftime('%Y-%m-%d')}")
    except Exception:
        pass


def write_csv(properties: list[dict], output_path: str):
    """結果をCSVファイルに出力する。"""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ソース", "カテゴリ", "タイトル", "価格", "エリア", "住所",
            "用途地域", "旅館業可否", "総合判定", "詳細",
            "特別用途地区", "学校警告", "URL",
        ])
        for p in properties:
            writer.writerow([
                p.get("source", ""),
                p.get("category", ""),
                p.get("title", ""),
                p.get("price", ""),
                p.get("area", ""),
                p.get("address", ""),
                p.get("youto_chiiki", ""),
                p.get("ryokan_kahi", ""),
                p.get("sogo_hantei", ""),
                p.get("sogo_detail", ""),
                p.get("tokubetsu_youto", ""),
                p.get("school_warning", ""),
                p.get("url", ""),
            ])
    print(f"\nCSV出力: {output_path}")


# ===== メイン =====

def main():
    parser = argparse.ArgumentParser(
        description="1NIWA 物件自動収集ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python3 auto_search.py                        # 全サイトから収集+判定
  python3 auto_search.py --site temposmart      # テンポスマートのみ
  python3 auto_search.py --site tempodas        # テンポダスのみ
  python3 auto_search.py --no-zoning            # 用途地域判定なし（収集のみ）
  python3 auto_search.py --output result.csv    # CSV出力
  python3 auto_search.py --pages 3              # 最大3ページ取得
  python3 auto_search.py --no-detail            # 詳細ページ取得スキップ（高速）
        """,
    )
    parser.add_argument(
        "--site", choices=["temposmart", "tempodas", "all"], default="all",
        help="取得するサイト（default: all）",
    )
    parser.add_argument(
        "--output", "-o", help="結果のCSV出力先ファイルパス",
    )
    parser.add_argument(
        "--pages", type=int, default=2, help="取得する最大ページ数（default: 2）",
    )
    parser.add_argument(
        "--no-zoning", action="store_true", help="用途地域判定をスキップ",
    )
    parser.add_argument(
        "--no-detail", action="store_true",
        help="テンポスマートの詳細ページ取得をスキップ（高速モード）",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("1NIWA 物件自動収集ツール")
    print(f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"対象: {args.site} | ページ数: {args.pages}")
    print("=" * 60)

    # キャッシュ読み込み
    cached_urls = load_cache()
    print(f"検索済みキャッシュ: {len(cached_urls)}件")

    all_properties = []

    # テンポスマート
    if args.site in ("temposmart", "all"):
        print(f"\n{'─' * 40}")
        print("テンポスマート 東京都（店舗・テナント）")
        print(f"{'─' * 40}")
        props = search_temposmart(
            max_pages=args.pages,
            fetch_detail=not args.no_detail,
        )
        # キャッシュで新着のみフィルタ
        props, skipped = filter_new_properties(props, cached_urls)
        print(f"\nテンポスマート: {len(props)}件（新着） / {skipped}件スキップ（検索済み）")
        all_properties.extend(props)

    # テンポダス
    if args.site in ("tempodas", "all"):
        print(f"\n{'─' * 40}")
        print("テンポダス 東京都（店舗・テナント）")
        print(f"{'─' * 40}")
        props = search_tempodas(max_pages=args.pages)
        # キャッシュで新着のみフィルタ
        props, skipped = filter_new_properties(props, cached_urls)
        print(f"\nテンポダス: {len(props)}件（新着） / {skipped}件スキップ（検索済み）")
        all_properties.extend(props)

    if not all_properties:
        print("\n新着物件が見つかりませんでした（全て検索済み）。")
        sys.exit(0)

    # 用途地域チェック
    if not args.no_zoning:
        all_properties = check_zoning_batch(all_properties)

    # キャッシュ更新（検索した物件URLを追加）
    for p in all_properties:
        url = p.get("url", "").split("?")[0]
        if url:
            cached_urls.add(url)
    save_cache(cached_urls)
    print(f"\nキャッシュ更新: {len(cached_urls)}件")

    # 結果表示
    print_results(all_properties)

    # CSV出力（累積保存）
    if args.output:
        write_csv(all_properties, args.output)
    else:
        # デフォルトで累積CSVに追記
        default_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bukken_history.csv")
        os.makedirs(os.path.dirname(default_csv), exist_ok=True)
        append_csv(all_properties, default_csv)
        print(f"\n累積CSV: {default_csv}")


if __name__ == "__main__":
    main()
