#!/usr/bin/env python3
"""
東京都23区 用途地域判定ツール

住所から用途地域を自動判定し、旅館業の営業可否を表示する。
国土数値情報の用途地域データ（A29）と国土地理院ジオコーディングAPIを使用。

使い方:
  # 単一住所の判定
  python3 zoning_checker.py "東京都文京区大塚5丁目"

  # 複数住所の判定
  python3 zoning_checker.py "東京都文京区大塚5丁目" "東京都新宿区歌舞伎町1丁目"

  # CSVファイルから一括判定（1列目が住所）
  python3 zoning_checker.py --csv input.csv

  # 結果をCSV出力
  python3 zoning_checker.py --csv input.csv --output result.csv

  # 結果をCSV出力（単一住所）
  python3 zoning_checker.py "東京都文京区大塚5丁目" --output result.csv
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

try:
    import geopandas as gpd
    from shapely.geometry import Point
except ImportError:
    print("エラー: 必要なライブラリがインストールされていません。")
    print("以下のコマンドでインストールしてください:")
    print("  pip3 install --user --break-system-packages geopandas shapely")
    sys.exit(1)


# ===== 定数 =====

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 国土数値情報 A29 用途地域コードと名称の対応表
# https://nlftp.mlit.go.jp/ksj/gml/codelist/UseDistrictCd.html
YOUTO_CODE_MAP = {
    "1": "第一種低層住居専用地域",
    "2": "第二種低層住居専用地域",
    "3": "第一種中高層住居専用地域",
    "4": "第二種中高層住居専用地域",
    "5": "第一種住居地域",
    "6": "第二種住居地域",
    "7": "準住居地域",
    "8": "近隣商業地域",
    "9": "商業地域",
    "10": "準工業地域",
    "11": "工業地域",
    "12": "工業専用地域",
    # 田園住居地域（2018年新設）
    "13": "田園住居地域",
}

# 旅館業営業可否判定
# ○: 可能, △: 条件付き可能, ×: 不可
RYOKAN_ELIGIBILITY = {
    "商業地域": ("○", "営業可能"),
    "近隣商業地域": ("○", "営業可能"),
    "準工業地域": ("○", "営業可能"),
    "第二種住居地域": ("○", "営業可能"),
    "準住居地域": ("○", "営業可能"),
    "工業地域": ("○", "営業可能（立地的に不向きだが法的には可能）"),
    "第一種住居地域": ("△", "条件付き可能（3,000㎡以下のみ）"),
    "第一種低層住居専用地域": ("×", "営業不可"),
    "第二種低層住居専用地域": ("×", "営業不可"),
    "第一種中高層住居専用地域": ("×", "営業不可"),
    "第二種中高層住居専用地域": ("×", "営業不可"),
    "工業専用地域": ("×", "営業不可"),
    "田園住居地域": ("×", "営業不可"),
}


# 文教地区が広範に指定されている区（旅館業が原則不可となる特別用途地区）
BUNKYO_CHIKU_AREAS = {
    "文京区": "区の大部分が文教地区に指定。旅館業は原則不可",
    "千代田区": "一部地域が文教地区（神田駿河台、一ツ橋周辺等）",
    "新宿区": "一部地域が文教地区（早稲田、市ヶ谷周辺等）",
    "豊島区": "一部地域が文教地区（目白、雑司が谷周辺等）",
    "世田谷区": "一部地域が文教地区",
    "目黒区": "一部地域が文教地区（駒場周辺等）",
    "杉並区": "一部地域が文教地区",
}

# 学校種別コード（P29_003）と110m照会対象
SCHOOL_TYPES_110M = {
    "16001": "小学校",
    "16002": "中学校",
    "16003": "高等学校",
    "16004": "中等教育学校",
    "16005": "大学",
    "16006": "高等専門学校",
    "16007": "特別支援学校",
    "16008": "幼稚園",
    "16009": "幼保連携型認定こども園",
    "16011": "義務教育学校",
}


@dataclass
class ZoningResult:
    """用途地域判定結果（強化版）"""
    address: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    youto_chiiki: Optional[str] = None
    ryokan_kahi: Optional[str] = None  # ○ △ ×
    ryokan_detail: Optional[str] = None
    # 学校距離判定
    schools_within_110m: Optional[list] = None  # [(学校名, 種別, 距離m)]
    schools_within_200m: Optional[list] = None  # 110-200m（精度誤差考慮の警告圏）
    school_warning: Optional[str] = None
    # 文教地区判定
    bunkyo_chiku: Optional[str] = None  # 文教地区の警告
    # 総合判定
    sogo_hantei: Optional[str] = None  # 総合判定（○△×要確認）
    sogo_detail: Optional[str] = None
    # 次のステップ
    next_steps: Optional[list] = None
    error: Optional[str] = None


# ===== ジオコーディング =====

def geocode_msearch(address: str) -> Optional[tuple[float, float]]:
    """
    国土地理院のジオコーディングAPI（msearch）を使用して住所→座標変換。
    戻り値: (経度, 緯度) or None
    """
    # 国土地理院 地理院地図API
    url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={urllib.parse.quote(address)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZoningChecker/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data and len(data) > 0:
            # [経度, 緯度] の形式
            coords = data[0].get("geometry", {}).get("coordinates")
            if coords:
                return (float(coords[0]), float(coords[1]))
    except Exception as e:
        print(f"  ジオコーディングエラー (msearch): {e}", file=sys.stderr)
    return None


def geocode_nominatim(address: str) -> Optional[tuple[float, float]]:
    """
    Nominatim（OpenStreetMap）ジオコーディングAPIを使用。フォールバック用。
    戻り値: (経度, 緯度) or None
    """
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address)}&format=json&limit=1&countrycodes=jp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZoningChecker/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data and len(data) > 0:
            lon = float(data[0]["lon"])
            lat = float(data[0]["lat"])
            return (lon, lat)
    except Exception as e:
        print(f"  ジオコーディングエラー (nominatim): {e}", file=sys.stderr)
    return None


def geocode(address: str) -> Optional[tuple[float, float]]:
    """
    住所→座標変換。国土地理院APIを優先し、失敗時はNominatimにフォールバック。
    戻り値: (経度, 緯度) or None
    """
    result = geocode_msearch(address)
    if result:
        return result

    # フォールバック（レート制限対策で1秒待つ）
    time.sleep(1)
    return geocode_nominatim(address)


# ===== GISデータ読み込み =====

_gdf_cache = None
_school_gdf_cache = None


def load_school_data() -> Optional[gpd.GeoDataFrame]:
    """学校データ（P29）を読み込む"""
    global _school_gdf_cache
    if _school_gdf_cache is not None:
        return _school_gdf_cache

    school_dir = os.path.join(DATA_DIR, "P29-21_13_GML")
    if not os.path.isdir(school_dir):
        return None

    shp = None
    for root, dirs, files in os.walk(school_dir):
        for f in files:
            if f.endswith(".shp"):
                shp = os.path.join(root, f)
                break

    if shp is None:
        return None

    try:
        gdf = gpd.read_file(shp)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        _school_gdf_cache = gdf
        print(f"学校データ読み込み: {len(gdf)}件")
        return gdf
    except Exception:
        return None


def check_schools_nearby(lon: float, lat: float, school_gdf: gpd.GeoDataFrame) -> tuple[list, list]:
    """
    周辺の学校等を検出する。
    戻り値: (110m以内のリスト, 110-200m以内のリスト)
    200mまで検出する理由: ジオコーディングの精度誤差（丁目レベルで最大100m程度）を考慮
    """
    from shapely.ops import transform
    import pyproj

    point_wgs84 = Point(lon, lat)
    proj = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:6677", always_xy=True)
    point_m = transform(proj.transform, point_wgs84)

    within_110m = []
    within_200m = []
    for _, row in school_gdf.iterrows():
        school_point = transform(proj.transform, row.geometry)
        dist = point_m.distance(school_point)
        if dist <= 200:
            school_type_code = str(row.get("P29_003", "")).strip()
            if "." in school_type_code:
                school_type_code = str(int(float(school_type_code)))
            school_type = SCHOOL_TYPES_110M.get(school_type_code, f"教育施設(コード:{school_type_code})")
            school_name = str(row.get("P29_004", "不明"))
            entry = (school_name, school_type, round(dist, 1))
            if dist <= 110:
                within_110m.append(entry)
            else:
                within_200m.append(entry)

    within_110m.sort(key=lambda x: x[2])
    within_200m.sort(key=lambda x: x[2])
    return within_110m, within_200m


def check_bunkyo_chiku(address: str) -> Optional[str]:
    """住所から文教地区の可能性を判定する"""
    for ku, detail in BUNKYO_CHIKU_AREAS.items():
        if ku in address:
            return f"⚠️ {ku}は{detail}"
    return None


def load_zoning_data() -> gpd.GeoDataFrame:
    """
    用途地域のGISデータを読み込む。
    Shapefile, GeoJSON, GMLのいずれかを自動検出して読み込む。
    """
    global _gdf_cache
    if _gdf_cache is not None:
        return _gdf_cache

    if not os.path.isdir(DATA_DIR):
        print(f"エラー: データディレクトリが見つかりません: {DATA_DIR}")
        print("先にセットアップを実行してください: bash setup.sh")
        sys.exit(1)

    # データファイルを探す（優先順: Shapefile > GeoJSON > GML）
    gis_file = None
    for root, dirs, files in os.walk(DATA_DIR):
        for f in files:
            path = os.path.join(root, f)
            if f.endswith(".shp"):
                gis_file = path
                break
            elif f.endswith(".geojson") and gis_file is None:
                gis_file = path
            elif f.endswith(".gml") and gis_file is None:
                gis_file = path
        if gis_file and gis_file.endswith(".shp"):
            break

    if gis_file is None:
        print(f"エラー: GISデータファイルが見つかりません。")
        print(f"  検索ディレクトリ: {DATA_DIR}")
        print("先にセットアップを実行してください: bash setup.sh")
        sys.exit(1)

    print(f"GISデータ読み込み中: {gis_file}")
    try:
        gdf = gpd.read_file(gis_file)
    except Exception as e:
        print(f"エラー: GISデータの読み込みに失敗しました: {e}")
        sys.exit(1)

    # 座標系をWGS84（EPSG:4326）に変換
    if gdf.crs is None:
        print("  警告: CRSが未設定。EPSG:4326を仮定します。")
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        print(f"  座標系変換: {gdf.crs} → EPSG:4326")
        gdf = gdf.to_crs("EPSG:4326")

    # 用途地域コードのカラム名を特定
    youto_col = _find_youto_column(gdf)
    if youto_col:
        print(f"  用途地域カラム: {youto_col}")
        gdf["_youto_code"] = gdf[youto_col].astype(str)
    else:
        print(f"  警告: 用途地域コードのカラムが特定できませんでした。")
        print(f"  カラム一覧: {list(gdf.columns)}")

    print(f"  レコード数: {len(gdf)}")
    _gdf_cache = gdf
    return gdf


def _find_youto_column(gdf: gpd.GeoDataFrame) -> Optional[str]:
    """用途地域コードが入っているカラム名を特定する"""
    # よくあるカラム名のパターン
    candidates = [
        "A29_004",   # 国土数値情報の標準カラム名（用途地域種別コード）
        "youto",
        "USE_DISTRICT",
        "用途地域",
        "YOUTO",
        "A29_003",
    ]
    for col in candidates:
        if col in gdf.columns:
            return col

    # カラム名に「用途」や「district」「A29」を含むものを探す
    for col in gdf.columns:
        col_lower = col.lower()
        if "a29_004" in col_lower or "youto" in col_lower or "用途" in col_lower:
            return col
        if "district" in col_lower and "use" in col_lower:
            return col

    # 値が1-13の整数であるカラムを探す（ヒューリスティック）
    for col in gdf.columns:
        if col == "geometry":
            continue
        try:
            vals = gdf[col].dropna().unique()
            str_vals = set(str(v).strip() for v in vals)
            # 用途地域コードは1〜13
            if str_vals and str_vals.issubset(set(str(i) for i in range(1, 14))):
                return col
        except Exception:
            pass

    return None


def get_youto_name(code: str) -> str:
    """用途地域コードから名称を取得"""
    code = str(code).strip()
    # 小数点が付いている場合の対応（例: "9.0" → "9"）
    if "." in code:
        try:
            code = str(int(float(code)))
        except ValueError:
            pass
    return YOUTO_CODE_MAP.get(code, f"不明（コード: {code}）")


# ===== 用途地域判定 =====

def check_zoning(address: str, gdf: gpd.GeoDataFrame, school_gdf: gpd.GeoDataFrame = None) -> ZoningResult:
    """
    住所から用途地域を判定し、旅館業の営業可否を総合判定する。
    判定項目:
      1. 用途地域（建築基準法48条）
      2. 文教地区（東京都文教地区建築条例）
      3. 学校等110m距離（旅館業法3条3項）
    """
    result = ZoningResult(address=address)

    # 1. ジオコーディング
    coords = geocode(address)
    if coords is None:
        result.error = "住所の座標変換に失敗しました"
        return result

    lon, lat = coords
    result.lon = lon
    result.lat = lat

    # 2. 用途地域の判定（点がどのポリゴンに含まれるかを判定）
    point = Point(lon, lat)

    try:
        possible_matches_idx = list(gdf.sindex.intersection(point.bounds))
        if possible_matches_idx:
            possible_matches = gdf.iloc[possible_matches_idx]
            matches = possible_matches[possible_matches.geometry.contains(point)]
        else:
            matches = gpd.GeoDataFrame()
    except Exception:
        matches = gdf[gdf.geometry.contains(point)]

    if len(matches) == 0:
        result.error = "該当する用途地域データが見つかりません（23区外または海上の可能性）"
        return result

    matched = matches.iloc[0]

    if "_youto_code" in matched.index:
        code = str(matched["_youto_code"])
        youto_name = get_youto_name(code)
    else:
        youto_name = "判定不可（用途地域コードカラム未検出）"

    result.youto_chiiki = youto_name

    if youto_name in RYOKAN_ELIGIBILITY:
        result.ryokan_kahi, result.ryokan_detail = RYOKAN_ELIGIBILITY[youto_name]
    else:
        result.ryokan_kahi = "?"
        result.ryokan_detail = "判定不可（用途地域が特定できません）"

    # 3. 文教地区チェック
    result.bunkyo_chiku = check_bunkyo_chiku(address)

    # 4. 学校距離チェック（110m + 200m警告圏）
    if school_gdf is not None and len(school_gdf) > 0:
        within_110, within_200 = check_schools_nearby(lon, lat, school_gdf)
        if within_110:
            result.schools_within_110m = within_110
            result.school_warning = f"🔴 110m以内に{len(within_110)}件の学校等あり（学校照会が必要）"
        if within_200:
            result.schools_within_200m = within_200
            if result.school_warning:
                result.school_warning += f"  + 110-200m圏内に{len(within_200)}件（住所精度により照会対象の可能性あり）"
            else:
                result.school_warning = f"⚠️ 110-200m圏内に{len(within_200)}件（住所の精度誤差により照会対象の可能性あり。要現地確認）"

    # 5. 総合判定
    next_steps = []

    if result.ryokan_kahi == "×":
        result.sogo_hantei = "×"
        result.sogo_detail = f"用途地域（{youto_name}）で旅館業営業不可"
    elif result.bunkyo_chiku and "文京区" in address:
        # 文京区は大部分が文教地区のため原則不可
        result.sogo_hantei = "×"
        result.sogo_detail = f"文京区は大部分が文教地区。旅館業は原則不可"
        next_steps.append("文教地区規制の詳細を区の都市計画課に確認")
    if result.sogo_hantei is None and result.ryokan_kahi == "△":
        if result.schools_within_110m:
            result.sogo_hantei = "要確認"
            result.sogo_detail = f"条件付き可能（3,000㎡以下）+ 学校照会が必要"
        else:
            result.sogo_hantei = "△"
            result.sogo_detail = f"条件付き可能（延べ面積3,000㎡以下のみ）"
        next_steps.append("物件の延べ面積が3,000㎡以下であることを確認")
    if result.sogo_hantei is None and result.ryokan_kahi == "○":
        if result.schools_within_110m:
            result.sogo_hantei = "要確認"
            result.sogo_detail = f"用途地域OK。ただし学校照会が必要（110m以内に学校等あり）"
        elif result.schools_within_200m:
            result.sogo_hantei = "要確認"
            result.sogo_detail = f"用途地域OK。110-200m圏内に学校等あり（住所精度の誤差により110m以内の可能性。現地確認推奨）"
        else:
            result.sogo_hantei = "○"
            result.sogo_detail = "用途地域OK・学校照会リスク低"
    if result.sogo_hantei is None:
        result.sogo_hantei = "?"
        result.sogo_detail = "判定不可"

    # 文教地区注記（一部の区の場合）
    if result.bunkyo_chiku and "文京区" not in address and result.sogo_hantei not in ("×",):
        if result.sogo_detail:
            result.sogo_detail += f"。注意: {result.bunkyo_chiku}"
        next_steps.append("当該住所が文教地区に該当するか区の都市計画課に確認")

    # 次のステップ
    if result.sogo_hantei in ("○", "△", "要確認"):
        next_steps.append("保健所への事前相談（用途地域・構造設備基準の確認）")
        next_steps.append("消防署への事前相談（消防法令適合通知書の取得）")
        if result.schools_within_110m:
            next_steps.append(f"学校照会の準備（110m以内: {len(result.schools_within_110m)}施設）")
        next_steps.append("近隣住民への事前説明（区の指導に従う）")
        next_steps.append("ICT帳場（無人フロント）の可否を区に確認")

    result.next_steps = next_steps if next_steps else None

    return result


# ===== 出力 =====

def print_result(result: ZoningResult):
    """結果をコンソールに見やすく出力する"""
    print(f"\n{'='*60}")
    print(f"住所: {result.address}")
    print(f"-"*60)

    if result.error:
        print(f"エラー: {result.error}")
        if result.lat and result.lon:
            print(f"座標: ({result.lat}, {result.lon})")
    else:
        print(f"座標: ({result.lat:.6f}, {result.lon:.6f})")
        print(f"用途地域: {result.youto_chiiki}")
        print(f"旅館業（用途地域）: {result.ryokan_kahi} {result.ryokan_detail}")
        if result.bunkyo_chiku:
            print(f"文教地区: {result.bunkyo_chiku}")
        if result.schools_within_110m or result.schools_within_200m:
            print(f"学校チェック: {result.school_warning}")
            if result.schools_within_110m:
                print(f"  【110m以内】")
                for name, stype, dist in result.schools_within_110m:
                    print(f"    🔴 {name}（{stype}）: {dist}m")
            if result.schools_within_200m:
                print(f"  【110-200m（要現地確認）】")
                for name, stype, dist in result.schools_within_200m:
                    print(f"    🟡 {name}（{stype}）: {dist}m")
        else:
            print(f"学校チェック: ✅ 200m以内に学校等なし")
        print(f"-"*60)
        print(f"【総合判定】: {result.sogo_hantei} {result.sogo_detail}")
        if result.next_steps:
            print(f"【次のステップ】:")
            for i, step in enumerate(result.next_steps, 1):
                print(f"  {i}. {step}")

    print(f"{'='*60}")


def write_csv(results: list[ZoningResult], output_path: str):
    """結果をCSVファイルに出力する"""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["住所", "緯度", "経度", "用途地域", "用途地域判定", "文教地区", "学校110m", "総合判定", "総合詳細", "次のステップ", "エラー"])
        for r in results:
            schools_str = ""
            if r.schools_within_110m:
                schools_str = "; ".join(f"{n}({t}){d}m" for n, t, d in r.schools_within_110m)
            writer.writerow([
                r.address,
                r.lat or "",
                r.lon or "",
                r.youto_chiiki or "",
                f"{r.ryokan_kahi or ''} {r.ryokan_detail or ''}",
                r.bunkyo_chiku or "該当なし",
                schools_str or "110m以内になし",
                r.sogo_hantei or "",
                r.sogo_detail or "",
                "; ".join(r.next_steps) if r.next_steps else "",
                r.error or "",
            ])
    print(f"\nCSV出力: {output_path}")


def read_addresses_from_csv(csv_path: str) -> list[str]:
    """CSVファイルから住所を読み込む（1列目を住所として扱う）"""
    addresses = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:
                continue
            addr = row[0].strip()
            # ヘッダー行をスキップ（「住所」「address」等）
            if i == 0 and any(h in addr.lower() for h in ["住所", "address", "addr"]):
                continue
            if addr:
                addresses.append(addr)
    return addresses


# ===== メイン =====

def main():
    parser = argparse.ArgumentParser(
        description="東京都23区の用途地域を住所から判定し、旅館業の営業可否を表示する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python3 zoning_checker.py "東京都文京区大塚5丁目"
  python3 zoning_checker.py "東京都新宿区歌舞伎町1丁目" "東京都港区六本木3丁目"
  python3 zoning_checker.py --csv addresses.csv
  python3 zoning_checker.py --csv addresses.csv --output results.csv
        """,
    )
    parser.add_argument("addresses", nargs="*", help="判定する住所（複数指定可）")
    parser.add_argument("--csv", metavar="FILE", help="住所一覧のCSVファイル")
    parser.add_argument("--output", "-o", metavar="FILE", help="結果のCSV出力先")
    args = parser.parse_args()

    # 住所の収集
    addresses = list(args.addresses) if args.addresses else []
    if args.csv:
        if not os.path.isfile(args.csv):
            print(f"エラー: CSVファイルが見つかりません: {args.csv}")
            sys.exit(1)
        addresses.extend(read_addresses_from_csv(args.csv))

    if not addresses:
        parser.print_help()
        sys.exit(1)

    print(f"判定対象: {len(addresses)}件")

    # GISデータ読み込み
    gdf = load_zoning_data()
    school_gdf = load_school_data()

    # 各住所を判定
    results = []
    for i, addr in enumerate(addresses):
        print(f"\n[{i+1}/{len(addresses)}] {addr}")
        result = check_zoning(addr, gdf, school_gdf)
        results.append(result)
        print_result(result)

        # APIレート制限対策（複数件の場合）
        if i < len(addresses) - 1:
            time.sleep(0.5)

    # CSV出力
    if args.output:
        write_csv(results, args.output)

    # サマリー
    if len(results) > 1:
        print(f"\n{'='*60}")
        print(f"総合サマリー: {len(results)}件中")
        ok = sum(1 for r in results if r.sogo_hantei == "○")
        cond = sum(1 for r in results if r.sogo_hantei == "△")
        check = sum(1 for r in results if r.sogo_hantei == "要確認")
        ng = sum(1 for r in results if r.sogo_hantei == "×")
        err = sum(1 for r in results if r.error)
        print(f"  ○ 営業可能（リスク低）: {ok}件")
        print(f"  △ 条件付き可能: {cond}件")
        print(f"  要確認（学校照会等）: {check}件")
        print(f"  × 営業不可: {ng}件")
        if err:
            print(f"  エラー: {err}件")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
