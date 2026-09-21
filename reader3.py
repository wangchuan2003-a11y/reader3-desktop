"""
Parses an EPUB file into a structured object that can be used to serve the book via a web interface.
"""

import os
import pickle
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from urllib.parse import unquote, urlsplit

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment

# --- Data structures ---

@dataclass
class ChapterContent:
    """
    Represents a physical file in the EPUB (Spine Item).
    A single file might contain multiple logical chapters (TOC entries).
    """
    id: str           # Internal ID (e.g., 'item_1')
    href: str         # Filename (e.g., 'part01.html')
    title: str        # Best guess title from file
    content: str      # Cleaned HTML with rewritten image paths
    text: str         # Plain text for search/LLM context
    order: int        # Linear reading order


@dataclass
class TOCEntry:
    """Represents a logical entry in the navigation sidebar."""
    title: str
    href: str         # original href (e.g., 'part01.html#chapter1')
    file_href: str    # just the filename (e.g., 'part01.html')
    anchor: str       # just the anchor (e.g., 'chapter1'), empty if none
    children: List['TOCEntry'] = field(default_factory=list)


@dataclass
class BookMetadata:
    """Metadata"""
    title: str
    language: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    identifiers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)


@dataclass
class Book:
    """The Master Object to be pickled."""
    metadata: BookMetadata
    spine: List[ChapterContent]  # The actual content (linear files)
    toc: List[TOCEntry]          # The navigation tree
    images: Dict[str, str]       # Map: original_path -> local_path

    # Meta info
    source_file: str
    processed_at: str
    version: str = "3.0"


# --- Utilities ---

# Never publish an EPUB-provided executable document extension as an image.
RASTER_EXTENSIONS = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/gif': '.gif', 'image/webp': '.webp', 'image/avif': '.avif',
    'image/bmp': '.bmp',
}
PASSIVE_TAGS = set(('html body p div span section article aside header footer main '
    'h1 h2 h3 h4 h5 h6 strong em b i u s strike small big sub sup code pre '
    'blockquote q cite abbr acronym dfn mark time ruby rt rp br hr wbr a img '
    'ul ol li dl dt dd table caption colgroup col thead tbody tfoot tr th td '
    'figure figcaption address').split())


def local_image_path(value):
    return bool(re.fullmatch(r'images/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|gif|webp|avif|bmp)', value, re.I))


def image_reference(src, document_path):
    """Resolve only archive-local images; fragments/queries are not filenames."""
    try:
        parsed = urlsplit(src)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or '\\' in src:
        return None
    path = unquote(parsed.path)
    if not path or path.startswith('/'):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(document_path), path))
    return None if resolved == '..' or resolved.startswith('../') else resolved

def clean_html_content(soup: BeautifulSoup) -> BeautifulSoup:

    # Remove dangerous/useless tags
    for tag in soup(['head', 'script', 'style', 'iframe', 'video', 'audio', 'nav', 'form', 'button', 'object', 'embed', 'link', 'meta', 'base', 'template']):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove input tags
    for tag in soup.find_all('input'):
        tag.decompose()

    for tag in soup.find_all(['svg', 'math']):
        if tag.parent is not None:
            placeholder = soup.new_tag('span')
            placeholder.string = '[此处的矢量图或公式暂未显示，请核对原书]'
            tag.replace_with(placeholder)

    # Keep readable content visible, including collapsed details/summary, while
    # discarding behavior from unsupported controls without dropping their text.
    for tag in soup.find_all(True):
        if tag.name == 'summary':
            tag.name = 'p'
        elif tag.name not in PASSIVE_TAGS:
            tag.unwrap()

    # Book markup must not run code, style the app, or load external resources.
    allowed = {'id', 'name', 'href', 'src', 'alt', 'title', 'colspan', 'rowspan', 'scope', 'start', 'value', 'lang', 'dir'}
    for tag in soup.find_all(True):
        for key in list(tag.attrs):
            if key not in allowed:
                del tag.attrs[key]
        if tag.has_attr('href'):
            value = str(tag['href']).strip()
            try:
                parsed = urlsplit(value)
                safe = (parsed.scheme.lower() in ('', 'http', 'https')
                        and not value.startswith('//') and '\\' not in value)
            except ValueError:
                safe = False
            if tag.name != 'a' or not safe:
                del tag['href']
        if tag.has_attr('src') and (tag.name != 'img' or not local_image_path(str(tag['src']))):
            del tag['src']

    return soup


def extract_plain_text(soup: BeautifulSoup) -> str:
    """Extract clean text for LLM/Search usage."""
    text = soup.get_text(separator=' ')
    # Collapse whitespace
    return ' '.join(text.split())


def parse_toc_recursive(toc_list, depth=0) -> List[TOCEntry]:
    """
    Recursively parses the TOC structure from ebooklib.
    """
    result = []

    for item in toc_list:
        # ebooklib TOC items are either `Link` objects or tuples (Section, [Children])
        if isinstance(item, tuple):
            section, children = item
            entry = TOCEntry(
                title=section.title,
                href=section.href,
                file_href=section.href.split('#')[0],
                anchor=section.href.split('#')[1] if '#' in section.href else "",
                children=parse_toc_recursive(children, depth + 1)
            )
            result.append(entry)
        elif isinstance(item, epub.Link):
            entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
            result.append(entry)
        # Note: ebooklib sometimes returns direct Section objects without children
        elif isinstance(item, epub.Section):
             entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
             result.append(entry)

    return result


