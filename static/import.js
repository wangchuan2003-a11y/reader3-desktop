(() => {
    const area = document.getElementById('import-area');
    if (!area) return;
    const input = document.getElementById('epub-file');
    const button = document.getElementById('choose-epub');
    const status = document.getElementById('import-status');
    let busy = false;

    function message(text, error = false) {
        status.textContent = text;
        status.classList.toggle('error', error);
    }
    function finished() {
        busy = false;
        button.disabled = false;
        input.disabled = false;
        input.value = '';
        area.removeAttribute('aria-busy');
    }
    function upload(files) {
        if (busy || !files.length) return;
        if (files.length !== 1) return message('请每次导入一本 EPUB。', true);
        const file = files[0];
        if (!file.name.toLowerCase().endsWith('.epub')) return message('请选择 .epub 格式的电子书。', true);
        if (file.size > 50 * 1024 * 1024) return message('文件超过 50 MB，请选择较小的 EPUB。', true);
        busy = true;
        button.disabled = true;
        input.disabled = true;
        area.setAttribute('aria-busy', 'true');
        message(`正在导入《${file.name}》…`);
        const request = new XMLHttpRequest();
        request.open('POST', '/api/books/import');
        request.setRequestHeader('Content-Type', 'application/epub+zip');
        request.setRequestHeader('X-Reader3-Import', '1');
        request.setRequestHeader('X-EPUB-Filename', encodeURIComponent(file.name));
        request.upload.onprogress = (event) => {
            if (event.lengthComputable) {
                const percent = Math.round(event.loaded / event.total * 100);
                message(percent === 100 ? '文件已收到，正在整理目录和正文…' : `正在读取文件… ${percent}%`);
            }
        };
        request.onload = () => {
            let data;
            try { data = JSON.parse(request.responseText); }
            catch { finished(); return message('导入没有完成，请重试。已有书籍未受影响。', true); }
            if (request.status >= 200 && request.status < 300 && data.book_id) {
                location.assign(`/?imported=${encodeURIComponent(data.book_id)}&duplicate=${data.duplicate ? '1' : '0'}`);
            } else {
                finished();
                message(data.error || '导入没有完成，请检查 EPUB 文件。', true);
            }
        };
        request.onerror = () => { finished(); message('无法连接本机阅读器，请重新启动后再试。', true); };
        request.onabort = () => { finished(); message('本次导入已取消。'); };
        request.send(file);
    }
    button.addEventListener('click', () => input.click());
    input.addEventListener('change', () => upload(input.files));
    area.addEventListener('dragover', (event) => { event.preventDefault(); if (!busy) area.classList.add('dragging'); });
    area.addEventListener('dragleave', (event) => { if (!area.contains(event.relatedTarget)) area.classList.remove('dragging'); });
    area.addEventListener('drop', (event) => { event.preventDefault(); area.classList.remove('dragging'); upload(event.dataTransfer.files); });
})();
