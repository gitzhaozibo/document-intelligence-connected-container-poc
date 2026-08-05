"""Document Intelligence 解析結果の根拠領域処理を集約した再利用可能モジュール。

Document Intelligence の解析結果（analyzeResult）が取得済みであることを前提に、
以下の一連の処理を :class:`DocumentEvidence` 1 クラスで提供します。

1. ``pages[].lines[]`` からの根拠領域（テキスト + 正規化座標）のリスト化
2. GPT へ渡す行 ID 付きプロンプトテキストの生成
3. GPT が返した根拠行 ID（source_ids）から領域情報への逆引き
4. PDF ページ画像への根拠領域ハイライト描画

依存関係を最小にするため Pydantic には依存せず、ハイライト描画に必要な
PyMuPDF（fitz）はメソッド内で遅延 import します。そのため OCR 抽出側
（描画不要）のアプリは PyMuPDF なしで利用できます。

他のアプリでの利用例::

    from shared.document_evidence import DocumentEvidence

    evidence = DocumentEvidence.from_analyze_result(analyze_result)
    prompt_text = evidence.to_prompt_text()      # GPT へ送る行一覧
    ...  # GPT に prompt_text を渡し、根拠行 ID（source_ids）を受け取る
    sources = evidence.resolve_source_ids(ids)   # ID → 領域情報へ逆引き
    png = DocumentEvidence.render_pdf_page(pdf_bytes, page_number, sources)
"""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceRegion:
    """抽出値の根拠となる PDF 上の 1 行分の領域。"""

    source_id: str
    page_number: int
    text: str
    polygon: list[float]
    """ページ幅・高さで 0〜1 に正規化した四角形（x1,y1,...,x4,y4 の 8 値）。"""

    def to_dict(self) -> dict[str, Any]:
        """JSON シリアライズ可能な辞書へ変換します。"""
        return asdict(self)


def _region_page_number(source: Any) -> int | None:
    if isinstance(source, dict):
        value = source.get("page_number")
    else:
        value = getattr(source, "page_number", None)
    return int(value) if value is not None else None


def _region_polygon(source: Any) -> list[float]:
    if isinstance(source, dict):
        polygon = source.get("polygon")
    else:
        polygon = getattr(source, "polygon", None)
    return list(polygon) if polygon else []


class DocumentEvidence:
    """Document Intelligence 解析結果の根拠領域を扱うユーティリティ。

    行 ID・ページ番号・テキスト・正規化座標を持つ領域リストを保持し、
    GPT 連携（プロンプト生成・根拠 ID の逆引き）と PDF ハイライト描画を
    提供します。領域は ``source_id`` / ``page_number`` / ``text`` /
    ``polygon`` 属性を持つ任意のオブジェクト（Pydantic モデル等）でも
    受け付けます。
    """

    def __init__(self, regions: list[Any]) -> None:
        self.regions = regions
        self._by_id = {region.source_id: region for region in regions}

    # ------------------------------------------------------------------
    # 1. 解析結果からの領域リスト化
    # ------------------------------------------------------------------
    @classmethod
    def from_analyze_result(cls, analyze_result: dict[str, Any]) -> "DocumentEvidence":
        """analyzeResult の ``pages[].lines[]`` から領域リストを構築します。

        polygon 座標はページの width / height で 0〜1 に正規化します
        （偶数インデックス = x 座標 → width、奇数 = y 座標 → height）。
        寸法が不正なページや、テキスト空・頂点数不正の行は除外します。
        """
        regions: list[EvidenceRegion] = []
        for page in analyze_result.get("pages") or []:
            width = float(page.get("width") or 0)
            height = float(page.get("height") or 0)
            if width <= 0 or height <= 0:
                continue
            page_number = int(page.get("pageNumber") or len(regions) + 1)
            for line in page.get("lines") or []:
                text = str(line.get("content") or "").strip()
                polygon = line.get("polygon") or []
                if not text or len(polygon) != 8:
                    continue
                normalized = [
                    float(value) / (width if index % 2 == 0 else height)
                    for index, value in enumerate(polygon)
                ]
                regions.append(
                    EvidenceRegion(
                        source_id=f"L{len(regions) + 1}",
                        page_number=page_number,
                        text=text,
                        polygon=normalized,
                    )
                )
        return cls(regions)

    # ------------------------------------------------------------------
    # 2. GPT プロンプト用テキスト生成
    # ------------------------------------------------------------------
    def to_prompt_text(self) -> str:
        """``L1 [page 1] テキスト`` 形式の行一覧テキストを生成します。"""
        return "\n".join(
            f"{region.source_id} [page {region.page_number}] {region.text}"
            for region in self.regions
        )

    # ------------------------------------------------------------------
    # 3. GPT が返した根拠 ID の逆引き
    # ------------------------------------------------------------------
    def resolve_source_ids(self, source_ids: Any) -> list[Any]:
        """GPT が返した source_ids を領域リストへ解決します。

        リスト以外や未知の ID は黙って除外し、有効な領域のみ返します。
        """
        valid_ids = source_ids if isinstance(source_ids, list) else []
        return [
            self._by_id[source_id]
            for source_id in valid_ids
            if isinstance(source_id, str) and source_id in self._by_id
        ]

    # ------------------------------------------------------------------
    # 4. PDF ハイライト描画
    # ------------------------------------------------------------------
    @staticmethod
    def render_pdf_page(
        pdf_content: bytes,
        page_number: int,
        sources: list[Any],
        *,
        zoom: float = 1.5,
    ) -> bytes:
        """指定ページを根拠領域のハイライト付き PNG に変換します。

        ``sources`` には :class:`EvidenceRegion`、Pydantic モデル、
        API レスポンス由来の辞書のいずれも指定できます。正規化済み
        polygon にページ寸法を掛け戻して矩形を復元します。

        NOTE: PyMuPDF（fitz）はこのメソッド内で遅延 import するため、
        描画を使わないアプリは PyMuPDF なしで本モジュールを利用できます。
        """
        import fitz

        with fitz.open(stream=pdf_content, filetype="pdf") as document:
            page = document[page_number - 1]
            for source in sources:
                if _region_page_number(source) != page_number:
                    continue
                polygon = _region_polygon(source)
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
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return pixmap.tobytes("png")