def get_fallback_toc(book_obj) -> List[TOCEntry]:
    """
    If TOC is missing, build a flat one from the Spine.
    """
    toc = []
    for item in book_obj.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            name = item.get_name()
            # Try to guess a title from the content or ID
            title = item.get_name().replace('.html', '').replace('.xhtml', '').replace('_', ' ').title()
            toc.append(TOCEntry(title=title, href=name, file_href=name, anchor=""))
    return toc


def extract_metadata_robust(book_obj) -> BookMetadata:
    """
    Extracts metadata handling both single and list values.
    """
    def get_list(key):
        data = book_obj.get_metadata('DC', key)
        return [x[0] for x in data] if data else []

    def get_one(key):
        data = book_obj.get_metadata('DC', key)
        return data[0][0] if data else None

    return BookMetadata(
        title=get_one('title') or "Untitled",
        language=get_one('language') or "en",
        authors=get_list('creator'),
        description=get_one('description'),
        publisher=get_one('publisher'),
        date=get_one('date'),
        identifiers=get_list('identifier'),
        subjects=get_list('subject')
    )


# --- Main Conversion Logic ---

def process_epub(epub_path: str, output_dir: str) -> Book:

    # 1. Load Book
    print(f"Loading {epub_path}...")
    book = epub.read_epub(epub_path)

    # 2. Extract Metadata
    metadata = extract_metadata_robust(book)

    # Import callers provide a fresh staging directory. Refuse accidental
    # direct reuse rather than deleting an already readable book.
    if os.path.exists(output_dir) and os.listdir(output_dir):
        raise ValueError('处理目录已有内容，拒绝覆盖已有书籍。')
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    # 4. Extract Images & Build Map
    print("Extracting images...")
    image_map = {} # Key: internal_path, Value: local_relative_path

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_IMAGE:
            extension = RASTER_EXTENSIONS.get(item.media_type)
            if extension is None:
                continue
            safe_fname = hashlib.sha256(item.get_name().encode()).hexdigest()[:20] + extension

            # Save to disk
            local_path = os.path.join(images_dir, safe_fname)
            with open(local_path, 'wb') as f:
                f.write(item.get_content())

            rel_path = f"images/{safe_fname}"
            image_map[posixpath.normpath(item.get_name())] = rel_path

    # 5. Process TOC
    print("Parsing Table of Contents...")
    toc_structure = parse_toc_recursive(book.toc)
    if not toc_structure:
        print("Warning: Empty TOC, building fallback from Spine...")
        toc_structure = get_fallback_toc(book)

    # 6. Process Content (Spine-based to preserve HTML validity)
    print("Processing chapters...")
    spine_chapters = []

    # We iterate over the spine (linear reading order)
    for i, spine_item in enumerate(book.spine):
        item_id, linear = spine_item
        item = book.get_item_with_id(item_id)

        if not item:
            continue

        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Raw content
            raw_content = item.get_content().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(raw_content, 'html.parser')

            # A. Fix Images
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if not src: continue

                resolved = image_reference(src, item.get_name())
                if resolved in image_map:
                    img['src'] = image_map[resolved]
                else:
                    placeholder = soup.new_tag('span')
                    placeholder.string = '[图片未显示，请核对原书] ' + img.get('alt', '')
                    img.replace_with(placeholder)

            # B. Clean HTML
            soup = clean_html_content(soup)

            # C. Extract Body Content only
            body = soup.find('body')
            if body:
                # Extract inner HTML of body
                final_html = "".join([str(x) for x in body.contents])
            else:
                final_html = str(soup)

            # D. Create Object
            chapter = ChapterContent(
                id=item_id,
                href=item.get_name(), # Important: This links TOC to Content
                title=(soup.find(['h1', 'h2', 'h3']).get_text(' ', strip=True) if soup.find(['h1', 'h2', 'h3']) else f"阅读单元 {i+1}"),
                content=final_html,
                text=extract_plain_text(BeautifulSoup(final_html, 'html.parser')),
                order=i
            )
            spine_chapters.append(chapter)

    # 7. Final Assembly
    final_book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images=image_map,
        source_file=os.path.basename(epub_path),
        processed_at=datetime.now().isoformat()
    )

    return final_book


def save_to_pickle(book: Book, output_dir: str):
    p_path = os.path.join(output_dir, 'book.pkl')
    with open(p_path, 'wb') as f:
        pickle.dump(book, f)
    print(f"Saved structured data to {p_path}")


# --- CLI ---

if __name__ == "__main__":

    import sys
    if len(sys.argv) < 2:
        print("Usage: python reader3.py <file.epub>")
        sys.exit(1)

    epub_file = sys.argv[1]
    assert os.path.exists(epub_file), "File not found."
    out_dir = os.path.splitext(epub_file)[0] + "_data"

    book_obj = process_epub(epub_file, out_dir)
    save_to_pickle(book_obj, out_dir)
    print("\n--- Summary ---")
    print(f"Title: {book_obj.metadata.title}")
    print(f"Authors: {', '.join(book_obj.metadata.authors)}")
    print(f"Physical Files (Spine): {len(book_obj.spine)}")
    print(f"TOC Root Items: {len(book_obj.toc)}")
    print(f"Images extracted: {len(book_obj.images)}")
