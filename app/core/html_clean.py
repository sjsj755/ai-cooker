"""HTML 清洗：移除脚本/样式与控制字符，空白归一。"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SPACE_RUN = re.compile(r"[ \t\u3000]+")
_REMOVE_TAGS = ("script", "style", "noscript", "svg")


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """原地移除 script/style/noscript/svg，避免抽取文本时残留。"""
    for tag in soup(_REMOVE_TAGS):
        tag.decompose()
    return soup


def clean_html(raw: str) -> BeautifulSoup:
    """解析并清洗 HTML，返回可用于抽取的 BeautifulSoup。"""
    soup = BeautifulSoup(raw or "", "html.parser")
    return clean_soup(soup)


def remove_control_chars(text: str) -> str:
    return _CONTROL_CHARS.sub("", text or "")


def clean_text(text: str | None) -> str:
    """单行文本：去控制字符、全角空格与多余空白，trim。"""
    if not text:
        return ""
    text = remove_control_chars(text)
    return _SPACE_RUN.sub(" ", text).strip()


def clean_multiline(text: str | None) -> str:
    """多行文本（步骤）：保留换行，逐行折叠空白并去除空行。"""
    if not text:
        return ""
    text = remove_control_chars(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)
