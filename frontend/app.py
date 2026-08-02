"""Document Intelligence PoC の Streamlit 画面。"""

import os
from typing import Any

import streamlit as st

from frontend.api_client import ApiError, DocumentApiClient

ACCEPTED_FILE_TYPES = ["pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "heif"]


def _display_result(result: dict[str, Any]) -> None:
    analyze_result = result.get("result") or {}
    content = analyze_result.get("content")
    st.success("解析が完了しました。")
    if content:
        st.subheader("抽出テキスト")
        st.text_area("OCR 結果", value=str(content), height=300, disabled=True)
    st.subheader("解析結果 JSON")
    st.json(analyze_result)


def run_app() -> None:
    """Streamlit アプリケーションを描画します。"""
    st.set_page_config(page_title="Document Intelligence PoC", page_icon="📄", layout="wide")
    st.title("Document Intelligence Connected Container PoC")
    st.write("PDF または画像をアップロードし、FastAPI 経由でローカル OCR を実行します。")

    uploaded_file = st.file_uploader(
        "解析するファイル",
        type=ACCEPTED_FILE_TYPES,
        help="PDF、JPEG、PNG、TIFF、BMP、HEIF（最大サイズは FastAPI の設定に従います）",
    )

    with st.expander("ページ・解析オプション", expanded=True):
        pages = st.text_input("対象ページ", placeholder="例: 1-3,5", help="空欄の場合は全ページ")
        locale = st.text_input("言語ロケール", placeholder="例: ja-JP")
        output_format = st.selectbox("出力形式", options=["json", "markdown"])
        high_resolution = st.checkbox("高解像度 OCR", help="ocrHighResolution 機能を有効にします")
        detect_languages = st.checkbox("言語検出", help="languages 機能を有効にします")
        detect_barcodes = st.checkbox("バーコード検出", help="barcodes 機能を有効にします")

    if st.button("実行", type="primary", disabled=uploaded_file is None):
        features = [
            feature
            for enabled, feature in (
                (high_resolution, "ocrHighResolution"),
                (detect_languages, "languages"),
                (detect_barcodes, "barcodes"),
            )
            if enabled
        ]
        options = {
            "pages": pages,
            "locale": locale,
            "features": ",".join(features),
            "output_content_format": output_format,
        }
        api_url = os.getenv("FASTAPI_BASE_URL", "http://localhost:8000")
        client = DocumentApiClient(api_url)

        with st.spinner("Document Intelligence で解析しています..."):
            try:
                result = client.analyze(
                    filename=uploaded_file.name,
                    content=uploaded_file.getvalue(),
                    content_type=uploaded_file.type or "application/octet-stream",
                    options=options,
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                _display_result(result)


run_app()
