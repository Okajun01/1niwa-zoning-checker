#!/usr/bin/env python3
"""
1NIWA 用途地域判定 Web アプリ
奥様がブラウザから住所を入力して旅館業の営業可否を確認できる。

起動方法:
  streamlit run app.py

ブラウザで http://localhost:8501 が自動的に開きます。
"""

import re
import unicodedata

import streamlit as st
import pandas as pd

APP_VERSION = "v4.2.0"

from zoning_checker import load_zoning_data, load_school_data, load_chiku_keikaku_data, load_tokubetsu_youto_data, check_zoning, ZoningResult

# ===== ページ設定 =====
st.set_page_config(
    page_title="1NIWA 用途地域チェッカー",
    page_icon="🏨",
    layout="wide",
)

# ===== カスタムCSS =====
st.markdown("""
<style>
    .result-ok { background-color: #f0f9f0; padding: 20px; border-radius: 10px; border-left: 6px solid #28a745; margin: 12px 0; color: #1a3a1a; }
    .result-cond { background-color: #fefaf0; padding: 20px; border-radius: 10px; border-left: 6px solid #e6a817; margin: 12px 0; color: #3a3010; }
    .result-ng { background-color: #fdf0f0; padding: 20px; border-radius: 10px; border-left: 6px solid #dc3545; margin: 12px 0; color: #3a1a1a; }
    .result-err { background-color: #f5f5f5; padding: 20px; border-radius: 10px; border-left: 6px solid #6c757d; margin: 12px 0; color: #333; }
    .result-ok h3, .result-cond h3, .result-ng h3, .result-err h3 { color: #222; margin-top: 0; }
    .result-ok td, .result-cond td, .result-ng td, .result-err td { color: #333; padding: 4px 8px; vertical-align: top; }
    .result-ok b, .result-cond b, .result-ng b, .result-err b { color: #111; }
    .result-ok li, .result-cond li, .result-ng li { color: #333; }
    .header-sub { color: #666; font-size: 14px; }
</style>
""", unsafe_allow_html=True)


# ===== GISデータの読み込み（キャッシュ）=====
@st.cache_resource
def setup_and_load():
    """初回起動時にGISデータを自動ダウンロードして読み込む"""
    import download_data
    download_data.main()
    import os, zipfile, requests
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    school_dir = os.path.join(data_dir, "P29-21_13_GML")
    if not os.path.isdir(school_dir):
        try:
            resp = requests.get("https://nlftp.mlit.go.jp/ksj/gml/data/P29/P29-21/P29-21_13_GML.zip", timeout=120)
            resp.raise_for_status()
            zpath = os.path.join(data_dir, "school.zip")
            with open(zpath, "wb") as f:
                f.write(resp.content)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(os.path.join(data_dir, "P29-21_13_GML"))
            os.remove(zpath)
        except Exception:
            pass
    # 地区計画データのダウンロード（東京都オープンデータ）
    chiku_dir = os.path.join(data_dir, "tokyo-toshikeikaku", "gis04_chikukeikaku")
    if not os.path.isdir(chiku_dir):
        try:
            resp = requests.get("https://www.opendata.metro.tokyo.lg.jp/toshiseibi/gis04_chikukeikaku.zip", timeout=120)
            resp.raise_for_status()
            os.makedirs(os.path.join(data_dir, "tokyo-toshikeikaku"), exist_ok=True)
            zpath = os.path.join(data_dir, "chiku_keikaku.zip")
            with open(zpath, "wb") as f:
                f.write(resp.content)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(os.path.join(data_dir, "tokyo-toshikeikaku", "gis04_chikukeikaku"))
            os.remove(zpath)
        except Exception as e:
            print(f"地区計画データのダウンロードに失敗: {e}")
    # 特別用途地区データ（A55 GeoJSON）はリポジトリに同梱済み（data/A55_tokubetsu_youto.geojson）
    # .prjファイルの確認・生成（CRS問題の防止）
    download_data.ensure_prj_files(data_dir)
    return load_zoning_data(), load_school_data(), load_chiku_keikaku_data(), load_tokubetsu_youto_data()

