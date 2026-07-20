import Foundation

/// 極簡 log。launchd 會把 stderr 收到 /tmp/open-dictate-shell.err.log（見 launchagents plist）。
func mdLog(_ message: String) {
    NSLog("[\(ProductConfig.appName)] %@", message)
}
