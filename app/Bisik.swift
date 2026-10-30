import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics
import ServiceManagement

/// Floating pill at the bottom center of the screen that shows the dictation state.
final class StatusOverlay {
    let panel: NSPanel
    private let dot = NSView()
    private let label = NSTextField(labelWithString: "")
    private let spinner = NSProgressIndicator()

    init() {
        let size = NSSize(width: 280, height: 44)
        panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]

        let background = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        background.material = .hudWindow
        background.blendingMode = .behindWindow
        background.state = .active
        background.wantsLayer = true
        background.layer?.cornerRadius = size.height / 2
        background.layer?.masksToBounds = true

        dot.frame = NSRect(x: 20, y: (size.height - 12) / 2, width: 12, height: 12)
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 6

        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.frame = NSRect(x: 18, y: (size.height - 16) / 2, width: 16, height: 16)
        spinner.isDisplayedWhenStopped = false

        label.font = .systemFont(ofSize: 14, weight: .medium)
        label.textColor = .labelColor
        label.lineBreakMode = .byTruncatingTail
        label.frame = NSRect(x: 44, y: (size.height - 18) / 2, width: size.width - 56, height: 18)

        background.addSubview(dot)
        background.addSubview(spinner)
        background.addSubview(label)
        panel.contentView = background
    }

    func show(_ state: String, text: String? = nil) {
        switch state {
        case "recording":
            label.stringValue = "Merekam..."
            spinner.stopAnimation(nil)
            dot.isHidden = false
            dot.layer?.backgroundColor = NSColor.systemRed.cgColor
            pulseDot()
            present()
        case "transcribing":
            showSpinner("Memproses...")
        case "setup":
            showSpinner(text ?? "Menyiapkan Bisik...")
        default:
            dot.layer?.removeAllAnimations()
            spinner.stopAnimation(nil)
            panel.orderOut(nil)
        }
    }

    private func showSpinner(_ text: String) {
        label.stringValue = text
        dot.isHidden = true
        dot.layer?.removeAllAnimations()
        spinner.startAnimation(nil)
        present()
    }

    private func pulseDot() {
        let pulse = CABasicAnimation(keyPath: "opacity")
        pulse.fromValue = 1.0
        pulse.toValue = 0.25
        pulse.duration = 0.7
        pulse.autoreverses = true
        pulse.repeatCount = .infinity
        dot.layer?.add(pulse, forKey: "pulse")
    }

    private func present() {
        let screen = NSScreen.main ?? NSScreen.screens[0]
        let visible = screen.visibleFrame
        let origin = NSPoint(
            x: visible.midX - panel.frame.width / 2,
            y: visible.minY + 80
        )
        panel.setFrameOrigin(origin)
        panel.orderFrontRegardless()
    }
}

