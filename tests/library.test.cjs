const test = require('node:test');
const assert = require('node:assert/strict');
const library = require('../static/library.js');

test('search matches Chinese titles, authors, English case and full-width input', () => {
    const book = {title: '超越感觉 Beyond Feelings', author: 'Vincent Ruggiero 鲁吉罗'};
    for (const query of ['超越', '鲁吉罗', 'BEYOND', 'Ｂｅｙｏｎｄ', 'Feelings   鲁吉罗', '  ']) {
        assert.equal(library.matchesBook(book, query), true, query);
    }
    assert.equal(library.matchesBook(book, '超越 未出现的作者'), false);
});

test('recent order is stable for unread books and malformed timestamps', () => {
    const books = [
        {title: 'Unread B', originalIndex: 0},
        {title: 'Recent', originalIndex: 1, updatedAt: '200'},
        {title: 'Unread A', originalIndex: 2, updatedAt: 'bad'},
        {title: 'Older', originalIndex: 3, updatedAt: 100},
        {title: 'Invalid', originalIndex: 4, updatedAt: Infinity}
    ];
    assert.deepEqual(library.sortBooks(books, 'recent').map(book => book.originalIndex), [1, 3, 0, 2, 4]);
    assert.deepEqual(books.map(book => book.originalIndex), [0, 1, 2, 3, 4], 'sorting must not mutate source order');
});

test('title order sorts numeric titles naturally and keeps duplicate titles stable', () => {
    const books = [
        {title: 'Book 10', originalIndex: 0}, {title: 'Book 2', originalIndex: 1},
        {title: 'Book 2', originalIndex: 2}
    ];
    assert.deepEqual(library.sortBooks(books, 'title').map(book => book.originalIndex), [1, 2, 0]);
});

test('late progress updates reorder existing cards while search and empty states remain correct', () => {
    const search = Object.assign(new EventTarget(), {value: '', focus() { doc.activeElement = this; }});
    const sort = Object.assign(new EventTarget(), {value: 'recent'});
    const clear = Object.assign(new EventTarget(), {hidden: true});
    const count = {textContent: ''};
    const empty = {hidden: true};
    function card(title, author) {
        const link = {dataset: {}, href: '/read/example/0', focus() { doc.activeElement = this; }};
        return {dataset: {bookTitle: title, bookAuthor: author}, hidden: false, link,
            querySelector: () => link, contains: node => node === link};
    }
    const a = card('超越感觉', '鲁吉罗');
    const b = card('学习方法', '张三');
    const grid = {children: [a, b], querySelectorAll() { return this.children; }, append(...nodes) { this.children = nodes; }};
    const elements = {'book-grid': grid, 'library-search': search, 'library-sort': sort,
        'library-result-count': count, 'library-no-results': empty, 'clear-library-search': clear};
    const doc = Object.assign(new EventTarget(), {activeElement: a.link, getElementById: id => elements[id]});
    const win = Object.assign(new EventTarget(), {document: doc});
    library.init(win);
    assert.equal(count.textContent, '共 2 本');
    b.link.dataset.readingUpdatedAt = '200';
    doc.dispatchEvent(new Event('reader:library-progress'));
    assert.deepEqual(grid.children, [b, a]);
    assert.equal(doc.activeElement, a.link);
    assert.equal(a.link.href, '/read/example/0', 'progress links must not be reconstructed');

    search.value = '鲁吉罗';
    search.dispatchEvent(new Event('input'));
    assert.equal(a.hidden, false);
    assert.equal(b.hidden, true);
    assert.equal(count.textContent, '找到 1 本 · 共 2 本');
    search.value = '不存在的书';
    search.dispatchEvent(new Event('input'));
    assert.equal(empty.hidden, false);
    assert.equal(count.textContent, '找到 0 本 · 共 2 本');
    clear.dispatchEvent(new Event('click'));
    assert.equal(search.value, '');
    assert.equal(empty.hidden, true);
    assert.equal(clear.hidden, true);
    assert.equal(a.hidden || b.hidden, false);
    assert.equal(doc.activeElement, search);
});
