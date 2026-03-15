#!/usr/bin/env python3
"""
1NIWA 用途地域判定 Web アプリ
奥様がブラウザから住所を入力して旅館業の営業可否を確認できる。

起動方法:
  streamlit run app.py

ブラウザで http://localhost:8501 が自動的に開きます。
"""

import streamlit as st
import pandas as pd

APP_VERSION = "v4.0.0"

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


# ===== メイン画面 =====
st.title("🏨 1NIWA 用途地域チェッカー")
st.markdown(f'<p class="header-sub">住所を入力すると、旅館業の営業可否を自動判定します（東京都23区対応）　{APP_VERSION}</p>', unsafe_allow_html=True)

st.divider()

# タブ切り替え
tab1, tab2, tab4, tab5, tab3 = st.tabs(["📝 住所を入力", "📄 CSVで一括チェック", "🔍 物件検索", "🏚️ 空き家バンク", "ℹ️ 用途地域の説明"])

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

with tab4:
    st.subheader("🔍 物件検索リンク集")
    st.markdown("1NIWAの条件（23区・賃貸一戸建て・月30〜50万円）に合わせた検索リンクです。")

    st.markdown("### 賃貸一戸建て")
    search_sites = {
        "SUUMO": {
            "台東区（谷根千）": "https://suumo.jp/chintai/tokyo/sc_taito/?ts=2&ts=3",
            "墨田区（向島・京島）": "https://suumo.jp/chintai/tokyo/sc_sumida/?ts=2&ts=3",
            "荒川区（日暮里）": "https://suumo.jp/chintai/tokyo/sc_arakawa/?ts=2&ts=3",
            "品川区（戸越・中延）": "https://suumo.jp/chintai/tokyo/sc_shinagawa/?ts=2&ts=3",
            "大田区（蒲田・池上）": "https://suumo.jp/chintai/tokyo/sc_ota/?ts=2&ts=3",
            "台東区（蔵前・浅草橋）": "https://suumo.jp/chintai/tokyo/sc_taito/?ts=2&ts=3",
        },
        "athome": {
            "東京23区 一戸建て": "https://www.athome.co.jp/chintai/kodate/tokyo/",
        },
        "LIFULL HOME'S": {
            "東京23区": "https://www.homes.co.jp/chintai/tokyo/city/",
        },
        "ジモティー": {
            "東京都 不動産": "https://jmty.jp/tokyo/estate",
        },
        "家いちば（空き家売買）": {
            "東京都": "https://www.ieichiba.com/area/tokyo",
        },
    }

    for site, links in search_sites.items():
        with st.expander(f"**{site}**", expanded=False):
            for name, url in links.items():
                st.markdown(f"- [{name}]({url})")

    st.divider()
    st.markdown("### 検索条件メモ")
    st.info("""
    **物件種別:** 一戸建て・長屋・古民家・倉庫・空き家
    **賃料:** 月額30〜50万円  |  **面積:** 50〜120㎡
    **必須:** 旅館業営業OK（オーナー同意）・リノベ可
    **優先:** 和の要素あり・路地裏/隠れ家感・庭付き

    気になる物件を見つけたら「📝 住所を入力」タブで旅館業可否をチェック！
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
