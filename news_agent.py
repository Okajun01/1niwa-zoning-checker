#!/usr/bin/env python3
"""
宿泊業界ニュース自動収集スクリプト（GitHub Actions用）。

Google News RSSから旅館業・宿泊業界の最新ニュースを収集し、
Gemini APIで要約・影響分析を生成した後、
GitHub APIで data/news_history.json を更新する。

使い方:
  GITHUB_TOKEN=xxx python news_agent.py          # 通常実行
  GITHUB_TOKEN=xxx python news_agent.py --dry-run # 保存せずプレビュー

環境変数:
  GITHUB_TOKEN       - GitHub APIトークン（必須）
  GEMINI_API_KEY     - Google Gemini APIキー（任意: なければ要約・影響は空）
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html import unescape

# === 設定 ===

GITHUB_REPO = "Okajun01/1niwa-zoning-checker"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
NEWS_FILE = "data/news_history.json"
MAX_ARTICLES = 500

# カテゴリ定義と検索クエリ
CATEGORY_QUERIES = {
    "旅館業法・条例改正": [
        "旅館業法 改正",
        "旅館業 条例",
        "宿泊施設 規制",
    ],
    "民泊・住宅宿泊事業": [
        "民泊 規制",
        "住宅宿泊事業法",
        "民泊新法",
    ],
    "観光・インバウンド": [
        "インバウンド 宿泊",
        "訪日外国人 観光",
        "観光立国",
    ],
    "補助金・助成金": [
        "宿泊業 補助金",
        "観光 助成金",
        "旅館 補助事業",
    ],
}

# 重要度判定キーワード
HIGH_KEYWORDS = [
    "旅館業法", "条例改正", "規制強化", "許可", "届出",
    "東京都", "23区", "補助金", "助成金", "公募",
    "簡易宿所", "違法民泊", "罰則",
]
LOW_KEYWORDS = [
    "海外", "地方", "北海道", "沖縄", "九州", "四国",
]

# Google News RSS URL
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"


# === GitHub API ===

def github_read(path: str) -> tuple[str | None, str | None]:
    """GitHubからファイルを読み込む。(content, sha) を返す。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("エラー: GITHUB_TOKEN が設定されていません")
        sys.exit(1)

    url = f"{GITHUB_API}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    except Exception as e:
        print(f"GitHub読み込みエラー: {e}")
        return None, None


def github_write(path: str, content: str, sha: str | None, message: str) -> bool:
    """GitHubにファイルを書き込む。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return False

    url = f"{GITHUB_API}/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True
    except Exception as e:
        print(f"GitHub書き込みエラー: {e}")
        return False


# === ニュース収集 ===

def fetch_google_news_rss(query: str, max_items: int = 5) -> list[dict]:
    """Google News RSSから記事を取得する。"""
    encoded_query = urllib.request.quote(query)
    url = GOOGLE_NEWS_RSS.format(query=encoded_query)

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; 1NIWA-NewsBot/1.0)",
    })

    articles = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)

            for item in root.findall(".//item")[:max_items]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                source_elem = item.find("source")
                source = source_elem.text if source_elem is not None else ""

                # タイトルからHTMLエンティティを除去
                title = unescape(title)
                # Google Newsのタイトルは「タイトル - ソース名」形式
                if " - " in title and source:
                    title = title.rsplit(" - ", 1)[0].strip()

                # 日付をパース
                date_str = _parse_rss_date(pub_date)

                articles.append({
                    "title": title,
                    "url": link,
                    "date": date_str,
                    "source": source,
                })
    except Exception as e:
        print(f"  RSS取得エラー ({query}): {e}")

    return articles


def _parse_rss_date(date_str: str) -> str:
    """RSS日付文字列をYYYY-MM-DD形式に変換する。"""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        # RFC 2822 形式: "Mon, 24 Mar 2026 07:00:00 GMT"
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# === AI要約・影響分析 ===

def generate_ai_analysis(articles: list[dict]) -> list[dict]:
    """Gemini APIで記事の要約と1NIWAへの影響を一括生成する。"""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("⚠️  GEMINI_API_KEY未設定: 要約・影響分析をスキップ")
        return articles

    # 分析対象の記事を抽出（要約が空のもの）
    targets = [i for i, a in enumerate(articles) if not a.get("summary")]
    if not targets:
        return articles

    print(f"\n🤖 Gemini APIで {len(targets)}件 の要約・影響分析を生成中...")

    # バッチで処理（全記事を1回のAPI呼び出しで）
    batch_items = []
    for idx in targets:
        a = articles[idx]
        batch_items.append(f"記事{idx}: タイトル「{a['title']}」 カテゴリ: {a['category']} 出典: {a['source']} 日付: {a['date']}")

    prompt = f"""以下の宿泊業界ニュース記事について、それぞれ「要約」と「1NIWAへの影響」を生成してください。

