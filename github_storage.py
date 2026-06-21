"""
GitHub APIを使った永続データストレージ。
Streamlit CloudでもデータがGitHubリポジトリに保存され、再起動後も保持される。
"""
import base64
import json
import urllib.request
import urllib.error
import os
import logging
import streamlit as st

GITHUB_REPO = "Okajun01/1niwa-zoning-checker"
GITHUB_BRANCH = "main"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"


def _get_token():
    """GitHubトークンを取得（Streamlit secrets または環境変数）"""
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return os.environ.get("GITHUB_TOKEN", "")


def read_file(path: str) -> str | None:
    """GitHubリポジトリからファイルを読み込む。

    トークンがあれば認証API、無ければ未認証rawにフォールバックする。
    公開リポジトリは読み取りにトークン不要なので、Streamlit側の GITHUB_TOKEN が
    未設定/空/失効でもニュース等のデータを読める（無音の空表示バグの恒久対策）。
    """
    token = _get_token()
    if token:
        url = f"{GITHUB_API}/{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data.get("content")
                if content:
                    return base64.b64decode(content).decode("utf-8")
                # content が無い（1MB超など）場合は raw フォールバックへ
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # ファイルが存在しない
            logging.warning("read_file: GitHub API HTTP %s for %s; falling back to raw", e.code, path)
        except Exception as e:
            logging.warning("read_file: GitHub API error for %s (%s); falling back to raw", path, e)

    # 未認証 raw フォールバック（公開リポ・トークン不要・1MB制限なし）
    raw_url = f"{GITHUB_RAW}/{path}"
    try:
        with urllib.request.urlopen(raw_url, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        logging.warning("read_file: raw fetch HTTP %s for %s", e.code, path)
        return None
    except Exception as e:
        logging.warning("read_file: raw fetch failed for %s (%s)", path, e)
        return None


def write_file(path: str, content: str, message: str = "auto: update data"):
    """GitHubリポジトリにファイルを書き込む（作成 or 更新）"""
    token = _get_token()
    if not token:
        return False

    # 既存ファイルのSHAを取得（更新時に必要）
    sha = None
    url = f"{GITHUB_API}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha")
    except Exception:
        pass  # 新規ファイルの場合はSHA不要

    # ファイルを書き込み
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


def read_json(path: str) -> dict | None:
    """JSONファイルを読み込んでdictとして返す"""
    content = read_file(path)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return None


def write_json(path: str, data: dict, message: str = "auto: update json"):
    """dictをJSONファイルとして書き込む"""
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return write_file(path, content, message)


def append_csv_line(path: str, header: str, line: str, message: str = "auto: append csv"):
    """CSVファイルに1行追記（ファイルがなければヘッダー付きで新規作成）"""
    existing = read_file(path)
    if existing:
        content = existing.rstrip("\n") + "\n" + line + "\n"
    else:
        content = header + "\n" + line + "\n"
    return write_file(path, content, message)
