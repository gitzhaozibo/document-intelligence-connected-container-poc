"""DocumentEvidence 共有クラスの単体テスト。"""

from shared.document_evidence import DocumentEvidence, EvidenceRegion

_ANALYZE_RESULT = {
    "pages": [
        {
            "pageNumber": 1,
            "width": 10,
            "height": 20,
            "lines": [
                {"content": "株式会社サンプル", "polygon": [1, 2, 5, 2, 5, 4, 1, 4]},
                {"content": "", "polygon": [1, 1, 2, 1, 2, 2, 1, 2]},
                {"content": "頂点数不正", "polygon": [1, 1, 2, 2]},
            ],
        },
        {
            "pageNumber": 2,
            "width": 0,
            "height": 20,
            "lines": [
                {"content": "寸法不正ページ", "polygon": [1, 1, 2, 1, 2, 2, 1, 2]},
            ],
        },
        {
            "pageNumber": 3,
            "width": 10,
            "height": 10,
            "lines": [
                {"content": "証券コード 1234", "polygon": [1, 1, 5, 1, 5, 2, 1, 2]},
            ],
        },
    ]
}


def test_from_analyze_result_normalizes_and_skips_invalid_entries() -> None:
    evidence = DocumentEvidence.from_analyze_result(_ANALYZE_RESULT)

    assert [region.source_id for region in evidence.regions] == ["L1", "L2"]
    first = evidence.regions[0]
    assert first.page_number == 1
    assert first.polygon == [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2]
    assert evidence.regions[1].page_number == 3


def test_to_prompt_text_formats_line_ids() -> None:
    evidence = DocumentEvidence.from_analyze_result(_ANALYZE_RESULT)

    assert evidence.to_prompt_text() == (
        "L1 [page 1] 株式会社サンプル\nL2 [page 3] 証券コード 1234"
    )


def test_resolve_source_ids_filters_unknown_and_invalid_values() -> None:
    evidence = DocumentEvidence.from_analyze_result(_ANALYZE_RESULT)

    resolved = evidence.resolve_source_ids(["L2", "unknown", 5])
    assert [region.source_id for region in resolved] == ["L2"]
    assert evidence.resolve_source_ids(None) == []
    assert evidence.resolve_source_ids("L1") == []


def test_render_pdf_page_accepts_region_objects_and_dicts() -> None:
    import fitz

    document = fitz.open()
    document.new_page(width=100, height=100)
    pdf_content = document.tobytes()
    document.close()

    sources = [
        EvidenceRegion(
            source_id="L1",
            page_number=1,
            text="サンプル",
            polygon=[0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2],
        ),
        {
            "source_id": "L2",
            "page_number": 1,
            "text": "辞書形式",
            "polygon": [0.2, 0.4, 0.6, 0.4, 0.6, 0.5, 0.2, 0.5],
        },
        {"source_id": "L3", "page_number": 2, "text": "対象外ページ", "polygon": []},
    ]

    png = DocumentEvidence.render_pdf_page(pdf_content, 1, sources)
    assert png.startswith(b"\x89PNG")
