import Darwin
import Foundation

public struct OpenPetsClient: Sendable {
    public var socketPath: String
    public var timeoutSeconds: Double

    public init(socketPath: String = OpenPetsPaths.defaultSocketPath, timeoutSeconds: Double = 5) {
        self.socketPath = socketPath
        self.timeoutSeconds = timeoutSeconds
    }

    @discardableResult
    public func send(_ command: PetCommand) throws -> PetResponse {
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw OpenPetsError.socketFailure("Could not create Unix socket: \(OpenPetsIPC.lastError())")
        }
        defer { Darwin.close(fd) }

        var timeout = timeval(tv_sec: Int(timeoutSeconds), tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))

        try OpenPetsIPC.withSocketAddress(path: socketPath) { address, length in
            guard Darwin.connect(fd, address, length) == 0 else {
                throw OpenPetsError.socketFailure("Could not connect to \(socketPath): \(OpenPetsIPC.lastError())")
            }
        }

        let encoder = JSONEncoder()
        var data = try encoder.encode(command)
        data.append(0x0A)
        try OpenPetsIPC.writeAll(data, to: fd)

        let line = try OpenPetsIPC.readLine(from: fd)
        guard let responseData = line.data(using: .utf8) else {
            throw OpenPetsError.protocolFailure("Server response was not valid UTF-8")
        }
        return try JSONDecoder().decode(PetResponse.self, from: responseData)
    }

    public func isPetRunning() -> Bool {
        (try? send(.ping).ok) == true
    }
}

public final class OpenPetsServer: @unchecked Sendable {
    public typealias Handler = @Sendable (PetCommand) -> PetResponse

    public let socketPath: String
    private let handler: Handler
    private let queue: DispatchQueue
    private let stateLock = NSLock()
    private var listenFD: Int32 = -1
    private var running = false
    private var ownsSocketPath = false

    public init(socketPath: String = OpenPetsPaths.defaultSocketPath, handler: @escaping Handler) {
        self.socketPath = socketPath
        self.handler = handler
        queue = DispatchQueue(label: "openpets.ipc.server.\(UUID().uuidString)")
    }

    deinit {
        stop()
    }

    public func start() throws {
        stateLock.lock()
        defer { stateLock.unlock() }

        guard !running else { return }
        try prepareSocketPathForBinding()

        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw OpenPetsError.socketFailure("Could not create Unix socket: \(OpenPetsIPC.lastError())")
        }

        var didBindSocketPath = false
        do {
            try OpenPetsIPC.withSocketAddress(path: socketPath) { address, length in
                guard Darwin.bind(fd, address, length) == 0 else {
                    throw OpenPetsError.socketFailure("Could not bind \(socketPath): \(OpenPetsIPC.lastError())")
                }
            }
            didBindSocketPath = true

            guard Darwin.listen(fd, SOMAXCONN) == 0 else {
                throw OpenPetsError.socketFailure("Could not listen on \(socketPath): \(OpenPetsIPC.lastError())")
            }
        } catch {
            Darwin.close(fd)
            if didBindSocketPath {
                try? FileManager.default.removeItem(atPath: socketPath)
            }
            throw error
        }

