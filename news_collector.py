"""
宿泊業界ニュースの保存・読み込みユーティリティ。
GitHub Storageを使ってニュースデータを永続化する。

スケジュールエージェントが収集した記事を保存し、
Streamlitアプリの「📰 業界ニュース」タブで表示する。
"""

import json
from datetime import datetime

NEWS_FILE = "data/news_history.json"

# カテゴリ定義
CATEGORIES = [
    "旅館業法・条例改正",
    "民泊・住宅宿泊事業",
    "観光・インバウンド",
    "補助金・助成金",
]

# 重要度定義
IMPORTANCE_LEVELS = ["高", "中", "低"]


def load_news() -> dict:
    """GitHubからニュースデータを読み込む"""
    try:
        from github_storage import read_json
        data = read_json(NEWS_FILE)
        if data and "articles" in data:
            return data
    except Exception:
        pass
    return {"last_updated": "", "articles": []}


def save_news(data: dict) -> bool:
    """ニュースデータをGitHubに保存する"""
    try:
        from github_storage import write_json
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return write_json(
            NEWS_FILE,
            data,
            f"auto: news update {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
    except Exception as e:
        print(f"ニュース保存エラー: {e}")
        return False


def add_article(
    title: str,
    summary: str,
    url: str,
    category: str,
    importance: str = "中",
    source: str = "",
    impact_memo: str = "",
) -> bool:
    """記事を1件追加する"""
    data = load_news()

    # 重複チェック（同じURLは追加しない）
    existing_urls = {a.get("url", "") for a in data["articles"]}
    if url and url in existing_urls:
        return False

    article = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "title": title,
        "summary": summary,
        "url": url,
        "category": category if category in CATEGORIES else "旅館業法・条例改正",
        "importance": importance if importance in IMPORTANCE_LEVELS else "中",
        "source": source,
        "impact_memo": impact_memo,
    }

    # 新しい記事を先頭に追加
    data["articles"].insert(0, article)

    # 最大500件に制限（古い記事を削除）
    if len(data["articles"]) > 500:
        data["articles"] = data["articles"][:500]

    return save_news(data)


def add_articles_batch(articles: list[dict]) -> int:
    """複数記事を一括追加する（スケジュールエージェント用）"""
    data = load_news()
    existing_urls = {a.get("url", "") for a in data["articles"]}
    added = 0

    for art in articles:
        url = art.get("url", "")
        if url and url in existing_urls:
            continue

        article = {
            "date": art.get("date", datetime.now().strftime("%Y-%m-%d")),
            "title": art.get("title", ""),
            "summary": art.get("summary", ""),
            "url": url,
            "category": art.get("category", "旅館業法・条例改正"),
            "importance": art.get("importance", "中"),
            "source": art.get("source", ""),
            "impact_memo": art.get("impact_memo", ""),
        }
        data["articles"].insert(0, article)
        existing_urls.add(url)
        added += 1

    if added > 0:
        if len(data["articles"]) > 500:
            data["articles"] = data["articles"][:500]
        save_news(data)

    return added


def get_articles_by_category(category: str) -> list[dict]:
    """カテゴリでフィルタした記事一覧を返す"""
    data = load_news()
    return [a for a in data["articles"] if a.get("category") == category]


def get_articles_by_importance(importance: str) -> list[dict]:
    """重要度でフィルタした記事一覧を返す"""
    data = load_news()
    return [a for a in data["articles"] if a.get("importance") == importance]


def update_impact_memo(url: str, memo: str) -> bool:
    """記事の影響メモを更新する"""
    data = load_news()
    for article in data["articles"]:
        if article.get("url") == url:
            article["impact_memo"] = memo
            return save_news(data)
    return False
