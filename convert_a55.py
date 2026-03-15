#!/usr/bin/env python3
"""
国土数値情報 A55（都市計画決定GML）から特別用途地区データを抽出し、GeoJSONに変換する。

CityGML形式の tkbt*.gml ファイルをパースし、ポリゴン＋属性を抽出してGeoJSONとして保存する。

使い方:
  python3 convert_a55.py

出力:
  data/A55_tokubetsu_youto.geojson
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

# 名前空間の定義
NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "urf": "https://www.geospatial.jp/iur/urf/3.0",
    "gml": "http://www.opengis.net/gml",
}

# 特別用途地区の用途コード → 名称
TOKUBETSU_YOUTO_CODE_MAP = {
    "0": "特別用途地区（種別不明）",
    "1": "特別工業地区",
    "2": "文教地区",
    "3": "小売店舗地区",
    "4": "事務所地区",
    "5": "厚生地区",
    "6": "娯楽・レクリエーション地区",
    "7": "観光地区",
    "8": "特別業務地区",
    "9": "中高層階住居専用地区",
    "10": "商業専用地区",
    "11": "研究開発地区",
    "12": "その他",
}

# 自治体コード → 名称（23区）
CITY_CODE_MAP = {
    "13101": "千代田区",
    "13102": "中央区",
    "13103": "港区",
    "13104": "新宿区",
    "13105": "文京区",
    "13106": "台東区",
    "13107": "墨田区",
    "13108": "江東区",
    "13109": "品川区",
    "13110": "目黒区",
    "13111": "世田谷区",
    "13112": "渋谷区",
    "13113": "中野区",
    "13114": "杉並区",
    "13115": "豊島区",
    "13116": "北区",
    "13117": "荒川区",
    "13118": "板橋区",
    "13119": "練馬区",
    "13120": "足立区",
    "13121": "葛飾区",
    "13122": "江戸川区",
    "13123": "大田区",
}


def parse_poslist(poslist_text: str) -> list[list[float]]:
    """
    gml:posList テキストを [lon, lat] のリストに変換する。
    CityGMLの座標は 緯度 経度 高さ の順序（EPSG:6697）。
    GeoJSONは [経度, 緯度] の順序。
    """
    vals = poslist_text.strip().split()
    coords = []
    # 3次元座標（lat, lon, height）
    for i in range(0, len(vals) - 2, 3):
        lat = float(vals[i])
        lon = float(vals[i + 1])
        # height = float(vals[i + 2])  # 不要
        coords.append([lon, lat])
    return coords


def parse_tkbt_gml(filepath: str) -> list[dict]:
    """
    tkbt.gml ファイルをパースし、特別用途地区のフィーチャーリストを返す。
    """
    features = []
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        print(f"  パースエラー: {filepath}: {e}", file=sys.stderr)
        return features

    root = tree.getroot()

    for member in root.findall("core:cityObjectMember", NS):
        district = member.find("urf:SpecialUseDistrict", NS)
        if district is None:
            continue

        # 属性の取得
        usage_elem = district.find("urf:usage", NS)
        city_elem = district.find("urf:city", NS)
        pref_elem = district.find("urf:prefecture", NS)

        usage_code = usage_elem.text.strip() if usage_elem is not None and usage_elem.text else "0"
        city_code = city_elem.text.strip() if city_elem is not None and city_elem.text else ""
        pref_code = pref_elem.text.strip() if pref_elem is not None and pref_elem.text else ""

        usage_name = TOKUBETSU_YOUTO_CODE_MAP.get(usage_code, f"不明（コード:{usage_code}）")
        city_name = CITY_CODE_MAP.get(city_code, city_code)

        # ポリゴンの取得（MultiSurface → surfaceMember → Polygon → exterior → LinearRing → posList）
        multi_surface = district.find(".//gml:MultiSurface", NS)
        if multi_surface is None:
            continue

        polygons = []
        for surface_member in multi_surface.findall("gml:surfaceMember", NS):
            polygon = surface_member.find("gml:Polygon", NS)
            if polygon is None:
                continue

            # 外周リング
            exterior = polygon.find("gml:exterior/gml:LinearRing/gml:posList", NS)
            if exterior is None or exterior.text is None:
                continue

            outer_ring = parse_poslist(exterior.text)
            if len(outer_ring) < 4:
                continue

            # リングが閉じていることを確認
            if outer_ring[0] != outer_ring[-1]:
                outer_ring.append(outer_ring[0])

            rings = [outer_ring]

            # 内周リング（穴）
            for interior in polygon.findall("gml:interior/gml:LinearRing/gml:posList", NS):
                if interior.text:
                    inner_ring = parse_poslist(interior.text)
                    if len(inner_ring) >= 4:
                        if inner_ring[0] != inner_ring[-1]:
                            inner_ring.append(inner_ring[0])
                        rings.append(inner_ring)

            polygons.append(rings)

        if not polygons:
            continue

        # GeoJSONフィーチャーの作成
        if len(polygons) == 1:
            geometry = {
                "type": "Polygon",
                "coordinates": polygons[0],
            }
        else:
            geometry = {
                "type": "MultiPolygon",
                "coordinates": polygons,
            }

        feature = {
            "type": "Feature",
            "properties": {
                "YoutoCode": usage_code,
                "YoutoName": usage_name,
                "CityCode": city_code,
                "Cityname": city_name,
                "Pref": pref_code,
            },
            "geometry": geometry,
        }
        features.append(feature)

    return features


def main():
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    a55_dir = os.path.join(data_dir, "A55-24_13000_GML")

    if not os.path.isdir(a55_dir):
        print(f"エラー: A55データディレクトリが見つかりません: {a55_dir}")
        sys.exit(1)

    all_features = []
    processed = 0
    skipped = 0

    # 全サブディレクトリの tkbt.gml を処理
    for subdir in sorted(os.listdir(a55_dir)):
        subdir_path = os.path.join(a55_dir, subdir)
        if not os.path.isdir(subdir_path):
            continue

        # 自治体コードを抽出
        # ディレクトリ名: A55-24_13104_GML → 13104
        parts = subdir.split("_")
        if len(parts) < 2:
            continue
        city_code = parts[1]

        tkbt_file = os.path.join(subdir_path, f"{city_code}_tkbt.gml")
        if not os.path.isfile(tkbt_file):
            skipped += 1
            continue

        print(f"処理中: {tkbt_file}")
        features = parse_tkbt_gml(tkbt_file)
        all_features.extend(features)
        processed += 1
        print(f"  → {len(features)}件のポリゴンを抽出")

    # GeoJSONとして保存
    geojson = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:EPSG::4326"},
        },
        "features": all_features,
    }

    output_path = os.path.join(data_dir, "A55_tokubetsu_youto.geojson")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"\n完了:")
    print(f"  処理ファイル数: {processed}")
    print(f"  スキップ（tkbtなし）: {skipped}")
    print(f"  総ポリゴン数: {len(all_features)}")

    # 用途種別ごとの集計
    usage_counts = {}
    for f in all_features:
        name = f["properties"]["YoutoName"]
        usage_counts[name] = usage_counts.get(name, 0) + 1
    print(f"  用途種別内訳:")
    for name, count in sorted(usage_counts.items()):
        print(f"    {name}: {count}件")

    print(f"\n出力: {output_path}")


if __name__ == "__main__":
    main()
