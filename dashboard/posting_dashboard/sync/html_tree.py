"""Tiny stdlib HTML tree for defensive, fixture-testable parsing."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Callable, Iterator, Optional

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template", "head", "title"}
BLOCK = {"div", "p", "li", "tr", "td", "th", "section", "article", "header", "footer",
         "h1", "h2", "h3", "h4", "h5", "h6", "br", "span", "a", "button", "time", "label",
         "ytcp-video-row", "ul", "ol", "table", "tbody", "main", "aside", "nav"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"] = None):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default) or default

    def iter(self) -> Iterator["Node"]:
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            for c in reversed(n.children):
                if isinstance(c, Node):
                    stack.append(c)

    def find_all(self, pred: Callable[["Node"], bool]) -> list["Node"]:
        return [n for n in self.iter() if n is not self and pred(n)]

    def text(self) -> str:
        parts: list[str] = []
        self._text(parts)
        return " ".join(" ".join(parts).split())

    def _text(self, parts: list[str]) -> None:
        if self.tag in SKIP_TEXT:
            return
        if self.attrs.get("aria-hidden") == "true" and self.tag not in ("body", "html"):
            return
        for c in self.children:
            if isinstance(c, Node):
                c._text(parts)
            else:
                parts.append(c)
        if self.tag in BLOCK:
            parts.append(" ")

    def ancestors(self) -> Iterator["Node"]:
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag.lower() not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def parse_html(html: str) -> Node:
    b = _Builder()
    try:
        b.feed(html or "")
        b.close()
    except Exception:
        pass
    return b.root


# ---- predicate helpers (selectors as data, easy to update) ---------------

def attr_eq(name: str, value: str) -> Callable[[Node], bool]:
    return lambda n: n.get(name) == value


def attr_contains(name: str, value: str) -> Callable[[Node], bool]:
    v = value.lower()
    return lambda n: v in n.get(name).lower()


def tag_is(tag: str) -> Callable[[Node], bool]:
    return lambda n: n.tag == tag
