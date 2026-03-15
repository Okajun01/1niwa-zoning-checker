"""
GitHub APIを使った永続データストレージ。
Streamlit CloudでもデータがGitHubリポジトリに保存され、再起動後も保持される。
"""
import base64
import json
import urllib.request
import urllib.error
import os
import streamlit as st

GITHUB_REPO = "Okajun01/1niwa-zoning-checker"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"


def _get_token():
    """GitHubトークンを取得（Streamlit secrets または環境変数）"""
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return os.environ.get("GITHUB_TOKEN", "")


def read_file(path: str) -> str | None:
    """GitHubリポジトリからファイルを読み込む"""
    token = _get_token()
    if not token:
        return None
    url = f"{GITHUB_API}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # ファイルが存在しない
        raise
    except Exception:
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
