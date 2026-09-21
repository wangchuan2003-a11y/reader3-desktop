const test = require('node:test');
const assert = require('node:assert/strict');
const progress = require('../static/progress.js');

const BOOK = 'sync-book_data';
const PATH = '/chapter/' + BOOK + '/0';
const settle = () => new Promise(resolve => setImmediate(resolve));
const reply = data => ({ok: true, json: async () => data});

function memoryStorage() {
    const values = new Map();
    return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
}

function savedRecord(scrollTop) {
    return {version: 1, bookId: BOOK, path: PATH, title: '第一章', scrollTop, anchor: null, updatedAt: Date.now() - 1000};
}

// A clock only schedules public timer/frame events; persistence and restore decisions
// all run through the production init() function below.
function clock() {
    let now = 0;
    let nextId = 1;
    const tasks = new Map();
    const schedule = (fn, delay = 0) => {
        const id = nextId++;
        tasks.set(id, {at: now + delay, fn});
        return id;
    };
    return {
        setTimeout: schedule,
        clearTimeout: id => tasks.delete(id),
        requestAnimationFrame: fn => schedule(fn, 16),
        async advance(ms) {
            const until = now + ms;
            await settle();
            for (;;) {
                const task = [...tasks.entries()].filter(([, item]) => item.at <= until).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
                if (!task) break;
                const [id, item] = task;
                tasks.delete(id);
                now = item.at;
                item.fn();
                await settle();
            }
            now = until;
            await settle();
        }
    };
}

function reader({storage = memoryStorage(), hidden = false, navigation = 'navigate', failedServer = false, hashTarget = null, performanceAPI, frameDelay = 16} = {}) {
    const scheduler = clock();
    const win = new EventTarget();
    const doc = new EventTarget();
    const main = new EventTarget();
    const requests = [];
    const network = {deferred: false};
    let scrollTop = 0;
    Object.defineProperty(main, 'scrollTop', {
        get: () => scrollTop,
        set: value => {
            scrollTop = Math.max(0, value);
            // Browsers deliver scroll after the assignment, rather than synchronously.
            scheduler.setTimeout(() => main.dispatchEvent(new Event('scroll')), 0);
        }
    });
    main.style = {scrollBehavior: ''};
    main.getBoundingClientRect = () => ({top: 0, bottom: 720, height: 720});
    const toolbar = {getBoundingClientRect: () => ({top: 0, bottom: 100, height: 100})};
    const blocks = [200, 800, 1600, 2400].map((top, index) => ({
        id: 'p' + index, tagName: 'P', textContent: '正文段落' + index,
        querySelector: () => null, getAttribute: () => null,
        getBoundingClientRect: () => ({top: top - scrollTop, bottom: top + 180 - scrollTop, height: 180})
    }));
    const content = {querySelectorAll: () => blocks};
    const status = {textContent: '正在恢复阅读位置…'};
    Object.assign(doc, {
        readyState: 'complete', visibilityState: hidden ? 'hidden' : 'visible',
        body: {dataset: {bookId: BOOK, chapterTitle: '第一章'}},
        getElementById: id => id === 'main' ? main : id === 'reading-progress-status' ? status : null,
        querySelector: selector => selector === '.book-content' ? content : selector === '.toolbar' ? toolbar : null,
        querySelectorAll: () => []
    });
    Object.assign(win, {
        document: doc, localStorage: storage, AbortController, Event, URLSearchParams,
        location: {pathname: PATH, search: '', hash: ''},
        performance: performanceAPI === undefined ? {getEntriesByType: () => [{type: navigation}]} : performanceAPI,
        setTimeout: scheduler.setTimeout, clearTimeout: scheduler.clearTimeout,
        requestAnimationFrame: frameDelay === null ? () => 0 : fn => scheduler.setTimeout(fn, frameDelay),
        fetch: (path, options = {}) => {
            if (options.method !== 'PUT') return Promise.resolve(failedServer ? {ok: false} : reply({progress: {}}));
            const request = {record: JSON.parse(options.body), options};
            requests.push(request);
            if (failedServer) return Promise.resolve({ok: false});
            if (!network.deferred) return Promise.resolve(reply({record: request.record}));
            return new Promise((resolve, reject) => {
                request.resolve = data => resolve(reply(data));
                options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
            });
        }
    });
    // Reader template's hash handler runs before the progress listener.
    if (hashTarget !== null) win.addEventListener('hashchange', () => win.requestAnimationFrame(() => { main.scrollTop = hashTarget; }));
    progress.init(win);
    return {
        win, doc, main, status, requests, network, storage,
        advance: scheduler.advance,
        record: () => JSON.parse(storage.getItem(progress.storageKey(BOOK))),
        emit: (target, type) => target.dispatchEvent(new Event(type))
    };
}

test('a hidden old window cannot replace newer reading progress through programmatic scrolling or closing', async () => {
    const shared = memoryStorage();
    shared.setItem(progress.storageKey(BOOK), JSON.stringify(savedRecord(688)));
    const old = reader({storage: shared, hidden: true, navigation: 'reload'});
    await old.advance(1000);
    assert.equal(old.main.scrollTop, 688, 'background restoration itself is allowed');
    assert.equal(old.requests.length, 0, 'restoration must not publish a fresh reading timestamp');

    const active = reader({storage: shared});
    await active.advance(1000);
    active.main.scrollTop = 1488;
    await active.advance(400);
    const newest = active.record();
    old.main.scrollTop = 400;
    await old.advance(400);
    old.emit(old.doc, 'visibilitychange');
    old.emit(old.win, 'pagehide');
    await old.win.reader3FlushProgress();
    assert.equal(old.requests.length, 0);
    assert.deepEqual(old.record(), newest, 'closing the old window must preserve the active window position');
});

