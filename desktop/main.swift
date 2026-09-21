import AppKit
import WebKit
import UniformTypeIdentifiers

enum HealthState { case ours, unavailable, other }

// An upload must never follow a redirect away from the local service.
final class LocalSessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

@MainActor
final class ReaderApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate,
    WKUIDelegate, WKScriptMessageHandlerWithReply {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusView: NSStackView!
    private var statusTitle: NSTextField!
    private var statusDetail: NSTextField!
    private var spinner: NSProgressIndicator!
    private var retryButton: NSButton!
    private var logButton: NSButton!
    private var project: URL?
    private var runtime: ReaderRuntime?
    private var ownedProcess: Process?
    private var logHandle: FileHandle?
    private var startupTask: Task<Void, Never>?
    private var ready = false
    private var quitting = false
    private var awaitingQuit = false
    private var quitShutdownStarted = false
    private var requestedImport = false
    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0, "SOCKSEnable": 0]
        configuration.timeoutIntervalForRequest = 1.5
        configuration.timeoutIntervalForResource = 2
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration, delegate: LocalSessionDelegate(), delegateQueue: nil)
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        runtime = ReaderPolicy.runtime()
        project = runtime?.backend
        createMenus()
        createWindow()
        startReader()
    }

    private func createWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "readerClipboard")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.translatesAutoresizingMaskIntoConstraints = false

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Reader3 阅读器"
        window.minSize = NSSize(width: 720, height: 520)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.setFrameAutosaveName("Reader3MainWindow")
        window.center()

        let root = NSView()
        root.addSubview(webView)
        window.contentView = root
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.topAnchor.constraint(equalTo: root.topAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])

        statusTitle = NSTextField(labelWithString: "正在打开书架…")
        statusTitle.font = .systemFont(ofSize: 24, weight: .semibold)
        statusTitle.alignment = .center
        statusDetail = NSTextField(wrappingLabelWithString: "书籍保存在这台电脑上。")
        statusDetail.font = .systemFont(ofSize: 15)
        statusDetail.textColor = .secondaryLabelColor
        statusDetail.alignment = .center
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        retryButton = NSButton(title: "重新打开", target: self, action: #selector(retryStartup))
        logButton = NSButton(title: "查看运行日志", target: self, action: #selector(showLog))
        statusView = NSStackView(views: [spinner, statusTitle, statusDetail, retryButton, logButton])
        statusView.orientation = .vertical
        statusView.spacing = 18
        statusView.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(statusView)
        NSLayoutConstraint.activate([
            statusView.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            statusView.centerYAnchor.constraint(equalTo: root.centerYAnchor),
            statusView.widthAnchor.constraint(lessThanOrEqualTo: root.widthAnchor, constant: -100),
            statusDetail.widthAnchor.constraint(lessThanOrEqualToConstant: 580),
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func createMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Reader3")
        appMenu.addItem(withTitle: "关于 Reader3 阅读器", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        let services = NSMenuItem(title: "服务", action: nil, keyEquivalent: "")
        services.submenu = NSMenu(title: "服务")
        appMenu.addItem(services)
        NSApp.servicesMenu = services.submenu
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 Reader3", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "隐藏其他", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "显示全部", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Reader3", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let file = NSMenu(title: "文件")
        add(file, "回到书架", #selector(showShelf), "l")
        add(file, "导入 EPUB…", #selector(importBook), "o")
        let folder = add(file, "在访达中显示书库", #selector(showLibrary), "o")
        folder.keyEquivalentModifierMask = [.command, .shift]
        file.addItem(.separator())
        file.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        append(file, to: main)

        let edit = NSMenu(title: "编辑")
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        for (title, selector, key) in [("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        append(edit, to: main)

        let view = NSMenu(title: "显示")
        add(view, "后退", #selector(goBack), "[")
        add(view, "前进", #selector(goForward), "]")
        add(view, "重新载入", #selector(reloadPage), "r")
        view.addItem(.separator())
        let fullscreen = view.addItem(withTitle: "切换全屏", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullscreen.keyEquivalentModifierMask = [.command, .control]
        append(view, to: main)

        let help = NSMenu(title: "帮助")
        add(help, "桌面版使用说明", #selector(showHelp), "")
        add(help, "查看运行日志", #selector(showLog), "")
        append(help, to: main)
        NSApp.helpMenu = help
        NSApp.mainMenu = main
    }

    @discardableResult private func add(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let item = menu.addItem(withTitle: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func append(_ menu: NSMenu, to parent: NSMenu) {
        let item = NSMenuItem(title: menu.title, action: nil, keyEquivalent: "")
        item.submenu = menu
        parent.addItem(item)
    }

    private func showStatus(_ title: String, detail: String, error: Bool) {
        webView.isHidden = true
        statusView.isHidden = false
        statusTitle.stringValue = title
        statusDetail.stringValue = detail
        retryButton.isHidden = !error
        logButton.isHidden = !error || project == nil
        spinner.isHidden = error
        if error { spinner.stopAnimation(nil) } else { spinner.startAnimation(nil) }
    }

    private func health() async -> HealthState {
        guard let library = runtime?.library else { return .other }
        do {
            let (data, response) = try await session.data(from: ReaderPolicy.origin.appendingPathComponent("api/health"))
            guard let response = response as? HTTPURLResponse, response.statusCode == 200,
                  let responseURL = response.url, ReaderPolicy.isLocal(responseURL),
                  ReaderPolicy.matchesHealth(data, project: library) else { return .other }
            return .ours
        } catch {
            // A bound port that doesn't answer may still belong to another app.
            // Starting ours cannot take that port; the readiness check will never load it.
            return .unavailable
        }
    }

    @objc private func retryStartup() { startReader() }

    private func startReader() {
        guard !quitting else { return }
        startupTask?.cancel()
        ready = false
        guard let project else {
            showStatus("找不到阅读器文件", detail: "应用的运行文件可能不完整。请重新构建或重新复制完整的 Reader3.app；书库不会因此被删除。", error: true)
            return
        }
        showStatus("正在打开书架…", detail: "第一次启动可能需要几秒钟。", error: false)
        startupTask = Task { [weak self] in
            guard let self else { return }
            let initial = await health()
            guard !Task.isCancelled, !quitting else { return }
            switch initial {
            case .ours:
                openShelfWhenReady()
                return
            case .other:
                showStatus("阅读器暂时无法打开", detail: "本机 8123 端口正在运行另一个程序或另一份阅读器。请关闭那一份服务后重试；本应用没有停止它。", error: true)
                return
            case .unavailable:
                break
            }
            if ownedProcess?.isRunning != true {
                do { try launchServer(project) }
                catch {
                    showStatus("未能启动阅读器", detail: "请检查应用文件是否完整，以及书库文件夹是否可以写入。运行日志保留了具体原因。", error: true)
                    return
                }
            }
            let deadline = Date().addingTimeInterval(20)
            while Date() < deadline, !Task.isCancelled, !quitting {
                let state = await health()
                if state == .ours { openShelfWhenReady(); return }
                if state == .other {
                    stopOwnedServer()
                    showStatus("本机地址被其他程序占用", detail: "请关闭占用 8123 端口的服务后重试；书籍没有受到影响。", error: true)
                    return
                }
                if ownedProcess?.isRunning != true { break }
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
            guard !Task.isCancelled, !quitting else { return }
            stopOwnedServer()
            showStatus("阅读器没有及时启动", detail: "可以重试，或查看运行日志。书籍仍保存在本机书库中。", error: true)
        }
    }

    private func launchServer(_ project: URL) throws {
        guard let runtime else { throw NSError(domain: "Reader3", code: 1) }
        let logs = runtime.library.appendingPathComponent(".reader3", isDirectory: true)
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let log = logs.appendingPathComponent("desktop-server.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: log)
        try handle.seekToEnd()
        let process = Process()
        process.executableURL = runtime.python
        process.arguments = ["-B", "-u", runtime.supervisor.path,
                             "--project", project.path, "--parent", String(ProcessInfo.processInfo.processIdentifier)]
        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"] { environment.removeValue(forKey: key) }
        environment["READER3_LIBRARY_DIR"] = runtime.library.path
        environment["PYTHONPATH"] = runtime.backend.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if let home = runtime.pythonHome { environment["PYTHONHOME"] = home.path }
        process.environment = environment
        process.currentDirectoryURL = project
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = handle
        process.standardError = handle
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self, self.ownedProcess === process else { return }
                self.ownedProcess = nil
                try? self.logHandle?.close()
                self.logHandle = nil
                if self.awaitingQuit {
                    self.awaitingQuit = false
                    NSApp.reply(toApplicationShouldTerminate: true)
                } else if self.ready && !self.quitting {
                    self.ready = false
                    self.showStatus("阅读器服务已停止", detail: "点击重新打开即可再试。书籍仍保存在本机。", error: true)
                }
            }
        }
        try process.run()
        ownedProcess = process
        logHandle = handle
    }

    private func stopOwnedServer() {
        if let process = ownedProcess, process.isRunning { process.terminate() }
    }

    private func openShelfWhenReady() {
        ready = true
        webView.load(URLRequest(url: ReaderPolicy.origin))
    }

    @objc private func showShelf() {
        if ready { webView.load(URLRequest(url: ReaderPolicy.origin)) } else { startReader() }
    }
    @objc private func goBack() { if webView.canGoBack { webView.goBack() } }
    @objc private func goForward() { if webView.canGoForward { webView.goForward() } }
    @objc private func reloadPage() { if ready { webView.reload() } else { startReader() } }
    @objc private func importBook() {
        requestedImport = true
        if ready { presentImportPanel() } else { showShelf() }
    }
    private func presentImportPanel() {
        requestedImport = false
        let panel = NSOpenPanel()
        panel.title = "选择 EPUB 电子书"
        panel.prompt = "导入"
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "epub") ?? .data]
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            self?.uploadEPUB(url)
        }
    }
    private func uploadEPUB(_ file: URL) {
        guard file.pathExtension.lowercased() == "epub",
              let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize,
              size <= 50 * 1024 * 1024 else {
            showMessage("请选择小于 50 MB 的 EPUB 文件。"); return
        }
        showStatus("正在导入电子书…", detail: "正在整理目录和正文，请稍候。", error: false)
        Task { [weak self] in
            guard let self else { return }
            do {
                var request = URLRequest(url: ReaderPolicy.origin.appendingPathComponent("api/books/import"))
                request.httpMethod = "POST"
                request.timeoutInterval = 180
                request.setValue("application/epub+zip", forHTTPHeaderField: "Content-Type")
                request.setValue("1", forHTTPHeaderField: "X-Reader3-Import")
                request.setValue(ReaderPolicy.origin.absoluteString, forHTTPHeaderField: "Origin")
                request.setValue(file.lastPathComponent.addingPercentEncoding(withAllowedCharacters: .alphanumerics), forHTTPHeaderField: "X-EPUB-Filename")
                let configuration = URLSessionConfiguration.ephemeral
                configuration.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0, "SOCKSEnable": 0]
                configuration.timeoutIntervalForResource = 180
                let uploadSession = URLSession(configuration: configuration, delegate: LocalSessionDelegate(), delegateQueue: nil)
                defer { uploadSession.finishTasksAndInvalidate() }
                let (data, response) = try await uploadSession.upload(for: request, fromFile: file)
                let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode),
                      let id = object?["book_id"] as? String else {
                    showShelf()
                    showMessage(object?["error"] as? String ?? "导入未完成，请重新选择 EPUB 文件。")
                    return
                }
                var destination = URLComponents(url: ReaderPolicy.origin, resolvingAgainstBaseURL: false)!
                destination.path = "/"
                destination.queryItems = [URLQueryItem(name: "imported", value: id)]
                if object?["duplicate"] as? Bool == true { destination.queryItems?.append(URLQueryItem(name: "duplicate", value: "1")) }
                webView.load(URLRequest(url: destination.url!))
            } catch {
                showShelf()
                showMessage("导入没有完成，请确认阅读服务仍在运行后重试。原文件和已有书籍未受影响。")
            }
        }
    }
    private func showMessage(_ message: String) {
        guard !quitting else { return }
        let alert = NSAlert()
        alert.messageText = "Reader3 阅读器"
        alert.informativeText = message
        alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: window)
    }
    @objc private func showLibrary() { if let library = runtime?.library { NSWorkspace.shared.open(library) } }
    @objc private func showLog() {
        guard let library = runtime?.library else { return }
        let url = library.appendingPathComponent(".reader3/desktop-server.log")
        if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.open(url) }
        else { NSWorkspace.shared.open(library) }
    }
    @objc private func showHelp() {
        guard let help = runtime?.help else { return }
        NSWorkspace.shared.open(help)
    }
    @objc private func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Reader3 阅读器",
            .applicationVersion: "0.1",
            .credits: NSAttributedString(string: "在本机阅读 EPUB，复制原文后与 AI 讨论。\n基于 Karpathy 的 reader3 改造。\n桌面版与网页使用同一份本机书库。"),
        ])
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return awaitingQuit ? .terminateLater : .terminateNow }
        quitting = true
        startupTask?.cancel()
        awaitingQuit = true
        // Await the shared progress save before stopping a service owned by this app.
        // Old pages without the new API still use their original pagehide save path.
        webView?.callAsyncJavaScript("""
            if (typeof window.reader3FlushProgress === 'function') {
                await window.reader3FlushProgress();
            } else {
                window.dispatchEvent(new Event('pagehide'));
            }
            """, arguments: [:], in: nil, in: .page) { [weak self] _ in
                self?.finishQuit()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.finishQuit() }
        // The supervisor independently notices loss of this parent and cleans up its backend.
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { [weak self] in
            guard let self, self.awaitingQuit else { return }
            self.awaitingQuit = false
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
    private func finishQuit() {
        guard awaitingQuit, !quitShutdownStarted else { return }
        quitShutdownStarted = true
        if ownedProcess?.isRunning == true { stopOwnedServer() }
        else {
            awaitingQuit = false
            NSApp.reply(toApplicationShouldTerminate: true)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if ReaderPolicy.isLocal(url) {
            if action.targetFrame == nil {
                webView.load(action.request)
                decisionHandler(.cancel)
            } else { decisionHandler(.allow) }
            return
        }
        if ReaderPolicy.isExternalWebsite(url), action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        statusView.isHidden = true
        spinner.stopAnimation(nil)
        webView.isHidden = false
        window.title = (webView.title?.isEmpty == false ? webView.title! : "Reader3 阅读器")
        window.makeFirstResponder(webView)
        if requestedImport { presentImportPanel() }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled, !quitting else { return }
        showStatus("页面没有打开", detail: "本机阅读服务可能已关闭。点击重新打开即可重试。", error: true)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        showStatus("阅读页面意外停止", detail: "点击重新打开即可恢复；已导入的书籍仍在本机。", error: true)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard frame.isMainFrame, let url = frame.request.url, ReaderPolicy.isLocal(url) else {
            completionHandler(nil); return
        }
        let panel = NSOpenPanel()
        panel.title = "选择 EPUB 电子书"
        panel.prompt = "导入"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "epub") ?? .data]
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.name == "readerClipboard", message.frameInfo.isMainFrame,
              let url = message.frameInfo.request.url, ReaderPolicy.isLocal(url),
              let text = message.body as? String, text.utf8.count <= 5_000_000 else {
            replyHandler(nil, "复制内容无效，请使用页面中的预览手动复制。"); return
        }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        guard pasteboard.setString(text, forType: .string) else {
            replyHandler(nil, "未能写入剪贴板。"); return
        }
        replyHandler(true, nil)
    }
}

func runPolicyChecks() {
    let accepted = ["http://127.0.0.1:8123/", "http://127.0.0.1:8123/chapter/demo_data/0#r0-note"]
    let rejected = ["https://127.0.0.1:8123/", "http://localhost:8123/", "http://127.0.0.1:8124/",
                    "http://127.0.0.1:8123.example.com/", "http://evil@127.0.0.1:8123/", "file:///etc/passwd",
                    "javascript:alert(1)", "data:text/html,hello"]
    for raw in accepted { precondition(ReaderPolicy.isLocal(URL(string: raw)!)) }
    for raw in rejected {
        if let url = URL(string: raw) { precondition(!ReaderPolicy.isLocal(url), raw) }
    }
    precondition(ReaderPolicy.isExternalWebsite(URL(string: "https://example.com/book")!))
    precondition(!ReaderPolicy.isExternalWebsite(URL(string: "javascript:alert(1)")!))
    precondition(!ReaderPolicy.isExternalWebsite(ReaderPolicy.origin))
    let root = URL(fileURLWithPath: "/tmp/reader3-policy-project")
    let health = try! JSONSerialization.data(withJSONObject: ["app": "reader3", "workspace": ReaderPolicy.workspaceID(root), "extra": true])
    precondition(ReaderPolicy.matchesHealth(health, project: root))
    precondition(!ReaderPolicy.matchesHealth(health, project: URL(fileURLWithPath: "/tmp/another-project")))
    precondition(!ReaderPolicy.matchesHealth(Data("{}".utf8), project: root))
    print("Desktop policy checks passed: origin isolation, unsafe URLs, health identity.")
}

if CommandLine.arguments.contains("--self-test") {
    runPolicyChecks()
} else {
    MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = ReaderApp()
    app.setActivationPolicy(.regular)
    app.delegate = delegate
    app.run()
    withExtendedLifetime(delegate) {}
    }
}
