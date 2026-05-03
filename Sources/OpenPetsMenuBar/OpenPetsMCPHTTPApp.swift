import Foundation
import Logging
import MCP
@preconcurrency import NIOCore
@preconcurrency import NIOHTTP1
@preconcurrency import NIOPosix

actor OpenPetsMCPHTTPApp {
    struct Configuration: Sendable {
        var host: String
        var port: Int
        var endpoint: String
        var sessionTimeout: TimeInterval
        var retryInterval: Int?

        init(
            host: String,
            port: Int,
            endpoint: String,
            sessionTimeout: TimeInterval = 3600,
            retryInterval: Int? = 1000
        ) {
            self.host = host
            self.port = port
            self.endpoint = endpoint.hasPrefix("/") ? endpoint : "/\(endpoint)"
            self.sessionTimeout = sessionTimeout
            self.retryInterval = retryInterval
        }

        var acceptsNetworkClients: Bool {
            ["0.0.0.0", "::", ""].contains(host)
        }
    }

    typealias ServerFactory = @Sendable (String) async throws -> Server

    private struct SessionContext {
        let server: Server
        let transport: StatefulHTTPServerTransport
        let createdAt: Date
        var lastAccessedAt: Date
    }

    private struct StatelessContext {
        let server: Server
        let transport: StatelessHTTPServerTransport
    }

    private struct FixedSessionIDGenerator: SessionIDGenerator {
        let sessionID: String
        func generateSessionID() -> String { sessionID }
    }

    private let configuration: Configuration
    private let serverFactory: ServerFactory
    private let validationPipeline: any HTTPRequestValidationPipeline
    private let statelessValidationPipeline: any HTTPRequestValidationPipeline
    private var channel: Channel?
    private var sessions: [String: SessionContext] = [:]
    private var statelessContext: StatelessContext?
    private var cleanupTask: Task<Void, Never>?

    nonisolated let logger: Logger

    var endpointURL: String {
        "http://\(configuration.host):\(configuration.port)\(configuration.endpoint)"
    }

    init(
        configuration: Configuration,
        serverFactory: @escaping ServerFactory,
        logger: Logger
    ) {
        self.configuration = configuration
        self.serverFactory = serverFactory
        self.logger = logger
        let originValidator: OriginValidator = configuration.acceptsNetworkClients
            ? .disabled
            : .localhost(port: configuration.port)
        validationPipeline = StandardValidationPipeline(validators: [
            originValidator,
            AcceptHeaderValidator(mode: .sseRequired),
            ContentTypeValidator(),
            ProtocolVersionValidator(),
            SessionValidator()
        ])
        statelessValidationPipeline = StandardValidationPipeline(validators: [
            originValidator,
            AcceptHeaderValidator(mode: .jsonOnly),
            ContentTypeValidator(),
            ProtocolVersionValidator()
        ])
    }

    func start() async throws {
        let group = MultiThreadedEventLoopGroup(numberOfThreads: System.coreCount)
        defer {
            Task {
                try? await group.shutdownGracefully()
            }
        }

        let bootstrap = ServerBootstrap(group: group)
            .serverChannelOption(ChannelOptions.backlog, value: 256)
            .serverChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelInitializer { channel in
                channel.pipeline.configureHTTPServerPipeline().flatMap {
                    channel.pipeline.addHandler(OpenPetsMCPHTTPHandler(app: self))
                }
            }
            .childChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelOption(ChannelOptions.maxMessagesPerRead, value: 1)

        let channel = try await bootstrap.bind(host: configuration.host, port: configuration.port).get()
        self.channel = channel
        cleanupTask = Task { await self.sessionCleanupLoop() }
        logger.info("OpenPets MCP HTTP server started", metadata: ["url": "\(endpointURL)"])
        try await channel.closeFuture.get()
    }

    func stop() async {
        cleanupTask?.cancel()
        cleanupTask = nil
        await closeAllSessions()
        await closeStatelessContext()
        try? await channel?.close()
        channel = nil
        logger.info("OpenPets MCP HTTP server stopped")
    }

    var endpoint: String {
        configuration.endpoint
    }

    func handleHTTPRequest(_ request: HTTPRequest) async -> HTTPResponse {
        let sessionID = request.header(HTTPHeaderName.sessionID)

        if let sessionID, var session = sessions[sessionID] {
            session.lastAccessedAt = Date()
            sessions[sessionID] = session

            let response = await session.transport.handleRequest(request)
            if request.method.uppercased() == "DELETE", response.statusCode == 200 {
                sessions.removeValue(forKey: sessionID)
            }
            return response
        }

        if request.method.uppercased() == "POST", isInitializeRequest(request.body) {
            return await createSessionAndHandle(request)
        }

        if request.method.uppercased() == "POST" {
            if let sessionID {
                logger.warning("Falling back to stateless MCP handling for unknown session", metadata: ["sessionID": "\(sessionID)"])
            }
            return await handleStatelessRequest(request)
        }

        if sessionID != nil {
            return .error(statusCode: 404, .invalidRequest("Not Found: Session not found or expired"))
        }
        return .error(statusCode: 400, .invalidRequest("Bad Request: Missing \(HTTPHeaderName.sessionID) header"))
    }

    private func createSessionAndHandle(_ request: HTTPRequest) async -> HTTPResponse {
        let sessionID = UUID().uuidString
        let transport = StatefulHTTPServerTransport(
            sessionIDGenerator: FixedSessionIDGenerator(sessionID: sessionID),
            validationPipeline: validationPipeline,
            retryInterval: configuration.retryInterval,
            logger: logger
        )

        do {
            let server = try await serverFactory(sessionID)
            try await server.start(transport: transport)
            sessions[sessionID] = SessionContext(
                server: server,
                transport: transport,
                createdAt: Date(),
                lastAccessedAt: Date()
            )

            let response = await transport.handleRequest(request)
            if case .error = response {
                sessions.removeValue(forKey: sessionID)
                await transport.disconnect()
            }
            return response
        } catch {
            await transport.disconnect()
            return .error(statusCode: 500, .internalError("Failed to create session: \(error.localizedDescription)"))
        }
    }

    private func closeSession(_ sessionID: String) async {
        guard let session = sessions.removeValue(forKey: sessionID) else { return }
        await session.transport.disconnect()
    }

    private func closeAllSessions() async {
        for sessionID in sessions.keys {
            await closeSession(sessionID)
        }
    }

    private func handleStatelessRequest(_ request: HTTPRequest) async -> HTTPResponse {
        do {
            let context = try await statelessContext()
            return await context.transport.handleRequest(request)
        } catch {
            return .error(statusCode: 500, .internalError("Failed to create stateless handler: \(error.localizedDescription)"))
        }
    }

    private func statelessContext() async throws -> StatelessContext {
        if let statelessContext {
            return statelessContext
        }

        let transport = StatelessHTTPServerTransport(
            validationPipeline: statelessValidationPipeline,
            logger: logger
        )
        let server = try await serverFactory("stateless")
        try await server.start(transport: transport)
        let context = StatelessContext(server: server, transport: transport)
        statelessContext = context
        return context
    }

    private func closeStatelessContext() async {
        guard let context = statelessContext else { return }
        statelessContext = nil
        await context.transport.disconnect()
    }

    private func sessionCleanupLoop() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(60))
            let now = Date()
            let expired = sessions.filter { _, context in
                now.timeIntervalSince(context.lastAccessedAt) > configuration.sessionTimeout
            }
            for (sessionID, _) in expired {
                await closeSession(sessionID)
            }
        }
    }

    private func isInitializeRequest(_ body: Data?) -> Bool {
        guard
            let body,
            let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
            let method = json["method"] as? String
        else {
            return false
        }
        return method == "initialize"
    }
}