test('synchronizing display preferences in a hidden window does not claim a new reading position', async () => {
    const store = memoryStorage();
    const newest = savedRecord(1488);
    store.setItem(progress.storageKey(BOOK), JSON.stringify(newest));
    const env = reader({storage: store, hidden: true, navigation: 'reload'});
    await env.advance(1000);
    // preferences.js dispatches this after a cross-window storage event changes layout.
    env.main.scrollTop = 1400;
    env.emit(env.doc, 'reader:layout-change');
    await env.advance(400);
    assert.equal(env.requests.length, 0);
    assert.deepEqual(env.record(), newest);
});

test('an explicit close flush waits for the immediate hash jump and saves its destination', async () => {
    const env = reader({hashTarget: 1488});
    await env.advance(1000);
    const before = env.requests.length;
    env.win.location.hash = '#p2';
    env.emit(env.win, 'hashchange');
    const flushed = env.win.reader3FlushProgress();
    await env.advance(16); // No need to wait out the normal 600ms hash debounce.
    assert.equal(await flushed, true);
    assert.equal(env.requests.length, before + 1);
    assert.equal(env.record().scrollTop, 1488);
    assert.equal(env.record().anchor.id, 'p2');
});

test('late network acknowledgements cannot roll the local cache back to an earlier paragraph', async () => {
    const env = reader();
    await env.advance(1000);
    const before = env.requests.length;
    env.network.deferred = true;
    env.main.scrollTop = 688;
    await env.advance(400);
    env.main.scrollTop = 1488;
    await env.advance(400);
    const [earlier, later] = env.requests.slice(before);
    assert.equal(earlier.record.scrollTop, 688);
    assert.equal(later.record.scrollTop, 1488);
    later.resolve({record: later.record});
    await settle();
    earlier.resolve({record: earlier.record});
    await settle();
    assert.deepEqual(env.record(), later.record);
    assert.match(env.status.textContent, /已保存在本机/);
});

test('failed server writes keep usable browser progress and an honest fallback status', async () => {
    const env = reader({failedServer: true});
    await env.advance(1000);
    env.main.scrollTop = 1488;
    await env.advance(400);
    assert.equal(await env.win.reader3FlushProgress(), true);
    assert.equal(env.record().scrollTop, 1488);
    assert.match(env.status.textContent, /暂存于此浏览器/);
    assert.match(env.status.textContent, /本机保存未成功/);
});

test('hiding a window still flushes reading performed while it was visible', async () => {
    const env = reader();
    await env.advance(1000);
    const before = env.requests.length;
    env.main.scrollTop = 688;
    await env.advance(0); // Dispatch the real reading scroll, before the debounce elapses.
    env.doc.visibilityState = 'hidden';
    env.emit(env.doc, 'visibilitychange');
    await settle();
    assert.equal(env.requests.length, before + 1);
    assert.equal(env.record().scrollTop, 688);
});

for (const scenario of [
    {name: 'performance absent', api: null, restored: false},
    {name: 'modern navigation method absent', api: {navigation: {type: 1}}, restored: true},
    {name: 'modern navigation method throws', api: {getEntriesByType() { throw new Error('unsupported'); }, navigation: {type: 1}}, restored: true},
    {name: 'legacy navigation getter throws', api: Object.defineProperty({}, 'navigation', {get() { throw new Error('unavailable'); }}), restored: false}
]) {
    test(`reading still saves with ${scenario.name}, using legacy reload detection when available`, async () => {
        const storage = memoryStorage();
        storage.setItem(progress.storageKey(BOOK), JSON.stringify(savedRecord(688)));
        const env = reader({storage, performanceAPI: scenario.api});
        await env.advance(1000);
        assert.equal(env.main.scrollTop, scenario.restored ? 688 : 0);
        const before = env.requests.length;
        env.main.scrollTop = 1488;
        await env.advance(400);
        assert.ok(env.requests.length > before, 'navigation API failures must not prevent subsequent saving');
        assert.equal(env.record().scrollTop, 1488);
        assert.match(env.status.textContent, /已保存在本机/);
    });
}

test('a hidden new page finishes its status without publishing a reading position', async () => {
    const env = reader({hidden: true});
    await env.advance(1000);
    assert.equal(env.requests.length, 0);
    assert.equal(env.status.textContent, '已就绪，等待继续阅读');
});

test('initialization completes and later reading saves when rendering frames never arrive', async () => {
    const env = reader({frameDelay: null});
    await env.advance(249);
    assert.equal(env.requests.length, 0);
    await env.advance(1);
    assert.equal(env.requests.length, 1);
    assert.match(env.status.textContent, /已保存在本机/);
    env.main.scrollTop = 1488;
    await env.advance(400);
    assert.equal(env.record().scrollTop, 1488);
});

test('the frame timeout still restores a hidden page without overwriting reading progress', async () => {
    const storage = memoryStorage();
    const original = savedRecord(688);
    storage.setItem(progress.storageKey(BOOK), JSON.stringify(original));
    const env = reader({storage, frameDelay: null, hidden: true, navigation: 'reload'});
    await env.advance(1000);
    assert.equal(env.main.scrollTop, 688);
    assert.equal(env.requests.length, 0);
    assert.deepEqual(env.record(), original);
});

for (const frameDelay of [16, 400]) {
    test(`initialization runs once when frames arrive ${frameDelay === 16 ? 'before' : 'after'} the timeout`, async () => {
        const env = reader({frameDelay});
        await env.advance(1000);
        assert.equal(env.requests.length, 1, 'competing frame and timer callbacks must not duplicate initialization');
        assert.match(env.status.textContent, /已保存在本机/);
    });
}
