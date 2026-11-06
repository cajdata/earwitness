"""The earwitness.dev site in site/: links resolve, pages are complete, nothing loads from
elsewhere, and no page carries coordinates. Standard library only, so the Pages workflow can
run it without installing the project."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SITE = Path(__file__).resolve().parent.parent / "site"
PAGES = sorted(SITE.glob("*.html"))
NAV = {"how-it-works.html", "protect.html", "results.html", "data.html", "install.html",
       "in-depth.html"}  # fmt: skip
COORDINATES = re.compile(r"-?\d{1,3}\.\d{3,}\s*[,/]\s*-?\d{1,3}\.\d{3,}")


class Page(HTMLParser):
    """Links, resources, ids and a few landmarks of one page."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.resources: list[str] = []
        self.ids: set[str] = set()
        self.tags: list[str] = []
        self.current: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        self.tags.append(tag)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
            if a.get("aria-current") == "page":
                self.current.append(a["href"])
        if tag in ("img", "script", "iframe", "audio", "video", "source") and "src" in a:
            self.resources.append(a["src"])
        if tag == "link" and "href" in a:
            self.resources.append(a["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def parse(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def test_the_site_has_its_pages():
    names = {p.name for p in PAGES}
    assert {"index.html", "404.html", *NAV} <= names
    assert (SITE / "style.css").is_file()
    assert (SITE / "favicon.svg").is_file()


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_page_is_complete_and_self_contained(path):
    page = parse(path)
    assert page.title.strip(), "every page needs a title"
    assert "main" in page.tags
    assert "footer" in page.tags
    assert "script" not in page.tags, "the site runs no scripts"
    for src in page.resources:
        assert not urlsplit(src).scheme, f"{path.name} loads {src} from elsewhere"
    if path.name in NAV:
        assert page.current == [path.name], "the nav marks the page you are on"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_internal_links_resolve(path):
    page = parse(path)
    for href in page.links + page.resources:
        parts = urlsplit(href)
        if parts.scheme or href.startswith("mailto:"):
            continue
        target = path if not parts.path else SITE / parts.path.lstrip("/")
        if parts.path in ("", "./", "/"):
            target = path if not parts.path else SITE / "index.html"
        assert target.is_file(), f"{path.name}: {href} has no file"
        if parts.fragment:
            assert parts.fragment in parse(target).ids, f"{path.name}: {href} has no anchor"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_no_coordinates_on_the_site(path):
    assert not COORDINATES.search(path.read_text(encoding="utf-8"))


def test_stylesheet_loads_nothing_from_elsewhere():
    css = (SITE / "style.css").read_text(encoding="utf-8")
    assert "@import" not in css
    assert "url(" not in css
