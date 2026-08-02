"""Streamlit 画面全体の E2E スモークテスト。"""

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.e2e
def test_streamlit_upload_screen_renders() -> None:
    app = AppTest.from_file("frontend/app.py").run()

    assert not app.exception
    assert app.title[0].value == "Document Intelligence Connected Container PoC"
    assert app.file_uploader[0].label == "解析するファイル"
    assert app.text_input[0].label == "対象ページ"
    assert app.text_input[1].label == "言語ロケール"
    assert app.selectbox[0].options == ["json", "markdown"]
    assert app.button[0].label == "実行"
    assert app.button[0].disabled
