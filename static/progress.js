(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root && root.document) {
        const start = () => api.init(root);
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, {once: true});
        else start();
    }
}(typeof window === 'undefined' ? null : window, function () {
    'use strict';
    const PREFIX = 'reader3:progress:v1:';
    const MAX_OFFSET = 100000000;
    const BLOCKS = 'p,h1,h2,h3,h4,h5,h6,li,pre,table,figure,img';

    function storageKey(bookId) { return PREFIX + bookId; }

    // Stored links must stay inside this book, even if browser storage was edited.
    function validPath(path, bookId) {
        if (typeof path !== 'string' || path.length > 1024) return false;
        const match = /^\/(?:chapter|read)\/([^/?#]+)\/(\d+)$/.exec(path);
        if (!match || !Number.isSafeInteger(Number(match[2]))) return false;
        try {
            const decoded = decodeURIComponent(match[1]);
            return decoded === bookId && decoded !== '.' && decoded !== '..' && !/[\\/\u0000-\u001f]/.test(decoded);
        } catch (_) { return false; }
    }

    function validOffset(value, signed) {
        return typeof value === 'number' && Number.isFinite(value) && value >= (signed ? -MAX_OFFSET : 0) && value <= MAX_OFFSET;
    }

    function validateRecord(value, bookId) {
        if (!value || value.version !== 1 || value.bookId !== bookId || !validPath(value.path, bookId)) return null;
        if (!validOffset(value.scrollTop, false) || !Number.isFinite(value.updatedAt) || value.updatedAt < 0 || value.updatedAt > Date.now() + 60000) return null;
        if (typeof value.title !== 'string' || value.title.length > 500) return null;
        let anchor = null;
        const candidate = value.anchor;
        if (candidate && Number.isSafeInteger(candidate.index) && candidate.index >= 0 && candidate.index < 1000000 &&
                typeof candidate.id === 'string' && candidate.id.length <= 1024 &&
                typeof candidate.signature === 'string' && candidate.signature.length <= 200 &&
                validOffset(candidate.offset, true)) {
            anchor = {index: candidate.index, id: candidate.id, signature: candidate.signature, offset: candidate.offset};
        }
        return {
            version: 1, bookId, path: value.path, title: value.title,
            scrollTop: value.scrollTop, anchor, updatedAt: value.updatedAt
        };
    }

    function readProgress(storage, bookId) {
        try {
            const raw = storage.getItem(storageKey(bookId));
            if (raw === null) return {record: null, available: true};
            try { return {record: validateRecord(JSON.parse(raw), bookId), available: true}; }
            catch (_) { return {record: null, available: true}; }
        } catch (_) { return {record: null, available: false}; }
    }

    function writeProgress(storage, bookId, value) {
        const record = validateRecord(value, bookId);
        if (!record) return false;
        try { storage.setItem(storageKey(bookId), JSON.stringify(record)); return true; }
        catch (_) { return false; }
    }

    function shouldRestore(record, path, hash, resume, navigationType) {
        return Boolean(record && record.path === path && !hash && (resume || navigationType === 'reload'));
    }

    // Navigation timing is optional in embedded browsers; saving must not depend on it.
    function navigationType(win) {
        try {
            const type = win.performance?.getEntriesByType?.('navigation')?.[0]?.type;
            if (typeof type === 'string') return type;
        } catch (_) {}
        try { return win.performance?.navigation?.type === 1 ? 'reload' : 'navigate'; }
        catch (_) { return 'navigate'; }
    }

    // Match both position and content. A unique text fallback survives modest layout/content shifts.
    function findAnchorIndex(items, anchor) {
        if (!anchor) return -1;
        if (anchor.id) {
            const byId = items.findIndex(item => item.id === anchor.id && item.signature === anchor.signature);
            if (byId !== -1) return byId;
        }
        if (items[anchor.index] && items[anchor.index].signature === anchor.signature) return anchor.index;
        const matches = items.map((item, index) => item.signature === anchor.signature ? index : -1).filter(index => index !== -1);
        return anchor.signature && matches.length === 1 ? matches[0] : -1;
    }

    function signature(element) {
        const text = (element.textContent || element.getAttribute('src') || element.tagName).replace(/\s+/g, ' ').trim();
        return text.length <= 160 ? text : text.slice(0, 128) + '…' + text.slice(-31);
    }

    function latestRecord(local, remote) {
        if (!local) return remote || null;
        if (!remote) return local;
        return remote.updatedAt >= local.updatedAt ? remote : local;
    }

    async function remoteRequest(win, path, options = {}) {
        const controller = new win.AbortController();
        const timeout = win.setTimeout(() => controller.abort(), 1500);
        try {
            const response = await win.fetch(path, {...options, signal: controller.signal});
            if (!response.ok) return null;
            return await response.json();
        } catch (_) { return null; }
        finally { win.clearTimeout(timeout); }
    }

    function init(win) {
        const doc = win.document;
        let storage;
        try { storage = win.localStorage; } catch (_) { storage = null; }
        const libraryDefaults = new Map();
        let serverRecords = {};
        let serverRequest;
        const getSaved = bookId => latestRecord(readProgress(storage, bookId).record, validateRecord(serverRecords[bookId], bookId));
        function refreshServer() {
            if (serverRequest) return serverRequest;
            serverRequest = remoteRequest(win, '/api/reading-progress').then(data => {
                if (data && data.progress && typeof data.progress === 'object') {
                    for (const [id, value] of Object.entries(data.progress)) {
                        serverRecords[id] = latestRecord(validateRecord(serverRecords[id], id), validateRecord(value, id));
                    }
                }
                updateLibrary();
            }).finally(() => { serverRequest = null; });
            return serverRequest;
        }
        const serverReady = refreshServer();
        win.reader3FlushProgress = () => Promise.resolve();

        function updateLibrary() {
            doc.querySelectorAll('a[data-book-id][data-reading-link]').forEach(link => {
                const label = link.querySelector('[data-reading-label]') || Array.from(link.childNodes).find(node => node.nodeType === 3 && node.textContent.trim());
                if (!libraryDefaults.has(link)) libraryDefaults.set(link, {href: link.getAttribute('href'), label: label?.textContent || '开始阅读'});
                const result = readProgress(storage, link.dataset.bookId);
                result.record = getSaved(link.dataset.bookId);
                const marker = link.closest('.book-card')?.querySelector('.reading-progress') || link.querySelector('.reading-progress');
                if (!result.record) {
                    link.dataset.readingUpdatedAt = '0';
                    link.setAttribute('href', libraryDefaults.get(link).href);
                    if (label) label.textContent = libraryDefaults.get(link).label;
                    if (marker) marker.textContent = result.available ? '' : '此浏览器暂时无法保存阅读位置';
                    return;
                }
                link.href = result.record.path + '?resume=1';
                link.dataset.readingUpdatedAt = String(result.record.updatedAt);
                if (label) label.textContent = '继续阅读';
                else link.insertBefore(doc.createTextNode('继续阅读'), link.firstChild);
                if (marker) marker.textContent = '上次读到：' + result.record.title;
            });
            doc.dispatchEvent(new win.Event('reader:library-progress'));
        }
        updateLibrary();
        function refreshShelf() {
            if (!doc.body.dataset.bookId && doc.visibilityState !== 'hidden') refreshServer();
        }
        win.addEventListener('pageshow', () => { updateLibrary(); refreshShelf(); });
        win.addEventListener('focus', refreshShelf);
        doc.addEventListener('visibilitychange', refreshShelf);
        win.addEventListener('storage', event => { if (event.key === null || event.key.startsWith(PREFIX)) updateLibrary(); });

        const bookId = doc.body.dataset.bookId;
        const main = doc.getElementById('main');
        const content = doc.querySelector('.book-content');
        if (!bookId || !main || !content || !validPath(win.location.pathname, bookId)) return;

        const status = doc.getElementById('reading-progress-status');
        const setStatus = text => { if (status) status.textContent = text; };
        const result = readProgress(storage, bookId);
        const toolbar = doc.querySelector('.toolbar');
        const elements = Array.from(content.querySelectorAll(BLOCKS)).filter(element => !element.querySelector(BLOCKS));
        const descriptors = elements.map(element => ({id: element.id || '', signature: signature(element)}));
        let armed = false;
        let timer;
        let userMoved = false;
        let initializing = true;
        let dirty = false;
        let pendingSave = Promise.resolve(false);
        let saveSequence = 0;
        let lastStamp = 0;
        let hashPending = false;
        const readingLine = () => main.getBoundingClientRect().top + (toolbar ? toolbar.getBoundingClientRect().height : 0) + 12;
        if (!result.available) setStatus('此浏览器暂时无法保存阅读位置；阅读和复制仍可使用');

        function save(force = false) {
            if (!armed || (!dirty && !force)) return pendingSave;
            dirty = false;
            win.clearTimeout(timer);
            const line = readingLine();
            let index = -1;
            for (let i = 0; i < elements.length; i++) {
                const rect = elements[i].getBoundingClientRect();
                if (!rect.height) continue;
                if (rect.top <= line) index = i;
                else { if (index === -1) index = i; break; }
            }
            const anchor = index === -1 ? null : {
                ...descriptors[index], index, offset: line - elements[index].getBoundingClientRect().top
            };
            lastStamp = Math.max(Date.now(), lastStamp + 1);
            const record = {
                version: 1, bookId, path: win.location.pathname,
                title: (doc.body.dataset.chapterTitle || '阅读中').slice(0, 500),
                scrollTop: main.scrollTop, anchor, updatedAt: lastStamp
            };
            const ok = writeProgress(storage, bookId, record);
            const sequence = ++saveSequence;
            pendingSave = remoteRequest(win, '/api/books/' + encodeURIComponent(bookId) + '/progress', {
                method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Reader3-Progress': '1'},
                body: JSON.stringify(record), keepalive: true
            }).then(data => {
                const confirmed = validateRecord(data?.record, bookId);
                if (confirmed) {
                    serverRecords[bookId] = latestRecord(serverRecords[bookId], confirmed);
                    const newest = latestRecord(readProgress(storage, bookId).record, confirmed);
                    writeProgress(storage, bookId, newest);
                }
                if (sequence === saveSequence) setStatus(confirmed ? '阅读位置已保存在本机' : ok ? '暂存于此浏览器 · 本机保存未成功' : '进度未能保存；阅读和复制仍可使用');
                return Boolean(confirmed || ok);
            });
            return pendingSave;
        }

        main.addEventListener('scroll', () => {
            if (!armed || doc.visibilityState === 'hidden') return;
            dirty = true;
            win.clearTimeout(timer);
            timer = win.setTimeout(() => save(), 350);
        }, {passive: true});
        main.addEventListener('wheel', () => { userMoved = true; }, {passive: true});
        main.addEventListener('touchstart', () => { userMoved = true; }, {passive: true});
        doc.addEventListener('keydown', event => {
            if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key)) userMoved = true;
        });
        function flush() {
            if (hashPending && !initializing) {
                hashPending = false;
                armed = true;
                return save(true);
            }
            return save();
        }
        win.addEventListener('pagehide', flush);
        win.reader3FlushProgress = () => {
            if (hashPending && !initializing) return new Promise(resolve => win.requestAnimationFrame(() => resolve(flush())));
            return flush();
        };
        doc.addEventListener('reader:layout-change', () => {
            if (doc.visibilityState === 'hidden') return;
            dirty = true;
            save();
        });
        doc.addEventListener('visibilitychange', () => { if (doc.visibilityState === 'hidden') save(); });
        win.addEventListener('hashchange', () => {
            // Let an explicit footnote/TOC jump finish before recording its destination.
            armed = false;
            hashPending = true;
            win.clearTimeout(timer);
            if (!initializing) timer = win.setTimeout(() => { hashPending = false; armed = true; save(true); }, 600);
        });

        async function ready() {
            await serverReady;
            if (doc.fonts?.ready) await Promise.race([doc.fonts.ready.catch(() => {}), new Promise(resolve => win.setTimeout(resolve, 1500))]);
            let completed = false;
            let fallbackTimer;
            function finishInitialization() {
                if (completed) return;
                completed = true;
                win.clearTimeout(fallbackTimer);
                const saved = getSaved(bookId);
                const resume = new win.URLSearchParams(win.location.search).get('resume') === '1';
                const restore = !userMoved && shouldRestore(saved, win.location.pathname, win.location.hash, resume, navigationType(win));
                if (restore) {
                    const index = findAnchorIndex(descriptors, saved.anchor);
                    const top = index === -1 ? saved.scrollTop : main.scrollTop + elements[index].getBoundingClientRect().top + saved.anchor.offset - readingLine();
                    const behavior = main.style.scrollBehavior;
                    main.style.scrollBehavior = 'auto';
                    main.scrollTop = Math.max(0, top);
                    main.style.scrollBehavior = behavior;
                }
                // The first write happens only after restoration or the existing hash handler.
                initializing = false;
                armed = true;
                if (restore) setStatus('已回到上次阅读位置');
                // Hidden old tabs must not displace the position of the active window.
                if (doc.visibilityState !== 'hidden') save(true);
                else if (!restore) setStatus('已就绪，等待继续阅读');
            }
            // Embedded browsers can pause rendering frames while obscured. Persistence
            // initialization must still complete; late frames cannot run it a second time.
            fallbackTimer = win.setTimeout(finishInitialization, 250);
            win.requestAnimationFrame(() => win.requestAnimationFrame(finishInitialization));
        }
        if (doc.readyState === 'complete') ready();
        else win.addEventListener('load', ready, {once: true});
    }

    return {storageKey, validPath, validateRecord, readProgress, writeProgress, shouldRestore, findAnchorIndex, latestRecord, remoteRequest, init};
}));
