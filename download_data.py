#!/usr/bin/env python3
"""
国土数値情報から東京都の用途地域データ（A29）をダウンロードするスクリプト。

国土数値情報ダウンロードサービスのAPIを使用して、
東京都（都道府県コード: 13）の用途地域データを取得する。
"""

import os
import sys
import zipfile
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 国土数値情報 用途地域データ（A29）東京都
# URLは国土数値情報のダウンロードページから取得
# https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A29-v3_1.html
# 東京都のデータファイル
DOWNLOAD_URLS = [
    # A29 用途地域 東京都 - 最新版を試行（年度はサイトにより異なる）
    "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-11/A29-11_13_GML.zip",
]

# 代替URL候補（年度違い）
FALLBACK_URLS = [
    "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-11/A29-11_13_GML.zip",
    "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-10/A29-10_13_GML.zip",
    "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-09/A29-09_13_GML.zip",
]


def download_file(url, dest_path):
    """URLからファイルをダウンロードする"""
    print(f"  ダウンロード中: {url}")
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded * 100 // total
                    print(f"\r  進捗: {pct}% ({downloaded // 1024}KB / {total // 1024}KB)", end="", flush=True)
        print()
        return True
    except requests.exceptions.RequestException as e:
        print(f"\n  エラー: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def extract_zip(zip_path, extract_to):
    """ZIPファイルを展開する"""
    print(f"  展開中: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)
    print(f"  展開完了")


def find_shapefile(directory):
    """ディレクトリ内のShapefileを探す"""
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith(".shp"):
                return os.path.join(root, f)
    return None


def find_geojson(directory):
    """ディレクトリ内のGeoJSONを探す"""
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith(".geojson") or f.endswith(".json"):
                return os.path.join(root, f)
    return None


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 既にデータがある場合はスキップ
    shp = find_shapefile(DATA_DIR)
    gjson = find_geojson(DATA_DIR)
    if shp or gjson:
        found = shp or gjson
        print(f"既にデータが存在します: {found}")
        print("再ダウンロードする場合は data/ ディレクトリを削除してから実行してください。")
        return

    # ダウンロード試行
    zip_path = os.path.join(DATA_DIR, "youto_tokyo.zip")
    success = False

    all_urls = DOWNLOAD_URLS + [u for u in FALLBACK_URLS if u not in DOWNLOAD_URLS]
    for url in all_urls:
        if download_file(url, zip_path):
            success = True
            break

    if not success:
        print("\n=== 自動ダウンロードに失敗しました ===")
        print("以下の手順で手動ダウンロードしてください:")
        print(f"1. https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A29-v3_1.html にアクセス")
        print(f"2. 東京都のデータをダウンロード")
        print(f"3. ダウンロードしたZIPファイルを {DATA_DIR}/ に展開")
        sys.exit(1)

    # 展開
    extract_zip(zip_path, DATA_DIR)
    os.remove(zip_path)

    # 結果確認
    shp = find_shapefile(DATA_DIR)
    gjson = find_geojson(DATA_DIR)
    if shp:
        print(f"\nShapefileを検出: {shp}")
        # 国土数値情報A29はJGD2000座標系だが.prjファイルが欠落している場合があるため生成
        prj_path = shp.replace(".shp", ".prj")
        if not os.path.exists(prj_path):
            prj_content = 'GEOGCS["JGD2000",DATUM["Japanese_Geodetic_Datum_2000",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
            with open(prj_path, "w") as f:
                f.write(prj_content)
            print(f"  .prjファイルを生成: {prj_path}")
    elif gjson:
        print(f"\nGeoJSONを検出: {gjson}")
    else:
        print("\n展開されたファイル一覧:")
        for root, dirs, files in os.walk(DATA_DIR):
            for f in files:
                print(f"  {os.path.join(root, f)}")
        print("\n注意: ShapefileまたはGeoJSONが見つかりませんでした。")
        print("GMLファイルがある場合、zoning_checker.py が自動的に読み込みを試みます。")

    print("\nデータのダウンロードが完了しました。")


if __name__ == "__main__":
    main()
