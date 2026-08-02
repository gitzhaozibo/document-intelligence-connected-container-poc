"""Excel 出力の単体テスト。"""

from io import BytesIO

from openpyxl import load_workbook

from app.excel import build_financial_summary_excel


def test_excel_contains_one_financial_summary_row_and_escapes_formulas() -> None:
    content = build_financial_summary_excel(
        filename="summary.pdf",
        company_name="株式会社サンプル",
        securities_code="1234",
        fiscal_period="=DANGEROUS",
    )

    workbook = load_workbook(BytesIO(content), read_only=True)
    rows = list(workbook["決算短信"].values)

    assert rows[0] == ("PDF名", "会社名", "コード名", "決算期")
    assert rows[1] == ("summary.pdf", "株式会社サンプル", "1234", "'=DANGEROUS")