【1NIWAについて】
東京都23区で旅館業法に基づく簡易宿所の許可取得を目指すスタートアップ企業。現在は物件探し段階。

【出力形式】
各記事について以下のJSON配列で出力してください。他の文章は不要です。
[
  {{"idx": 記事番号, "summary": "1-2文の要約", "impact": "1NIWAへの具体的な影響（1-2文）"}}
]

【記事一覧】
{chr(10).join(batch_items)}"""

    try:
        req_body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        })
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        req = urllib.request.Request(
            gemini_url,
            data=req_body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            text = result["candidates"][0]["content"]["parts"][0]["text"]

        # JSONを抽出（```json ... ``` で囲まれている場合も対応）
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        analyses = json.loads(text.strip())

        for item in analyses:
            idx = item["idx"]
            if 0 <= idx < len(articles):
                articles[idx]["summary"] = item.get("summary", "")
                articles[idx]["impact_memo"] = item.get("impact", "")
        print(f"  ✅ {len(analyses)}件の分析を生成しました")

    except Exception as e:
        print(f"  ⚠️  AI分析エラー（記事収集は続行）: {e}")

    return articles


def classify_importance(title: str, summary: str = "") -> str:
    """タイトルと要約から重要度を判定する。"""
    text = f"{title} {summary}".lower()
    high_count = sum(1 for kw in HIGH_KEYWORDS if kw.lower() in text)
    low_count = sum(1 for kw in LOW_KEYWORDS if kw.lower() in text)

    if high_count >= 2:
        return "高"
    elif high_count >= 1 and low_count == 0:
        return "高"
    elif low_count >= 2:
        return "低"
    return "中"


def collect_news() -> list[dict]:
    """全カテゴリのニュースを収集する。"""
    all_articles = []
    seen_urls = set()
    # 1週間以内の記事のみ
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    for category, queries in CATEGORY_QUERIES.items():
        print(f"\n📂 カテゴリ: {category}")
        for query in queries:
            print(f"  🔍 検索: {query}")
            items = fetch_google_news_rss(query, max_items=3)
            for item in items:
                url = item["url"]
                if url in seen_urls:
                    continue
                if item["date"] < cutoff:
                    continue
                seen_urls.add(url)

                article = {
                    "date": item["date"],
                    "title": item["title"],
                    "summary": "",  # RSSには要約がないため空
                    "url": url,
                    "category": category,
                    "importance": classify_importance(item["title"]),
                    "source": item["source"],
                    "impact_memo": "",
                }
                all_articles.append(article)
                print(f"    ✅ {item['title'][:50]}...")

    return all_articles


# === メイン処理 ===

def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("📰 1NIWA 宿泊業界ニュース自動収集")
    print(f"   実行時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   モード: {'ドライラン（保存なし）' if dry_run else '本番実行'}")
    print("=" * 60)

    # 1. 新しいニュースを収集
    new_articles = collect_news()
    print(f"\n📊 収集結果: {len(new_articles)}件")

    if not new_articles:
        print("新しい記事はありませんでした。")
        return

    # AI要約・影響分析を生成
    new_articles = generate_ai_analysis(new_articles)

    if dry_run:
        print("\n--- ドライラン結果 ---")
        for art in new_articles:
            print(f"  [{art['importance']}] [{art['category']}] {art['title']}")
        return

    # 2. 既存データを読み込み
    print("\n📥 GitHubから既存データを読み込み中...")
    content, sha = github_read(NEWS_FILE)
    if content:
        existing = json.loads(content)
    else:
        existing = {"last_updated": "", "articles": []}
        sha = None

    existing_urls = {a.get("url", "") for a in existing["articles"]}

    # 3. 重複を除外して追加
    added = 0
    for article in new_articles:
        if article["url"] not in existing_urls:
            existing["articles"].insert(0, article)
            existing_urls.add(article["url"])
            added += 1

    if added == 0:
        print("重複を除外した結果、追加する新規記事はありませんでした。")
        return

    # 4. 件数制限
    if len(existing["articles"]) > MAX_ARTICLES:
        existing["articles"] = existing["articles"][:MAX_ARTICLES]

    # 5. 更新日時を設定
    existing["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 6. GitHubに保存
    print(f"\n📤 GitHubに保存中... ({added}件追加)")
    new_content = json.dumps(existing, ensure_ascii=False, indent=2)
    message = f"auto: news update {datetime.now().strftime('%Y-%m-%d %H:%M')} (+{added}件)"

    if github_write(NEWS_FILE, new_content, sha, message):
        print(f"✅ 保存完了！ {added}件の新規記事を追加しました。")
        print(f"   合計記事数: {len(existing['articles'])}件")
    else:
        print("❌ 保存に失敗しました。")
        sys.exit(1)


if __name__ == "__main__":
    main()
