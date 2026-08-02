"""Document Intelligence 解析オプションの単体テスト。"""

import pytest

from app.main import build_analyze_options


def test_build_analyze_options_normalizes_values() -> None:
    options = build_analyze_options(
        pages=" 1-3, 5 ",
        locale="ja-JP",
        features="languages,ocrHighResolution,languages",
        output_content_format="markdown",
    )

    assert options == {
        "pages": "1-3,5",
        "locale": "ja-JP",
        "features": "languages,ocrHighResolution",
        "outputContentFormat": "markdown",
    }


@pytest.mark.parametrize("pages", ["0", "3-1", "1,,2", "all", "1;2"])
def test_build_analyze_options_rejects_invalid_pages(pages: str) -> None:
    with pytest.raises(ValueError, match="ページ"):
        build_analyze_options(pages, None, None, "json")


def test_build_analyze_options_rejects_unknown_feature() -> None:
    with pytest.raises(ValueError, match="unknownFeature"):
        build_analyze_options(None, None, "languages,unknownFeature", "json")


def test_build_analyze_options_rejects_invalid_locale() -> None:
    with pytest.raises(ValueError, match="ロケール"):
        build_analyze_options(None, "../../ja", None, "json")
