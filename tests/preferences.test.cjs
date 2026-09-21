const test = require('node:test');
const assert = require('node:assert/strict');
const settings = require('../static/preferences.js');

function storage() {
    const values = new Map();
    return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
}

test('missing or incompatible stored settings fall back to readable defaults', () => {
    for (const value of [null, {}, [], true, {version: 2, fontSize: 28, theme: 'dark'}]) {
        assert.deepEqual(settings.normalizePreferences(value), settings.DEFAULTS);
    }
    assert.equal(settings.DEFAULTS.theme, 'light');
    assert.equal(settings.DEFAULTS.fontSize, 20);
});

test('invalid field values cannot become CSS and do not discard unrelated valid choices', () => {
    const safe = settings.normalizePreferences({version: 1, fontSize: -12, lineHeight: Infinity,
        width: '__proto__', theme: 'dark', arbitrary: 'url(https://example.invalid)'});
    assert.deepEqual(safe, {...settings.DEFAULTS, theme: 'dark'});
    assert.equal(settings.normalizePreferences({...settings.DEFAULTS, fontSize: '28'}).fontSize, 20);
    assert.equal(settings.normalizePreferences({...settings.DEFAULTS, width: 'constructor'}).width, 'balanced');
});

test('preferences round-trip without sharing the reading-progress namespace', () => {
    const store = storage();
    store.setItem('reader3:progress:v1:book', '{"path":"/read/book/2"}');
    const wanted = {...settings.DEFAULTS, fontSize: 28, lineHeight: 2.15, width: 'narrow', theme: 'warm'};
    assert.equal(settings.writePreferences(store, wanted), true);
    assert.deepEqual(settings.readPreferences(store), {preferences: wanted, available: true});
    assert.equal(store.getItem('reader3:progress:v1:book'), '{"path":"/read/book/2"}');
});

test('corrupt JSON and malformed fields return usable settings', () => {
    const store = storage();
    store.setItem(settings.STORAGE_KEY, '{broken');
    assert.deepEqual(settings.readPreferences(store), {preferences: {...settings.DEFAULTS}, available: true});
    store.setItem(settings.STORAGE_KEY, JSON.stringify({version: 1, theme: 'dark', width: 'url(evil)', fontSize: 2000}));
    assert.deepEqual(settings.readPreferences(store).preferences, {...settings.DEFAULTS, theme: 'dark'});
});

test('blocked storage and quota failure do not break settings or claim persistence', () => {
    const denied = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }};
    assert.equal(settings.readPreferences(denied).available, false);
    assert.equal(settings.writePreferences(denied, settings.DEFAULTS), false);
    assert.deepEqual(settings.readPreferences(null).preferences, settings.DEFAULTS);
});

test('early application uses validated CSS values and leaves content untouched', () => {
    const values = new Map();
    const root = {dataset: {}, textContent: '原文', style: {setProperty: (key, value) => values.set(key, value)}};
    settings.applyPreferences(root, {...settings.DEFAULTS, fontSize: 24, width: 'narrow', theme: 'dark'});
    assert.equal(root.dataset.theme, 'dark');
    assert.equal(values.get('--reading-font-size'), '24px');
    assert.equal(values.get('--reading-measure'), '30em');
    settings.applyPreferences(root, {...settings.DEFAULTS, fontSize: 'calc(100vw)', width: 'inherit'});
    assert.equal(values.get('--reading-font-size'), '20px');
    assert.equal(values.get('--reading-measure'), '36em');
    assert.equal(root.textContent, '原文');
});

test('bootstrap handles a denied localStorage getter before body exists', () => {
    const values = new Map();
    const win = {document: {documentElement: {dataset: {}, style: {setProperty: (key, value) => values.set(key, value)}}}};
    Object.defineProperty(win, 'localStorage', {get() { throw new Error('denied'); }});
    assert.doesNotThrow(() => settings.bootstrap(win));
    assert.equal(values.get('--reading-line-height'), '1.9');
});

test('paragraph fraction stays bounded across layout changes and empty blocks', () => {
    assert.equal(settings.fractionInBlock(100, 200, 150), .25);
    assert.equal(settings.fractionInBlock(100, 200, 50), 0);
    assert.equal(settings.fractionInBlock(100, 200, 350), 1);
    assert.equal(settings.fractionInBlock(100, 0, 100), 0);
    assert.equal(settings.fractionInBlock(100, NaN, 100), 0);
    // Keeping 25% of a paragraph remains meaningful after its height changes with font size.
    const fraction = settings.fractionInBlock(100, 200, 150);
    assert.equal(300 * fraction, 75);
});