@st.cache_resource
def get_gdf():
    gdf, _, _, _ = setup_and_load()
    return gdf

@st.cache_resource
def get_school_gdf():
    _, school, _, _ = setup_and_load()
    return school

@st.cache_resource
def get_chiku_gdf():
    _, _, chiku, _ = setup_and_load()
    return chiku

@st.cache_resource
def get_tokubetsu_gdf():
    _, _, _, tokubetsu = setup_and_load()
    return tokubetsu


def display_result(result: ZoningResult):
    """判定結果をStreamlitネイティブコンポーネントで表示"""
    if result.error:
        st.error(f"📍 **{result.address}**\n\n{result.error}")
        return

    sogo = result.sogo_hantei or result.ryokan_kahi
    if sogo == "○":
        container = st.success
        emoji = "✅"
        label = "営業可能（リスク低）"
    elif sogo in ("△", "要確認"):
        container = st.warning
        emoji = "⚠️"
        label = sogo
    else:
        container = st.error
        emoji = "❌"
        label = "営業不可"

    container(f"📍 **{result.address}**\n\n"
              f"{emoji} **総合判定: {label}**\n\n"
              f"{result.sogo_detail or result.ryokan_detail}")

    with st.expander("詳細情報", expanded=True):
        st.markdown(f"**用途地域**: {result.youto_chiiki}（{result.ryokan_kahi} {result.ryokan_detail}）")

        # 特別用途地区（常に表示）
        if result.tokubetsu_youto:
            st.markdown(f"**特別用途地区**: ⚠️ {result.tokubetsu_youto}")
        else:
            st.markdown("**特別用途地区**: ✅ 該当なし")

        # 文教地区フォールバック（GISデータ未収録区のみ表示）
        if result.bunkyo_chiku:
            st.markdown(f"**文教地区（参考）**: {result.bunkyo_chiku}")

        # 地区計画（常に表示）
        if result.chiku_keikaku:
            st.markdown(f"**地区計画**: ⚠️ {result.chiku_keikaku}（区の都市計画課に用途制限を確認）")
        else:
            st.markdown("**地区計画**: ✅ 該当なし")

        # 学校チェック（常に表示）
        if result.schools_within_110m:
            st.markdown(f"**学校チェック**: 🔴 110m以内に{len(result.schools_within_110m)}件（学校照会が必要）")
            for name, stype, dist in result.schools_within_110m:
                st.markdown(f"- 🔴 **{name}**（{stype}）: {dist}m")
        if result.schools_within_300m:
            if not result.schools_within_110m:
                st.markdown(f"**学校チェック**: ⚠️ 110-300m圏内に{len(result.schools_within_300m)}件（要現地確認）")
            for name, stype, dist in result.schools_within_300m:
                st.markdown(f"- 🟡 {name}（{stype}）: {dist}m")
            st.caption("※住所のジオコーディング精度により実距離と誤差あり。最終確認は現地測定で。")
        if not result.schools_within_110m and not result.schools_within_300m:
            st.markdown("**学校チェック**: ✅ 300m以内に学校等なし")

        st.markdown(f"**座標**: ({result.lat:.6f}, {result.lon:.6f})")

        # 次のステップ
        if result.next_steps and sogo != "×":
            st.markdown("**次のステップ**:")
            for i, step in enumerate(result.next_steps, 1):
                st.markdown(f"{i}. {step}")


