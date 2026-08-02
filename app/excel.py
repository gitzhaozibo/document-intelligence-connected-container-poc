"""決算情報の Excel 出力。"""

from io import BytesIO

from openpyxl import Workbook


def _safe_cell(value: str | None) -> str:
    """外部入力を数式として解釈させず文字列として出力します。"""
    text = value or ""
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def build_financial_summary_excel(
    *,
    filename: str,
    company_name: str | None,
    securities_code: str | None,
    fiscal_period: str | None,
) -> bytes:
    """決算短信一件を一行の xlsx として生成します。"""
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("決算短信")
    worksheet.append(["PDF名", "会社名", "コード名", "決算期"])
    worksheet.append(
        [
            _safe_cell(filename),
            _safe_cell(company_name),
            _safe_cell(securities_code),
            _safe_cell(fiscal_period),
        ]
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
