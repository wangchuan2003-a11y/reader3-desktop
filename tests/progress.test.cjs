const test = require('node:test');
const assert = require('node:assert/strict');
const progress = require('../static/progress.js');

function memoryStorage() {
    const values = new Map();
    return {getItem: key => values.has(key) ? values.get(key) : null, setItem: (key, value) => values.set(key, value)};
}
function record(overrides = {}) {
    return {version: 1, bookId: 'demo_data', path: '/chapter/demo_data/3', title: '第四章', scrollTop: 850,
        updatedAt: 1750000000000, anchor: {index: 8, id: 'paragraph-8', signature: '一段正文', offset: 23}, ...overrides};
}

test('only accepts local numeric reading routes for exactly the same book', () => {
    for (const path of ['/chapter/demo_data/3', '/read/demo_data/0']) assert.equal(progress.validPath(path, 'demo_data'), true);
    for (const path of ['https://evil.example/chapter/demo_data/3', '//evil.example', '/chapter/other/3', '/chapter/demo_data/-1',
        '/chapter/demo_data/3?resume=1', '/chapter/demo_data/3#p', '/chapter/demo_data/3/..', '/read/demo_data/9007199254740992',
        '/chapter/%zz/0', '/chapter/demo_data%2Fother/0', '/read/../0']) {
        assert.equal(progress.validPath(path, 'demo_data'), false, path);
    }
    assert.equal(progress.validPath('/read/%E4%B9%A6_data/1', '书_data'), true);
});

test('round-trip progress remains isolated by book', () => {
    const storage = memoryStorage();
    assert.equal(progress.writeProgress(storage, 'demo_data', record()), true);
    assert.deepEqual(progress.readProgress(storage, 'demo_data'), {record: record(), available: true});
    assert.deepEqual(progress.readProgress(storage, 'other'), {record: null, available: true});
});

test('corrupt, incompatible, and malformed records never supply a resume link', () => {
    const storage = memoryStorage();
    for (const raw of ['{broken', 'null', '[]', JSON.stringify(record({version: 2})), JSON.stringify(record({path: '/read/other/0'})),
        JSON.stringify(record({scrollTop: -2})), JSON.stringify(record({scrollTop: '850'})), JSON.stringify(record({title: {}}))]) {
        storage.setItem(progress.storageKey('demo_data'), raw);
        assert.deepEqual(progress.readProgress(storage, 'demo_data'), {record: null, available: true});
    }
    assert.equal(progress.validateRecord(record({scrollTop: Infinity}), 'demo_data'), null);
    assert.equal(progress.validateRecord(record({updatedAt: NaN}), 'demo_data'), null);
});

test('an invalid paragraph anchor still allows a safe pixel fallback', () => {
    const parsed = progress.validateRecord(record({anchor: {index: -4}}), 'demo_data');
    assert.equal(parsed.anchor, null);
    assert.equal(parsed.scrollTop, 850);
});

test('denied storage and quota exhaustion do not throw or claim success', () => {
    const denied = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }};
    assert.deepEqual(progress.readProgress(denied, 'demo_data'), {record: null, available: false});
    assert.equal(progress.writeProgress(denied, 'demo_data', record()), false);
    assert.deepEqual(progress.readProgress(null, 'demo_data'), {record: null, available: false});
    assert.equal(progress.writeProgress(memoryStorage(), 'demo_data', record({bookId: 'other'})), false);
});

test('restore requires explicit resume or reload, and never overrides hash/other chapter navigation', () => {
    const saved = record();
    const path = saved.path;
    assert.equal(progress.shouldRestore(saved, path, '', true, 'navigate'), true);
    assert.equal(progress.shouldRestore(saved, path, '', false, 'reload'), true);
    assert.equal(progress.shouldRestore(saved, path, '', false, 'navigate'), false);
    assert.equal(progress.shouldRestore(saved, '/chapter/demo_data/2', '', true, 'reload'), false);
    assert.equal(progress.shouldRestore(saved, path, '#footnote', true, 'reload'), false);
    assert.equal(progress.shouldRestore(null, path, '', true, 'reload'), false);
});

test('paragraph restoration checks text and recovers a uniquely moved paragraph', () => {
    const items = [{id: 'p0', signature: '开头'}, {id: 'p1', signature: '目标段落'}, {id: '', signature: '结尾'}];
    assert.equal(progress.findAnchorIndex(items, {id: 'p1', index: 99, signature: '目标段落'}), 1);
    assert.equal(progress.findAnchorIndex(items, {id: '', index: 2, signature: '结尾'}), 2);
    assert.equal(progress.findAnchorIndex(items, {id: 'old-id', index: 0, signature: '目标段落'}), 1);
    assert.equal(progress.findAnchorIndex(items, {id: 'p1', index: 1, signature: '已改变的正文'}), -1);
    assert.equal(progress.findAnchorIndex([{id: '', signature: '重复'}, {id: '', signature: '重复'}],
        {id: '', index: 99, signature: '重复'}), -1);
});