def extract_addresses_from_text(text: str) -> list[str]:
    """テキストから日本の住所を自動抽出する"""
    addresses = []

    # パターン1: 「東京都〇〇区...」形式
    pattern1 = r'東京都[^\s,、。\n]{2,}区[^\s,、。\n]*[0-9０-９丁目番号\-－ー]+'

    # パターン2: 「〇〇区...」形式（東京都省略）
    tokyo_wards = ['千代田区','中央区','港区','新宿区','文京区','台東区','墨田区','江東区',
                   '品川区','目黒区','大田区','世田谷区','渋谷区','中野区','杉並区','豊島区',
                   '北区','荒川区','板橋区','練馬区','足立区','葛飾区','江戸川区']
    pattern2 = r'(?:' + '|'.join(tokyo_wards) + r')[^\s,、。\n]*[0-9０-９丁目番号\-－ー]+'

    # パターン3: 「所在地」「住所」等のラベルの後ろ
    pattern3 = r'(?:所在地|住所|物件所在地|所在)[：:]\s*([^\n]+)'

    # パターン1で検索
    matches = re.findall(pattern1, text)
    for m in matches:
        addr = m.strip()
        if addr and addr not in addresses and len(addr) >= 6:
            addresses.append(addr)

    # パターン2で検索（パターン1で見つからなかった場合の補完）
    matches = re.findall(pattern2, text)
    for m in matches:
        addr = m.strip()
        # 既に見つかったものと重複チェック
        if addr and len(addr) >= 5 and not any(addr in a or a in addr for a in addresses):
            addresses.append(addr)

    # パターン3で検索
    matches = re.findall(pattern3, text)
    for m in matches:
        addr = m.strip()
        if addr and len(addr) >= 5 and not any(addr in a or a in addr for a in addresses):
            addresses.append(addr)

    # 全角数字を半角に正規化
    addresses = [unicodedata.normalize("NFKC", a) for a in addresses]

    return addresses


# ===== メイン画面 =====
st.title("🏨 1NIWA 用途地域チェッカー")
st.markdown(f'<p class="header-sub">住所を入力すると、旅館業の営業可否を自動判定します（東京都23区対応）　{APP_VERSION}</p>', unsafe_allow_html=True)

st.divider()

# タブ切り替え
tab1, tab2, tab6, tab4, tab5, tab3 = st.tabs(["📝 住所を入力", "📄 CSVで一括チェック", "📧 メールから物件チェック", "🔍 物件検索", "🏚️ 空き家バンク", "ℹ️ 用途地域の説明"])

with tab1:
    st.subheader("住所を入力して判定")

    col1, col2 = st.columns([3, 1])
    with col1:
        address = st.text_input(
            "住所",
            placeholder="例: 東京都新宿区歌舞伎町1丁目",
            label_visibility="collapsed",
        )
    with col2:
        check_btn = st.button("🔍 判定する", type="primary", use_container_width=True)

    # 複数住所の入力
    st.markdown("---")
    st.markdown("**複数住所をまとめて判定する場合（1行に1住所）:**")
    multi_addresses = st.text_area(
        "複数住所",
        placeholder="東京都新宿区歌舞伎町1丁目\n東京都渋谷区神宮前5丁目\n東京都港区六本木3丁目",
        height=120,
        label_visibility="collapsed",
    )
    multi_btn = st.button("🔍 まとめて判定", use_container_width=True)

    # 単一住所の判定
    if check_btn and address.strip():
        with st.spinner("判定中..."):
            gdf = get_gdf()
            result = check_zoning(address.strip(), gdf, get_school_gdf(), get_chiku_gdf(), get_tokubetsu_gdf())
        display_result(result)

    # 複数住所の判定
    if multi_btn and multi_addresses.strip():
        addresses = [a.strip() for a in multi_addresses.strip().split("\n") if a.strip()]
        if addresses:
            with st.spinner(f"{len(addresses)}件を判定中..."):
                gdf = get_gdf()
                results = []
                progress = st.progress(0)
                for i, addr in enumerate(addresses):
                    result = check_zoning(addr, gdf, get_school_gdf(), get_chiku_gdf(), get_tokubetsu_gdf())
                    results.append(result)
                    progress.progress((i + 1) / len(addresses))

            # 結果表示
            ok = sum(1 for r in results if r.ryokan_kahi == "○")
            cond = sum(1 for r in results if r.ryokan_kahi == "△")
            ng = sum(1 for r in results if r.ryokan_kahi == "×")
            err = sum(1 for r in results if r.error)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("✅ 営業可能", f"{ok}件")
            col2.metric("⚠️ 条件付き", f"{cond}件")
            col3.metric("❌ 営業不可", f"{ng}件")
            col4.metric("⚠️ エラー", f"{err}件")

            for r in results:
                display_result(r)

            # CSV ダウンロード
            df = pd.DataFrame([{
                "住所": r.address,
                "緯度": r.lat,
                "経度": r.lon,
                "用途地域": r.youto_chiiki or "",
                "旅館業可否": r.ryokan_kahi or "",
                "詳細": r.ryokan_detail or "",
                "エラー": r.error or "",
            } for r in results])

            csv_data = df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "📥 結果をCSVでダウンロード",
                csv_data,
                "zoning_results.csv",
                "text/csv",
                use_container_width=True,
            )

