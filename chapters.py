"""Resolve conservative logical chapter ranges against EPUB reading order.

Book content is data: boundaries are proven from the TOC and unique headings,
never inferred by an LLM. Unresolved boundaries disable whole-chapter copying.
"""
from dataclasses import dataclass, field
from copy import copy
import posixpath
import re
from urllib.parse import unquote, urlsplit
from bs4 import BeautifulSoup, Tag, NavigableString


def normalized(text):
    return re.sub(r'\s+', '', text).casefold()


def chapter_title(text):
    return bool(re.match(r'^第[〇零一二三四五六七八九十百千万两\d]+章|^chapter\s+[\divxlc]+\b', text.strip(), re.I))


def reference(href, base=''):
    try:
        parsed = urlsplit(href)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if path.startswith('/') or '\\' in path:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    if path == '..' or path.startswith('../'):
        return None
    return path, unquote(parsed.fragment)


@dataclass
class Boundary:
    title: str
    depth: int
    entry: object
    position: tuple | None
    note: str = ''


@dataclass
class LogicalChapter:
    title: str
    start: tuple | None
    end: tuple | None
    source_indices: list = field(default_factory=list)
    warning: str = ''
    note: str = ''

    @property
    def verified(self):
        return not self.warning and self.start is not None and self.end is not None


class ChapterIndex:
    def __init__(self, book):
        self.book = book
        self.soups = [BeautifulSoup(ch.content, 'html.parser') for ch in book.spine]
        self.nodes = [list(s.descendants) for s in self.soups]
        self.positions = [{id(n): j for j, n in enumerate(ns)} for ns in self.nodes]
        self.files = {posixpath.normpath(unquote(ch.href)): i for i, ch in enumerate(book.spine)}
        self.boundaries = []

        def walk(entries, depth=0):
            for entry in entries:
                position, note = self.resolve(entry.href, entry.title, prefer_heading=chapter_title(entry.title))
                self.boundaries.append(Boundary(entry.title, depth, entry, position, note))
                walk(entry.children, depth + 1)
        walk(book.toc)
        self.chapters = []
        for n, boundary in enumerate(self.boundaries):
            if not chapter_title(boundary.title):
                continue
            following = next((b for b in self.boundaries[n + 1:] if b.depth <= boundary.depth), None)
            end = following.position if following else (len(book.spine), 0)
            warning = ''
            if boundary.position is None or end is None:
                warning = '目录起止位置无法可靠定位，暂不提供整章复制。'
            elif boundary.position >= end:
                warning = '目录顺序或章节范围有冲突，暂不提供整章复制。'
            else:
                for child in self.boundaries[n + 1:]:
                    if child.depth <= boundary.depth:
                        break
                    if chapter_title(child.title):
                        warning = '目录把另一章嵌入本章，范围可能重叠，暂不提供整章复制。'
                        break
                    if child.position is None or not boundary.position <= child.position < end:
                        warning = '章内目录存在无法定位或越界的小节，暂不提供整章复制。'
                        break
            indices = []
            if not warning:
                indices = [i for i in range(boundary.position[0], min(end[0] + 1, len(book.spine)))
                           if i < end[0] or end[1] > 0]
                start_node = self.nodes[boundary.position[0]][boundary.position[1]]
                level = int(start_node.name[1])
                for i in indices:
                    for heading in self.soups[i].find_all(re.compile(r'^h[1-6]$')):
                        position = (i, self.positions[i][id(heading)])
                        if boundary.position < position < end and (
                            int(heading.name[1]) <= level or chapter_title(heading.get_text(' ', strip=True))
                        ):
                            warning = '范围内发现额外的同级标题，需要核对章节边界，暂不提供整章复制。'
            self.chapters.append(LogicalChapter(boundary.title, boundary.position, end, indices, warning, boundary.note))

    def resolve(self, href, title='', prefer_heading=False):
        ref = reference(href)
        if ref is None or ref[0] not in self.files:
            return None, ''
        i = self.files[ref[0]]
        soup = self.soups[i]
        target = None
        if ref[1]:
            matches = soup.find_all(id=ref[1]) or soup.find_all('a', attrs={'name': ref[1]})
            if len(matches) == 1:
                target = matches[0]
        if prefer_heading:
            headings = [h for h in soup.select('h1,h2,h3,h4,h5,h6') if normalized(h.get_text()) == normalized(title)]
            if len(headings) == 1:
                heading = headings[0]
                note = ''
                if ref[1] and (target is None or (target is not heading and heading not in target.parents)):
                    note = '目录标记偏离章标题，已按唯一匹配的正文标题校正。'
                return (i, self.positions[i][id(heading)]), note
            return None, ''
        if not ref[1]:
            return (i, 0), ''
        if target is None:
            return None, ''
        return (i, self.positions[i][id(target)]), ''

    def chapter_at(self, position):
        if position is None:
            return None
        return next((i for i, c in enumerate(self.chapters) if c.verified and c.start <= position < c.end), None)

    def fragments(self, chapter):
        """Prune by document order while retaining balanced ancestor elements."""
        if not chapter.verified:
            return []
        result = []
        for i in chapter.source_indices:
            lo = chapter.start[1] if i == chapter.start[0] else 0
            hi = chapter.end[1] if i == chapter.end[0] else len(self.nodes[i])

            def clone(node):
                position = self.positions[i].get(id(node), -1)
                if isinstance(node, NavigableString):
                    return copy(node) if lo <= position < hi else None
                if not isinstance(node, Tag):
                    return None
                if position >= hi:
                    return None
                children = [kept for child in node.contents if (kept := clone(child)) is not None]
                if not children and not lo <= position < hi:
                    return None
                kept = Tag(name=node.name, attrs=dict(node.attrs))
                for child in children:
                    kept.append(child)
                return kept

            parts = [kept for child in self.soups[i].contents if (kept := clone(child)) is not None]
            result.append((i, ''.join(str(p) for p in parts)))
        return result
