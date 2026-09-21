import os
import pickle
from pathlib import Path
import tempfile
import hashlib
import json
import sqlite3
from functools import lru_cache
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, clean_html_content
from chapters import ChapterIndex, reference
from bs4 import BeautifulSoup
from starlette.concurrency import run_in_threadpool
from book_import import import_epub_file, ImportErrorForUser, MAX_UPLOAD_BYTES
from local_state import ProgressStore, valid_book_id, validate_progress, library_directory

app = FastAPI()
PROJECT_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PROJECT_DIR / 'templates'))
templates.env.globals['asset_url'] = lambda name: '/static/' + name + '?v=' + str((PROJECT_DIR / 'static' / name).stat().st_mtime_ns)
app.mount('/static', StaticFiles(directory=str(PROJECT_DIR / 'static'), check_dir=False), name='static')

# Where are the book folders located?
BOOKS_DIR = str(library_directory(PROJECT_DIR))


@app.get('/api/health')
async def health():
    return {'app': 'reader3', 'workspace': hashlib.sha256(str(Path(BOOKS_DIR).resolve()).encode()).hexdigest()[:16]}


def progress_store():
    return ProgressStore(Path(BOOKS_DIR) / '.reader3' / 'reading-state.sqlite')


def progress_target_exists(record):
    book = load_book_cached(record['bookId'])
    if not book:
        return False
    route, _, number = record['path'].strip('/').split('/')
    index = int(number)
    count = len(get_chapter_index(record['bookId']).chapters) if route == 'chapter' else len(book.spine)
    return index < count


@app.get('/api/reading-progress')
async def reading_progress():
    try:
        records = await run_in_threadpool(progress_store().read_all)
        usable = {key: value for key, value in records.items() if progress_target_exists(value)}
        return JSONResponse({'progress': usable})
    except (OSError, sqlite3.Error):
        return JSONResponse({'error': '本机阅读进度暂时不可用，仍可使用浏览器中的记录。'}, status_code=503)


@app.put('/api/books/{book_id}/progress')
async def save_reading_progress(book_id: str, request: Request):
    origin = request.headers.get('origin')
    if request.headers.get('x-reader3-progress') != '1' or (origin and origin != str(request.base_url).rstrip('/')):
        return JSONResponse({'error': '请从本机阅读器保存进度。'}, status_code=403)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            return JSONResponse({'error': '阅读进度数据过大。'}, status_code=413)
    try:
        record = validate_progress(json.loads(raw), book_id)
    except (ValueError, UnicodeDecodeError):
        record = None
    if not record:
        return JSONResponse({'error': '阅读进度格式无效。'}, status_code=400)
    if not progress_target_exists(record):
        return JSONResponse({'error': '对应书籍或章节不存在。'}, status_code=404)
    try:
        saved = await run_in_threadpool(progress_store().save, record)
        return JSONResponse({'record': saved})
    except (OSError, sqlite3.Error):
        return JSONResponse({'error': '本机进度未能保存，已保留浏览器回退。'}, status_code=503)


@app.post('/api/books/import')
async def import_book(request: Request):
    """Receive one EPUB from this local page; no multipart dependency needed."""
    origin = request.headers.get('origin')
    if request.headers.get('x-reader3-import') != '1' or (origin and origin != str(request.base_url).rstrip('/')):
        return JSONResponse({'error': '请从本机阅读器的书架页面导入。'}, status_code=403)
    filename = unquote(request.headers.get('x-epub-filename', ''))
    if not filename.lower().endswith('.epub'):
        return JSONResponse({'error': '请选择 .epub 格式的电子书。'}, status_code=400)
    try:
        content_length = int(request.headers.get('content-length', '0'))
    except ValueError:
        return JSONResponse({'error': '无法读取上传文件的大小。'}, status_code=400)
    if content_length > MAX_UPLOAD_BYTES:
        return JSONResponse({'error': '文件超过 50 MB，请选择较小的 EPUB。'}, status_code=413)
    root = Path(BOOKS_DIR)
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.reader3-upload-', dir=root) as stage:
            path = Path(stage) / 'upload.epub'
            size = 0
            with path.open('wb') as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ImportErrorForUser('文件超过 50 MB，请选择较小的 EPUB。', 413)
                    target.write(chunk)
            result = await run_in_threadpool(import_epub_file, path, root, filename)
        load_book_cached.cache_clear()
        get_chapter_index.cache_clear()
        return JSONResponse(result, status_code=200 if result['duplicate'] else 201)
    except ImportErrorForUser as exc:
        return JSONResponse({'error': str(exc)}, status_code=exc.status_code)
    except OSError:
        return JSONResponse({'error': '文件未能保存，请检查本机可用空间。已有书籍未受影响。'}, status_code=500)

@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    if not valid_book_id(folder_name):
        return None
    root = Path(BOOKS_DIR).resolve()
    folder = (root / folder_name).resolve()
    if folder.parent != root:
        return None
    file_path = folder / 'book.pkl'
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []

    # Scan directory for folders ending in '_data' that have a book.pkl
    if os.path.exists(BOOKS_DIR):
        for item in os.listdir(BOOKS_DIR):
            if item.endswith("_data") and os.path.isdir(os.path.join(BOOKS_DIR, item)):
                # Try to load it to get the title
                book = load_book_cached(item)
                if book:
                    chapter_index = get_chapter_index(item)
                    first = next((i for i, c in enumerate(chapter_index.chapters) if c.verified), None)
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine),
                        "logical_chapters": sum(c.verified for c in chapter_index.chapters),
                        "reading_url": f'/chapter/{quote(item, safe="")}/{first}' if first is not None else f'/read/{quote(item, safe="")}/0',
                    })

    imported = next((book for book in books if book['id'] == request.query_params.get('imported')), None)
    return templates.TemplateResponse(request=request, name="library.html", context={
        "books": books, "imported": imported,
        "duplicate_import": request.query_params.get('duplicate') == '1',
    })