with tab2:
    st.subheader("CSVファイルで一括チェック")
    st.markdown("1列目に住所が入ったCSVファイルをアップロードしてください。")

    uploaded = st.file_uploader("CSVファイル", type=["csv"], label_visibility="collapsed")

    if uploaded:
        try:
            input_df = pd.read_csv(uploaded, encoding="utf-8-sig", header=None)
        except Exception:
            input_df = pd.read_csv(uploaded, encoding="shift_jis", header=None)

        addresses = []
        for val in input_df.iloc[:, 0]:
            s = str(val).strip()
            if s and s.lower() not in ["住所", "address", "addr", "nan"]:
                addresses.append(s)

        st.info(f"📋 {len(addresses)}件の住所を検出しました")

        if st.button("🔍 一括判定を実行", type="primary", use_container_width=True):
            with st.spinner(f"{len(addresses)}件を判定中..."):
                gdf = get_gdf()
                results = []
                progress = st.progress(0)
                for i, addr in enumerate(addresses):
                    result = check_zoning(addr, gdf, get_school_gdf(), get_chiku_gdf(), get_tokubetsu_gdf())
                    results.append(result)
                    progress.progress((i + 1) / len(addresses))

            ok = sum(1 for r in results if r.ryokan_kahi == "○")
            cond = sum(1 for r in results if r.ryokan_kahi == "△")
            ng = sum(1 for r in results if r.ryokan_kahi == "×")

            col1, col2, col3 = st.columns(3)
            col1.metric("✅ 営業可能", f"{ok}件")
            col2.metric("⚠️ 条件付き", f"{cond}件")
            col3.metric("❌ 営業不可", f"{ng}件")

            result_df = pd.DataFrame([{
                "住所": r.address,
                "用途地域": r.youto_chiiki or "",
                "旅館業": r.ryokan_kahi or "?",
                "詳細": r.ryokan_detail or r.error or "",
            } for r in results])

            st.dataframe(result_df, use_container_width=True, height=400)

            csv_data = result_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "📥 結果をCSVでダウンロード",
                csv_data,
                "zoning_results.csv",
                "text/csv",
                use_container_width=True,
            )

with tab6:
    st.subheader("📧 メールから物件チェック")
    st.markdown("athome等の物件通知メールの内容を貼り付けると、住所を自動抽出して旅館業可否を判定します。")

    email_text = st.text_area(
        "メール内容を貼り付け",
        placeholder="athomeやその他の不動産サイトから届いたメールの内容をここに貼り付けてください...",
        height=300,
        label_visibility="collapsed",
    )

    email_check_btn = st.button("🔍 住所を抽出して判定", type="primary", use_container_width=True, key="email_check")

    if email_check_btn and email_text.strip():
        # 住所を自動抽出
        addresses = extract_addresses_from_text(email_text)

        if addresses:
            st.success(f"📍 {len(addresses)}件の住所を検出しました")

            # 抽出した住所を表示
            with st.expander("抽出された住所", expanded=False):
                for i, addr in enumerate(addresses, 1):
                    st.markdown(f"{i}. {addr}")

            # チェッカーで判定
            with st.spinner(f"{len(addresses)}件を判定中..."):
                gdf = get_gdf()
                results = []
                progress = st.progress(0)
                for i, addr in enumerate(addresses):
                    result = check_zoning(addr, gdf, get_school_gdf(), get_chiku_gdf(), get_tokubetsu_gdf())
                    results.append(result)
                    progress.progress((i + 1) / len(addresses))

            # 結果サマリー
            ok = sum(1 for r in results if r.sogo_hantei == "○")
            cond = sum(1 for r in results if r.sogo_hantei == "△")
            check = sum(1 for r in results if r.sogo_hantei == "要確認")
            ng = sum(1 for r in results if r.sogo_hantei == "×")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("✅ 営業可能", f"{ok}件")
            col2.metric("⚠️ 条件付き", f"{cond}件")
            col3.metric("🔍 要確認", f"{check}件")
            col4.metric("❌ 営業不可", f"{ng}件")

            for r in results:
                display_result(r)
        else:
            st.warning("住所を検出できませんでした。メールの内容を確認してください。")

