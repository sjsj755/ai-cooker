"""HTML 清洗：script/style 移除、控制字符与空白归一。"""

from app.core.html_clean import clean_html, clean_multiline, clean_text


def test_removes_scripts_and_styles():
    html = (
        "<html><head><style>.x{}</style></head>"
        "<body><script>var a=1;</script><p> 土豆 </p></body></html>"
    )
    soup = clean_html(html)
    assert soup.select("script") == []
    assert soup.select("style") == []
    assert clean_text(soup.get_text()) == "土豆"


def test_control_chars_and_whitespace():
    assert clean_text("土豆\x00 炒\x1f鸡蛋\u3000 ") == "土豆 炒鸡蛋"
    assert clean_multiline("第一行 \r\n 第二行\r\n") == "第一行\n第二行"
