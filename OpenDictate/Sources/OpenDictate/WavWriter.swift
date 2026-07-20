import Foundation

/// 純手工 WAV（RIFF/PCM）寫出。輸入是已轉好的 16kHz mono PCM16 raw bytes。
enum WavWriter {
    static func wavData(pcm16 pcm: Data, sampleRate: UInt32 = 16000, channels: UInt16 = 1) -> Data {
        let bitsPerSample: UInt16 = 16
        let blockAlign = channels * bitsPerSample / 8
        let byteRate = sampleRate * UInt32(blockAlign)
        let dataSize = UInt32(pcm.count)

        var out = Data(capacity: 44 + pcm.count)
        out.append(contentsOf: Array("RIFF".utf8))
        out.appendLE(UInt32(36) + dataSize)
        out.append(contentsOf: Array("WAVE".utf8))
        out.append(contentsOf: Array("fmt ".utf8))
        out.appendLE(UInt32(16))            // fmt chunk size
        out.appendLE(UInt16(1))             // PCM
        out.appendLE(channels)
        out.appendLE(sampleRate)
        out.appendLE(byteRate)
        out.appendLE(blockAlign)
        out.appendLE(bitsPerSample)
        out.append(contentsOf: Array("data".utf8))
        out.appendLE(dataSize)
        out.append(pcm)
        return out
    }

    static func write(pcm16: Data, to url: URL, sampleRate: UInt32 = 16000, channels: UInt16 = 1) throws {
        try wavData(pcm16: pcm16, sampleRate: sampleRate, channels: channels)
            .write(to: url, options: .atomic)
    }
}

private extension Data {
    mutating func appendLE(_ v: UInt16) {
        var le = v.littleEndian
        Swift.withUnsafeBytes(of: &le) { append(contentsOf: $0) }
    }
    mutating func appendLE(_ v: UInt32) {
        var le = v.littleEndian
        Swift.withUnsafeBytes(of: &le) { append(contentsOf: $0) }
    }
}