with tab4:
    st.subheader("🔍 物件検索")

    # === 自動収集セクション ===
    st.markdown("### 自動収集（ジモティー・家いちば）")
    st.markdown("ボタンを押すと、ジモティーと家いちばから東京23区の物件を自動収集し、旅館業可否を判定します。")

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        auto_all = st.button("🤖 全サイト自動収集", type="primary", use_container_width=True)
    with col_s2:
        auto_ieichiba = st.button("🏠 家いちばのみ", use_container_width=True)
    with col_s3:
        auto_jimoty = st.button("📦 ジモティーのみ", use_container_width=True)

    if auto_all or auto_ieichiba or auto_jimoty:
        try:
            from auto_search import search_ieichiba, search_jimoty
            all_properties = []

            if auto_all or auto_ieichiba:
                with st.spinner("家いちばを検索中..."):
                    ie_props = search_ieichiba()
                    all_properties.extend(ie_props)
                    st.success(f"家いちば: {len(ie_props)}件取得")

            if auto_all or auto_jimoty:
                with st.spinner("ジモティーを検索中（時間がかかる場合があります）..."):
                    jm_props = search_jimoty(max_pages=1)
                    all_properties.extend(jm_props)
                    st.success(f"ジモティー: {len(jm_props)}件取得")

            if all_properties:
                # 住所があるものをチェッカーで判定
                props_with_addr = [p for p in all_properties if p.get("address")]
                props_no_addr = [p for p in all_properties if not p.get("address")]

                if props_with_addr:
                    st.markdown(f"### 判定結果（住所あり: {len(props_with_addr)}件）")
                    gdf = get_gdf()
                    progress = st.progress(0)
                    for i, prop in enumerate(props_with_addr):
                        result = check_zoning(prop["address"], gdf, get_school_gdf(), get_chiku_gdf(), get_tokubetsu_gdf())
                        prop["zoning_result"] = result
                        progress.progress((i + 1) / len(props_with_addr))

                    # 旅館業可否別に表示
                    ok_props = [p for p in props_with_addr if p["zoning_result"].ryokan_kahi in ("○",)]
                    cond_props = [p for p in props_with_addr if p["zoning_result"].ryokan_kahi in ("△",)]
                    check_props = [p for p in props_with_addr if p["zoning_result"].sogo_hantei == "要確認"]
                    ng_props = [p for p in props_with_addr if p["zoning_result"].ryokan_kahi in ("×",) or p["zoning_result"].sogo_hantei == "×"]

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("✅ 営業可能", f"{len(ok_props)}件")
                    col2.metric("⚠️ 条件付き", f"{len(cond_props)}件")
                    col3.metric("🔍 要確認", f"{len(check_props)}件")
                    col4.metric("❌ 営業不可", f"{len(ng_props)}件")

                    # 候補物件を表示
                    good_props = ok_props + cond_props + check_props
                    if good_props:
                        st.markdown("#### 候補物件")
                        for p in good_props:
                            r = p["zoning_result"]
                            with st.expander(f"{r.sogo_hantei} {p.get('title', '物件名不明')} — {p.get('address', '')}"):
                                st.markdown(f"**ソース**: [{p.get('source', '')}]({p.get('url', '')})")
                                if p.get("price"):
                                    st.markdown(f"**価格**: {p['price']}")
                                display_result(r)

                    # CSV出力
                    result_rows = []
                    for p in props_with_addr:
                        r = p["zoning_result"]
                        result_rows.append({
                            "物件名": p.get("title", ""),
                            "住所": p.get("address", ""),
                            "価格": p.get("price", ""),
                            "ソース": p.get("source", ""),
                            "URL": p.get("url", ""),
                            "用途地域": r.youto_chiiki or "",
                            "旅館業可否": r.ryokan_kahi or "",
                            "特別用途地区": r.tokubetsu_youto or "該当なし",
                            "地区計画": r.chiku_keikaku or "該当なし",
                            "総合判定": r.sogo_hantei or "",
                        })
                    df = pd.DataFrame(result_rows)
                    csv_data = df.to_csv(index=False, encoding="utf-8-sig")
                    st.download_button("📥 判定結果をCSVでダウンロード", csv_data, "bukken_auto_results.csv", "text/csv", use_container_width=True)

                if props_no_addr:
                    st.markdown(f"#### 住所未取得（{len(props_no_addr)}件 — 手動確認）")
                    for p in props_no_addr:
                        st.markdown(f"- [{p.get('title', '物件名不明')}]({p.get('url', '')}) — {p.get('price', '価格不明')}")
            else:
                st.info("該当する物件が見つかりませんでした。")

        except Exception as e:
            st.error(f"自動収集でエラーが発生しました: {e}")

    st.divider()

    # === 手動検索リンク集 ===
    st.markdown("### 手動検索リンク集")
    search_sites = {
        "athome（賃貸一戸建て）": {
            "東京23区 一戸建て": "https://www.athome.co.jp/chintai/kodate/tokyo/",
        },
        "ジモティー": {
            "東京都 不動産": "https://jmty.jp/tokyo/estate",
        },
        "家いちば（空き家売買）": {
            "東京都": "https://www.ieichiba.com/area/tokyo",
        },
        "LIFULL HOME'S": {
            "東京23区": "https://www.homes.co.jp/chintai/tokyo/city/",
        },
    }

    for site, links in search_sites.items():
        for name, url in links.items():
            st.markdown(f"- **{site}**: [{name}]({url})")

    st.divider()
    st.markdown("### 検索条件メモ")
    st.info("""
    **物件種別:** 一戸建て・長屋・古民家・倉庫・空き家
    **賃料:** 月額30〜50万円  |  **面積:** 50〜120㎡
    **必須:** 旅館業営業OK（オーナー同意）・リノベ可
    **優先:** 和の要素あり・路地裏/隠れ家感・庭付き
    """)

    st.markdown("### 優先エリア（Aランク）")
    area_data = pd.DataFrame([
        {"エリア": "台東区 谷根千", "特徴": "古民家・長屋多数、欧米旅行者に人気", "賃料感": "やや高め"},
        {"エリア": "墨田区 向島・京島", "特徴": "長屋密集地、スカイツリー近接", "賃料感": "安価"},
        {"エリア": "荒川区 日暮里", "特徴": "成田空港直結、谷根千の裏手", "賃料感": "中程度"},
        {"エリア": "台東区 蔵前・浅草橋", "特徴": "倉庫リノベ文化、浅草至近", "賃料感": "中〜高"},
    ])
    st.dataframe(area_data, use_container_width=True, hide_index=True)

