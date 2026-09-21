(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root?.document) {
        // Apply before body paint and before reading-progress restoration.
        api.bootstrap(root);
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => api.init(root), {once: true});
        else api.init(root);
    }
}(typeof window === 'undefined' ? null : window, function () {
    'use strict';
    const STORAGE_KEY = 'reader3:preferences:v1';
    const DEFAULTS = Object.freeze({version: 1, fontSize: 20, lineHeight: 1.9, width: 'balanced', theme: 'light'});
    const FONT_SIZES = [18, 20, 22, 24, 28];
    const LINE_HEIGHTS = [1.65, 1.9, 2.15];
    const WIDTHS = Object.freeze({narrow: '30em', balanced: '36em', wide: '42em'});
    const THEMES = ['light', 'warm', 'dark'];
    const BLOCKS = 'p,h1,h2,h3,h4,h5,h6,li,pre,table,figure,img';

    function normalizePreferences(value) {
        if (!value || value.version !== 1) return {...DEFAULTS};
        return {
            version: 1,
            fontSize: FONT_SIZES.includes(value.fontSize) ? value.fontSize : DEFAULTS.fontSize,
            lineHeight: LINE_HEIGHTS.includes(value.lineHeight) ? value.lineHeight : DEFAULTS.lineHeight,
            width: Object.hasOwn(WIDTHS, value.width) ? value.width : DEFAULTS.width,
            theme: THEMES.includes(value.theme) ? value.theme : DEFAULTS.theme
        };
    }

    function readPreferences(storage) {
        try {
            const raw = storage.getItem(STORAGE_KEY);
            try { return {preferences: normalizePreferences(raw ? JSON.parse(raw) : null), available: true}; }
            catch (_) { return {preferences: {...DEFAULTS}, available: true}; }
        } catch (_) { return {preferences: {...DEFAULTS}, available: false}; }
    }

    function writePreferences(storage, value) {
        try { storage.setItem(STORAGE_KEY, JSON.stringify(normalizePreferences(value))); return true; }
        catch (_) { return false; }
    }

    function applyPreferences(element, value) {
        const safe = normalizePreferences(value);
        element.dataset.theme = safe.theme;
        element.style.setProperty('--reading-font-size', safe.fontSize + 'px');
        element.style.setProperty('--reading-line-height', String(safe.lineHeight));
        element.style.setProperty('--reading-measure', WIDTHS[safe.width]);
        return safe;
    }

    function getStorage(win) {
        try { return win.localStorage; } catch (_) { return null; }
    }

    function bootstrap(win) {
        applyPreferences(win.document.documentElement, readPreferences(getStorage(win)).preferences);
    }

    function fractionInBlock(top, height, line) {
        if (!Number.isFinite(height) || height <= 0) return 0;
        return Math.max(0, Math.min(1, (line - top) / height));
    }

    function init(win) {
        const doc = win.document;
        const panel = doc.getElementById('reading-settings');
        if (!panel) return;
        const storage = getStorage(win);
        const controls = {
            fontSize: doc.getElementById('reading-font-size'),
            lineHeight: doc.getElementById('reading-line-height'),
            width: doc.getElementById('reading-width'),
            theme: doc.getElementById('reading-theme')
        };
        const status = doc.getElementById('preferences-status');
        const main = doc.getElementById('main');
        const content = doc.querySelector('.book-content');
        const toolbar = doc.querySelector('.toolbar');
        const blocks = Array.from(content.querySelectorAll(BLOCKS)).filter(node => !node.querySelector(BLOCKS));
        const initial = readPreferences(storage);
        let preferences = initial.preferences;
        if (!initial.available) status.textContent = '可调整本次显示，关闭后可能无法保留设置。';
        let changeFrame = 0;
        const line = () => main.getBoundingClientRect().top + toolbar.getBoundingClientRect().height + 12;

        function renderControls() {
            for (const [name, control] of Object.entries(controls)) control.value = String(preferences[name]);
        }

        function capturePosition() {
            if (main.scrollTop < 1) return {atTop: true};
            const readingLine = line();
            const node = blocks.find(block => block.getBoundingClientRect().bottom > readingLine);
            if (!node) return {scrollTop: main.scrollTop};
            const rect = node.getBoundingClientRect();
            return {node, fraction: fractionInBlock(rect.top, rect.height, readingLine), gap: Math.max(0, rect.top - readingLine)};
        }

        function update(next, persist) {
            const position = capturePosition();
            preferences = applyPreferences(doc.documentElement, next);
            renderControls();
            if (persist) {
                const saved = writePreferences(storage, preferences);
                status.textContent = saved ? '设置已保存，适用于所有书籍。' : '本次设置已生效，关闭后可能无法保留。';
            }
            win.cancelAnimationFrame(changeFrame);
            changeFrame = win.requestAnimationFrame(() => {
                const behavior = main.style.scrollBehavior;
                main.style.scrollBehavior = 'auto';
                if (position.atTop) main.scrollTop = 0;
                else if (position.node) {
                    const rect = position.node.getBoundingClientRect();
                    main.scrollTop = Math.max(0, main.scrollTop + rect.top + rect.height * position.fraction - line() - position.gap);
                } else main.scrollTop = position.scrollTop;
                main.style.scrollBehavior = behavior;
                doc.dispatchEvent(new win.CustomEvent('reader:layout-change'));
            });
        }

        renderControls();
        for (const [name, control] of Object.entries(controls)) {
            control.addEventListener('change', () => update({
                ...preferences, [name]: name === 'fontSize' || name === 'lineHeight' ? Number(control.value) : control.value
            }, true));
        }
        doc.getElementById('reset-reading-settings').addEventListener('click', () => update(DEFAULTS, true));
        doc.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || doc.body.classList.contains('sidebar-open')) return;
            const current = doc.activeElement?.closest('details[open]');
            if (current && current.closest('.toolbar')) {
                current.open = false;
                current.querySelector('summary').focus();
                event.preventDefault();
            }
        });
        win.addEventListener('storage', event => {
            if (event.key === STORAGE_KEY || event.key === null) {
                update(readPreferences(storage).preferences, false);
                status.textContent = '已同步当前阅读器的显示设置。';
            }
        });
    }

    return {STORAGE_KEY, DEFAULTS, normalizePreferences, readPreferences, writePreferences, applyPreferences, fractionInBlock, bootstrap, init};
}));