final class BisikApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let home = FileManager.default.homeDirectoryForCurrentUser
    private lazy var supportDir: URL = {
        if let override = ProcessInfo.processInfo.environment["BISIK_SUPPORT_DIR"] {
            return URL(fileURLWithPath: override)
        }
        return home.appendingPathComponent("Library/Application Support/Bisik")
    }()
    private lazy var logURL: URL = {
        if let override = ProcessInfo.processInfo.environment["BISIK_LOG"] {
            return URL(fileURLWithPath: override)
        }
        return home.appendingPathComponent("Library/Logs/bisik.log")
    }()
    private var python: URL { supportDir.appendingPathComponent("venv/bin/python") }

    private let overlay = StatusOverlay()
    private var statusItem: NSStatusItem?
    private var loginItem: NSMenuItem?
    private var engine: Process?
    private var logHandle: FileHandle?
    private var pending = ""
    private var quitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        setUpMenu()
        if CommandLine.arguments.contains("--preview-overlay") {
            overlay.show("recording")
            return
        }
        requestPermissions()
        ensureEnvironment { [weak self] in self?.startEngine() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        engine?.terminate()
    }

    // MARK: Menu

    private func setUpMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: "Bisik")
        let menu = NSMenu()
        menu.delegate = self
        menu.addItem(withTitle: "Bisik - tekan fn 2x untuk dikte", action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        for permission in permissions {
            let entry = menu.addItem(withTitle: "", action: #selector(openPermissionPane(_:)), keyEquivalent: "")
            entry.target = self
            entry.representedObject = permission.pane
        }
        menu.addItem(.separator())
        let login = menu.addItem(withTitle: "Buka saat login", action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self
        loginItem = login
        menu.addItem(withTitle: "Buka Log", action: #selector(openLog), keyEquivalent: "l").target = self
        menu.addItem(withTitle: "Keluar", action: #selector(quit), keyEquivalent: "q").target = self
        item.menu = menu
        statusItem = item
    }

    private var permissions: [(name: String, pane: String, granted: Bool)] {
        [
            ("Mikrofon", "Privacy_Microphone", AVCaptureDevice.authorizationStatus(for: .audio) == .authorized),
            ("Accessibility", "Privacy_Accessibility", AXIsProcessTrusted()),
            ("Input Monitoring", "Privacy_ListenEvent", CGPreflightListenEventAccess()),
        ]
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        for (index, permission) in permissions.enumerated() {
            menu.item(at: 2 + index)?.title = permission.granted
                ? "\(permission.name): diizinkan"
                : "\(permission.name): BELUM diizinkan - klik untuk membuka"
        }
        loginItem?.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    @objc private func openPermissionPane(_ sender: NSMenuItem) {
        guard let pane = sender.representedObject as? String,
              let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            alert("Tidak bisa mengubah pengaturan login", detail: "\(error.localizedDescription)")
        }
    }

    @objc private func openLog() {
        NSWorkspace.shared.open(logURL)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // MARK: Permissions

    // Prompts appear under the name "Bisik"; the Python engine inherits them as a child process.
    private func requestPermissions() {
        AVCaptureDevice.requestAccess(for: .audio) { _ in }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        if !CGPreflightListenEventAccess() {
            _ = CGRequestListenEventAccess()
        }
    }

    // MARK: First-launch setup

    private static let pythonCandidates: [String] = {
        let versions = ["3.12", "3.13", "3.11", "3.10"]
        let prefixes = ["/opt/homebrew/bin", "/usr/local/bin"]
        return versions.flatMap { version in
            prefixes.map { "\($0)/python\(version)" }
                + ["/Library/Frameworks/Python.framework/Versions/\(version)/bin/python3"]
        }
    }()

    /// Creates the Python environment on first launch, then calls `completion` on the main queue.
    private func ensureEnvironment(completion: @escaping () -> Void) {
        if FileManager.default.isExecutableFile(atPath: python.path) {
            completion()
            return
        }
        guard let requirements = Bundle.main.url(forResource: "requirements", withExtension: "txt") else {
            alert("Bisik tidak lengkap", detail: "requirements.txt tidak ditemukan di dalam app.")
            return
        }
        guard let base = Self.pythonCandidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            alert(
                "Python belum terpasang",
                detail: "Bisik butuh Python 3.10 sampai 3.13. Pasang dengan: brew install python@3.12, lalu buka Bisik lagi."
            )
            return
        }

        overlay.show("setup")
        openLogHandle()
        let venv = supportDir.appendingPathComponent("venv")
        let steps: [[String]] = [
            [base, "-m", "venv", venv.path],
            [python.path, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            [python.path, "-m", "pip", "install", "--progress-bar", "off", "-r", requirements.path],
        ]
        DispatchQueue.global().async { [weak self] in
            guard let self else { return }
            var failure: String?
            try? FileManager.default.createDirectory(at: self.supportDir, withIntermediateDirectories: true)
            for (index, step) in steps.enumerated() where failure == nil {
                let prefix = "Menyiapkan Bisik (\(index + 1)/\(steps.count))"
                DispatchQueue.main.async { self.overlay.show("setup", text: "\(prefix)...") }
                let status = self.run(step) { line in
                    guard let detail = Self.describe(pipLine: line) else { return }
                    DispatchQueue.main.async { self.overlay.show("setup", text: "\(prefix) \(detail)") }
                }
                if status != 0 { failure = step.prefix(4).joined(separator: " ") }
            }
            _ = self.run(["/usr/bin/defaults", "write", "com.apple.HIToolbox", "AppleFnUsageType", "-int", "0"])
            DispatchQueue.main.async {
                self.overlay.show("idle")
                if let failure {
                    try? FileManager.default.removeItem(at: venv)
                    self.alert("Penyiapan gagal", detail: "Langkah ini gagal: \(failure). Lihat ~/Library/Logs/bisik.log.")
                } else {
                    completion()
                }
            }
        }
    }

    /// Short overlay text for a pip output line, or nil if the line is not worth showing.
    private static func describe(pipLine line: String) -> String? {
        guard line.hasPrefix("Downloading ") else { return nil }
        let file = line.dropFirst("Downloading ".count)
        let name = file.prefix { $0 != "-" && $0 != " " }
        return name.isEmpty ? nil : "- mengunduh \(name)"
    }

    private func run(_ command: [String], onLine: ((String) -> Void)? = nil) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        var buffered = ""
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.logHandle?.write(data)
            buffered += String(decoding: data, as: UTF8.self)
            var lines = buffered.components(separatedBy: CharacterSet(charactersIn: "\r\n"))
            buffered = lines.removeLast()
            lines.forEach { onLine?($0) }
        }
        defer { pipe.fileHandleForReading.readabilityHandler = nil }
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus
        } catch {
            return -1
        }
    }

    private func alert(_ message: String, detail: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = detail
        alert.runModal()
    }

    // MARK: Engine

    private func openLogHandle() {
        guard logHandle == nil else { return }
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: nil)
        logHandle = try? FileHandle(forWritingTo: logURL)
    }

    private func startEngine() {
        guard let script = Bundle.main.url(forResource: "bisik", withExtension: "py"),
              FileManager.default.isExecutableFile(atPath: python.path) else {
            log("Python venv or bisik.py not found.")
            return
        }
        openLogHandle()

        let pipe = Pipe()
        let process = Process()
        process.executableURL = python
        process.arguments = [script.path]
        process.environment = ProcessInfo.processInfo.environment.merging(
            ["PYTHONUNBUFFERED": "1", "BISIK_HUD": "1"]
        ) { _, new in new }
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { self?.consume(data) }
        }
        process.terminationHandler = { [weak self] _ in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                self.overlay.show("idle")
                // The engine exits when a permission is still missing; retry until it is granted.
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.startEngine() }
            }
        }
        do {
            try process.run()
            engine = process
        } catch {
            log("Failed to start engine: \(error)")
        }
    }

    /// Writes engine output to the log and turns "@state:<name>" lines into overlay updates.
    private func consume(_ data: Data) {
        logHandle?.seekToEndOfFile()
        logHandle?.write(data)
        pending += String(decoding: data, as: UTF8.self)
        var lines = pending.components(separatedBy: CharacterSet(charactersIn: "\r\n"))
        pending = lines.removeLast()
        for line in lines where line.hasPrefix("@state:") {
            overlay.show(String(line.dropFirst("@state:".count)))
        }
    }

    private func log(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
    }
}

let app = NSApplication.shared
let delegate = BisikApp()
app.delegate = delegate
app.setActivationPolicy(.accessory)

// Without this, SIGTERM (pkill, launchctl bootout) would leave the Python engine orphaned.
signal(SIGTERM, SIG_IGN)
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termSource.setEventHandler { NSApp.terminate(nil) }
termSource.resume()

app.run()