with tab5:
    st.subheader("🏚️ 空き家バンク・自治体情報")
    st.markdown("各区の空き家対策ページ・空き家バンクへのリンクです。")

    akiya_data = [
        ("台東区", "https://www.city.taito.lg.jp/", "谷中・根津・千駄木。古民家多数"),
        ("墨田区", "https://www.city.sumida.lg.jp/", "京島・向島。長屋密集地帯"),
        ("荒川区", "https://www.city.arakawa.tokyo.jp/", "日暮里・西日暮里。谷根千に隣接"),
        ("品川区", "https://www.city.shinagawa.tokyo.jp/", "戸越・中延。商店街エリア"),
        ("大田区", "https://www.city.ota.tokyo.jp/", "蒲田・池上。羽田空港近接。注意: 一部文教地区"),
        ("江東区", "https://www.city.koto.lg.jp/", "門前仲町・清澄白河エリア"),
        ("中央区", "https://www.city.chuo.lg.jp/", "日本橋・人形町に和の物件の可能性"),
        ("北区", "https://www.city.kita.tokyo.jp/", "十条・王子。商店街文化"),
        ("文京区", "https://www.city.bunkyo.lg.jp/", "注意: 大部分が文教地区。旅館業は原則不可"),
        ("豊島区", "https://www.city.toshima.lg.jp/", "注意: 一部文教地区あり（目白・雑司が谷）"),
    ]

    for ku, url, note in akiya_data:
        st.markdown(f"**{ku}** — [{ku}公式サイト]({url})　{note}")

    st.divider()
    st.markdown("### 全国空き家バンク")
    st.markdown("- [LIFULL HOME'S 空き家バンク（東京都）](https://www.homes.co.jp/akiyabank/tokyo/)")
    st.markdown("- [家いちば（空き家マッチング）](https://www.ieichiba.com/area/tokyo)")

    st.info("空き家を見つけたら「📝 住所を入力」タブで旅館業可否をすぐチェックできます。")