private final class OpenPetsMCPHTTPHandler: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = HTTPServerRequestPart
    typealias OutboundOut = HTTPServerResponsePart

    private struct UnsafeContext: @unchecked Sendable {
        let value: ChannelHandlerContext
    }

    private struct RequestState {
        var head: HTTPRequestHead
        var bodyBuffer: ByteBuffer
    }

    private let app: OpenPetsMCPHTTPApp
    private var requestState: RequestState?

    init(app: OpenPetsMCPHTTPApp) {
        self.app = app
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        switch unwrapInboundIn(data) {
        case .head(let head):
            requestState = RequestState(
                head: head,
                bodyBuffer: context.channel.allocator.buffer(capacity: 0)
            )
        case .body(var buffer):
            requestState?.bodyBuffer.writeBuffer(&buffer)
        case .end:
            guard let state = requestState else { return }
            requestState = nil
            let unsafeContext = UnsafeContext(value: context)
            Task {
                await self.handleRequest(state: state, context: unsafeContext.value)
            }
        }
    }

    private func handleRequest(state: RequestState, context: ChannelHandlerContext) async {
        let head = state.head
        let path = head.uri.split(separator: "?").first.map(String.init) ?? head.uri
        let endpoint = await app.endpoint

        guard path == endpoint else {
            await writeResponse(
                .error(statusCode: 404, .invalidRequest("Not Found")),
                version: head.version,
                context: context
            )
            return
        }

        let response = await app.handleHTTPRequest(makeHTTPRequest(from: state))
        await writeResponse(response, version: head.version, context: context)
    }

    private func makeHTTPRequest(from state: RequestState) -> HTTPRequest {
        var headers: [String: String] = [:]
        for (name, value) in state.head.headers {
            if let existing = headers[name] {
                headers[name] = "\(existing), \(value)"
            } else {
                headers[name] = value
            }
        }

        let body: Data?
        if state.bodyBuffer.readableBytes > 0,
           let bytes = state.bodyBuffer.getBytes(at: 0, length: state.bodyBuffer.readableBytes) {
            body = Data(bytes)
        } else {
            body = nil
        }

        let path = String(state.head.uri.split(separator: "?").first ?? Substring(state.head.uri))
        return HTTPRequest(method: state.head.method.rawValue, headers: headers, body: body, path: path)
    }

    private func writeResponse(
        _ response: HTTPResponse,
        version: HTTPVersion,
        context: ChannelHandlerContext
    ) async {
        nonisolated(unsafe) let context = context
        let eventLoop = context.eventLoop
        let statusCode = response.statusCode
        let headers = response.headers

        switch response {
        case .stream(let stream, _):
            eventLoop.execute {
                var head = HTTPResponseHead(version: version, status: HTTPResponseStatus(statusCode: statusCode))
                for (name, value) in headers {
                    head.headers.add(name: name, value: value)
                }
                context.write(self.wrapOutboundOut(.head(head)), promise: nil)
                context.flush()
            }

            do {
                for try await chunk in stream {
                    eventLoop.execute {
                        var buffer = context.channel.allocator.buffer(capacity: chunk.count)
                        buffer.writeBytes(chunk)
                        context.writeAndFlush(self.wrapOutboundOut(.body(.byteBuffer(buffer))), promise: nil)
                    }
                }
            } catch {
                context.close(promise: nil)
                return
            }

            eventLoop.execute {
                context.writeAndFlush(self.wrapOutboundOut(.end(nil)), promise: nil)
            }

        default:
            let bodyData = response.bodyData
            eventLoop.execute {
                var head = HTTPResponseHead(version: version, status: HTTPResponseStatus(statusCode: statusCode))
                for (name, value) in headers {
                    head.headers.add(name: name, value: value)
                }
                context.write(self.wrapOutboundOut(.head(head)), promise: nil)

                if let bodyData {
                    var buffer = context.channel.allocator.buffer(capacity: bodyData.count)
                    buffer.writeBytes(bodyData)
                    context.write(self.wrapOutboundOut(.body(.byteBuffer(buffer))), promise: nil)
                }

                context.writeAndFlush(self.wrapOutboundOut(.end(nil)), promise: nil)
            }
        }
    }
}
