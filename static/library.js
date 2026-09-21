(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root?.document) {
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => api.init(root), {once: true});
        else api.init(root);
    }
}(typeof window === 'undefined' ? null : window, function () {
    'use strict';
    const collator = new Intl.Collator('zh-CN', {numeric: true, sensitivity: 'base'});
    const normalize = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase('zh-CN').replace(/\s+/g, ' ').trim();

    function matchesBook(book, query) {
        const terms = normalize(query).split(' ').filter(Boolean);
        const searchable = normalize(book.title) + ' ' + normalize(book.author);
        return terms.every(term => searchable.includes(term));
    }

    function timestamp(value) {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? number : 0;
    }

    function sortBooks(books, mode) {
        return [...books].sort((a, b) => {
            const comparison = mode === 'title'
                ? collator.compare(a.title, b.title)
                : timestamp(b.updatedAt) - timestamp(a.updatedAt);
            return comparison || a.originalIndex - b.originalIndex;
        });
    }

    function init(win) {
        const doc = win.document;
        const grid = doc.getElementById('book-grid');
        const search = doc.getElementById('library-search');
        const sort = doc.getElementById('library-sort');
        const count = doc.getElementById('library-result-count');
        const empty = doc.getElementById('library-no-results');
        const clear = doc.getElementById('clear-library-search');
        if (!grid || !search || !sort) return;
        const books = Array.from(grid.querySelectorAll('.book-card')).map((node, originalIndex) => ({
            node, originalIndex, title: node.dataset.bookTitle || '', author: node.dataset.bookAuthor || '',
            link: node.querySelector('a[data-reading-link]')
        }));

        function render() {
            const ordered = sortBooks(books.map(book => ({...book, updatedAt: book.link?.dataset.readingUpdatedAt})), sort.value);
            const focused = doc.activeElement;
            let visible = 0;
            for (const book of ordered) {
                book.node.hidden = !matchesBook(book, search.value);
                if (!book.node.hidden) visible++;
            }
            // Move existing cards rather than rebuilding them: progress links and handlers stay intact.
            const current = Array.from(grid.children);
            if (ordered.some((book, index) => current[index] !== book.node)) {
                grid.append(...ordered.map(book => book.node));
                const focusedBook = ordered.find(book => book.node.contains(focused));
                if (focusedBook && !focusedBook.node.hidden) focused.focus({preventScroll: true});
            }
            const filtered = Boolean(normalize(search.value));
            count.textContent = filtered ? `找到 ${visible} 本 · 共 ${books.length} 本` : `共 ${books.length} 本`;
            empty.hidden = visible !== 0 || books.length === 0;
            clear.hidden = !search.value;
        }

        search.addEventListener('input', render);
        search.addEventListener('search', render);
        sort.addEventListener('change', render);
        clear.addEventListener('click', () => { search.value = ''; render(); search.focus(); });
        doc.addEventListener('reader:library-progress', render);
        win.addEventListener('pageshow', render);
        render();
    }

    return {normalize, matchesBook, sortBooks, init};
}));
