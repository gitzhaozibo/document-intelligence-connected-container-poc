"""Document Intelligence PoC の Streamlit 画面。"""

import os
from hashlib import sha256
from typing import Any

import fitz
import streamlit as st

from frontend.api_client import ApiError, DocumentApiClient

ACCEPTED_FILE_TYPES = ["pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "heif"]


def _render_pdf_page(content: bytes, page_number: int, sources: list[dict[str, Any]]) -> bytes:
    """指定ページを根拠領域付きの PNG に変換します。"""
    with fitz.open(stream=content, filetype="pdf") as document:
        page = document[page_number - 1]
        for source in sources:
            if source.get("page_number") != page_number:
                continue
            polygon = source.get("polygon") or []
            if len(polygon) != 8:
                continue
            xs = polygon[0::2]
            ys = polygon[1::2]
            rect = fitz.Rect(
                min(xs) * page.rect.width,
                min(ys) * page.rect.height,
                max(xs) * page.rect.width,
                max(ys) * page.rect.height,
            )
            annotation = page.add_rect_annot(rect)
            annotation.set_colors(stroke=(1, 0.55, 0), fill=(1, 0.85, 0))
            annotation.set_opacity(0.35)
            annotation.update()
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return pixmap.tobytes("png")


def _display_financial_summary(result: dict[str, Any], pdf_content: bytes) -> None:
    fields = result.get("fields") or []
    st.success("決算短信から情報を抽出しました。")
    if result.get("cache_hit"):
        st.info("同一 PDF の保存済み解析結果をデータベースから取得しました。")
    else:
        st.caption("OCR と情報抽出を新規実行し、データベースへ保存しました。")
    st.subheader("抽出結果")
    st.dataframe(
        [
            {
                "項目": field.get("label", ""),
                "値": field.get("value") or "（未検出）",
                "元情報": " / ".join(
                    source.get("text", "") for source in field.get("sources") or []
                ),
            }
            for field in fields
        ],
        use_container_width=True,
        hide_index=True,
    )

    field_options = {field.get("label", field.get("name", "")): field for field in fields}
    selected_label = st.selectbox("PDF 上で確認する項目", options=list(field_options))
    selected = field_options[selected_label]
    sources = selected.get("sources") or []
    if sources:
        st.caption("元情報: " + " / ".join(source["text"] for source in sources))
        source_pages = sorted({int(source["page_number"]) for source in sources})
        page_number = st.selectbox("根拠ページ", options=source_pages)
        st.image(
            _render_pdf_page(pdf_content, page_number, sources),
            caption=f"{page_number} ページ（オレンジ色が元情報の位置）",
            use_container_width=True,
        )
    else:
        st.info("この項目の元情報は特定できませんでした。")


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

    if uploaded_file is not None and uploaded_file.type == "application/pdf":
        pdf_content = uploaded_file.getvalue()
        document_id = sha256(pdf_content).hexdigest()
        if st.session_state.get("summary_document_id") != document_id:
            st.session_state.pop("summary_result", None)
            st.session_state.pop("summary_excel", None)
        st.subheader("アップロード PDF")
        try:
            with fitz.open(stream=pdf_content, filetype="pdf") as document:
                preview_page = st.number_input(
                    "表示ページ",
                    min_value=1,
                    max_value=document.page_count,
                    value=1,
                    step=1,
                )
            st.image(
                _render_pdf_page(pdf_content, int(preview_page), []),
                use_container_width=True,
            )
        except (fitz.FileDataError, ValueError):
            st.error("PDF を表示できません。ファイルが破損していないか確認してください。")

        if st.button("決算短信の情報を抽出", type="primary"):
            api_url = os.getenv("FASTAPI_BASE_URL", "http://localhost:8000")
            client = DocumentApiClient(api_url)
            with st.spinner("OCR と Azure GPT で会社名・コード・決算期を抽出しています..."):
                try:
                    summary = client.extract_financial_summary(
                        filename=uploaded_file.name,
                        content=pdf_content,
                        content_type=uploaded_file.type,
                    )
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["summary_result"] = summary
                    st.session_state["summary_document_id"] = document_id

        summary_result = st.session_state.get("summary_result")
        if summary_result and st.session_state.get("summary_document_id") == document_id:
            _display_financial_summary(summary_result, pdf_content)
            document_id = summary_result.get("document_id")
            if document_id and st.button("Excelを作成"):
                api_url = os.getenv("FASTAPI_BASE_URL", "http://localhost:8000")
                client = DocumentApiClient(api_url)
                try:
                    st.session_state["summary_excel"] = client.download_financial_summary_excel(
                        str(document_id)
                    )
                except ApiError as exc:
                    st.error(str(exc))
            excel_content = st.session_state.get("summary_excel")
            if excel_content:
                st.download_button(
                    "Excelをダウンロード",
                    data=excel_content,
                    file_name="financial-summary.xlsx",
                    mime=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                )

    with st.expander("ページ・解析オプション", expanded=True):
        pages = st.text_input("対象ページ", placeholder="例: 1-3,5", help="空欄の場合は全ページ")
        locale = st.text_input("言語ロケール", placeholder="例: ja-JP")
        output_format = st.selectbox("本文形式", options=["text", "markdown"])
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