with tab3:
    st.subheader("用途地域と旅館業営業の関係")

    st.markdown("""
    旅館業（簡易宿所）の営業には、物件の所在地が**旅館業営業可能な用途地域**であることが必須です。
    これは建築基準法第48条に基づく制限です。
    """)

    zone_data = pd.DataFrame([
        {"用途地域": "商業地域", "旅館業": "○ 可能", "備考": "最も営業しやすい"},
        {"用途地域": "近隣商業地域", "旅館業": "○ 可能", "備考": "営業可能"},
        {"用途地域": "準工業地域", "旅館業": "○ 可能", "備考": "営業可能"},
        {"用途地域": "第二種住居地域", "旅館業": "○ 可能", "備考": "営業可能"},
        {"用途地域": "準住居地域", "旅館業": "○ 可能", "備考": "営業可能"},
        {"用途地域": "工業地域", "旅館業": "○ 可能", "備考": "法的には可能（立地的に不向き）"},
        {"用途地域": "第一種住居地域", "旅館業": "△ 条件付き", "備考": "3,000㎡以下のみ可能"},
        {"用途地域": "第一種低層住居専用地域", "旅館業": "× 不可", "備考": ""},
        {"用途地域": "第二種低層住居専用地域", "旅館業": "× 不可", "備考": ""},
        {"用途地域": "第一種中高層住居専用地域", "旅館業": "× 不可", "備考": ""},
        {"用途地域": "第二種中高層住居専用地域", "旅館業": "× 不可", "備考": ""},
        {"用途地域": "工業専用地域", "旅館業": "× 不可", "備考": ""},
        {"用途地域": "田園住居地域", "旅館業": "× 不可", "備考": ""},
    ])
    st.dataframe(zone_data, use_container_width=True, hide_index=True)

    st.warning("""
    **注意**: 用途地域が「○」でも、以下の追加条件があります:
    - **学校照会**: 施設から110m以内の学校等への照会
    - **地区計画・特別用途地区**: 区独自の制限がある場合あり
    - **文教地区規制**: 文京区等で追加制限
    - **構造設備基準**: 客室面積、帳場、衛生設備等
    - **消防設備**: 消防法に基づく設備要件
    - **近隣説明**: 区によっては事前の近隣説明が必要

    このツールは用途地域による判定のみを行います。最終的な許可可否は保健所への事前相談で確認してください。
    """)