        listenFD = fd
        running = true
        ownsSocketPath = true
        queue.async { [self] in
            acceptLoop(fileDescriptor: fd)
        }
    }

    private func prepareSocketPathForBinding() throws {
        guard FileManager.default.fileExists(atPath: socketPath) else {
            return
        }

        var status = stat()
        guard Darwin.lstat(socketPath, &status) == 0 else {
            if errno == ENOENT {
                return
            }
            throw OpenPetsError.socketFailure("Could not inspect \(socketPath): \(OpenPetsIPC.lastError())")
        }

        guard status.st_mode & S_IFMT == S_IFSOCK else {
            throw OpenPetsError.socketFailure("Socket path exists and is not a Unix socket: \(socketPath)")
        }

        if try OpenPetsServer.socketHasListener(at: socketPath) {
            throw OpenPetsError.socketAlreadyInUse(socketPath)
        }

        try FileManager.default.removeItem(atPath: socketPath)
    }

    private static func socketHasListener(at path: String) throws -> Bool {
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw OpenPetsError.socketFailure("Could not create Unix socket: \(OpenPetsIPC.lastError())")
        }
        defer { Darwin.close(fd) }

        return try OpenPetsIPC.withSocketAddress(path: path) { address, length in
            if Darwin.connect(fd, address, length) == 0 {
                return true
            }

            switch errno {
            case ECONNREFUSED, ENOENT:
                return false
            default:
                throw OpenPetsError.socketFailure("Could not connect to \(path): \(OpenPetsIPC.lastError())")
            }
        }
    }

    public func stop() {
        stateLock.lock()
        let fd = listenFD
        let shouldRemoveSocketPath = ownsSocketPath
        running = false
        listenFD = -1
        ownsSocketPath = false
        stateLock.unlock()

        if fd >= 0 {
            Darwin.shutdown(fd, SHUT_RDWR)
            Darwin.close(fd)
        }
        if shouldRemoveSocketPath {
            try? FileManager.default.removeItem(atPath: socketPath)
        }
    }

    private func isRunning() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return running
    }

    private func acceptLoop(fileDescriptor fd: Int32) {
        while isRunning() {
            let clientFD = Darwin.accept(fd, nil, nil)
            if clientFD < 0 {
                if isRunning() {
                    continue
                }
                break
            }
            handleClient(fileDescriptor: clientFD)
            Darwin.close(clientFD)
        }
    }

    private func handleClient(fileDescriptor fd: Int32) {
        do {
            while true {
                let line = try OpenPetsIPC.readLine(from: fd)
                if line.isEmpty {
                    return
                }

                guard let data = line.data(using: .utf8) else {
                    try sendResponse(.init(ok: false, message: "Command was not valid UTF-8"), to: fd)
                    continue
                }

                let command = try JSONDecoder().decode(PetCommand.self, from: data)
                try sendResponse(handler(command), to: fd)
            }
        } catch {
            try? sendResponse(.init(ok: false, message: error.localizedDescription), to: fd)
        }
    }

    private func sendResponse(_ response: PetResponse, to fd: Int32) throws {
        var data = try JSONEncoder().encode(response)
        data.append(0x0A)
        try OpenPetsIPC.writeAll(data, to: fd)
    }
}

enum OpenPetsIPC {
    static func withSocketAddress<T>(
        path: String,
        _ body: (UnsafePointer<sockaddr>, socklen_t) throws -> T
    ) throws -> T {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)

        let maxPathLength = MemoryLayout.size(ofValue: address.sun_path)
        guard path.utf8.count < maxPathLength else {
            throw OpenPetsError.invalidSocketPath(path)
        }

        path.withCString { cPath in
            withUnsafeMutablePointer(to: &address.sun_path) { sunPath in
                sunPath.withMemoryRebound(to: CChar.self, capacity: maxPathLength) { destination in
                    memset(destination, 0, maxPathLength)
                    strncpy(destination, cPath, maxPathLength - 1)
                }
            }
        }

        let length = socklen_t(MemoryLayout<sockaddr_un>.size)
        return try withUnsafePointer(to: &address) { pointer in
            try pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
                try body(socketAddress, length)
            }
        }
    }

    static func writeAll(_ data: Data, to fd: Int32) throws {
        try data.withUnsafeBytes { rawBuffer in
            guard let baseAddress = rawBuffer.baseAddress else { return }
            var offset = 0
            while offset < data.count {
                let written = Darwin.write(fd, baseAddress.advanced(by: offset), data.count - offset)
                guard written > 0 else {
                    throw OpenPetsError.socketFailure("Socket write failed: \(lastError())")
                }
                offset += written
            }
        }
    }

    static func readLine(from fd: Int32) throws -> String {
        var bytes: [UInt8] = []
        var byte: UInt8 = 0

        while true {
            let count = Darwin.read(fd, &byte, 1)
            if count == 0 {
                break
            }
            guard count > 0 else {
                throw OpenPetsError.socketFailure("Socket read failed: \(lastError())")
            }
            if byte == 0x0A {
                break
            }
            bytes.append(byte)
        }

        return String(decoding: bytes, as: UTF8.self)
    }

    static func lastError() -> String {
        String(cString: strerror(errno))
    }
}
