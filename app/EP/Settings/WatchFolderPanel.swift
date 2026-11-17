import AppKit
import EPAPI

/// "Watch a folder…" (Settings' +, the empty library): choose a folder, watch it, then ask
/// whether what is in it already should be imported too (the server leaves it alone
/// unless asked: a watched folder imports what lands there from now on).
@MainActor
enum WatchFolderPanel {
    static func run(folders: WatchFolders, connection: ServerConnection, window: NSWindow?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = String(localized: "Watch")
        panel.message = String(localized: "New album folders that land in this folder are imported automatically.")
        if case .connected(let server) = connection.state,
            let root = server.mediaRoots.lazy.compactMap({ $0.hostPath ?? $0.path }).first(where: {
                FileManager.default.fileExists(atPath: $0)
            })
        {
            panel.directoryURL = URL(fileURLWithPath: root)
        }
        let handle = { (response: NSApplication.ModalResponse) in
            guard response == .OK, let url = panel.url else { return }
            Task {
                guard let folder = await folders.add(path: url.path) else { return }
                if askImportExisting(folder, window: window) {
                    await folders.importExisting(folder.id)
                }
            }
        }
        if let window {
            panel.beginSheetModal(for: window, completionHandler: handle)
        } else {
            handle(panel.runModal())
        }
    }

    private static func askImportExisting(_ folder: Components.Schemas.WatchDirRead, window: NSWindow?) -> Bool {
        let alert = NSAlert()
        alert.messageText = String(localized: "Import what is in “\(folder.displayPath)” now?")
        alert.informativeText = String(
            localized: "EP imports new folders that land here. It can also import the albums already in it.")
        alert.addButton(withTitle: String(localized: "Import Now"))
        alert.addButton(withTitle: String(localized: "Only New Folders"))
        return alert.runModal() == .alertFirstButtonReturn
    }
}