@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(request: Request, book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(request=request, book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    index = get_chapter_index(book_id)
    return render_reading(request, book_id, book, index, chapter_index, logical=False)


@lru_cache(maxsize=10)
def get_chapter_index(book_id):
    return ChapterIndex(load_book_cached(book_id))


def reading_url(book_id, index, href, base='', title=''):
    ref = reference(href, base)
    if ref is None:
        try:
            return href if urlsplit(href).scheme in ('http', 'https') else None
        except ValueError:
            return None
    file_index = index.files.get(ref[0])
    if file_index is None:
        return None
    full = ref[0] + ('#' + ref[1] if ref[1] else '')
    position, _ = index.resolve(full, title, prefer_heading=bool(title))
    logical_index = index.chapter_at(position)
    # File-start TOC entries can precede their first heading by whitespace.
    if not ref[1] and logical_index is None:
        candidates = [i for i,c in enumerate(index.chapters) if c.verified and c.start[0] == file_index]
        if len(candidates) == 1:
            logical_index = candidates[0]
    if logical_index is not None:
        url = f'/chapter/{quote(book_id, safe="")}/{logical_index}'
    else:
        url = f'/read/{quote(book_id, safe="")}/{file_index}'
    # Chapter-title navigation uses the corrected beginning, not an erroneous TOC anchor.
    is_title = logical_index is not None and title == index.chapters[logical_index].title
    if ref[1] and not is_title:
        url += '#' + quote(f'r{file_index}-{ref[1]}', safe='')
    return url


def prepare_content(book_id, index, fragments):
    parts = []
    for i, html in fragments:
        soup = clean_html_content(BeautifulSoup(html, 'html.parser'))
        for tag in soup.find_all(True):
            if tag.get('id'):
                tag['id'] = f'r{i}-' + tag['id']
            if tag.name == 'a' and tag.get('name'):
                tag['name'] = f'r{i}-' + tag['name']
            if tag.name == 'a' and tag.get('href'):
                url = reading_url(book_id, index, tag['href'], index.book.spine[i].href)
                if url:
                    tag['href'] = url
                    if url.startswith(('http:', 'https:')):
                        tag['rel'] = 'noopener noreferrer'
                        tag['target'] = '_blank'
                else:
                    del tag['href']
                    tag['title'] = '该书内链接暂未定位，请核对原书'
            if tag.name == 'img' and tag.get('src', '').startswith('images/'):
                tag['src'] = f'/read/{quote(book_id, safe="")}/images/' + quote(tag['src'][7:], safe='')
        parts.append(str(soup))
    return '\n'.join(parts)


def render_reading(request, book_id, book, index, item_index, logical, warning=''):
    if logical:
        chapter = index.chapters[item_index]
        fragments = index.fragments(chapter)
        title = chapter.title
        total = len(index.chapters)
        scope = '完整章节（按目录与正文标题定位；含本章范围内的练习和注释）'
        notice = chapter.note
        copy_label = '复制整章'
        route = 'chapter'
        active_files = {book.spine[i].href for i in chapter.source_indices}
    else:
        chapter = book.spine[item_index]
        fragments = [(item_index, chapter.content)]
        title = chapter.title
        total = len(book.spine)
        scope = f'当前阅读单元（第 {item_index+1} / {total} 单元，不保证是完整一章）'
        notice = warning or '当前按原书阅读单元展示；从目录进入已识别的章节可复制整章。'
        copy_label = '复制当前内容'
        route = 'read'
        active_files = {chapter.href}
    content = prepare_content(book_id, index, fragments)
    prev_url = f'/{route}/{quote(book_id, safe="")}/{item_index-1}' if item_index > 0 else None
    next_url = f'/{route}/{quote(book_id, safe="")}/{item_index+1}' if item_index+1 < total else None
    return templates.TemplateResponse(request=request, name='reader.html', context={
        'book': book, 'book_id': book_id, 'current_chapter': book.spine[fragments[0][0]],
        'rendered_content': content, 'chapter_title': title, 'copy_scope': scope,
        'copy_label': copy_label, 'boundary_notice': notice, 'is_logical': logical,
        'active_files': active_files, 'chapter_index': item_index, 'total_units': total,
        'prev_url': prev_url, 'next_url': next_url,
        'toc_url': lambda entry: reading_url(book_id, index, entry.href, title=entry.title),
    })


@app.get('/chapter/{book_id}/{chapter_index}', response_class=HTMLResponse)
async def read_logical_chapter(request: Request, book_id: str, chapter_index: int):
    book = load_book_cached(book_id)
    if book is None:
        raise HTTPException(404, 'Book not found')
    index = get_chapter_index(book_id)
    if not 0 <= chapter_index < len(index.chapters):
        raise HTTPException(404, 'Chapter not found')
    chapter = index.chapters[chapter_index]
    if not chapter.verified:
        # A clear fallback, without a button claiming the range is complete.
        fallback = chapter.start[0] if chapter.start else 0
        response = render_reading(request, book_id, book, index, fallback, logical=False, warning=chapter.warning)
        return response
    return render_reading(request, book_id, book, index, chapter_index, logical=True)


@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """
    Serves images specifically for a book.
    The HTML contains <img src="images/pic.jpg">.
    The browser resolves this to /read/{book_id}/images/pic.jpg.
    """
    # Security check: ensure book_id is clean
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)

if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
