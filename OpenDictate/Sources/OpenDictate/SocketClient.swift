import Foundation

enum SocketClientError: Error, CustomStringConvertible {
    /// daemon 沒起 / socket 檔不存在 / 連線被拒
    case connectFailed(String)
    /// 等回應超時（預設 10s，IO-CONTRACT 停損）
    case timeout
    /// daemon 半途斷線
    case connectionClosed
    /// 回應不是合法 JSON 行
    case badResponse(String)
    case internalError(String)

    var description: String {
        switch self {
        case .connectFailed(let why): return "連不上 daemon（\(why)）"
        case .timeout: return "daemon 回應超時"
        case .connectionClosed: return "daemon 中斷連線"
        case .badResponse(let line): return "daemon 回應格式錯誤：\(line.prefix(200))"
        case .internalError(let why): return "socket 內部錯誤：\(why)"
        }
    }
}

/// 阻塞式 unix domain socket client（一問一答，每次請求開新連線）。
/// 在背景 queue 呼叫；不要在 main thread 用。
struct SocketClient {
    static var defaultSocketPath: String { ProductConfig.socketPath }

    let socketPath: String
    /// 秒。連線後等回應的上限（SO_RCVTIMEO）。
    let timeout: TimeInterval

    init(socketPath: String = SocketClient.defaultSocketPath, timeout: TimeInterval = 10) {
        self.socketPath = socketPath
        self.timeout = timeout
    }

    /// 送一則請求、讀一行回應。throws SocketClientError。
    ///
    /// slowAfter（v0.9）：過了這麼多秒還沒回應就呼叫 onSlow 一次，然後繼續等到 timeout 才放棄——
    /// 長口述的殼逾時不再等於丟掉那句（10-16 殼放棄 9 次，daemon 其實轉完 7 句）。onSlow 在這條背景執行緒上呼叫。
    func roundTrip(_ request: DaemonRequest, slowAfter: TimeInterval? = nil,
                   onSlow: (() -> Void)? = nil) throws -> DaemonResponse {
        let requestData = try request.jsonLine()

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw SocketClientError.internalError("socket(): \(Self.errnoString())")
        }
        defer { close(fd) }

        // 防 SIGPIPE 弄死整個 app（daemon 半途關線時 write 會觸發）
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))

        // 讀寫 timeout（有 slowAfter 時先只等到 slowAfter，見讀取迴圈）
        Self.setTimeout(fd, SO_SNDTIMEO, timeout)
        var slowPending = slowAfter.map { $0 < timeout } ?? false
        Self.setTimeout(fd, SO_RCVTIMEO, slowPending ? slowAfter! : timeout)

        // sockaddr_un
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = socketPath.utf8CString // 含結尾 \0
        let sunPathSize = MemoryLayout.size(ofValue: addr.sun_path)
        guard pathBytes.count <= sunPathSize else {
            throw SocketClientError.internalError("socket path 過長")
        }
        withUnsafeMutableBytes(of: &addr.sun_path) { dst in
            pathBytes.withUnsafeBytes { src in
                dst.copyBytes(from: src)
            }
        }

        let connectResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                connect(fd, saPtr, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connectResult == 0 else {
            throw SocketClientError.connectFailed(Self.errnoString())
        }

        // 送出（write 迴圈，處理 partial write）
        try requestData.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            var sent = 0
            while sent < raw.count {
                let n = write(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent)
                if n < 0 {
                    if errno == EAGAIN || errno == EWOULDBLOCK { throw SocketClientError.timeout }
                    throw SocketClientError.connectFailed("write: \(Self.errnoString())")
                }
                sent += n
            }
        }

        // 讀到第一個 '\n' 為止
        let started = Date()
        var acc = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            if let idx = acc.firstIndex(of: 0x0A) {
                let line = acc.subdata(in: acc.startIndex..<idx)
                return try DaemonResponse.parse(line: line)
            }
            guard acc.count < 4_000_000 else {
                throw SocketClientError.badResponse("回應超過 4MB 沒有換行")
            }
            let n = recv(fd, &buf, buf.count, 0)
            if n == 0 {
                throw SocketClientError.connectionClosed
            }
            if n < 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    let left = timeout - Date().timeIntervalSince(started)
                    if slowPending && left > 0.05 {
                        slowPending = false
                        onSlow?()
                        Self.setTimeout(fd, SO_RCVTIMEO, left)
                        continue
                    }
                    throw SocketClientError.timeout
                }
                throw SocketClientError.connectFailed("recv: \(Self.errnoString())")
            }
            acc.append(contentsOf: buf[0..<n])
        }
    }

    private static func setTimeout(_ fd: Int32, _ option: Int32, _ seconds: TimeInterval) {
        var tv = timeval(tv_sec: Int(seconds), tv_usec: Int32((seconds - Double(Int(seconds))) * 1_000_000))
        setsockopt(fd, SOL_SOCKET, option, &tv, socklen_t(MemoryLayout<timeval>.size))
    }

    private static func errnoString() -> String {
        String(cString: strerror(errno))
    }
}
