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

APP_VERSION = "v2.1.0-crs-fix"

# 起動時にキャッシュを全クリア（CRS修正を確実に反映するため）
st.cache_resource.clear()

from zoning_checker import load_zoning_data, load_school_data, check_zoning, ZoningResult

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
    # .prjファイルの確認・生成（CRS問題の防止）
    download_data.ensure_prj_files(data_dir)

    # デバッグ情報を収集
    debug_info = {}
    # .prjファイルの存在確認
    for root, dirs, files in os.walk(data_dir):
        for f in files:
            if f.endswith(".prj"):
                prj_path = os.path.join(root, f)
                with open(prj_path, "r") as pf:
                    debug_info["prj_content"] = pf.read()
                debug_info["prj_path"] = prj_path

    gdf = load_zoning_data()
    school_gdf = load_school_data()

    debug_info["crs"] = str(gdf.crs) if gdf.crs else "None"
    debug_info["epsg"] = str(gdf.crs.to_epsg()) if gdf.crs else "None"
    debug_info["records"] = len(gdf)
    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    debug_info["bounds"] = f"lon: {bounds[0]:.6f}~{bounds[2]:.6f}, lat: {bounds[1]:.6f}~{bounds[3]:.6f}"

    return gdf, school_gdf, debug_info

@st.cache_resource
def get_gdf():
    gdf, _, _ = setup_and_load()
    return gdf

@st.cache_resource
def get_school_gdf():
    _, school, _ = setup_and_load()
    return school

def get_debug_info():
    _, _, debug = setup_and_load()
    return debug


def result_to_html(result: ZoningResult) -> str:
    """判定結果をHTML表示用に変換"""
    if result.error:
        return f"""<div class="result-err">
            <h3>📍 {result.address}</h3>
            <p>⚠️ {result.error}</p>
        </div>"""

    sogo = result.sogo_hantei or result.ryokan_kahi
    if sogo == "○":
        css_class = "result-ok"
        emoji = "✅"
        label = "営業可能（リスク低）"
    elif sogo in ("△", "要確認"):
        css_class = "result-cond"
        emoji = "⚠️"
        label = sogo
    else:
        css_class = "result-ng"
        emoji = "❌"
        label = "営業不可"

    school_html = ""
    school_content = ""
    if result.schools_within_110m:
        lines = "".join(f"<li>🔴 <b>{n}</b>（{t}）: {d}m</li>" for n, t, d in result.schools_within_110m)
        school_content += f"<b>110m以内: {len(result.schools_within_110m)}件（学校照会必要）</b><ul style='margin:5px 0;'>{lines}</ul>"
    if result.schools_within_200m:
        lines = "".join(f"<li>🟡 {n}（{t}）: {d}m</li>" for n, t, d in result.schools_within_200m)
        school_content += f"<b>110-200m圏内: {len(result.schools_within_200m)}件（要現地確認）</b><ul style='margin:5px 0;'>{lines}</ul>"
    if school_content:
        school_html = f'<tr><td><b>学校チェック</b></td><td>{school_content}<small style="color:#888;">※住所のジオコーディング精度により実距離と誤差あり。最終確認は現地測定で。</small></td></tr>'
    else:
        school_html = '<tr><td><b>学校チェック</b></td><td>✅ 200m以内に学校等なし</td></tr>'

    bunkyo_html = ""
    if result.bunkyo_chiku:
        bunkyo_html = f'<tr><td><b>文教地区</b></td><td>{result.bunkyo_chiku}</td></tr>'

    next_html = ""
    if result.next_steps and sogo != "×":
        steps = "".join(f"<li>{s}</li>" for s in result.next_steps)
        next_html = f'<tr><td><b>次のステップ</b></td><td><ol style="margin:5px 0;padding-left:20px;">{steps}</ol></td></tr>'

    return f"""<div class="{css_class}">
        <h3>📍 {result.address}</h3>
        <table style="width:100%; font-size:15px;">
            <tr><td style="width:120px;"><b>総合判定</b></td><td>{emoji} <b>{label}</b> — {result.sogo_detail or result.ryokan_detail}</td></tr>
            <tr><td><b>用途地域</b></td><td>{result.youto_chiiki}（{result.ryokan_kahi} {result.ryokan_detail}）</td></tr>
            {bunkyo_html}
            {school_html}
            <tr><td><b>座標</b></td><td>({result.lat:.6f}, {result.lon:.6f})</td></tr>
            {next_html}
        </table>
    </div>"""


# ===== メイン画面 =====
st.title("🏨 1NIWA 用途地域チェッカー")
st.markdown(f'<p class="header-sub">住所を入力すると、旅館業の営業可否を自動判定します（東京都23区対応）　{APP_VERSION}</p>', unsafe_allow_html=True)

# デバッグ情報（原因特定用・後で削除）
with st.expander("🔧 デバッグ情報（開発用）"):
    debug = get_debug_info()
    st.json(debug)
    # テスト用ジオコーディング
    from zoning_checker import geocode
    test_coords = geocode("江東区豊洲3丁目4-1")
    if test_coords:
        st.write(f"テスト座標（豊洲3-4-1）: lon={test_coords[0]}, lat={test_coords[1]}")
    else:
        st.write("テスト座標: ジオコーディング失敗")

st.divider()

# タブ切り替え
tab1, tab2, tab3 = st.tabs(["📝 住所を入力", "📄 CSVで一括チェック", "ℹ️ 用途地域の説明"])

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
            result = check_zoning(address.strip(), gdf, get_school_gdf())
        st.markdown(result_to_html(result), unsafe_allow_html=True)

    # 複数住所の判定
    if multi_btn and multi_addresses.strip():
        addresses = [a.strip() for a in multi_addresses.strip().split("\n") if a.strip()]
        if addresses:
            with st.spinner(f"{len(addresses)}件を判定中..."):
                gdf = get_gdf()
                results = []
                progress = st.progress(0)
                for i, addr in enumerate(addresses):
                    result = check_zoning(addr, gdf, get_school_gdf())
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
                st.markdown(result_to_html(r), unsafe_allow_html=True)

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
                    result = check_zoning(addr, gdf, get_school_gdf())
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
