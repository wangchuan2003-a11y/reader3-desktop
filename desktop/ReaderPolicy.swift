import Foundation
import CryptoKit

struct ReaderRuntime {
    let backend: URL
    let python: URL
    let supervisor: URL
    let library: URL
    let help: URL
    let pythonHome: URL?
}

/// Shared, side-effect-free rules. The desktop must not mistake another local service for this library.
enum ReaderPolicy {
    static let origin = URL(string: "http://127.0.0.1:8123")!

    static func workspaceID(_ project: URL) -> String {
        let path = project.standardizedFileURL.resolvingSymlinksInPath().path
        return SHA256.hash(data: Data(path.utf8)).map { String(format: "%02x", $0) }.joined().prefix(16).description
    }

    static func isLocal(_ url: URL) -> Bool {
        url.scheme?.lowercased() == "http" && url.host == "127.0.0.1" && url.port == 8123
            && url.user == nil && url.password == nil
    }

    static func isExternalWebsite(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil else { return false }
        return !isLocal(url)
    }

    static func matchesHealth(_ data: Data, project: URL) -> Bool {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return object["app"] as? String == "reader3" && object["workspace"] as? String == workspaceID(project)
    }

    static func isProject(_ url: URL) -> Bool {
        let manager = FileManager.default
        return ["server.py", "desktop/server_host.py", "templates/library.html"].allSatisfy {
            manager.fileExists(atPath: url.appendingPathComponent($0).path)
        } && manager.isExecutableFile(atPath: url.appendingPathComponent(".venv/bin/python").path)
    }

    static func projectURL(bundle: Bundle = .main) -> URL? {
        // Keeping the whole project together allows a new build after moving it. The venv may itself need rebuilding.
        let besideApp = bundle.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        var candidates = [besideApp]
        if let saved = bundle.object(forInfoDictionaryKey: "ReaderProjectPath") as? String {
            candidates.append(URL(fileURLWithPath: saved, isDirectory: true))
        }
        return candidates.map { $0.standardizedFileURL.resolvingSymlinksInPath() }.first(where: isProject)
    }

    static func runtime(bundle: Bundle = .main) -> ReaderRuntime? {
        if bundle.object(forInfoDictionaryKey: "ReaderBundledRuntime") as? Bool == true {
            guard let resources = bundle.resourceURL else { return nil }
            let backend = resources.appendingPathComponent("backend", isDirectory: true)
            let pythonHome = resources.appendingPathComponent("runtime", isDirectory: true)
            let python = pythonHome.appendingPathComponent("bin/python3.12")
            guard FileManager.default.isExecutableFile(atPath: python.path),
                  FileManager.default.fileExists(atPath: backend.appendingPathComponent("server.py").path),
                  FileManager.default.fileExists(atPath: backend.appendingPathComponent("server_host.py").path),
                  let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return nil }
            let library = support.appendingPathComponent("Reader3", isDirectory: true).standardizedFileURL.resolvingSymlinksInPath()
            return ReaderRuntime(backend: backend, python: python, supervisor: backend.appendingPathComponent("server_host.py"),
                                 library: library, help: resources.appendingPathComponent("桌面版.md"), pythonHome: pythonHome)
        }
        guard let project = projectURL(bundle: bundle) else { return nil }
        return ReaderRuntime(backend: project, python: project.appendingPathComponent(".venv/bin/python"),
                             supervisor: project.appendingPathComponent("desktop/server_host.py"), library: project,
                             help: project.appendingPathComponent("docs/桌面版.md"), pythonHome: nil)
    }
}
