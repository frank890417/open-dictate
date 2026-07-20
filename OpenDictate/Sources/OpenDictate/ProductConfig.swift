import Foundation

/// Product identity and runtime namespace supplied by the app bundle.
///
/// The public build defaults to Open Dictate. A private distribution may override
/// these values at build time without forking the Swift sources.
enum ProductConfig {
    private static let info = Bundle.main.infoDictionary ?? [:]

    static let appName = string("CFBundleDisplayName", default: "OpenDictate")
    static let productID = string("ODProductID", default: "open-dictate")
    static let socketPath = environment("SOCKET_PATH")
        ?? string("ODSocketPath", default: "/tmp/open-dictate.sock")
    static let dataRoot = NSString(
        string: string("ODDataRoot", default: "~/.open-dictate")
    ).expandingTildeInPath
    static let logRoot = NSString(
        string: string("ODLogRoot", default: "~/.open-dictate/dictation-log")
    ).expandingTildeInPath
    static let daemonLaunchLabel = string("ODDaemonLaunchLabel", default: "org.opendictate.daemon")
    static let shellLaunchLabel = string("ODShellLaunchLabel", default: "org.opendictate.shell")
    static let environmentPrefix = string("ODEnvironmentPrefix", default: "OPEN_DICTATE")
    static let priorityTerms = string("ODPriorityTerms", default: "")

    static var bundledLexiconRoot: String {
        Bundle.main.resourceURL?.appendingPathComponent("vendor").path
            ?? "\(Bundle.main.bundlePath)/Contents/Resources/vendor"
    }

    static var lexiconRoot: String {
        environment("LEXICON_ROOT") ?? bundledLexiconRoot
    }

    static func environment(_ suffix: String) -> String? {
        let env = ProcessInfo.processInfo.environment
        return env["\(environmentPrefix)_\(suffix)"] ?? env["OPEN_DICTATE_\(suffix)"]
    }

    private static func string(_ key: String, default fallback: String) -> String {
        guard let value = info[key] as? String, !value.isEmpty else { return fallback }
        return value
    }
}
