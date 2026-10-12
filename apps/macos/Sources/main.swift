import AppKit
import CoreImage
import CoreMedia
import CoreVideo
import VideoToolbox
import WebKit

private func macDiagnostic(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

private let nativePrompt = "AgentBrowser.Native"
private let maximumCommandBytes = 64 * 1024
private let maximumFrameBytes = 4 * 1024 * 1024


private struct FrameHeader: Decodable {
    let type: String
    let ticket: UInt64
    let generation: UInt64
    let session_id: String
    let sequence: UInt64
    let document_revision: UInt64
    let viewport_revision: UInt64
    let coded_width: Int
    let coded_height: Int
    let visible_width: Int
    let visible_height: Int
    let pts_us: UInt64
    let byte_length: Int
}

private struct ResponseEnvelope: Decodable {
    let type: String
    let id: UInt64
    let value: String
}

private struct Snapshot: Decodable {
    let state: String
    let generation: UInt64
    let renderedFrames: UInt64
    let released: Bool
    let codec: String
    let error: String?
    let connectionState: String?
    let controlMode: String?
    let sessionId: String?
    let documentRevision: UInt64?
    let viewportRevision: UInt64?
    let displayedTicket: UInt64?
    let epoch: UInt64?
    let inputReady: Bool
    let editorContext: EditorContext?
    let inputLease: InputLease?
    let editingOutcome: EditingEnvelope?
    let activeTabId: String?
    let viewportPending: Bool?
    let displayedGeneration: UInt64?
    let displayedSessionId: String?
    let displayedTabId: String?
    let displayedDocumentRevision: UInt64?
    let displayedViewportRevision: UInt64?
    let provisioningAvailable: Bool?
    let transport: String?
    let path: String?
}


private enum PresentationState: String {
    case browsing
    case overlay
}

private struct PresentationRequest {
    let id: String
    let state: PresentationState
}

private struct AppliedLayout: Equatable {
    let drawableWindowRect: NSRect
    let safeContentRect: NSRect
    let advertisedViewportRect: NSRect
    let presentedContentRect: NSRect
    let chromeRect: NSRect
    let webRect: NSRect
    let presentation: PresentationState
}

private struct SelectionRange: Decodable {
    init(start: UInt64, end: UInt64, direction: String) {
        self.start = start
        self.end = end
        self.direction = direction
    }
    let start: UInt64
    let end: UInt64
    let direction: String
}

private struct CompositionState: Decodable {
    init(data: String, start: UInt64, end: UInt64) {
        self.data = data
        self.start = start
        self.end = end
    }
    let data: String
    let start: UInt64
    let end: UInt64
}

private struct CaretRect: Decodable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

private struct SurroundingText: Decodable {
    enum CodingKeys: String, CodingKey {
        case text
        case offset
        case beforeLength = "before_length"
        case afterLength = "after_length"
    }
    let text: String
    let offset: UInt64
    let beforeLength: UInt64
    let afterLength: UInt64
}

private struct EditorContext: Decodable {
    enum CodingKeys: String, CodingKey {
        case targetId = "target_id"
        case kind
        case editable
        case readOnly = "read_only"
        case inputType = "input_type"
        case inputMode = "input_mode"
        case enterKeyHint = "enter_key_hint"
        case multiline
        case password
        case focusRevision = "focus_revision"
        case editorRevision = "editor_revision"
        case selection
        case composition
        case caret
        case surrounding
    }
    let targetId: String
    let kind: String
    let editable: Bool
    let readOnly: Bool
    let inputType: String?
    let inputMode: String?
    let enterKeyHint: String?
    let multiline: Bool
    let password: Bool
    let focusRevision: UInt64
    let editorRevision: UInt64
    let selection: SelectionRange?
    let composition: CompositionState?
    let caret: CaretRect?
    let surrounding: SurroundingText?
}

private struct InputLease: Decodable {
    enum CodingKeys: String, CodingKey {
        case kind
        case operationId = "operation_id"
        case attachmentId = "attachment_id"
        case gestureSessionId = "gesture_session_id"
        case editSessionId = "edit_session_id"
        case targetId = "target_id"
    }
    let kind: String
    let operationId: String
    let attachmentId: UInt64
    let gestureSessionId: String?
    let editSessionId: String?
    let targetId: String?
}

private struct EditingReceipt: Decodable {
    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case editSessionId = "edit_session_id"
        case targetId = "target_id"
        case focusRevision = "focus_revision"
        case editorRevision = "editor_revision"
        case eventSequence = "event_sequence"
        case state
        case selection
        case composition
        case message
    }
    let operationId: String
    let editSessionId: String
    let targetId: String
    let focusRevision: UInt64
    let editorRevision: UInt64
    let eventSequence: UInt64
    let state: String
    let selection: SelectionRange?
    let composition: CompositionState?
    let message: String?
}

private struct EditingEnvelope: Decodable {
    let type: String
    let receipt: EditingReceipt?

    enum CodingKeys: String, CodingKey {
        case type
        case receipt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = try container.decode(String.self, forKey: .type)
        // Gesture receipts share the envelope but not the editor receipt shape;
        // only an editor receipt is projected into the native editing client.
        receipt = try? container.decode(EditingReceipt.self, forKey: .receipt)
    }
}

private enum BridgeError: Error {
    case closed
    case malformedOutput
    case outputTooLarge
    case timeout
}

private struct DecodeError: Error, CustomStringConvertible {
    let message: String
    init(_ message: String) {
        self.message = message
    }
    var description: String { message }
}

private func readExactly(_ handle: FileHandle, count: Int) throws -> Data {
    var result = Data()
    result.reserveCapacity(count)
    while result.count < count {
        guard let chunk = try handle.read(upToCount: count - result.count), !chunk.isEmpty else {
            throw BridgeError.closed
        }
        result.append(chunk)
    }
    return result
}

private func bigEndianUInt32(_ data: Data) throws -> UInt32 {
    guard data.count == 4 else { throw BridgeError.malformedOutput }
    let bytes = [UInt8](data)
    return UInt32(bytes[0]) << 24 | UInt32(bytes[1]) << 16 | UInt32(bytes[2]) << 8 | UInt32(bytes[3])
}

private func rejection(_ message: String) -> String {
    let value: [String: String] = ["rejection": message]
    guard let data = try? JSONSerialization.data(withJSONObject: value), let raw = String(data: data, encoding: .utf8) else {
        return "{\"rejection\":\"NATIVE_BRIDGE_ERROR\"}"
    }
    return raw
}

private final class NativeBridge {
    private let process: Process
    private let input: FileHandle
    private let output: FileHandle
    private let condition = NSCondition()
    private var nextID: UInt64 = 0
    private var responses: [UInt64: String] = [:]
    private var alive = true
    private var readerStarted = false

    var onFrame: ((FrameHeader, Data) -> Void)?
    var onSnapshot: ((String) -> Void)?
    var responseTransform: ((String) -> String)?

    init(helperURL: URL, pairingDirectory: String?, adminSocketPath: String?) throws {
        process = Process()
        let inputPipe = Pipe()
        let outputPipe = Pipe()
        input = inputPipe.fileHandleForWriting
        output = outputPipe.fileHandleForReading
        process.executableURL = helperURL
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = FileHandle.standardError
        var environment = ProcessInfo.processInfo.environment
        if let pairingDirectory {
            environment["AGENTBROWSER_MAC_PAIRING"] = pairingDirectory
        }
        if let adminSocketPath {
            environment[hostServiceAdminSocketEnvironment] = adminSocketPath
        }
        process.environment = environment
        try process.run()
        startReader()
    }

    deinit {
        close()
    }

    func request(_ raw: String) -> String {
        let commandName = (try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any])?["op"] as? String ?? "unknown"
        macDiagnostic("MAC_NATIVE_SEND op=\(commandName) raw=\(raw)\\n")
        guard raw.utf8.count <= maximumCommandBytes else { return rejection("COMMAND_SIZE") }
        condition.lock()
        guard alive else {
            condition.unlock()
            return rejection("NATIVE_BRIDGE_CLOSED")
        }
        nextID = nextID == UInt64.max ? 1 : nextID + 1
        let id = nextID
        let envelope: [String: Any] = ["id": id, "command": raw]
        guard let data = try? JSONSerialization.data(withJSONObject: envelope) else {
            condition.unlock()
            return rejection("INVALID_COMMAND_JSON")
        }
        do {
            try input.write(contentsOf: data)
            try input.write(contentsOf: Data([0x0a]))
        } catch {
            alive = false
            condition.broadcast()
            condition.unlock()
            return rejection("NATIVE_BRIDGE_WRITE_FAILED")
        }
        let deadline = Date(timeIntervalSinceNow: 20)
        while responses[id] == nil && alive {
            if !condition.wait(until: deadline) { break }
        }
        let value = responses.removeValue(forKey: id)
        let stillAlive = alive
        condition.unlock()
        guard let value else {
            return rejection(stillAlive ? "NATIVE_BRIDGE_TIMEOUT" : "NATIVE_BRIDGE_CLOSED")
        }
        let projected = responseTransform?(value) ?? value
        let snapshotState = (try? JSONSerialization.jsonObject(with: Data(projected.utf8)) as? [String: Any])?["connectionState"] as? String ?? "unknown"
        macDiagnostic("MAC_NATIVE_RECV op=\(commandName) connectionState=\(snapshotState) response=\(projected)\\n")
        onSnapshot?(projected)
        return projected
    }

    func close() {
        condition.lock()
        alive = false
        condition.broadcast()
        condition.unlock()
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
    }

    private func startReader() {
        guard !readerStarted else { return }
        readerStarted = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.readLoop()
        }
    }

    private func readLoop() {
        do {
            while true {
                let kind = try readExactly(output, count: 1).first!
                let headerLength = Int(try bigEndianUInt32(readExactly(output, count: 4)))
                guard headerLength > 0 && headerLength <= maximumCommandBytes else { throw BridgeError.outputTooLarge }
                let headerData = try readExactly(output, count: headerLength)
                let byteLength = Int(try bigEndianUInt32(readExactly(output, count: 4)))
                guard byteLength >= 0 && byteLength <= maximumFrameBytes else { throw BridgeError.outputTooLarge }
                let bytes = try readExactly(output, count: byteLength)
                if kind == 0 {
                    let response = try JSONDecoder().decode(ResponseEnvelope.self, from: headerData)
                    guard response.type == "response" else { throw BridgeError.malformedOutput }
                    condition.lock()
                    responses[response.id] = response.value
                    condition.broadcast()
                    condition.unlock()
                } else if kind == 1 {
                    guard byteLength > 0 else { throw BridgeError.malformedOutput }
                    let frame = try JSONDecoder().decode(FrameHeader.self, from: headerData)
                    guard frame.type == "frame" && frame.byte_length == byteLength else { throw BridgeError.malformedOutput }
                    onFrame?(frame, bytes)
                } else {
                    throw BridgeError.malformedOutput
                }
            }
        } catch {
            condition.lock()
            alive = false
            condition.broadcast()
            condition.unlock()
        }
    }
}

private final class FrameToken {
    let ticket: UInt64
    let generation: UInt64
    let visibleWidth: Int
    let visibleHeight: Int
    init(ticket: UInt64, generation: UInt64, visibleWidth: Int, visibleHeight: Int) {
        self.ticket = ticket
        self.generation = generation
        self.visibleWidth = visibleWidth
        self.visibleHeight = visibleHeight
    }
}

private let videoToolboxCallback: VTDecompressionOutputCallback = {
    refcon, sourceFrameRefCon, status, _, imageBuffer, presentationTimeStamp, _ in
    guard let refcon else { return }
    let view = Unmanaged<VideoSurfaceView>.fromOpaque(refcon).takeUnretainedValue()
    var token: FrameToken?
    if let sourceFrameRefCon {
        token = Unmanaged<FrameToken>.fromOpaque(sourceFrameRefCon).takeRetainedValue()
    }
    view.decoded(status: status, imageBuffer: imageBuffer, presentationTimeStamp: presentationTimeStamp, token: token)
}

private final class BrowserContentView: NSView {
    var onLayout: (() -> Void)?

    override var isFlipped: Bool { true }

    override func layout() {
        super.layout()
        onLayout?()
    }
}

private final class VideoSurfaceView: NSView, NSTextInputClient {
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    private var decompressionSession: VTDecompressionSession?
    private var formatDescription: CMVideoFormatDescription?
    private var sps = Data()
    private var pps = Data()
    private var image: CGImage?
    private var visibleWidth = 0
    private var visibleHeight = 0
    private var activeSessionID: String?
    private(set) var activeGeneration: UInt64?
    private struct PendingScroll {
        let epoch: UInt64
        let raw: String
    }
    private struct PendingPointer {
        let operationId: String
        let pointerId: UInt64
        let buttons: UInt64
        let startX: Double
        let startY: Double
        var lastX: Double
        var lastY: Double
        var sequence: UInt64
    }
    private var pendingScrolls: [PendingScroll] = []
    private var inputEpoch: UInt64?
    private var inputControlMode: String?
    private var editorContext: EditorContext?
    private var inputLease: InputLease?
    private var inputDocumentRevision: UInt64?
    private var inputViewportRevision: UInt64?
    private var inputSessionId: String?
    private let editSessionId = UUID().uuidString
    private var editTargetId: String?
    private var editValue = ""
    private var editValueOffset: UInt64 = 0
    private var editSelection = NSRange(location: 0, length: 0)
    private var editMarkedRange = NSRange(location: NSNotFound, length: 0)
    private var editComposing = false
    private var editMarkedData = ""
    private var editOperationId: String?
    private var editEventSequence: UInt64 = 0
    private var editSequenceCounter: UInt64 = 0
    private var pendingPointer: PendingPointer?
    private var editingActivated = false

    private(set) var displayedViewport: NSRect?
    var onDisplayed: ((UInt64) -> Void)?
    var onDecodeError: ((UInt64, String) -> Void)?
    var onInput: ((String) -> Void)?
    var onEdit: ((String) -> String?)?
    var currentEpoch: (() -> UInt64)?
    var inputReady: (() -> Bool)?
    var nativeVideoInputEnabled: (() -> Bool)?

    override var isFlipped: Bool { true }

    override var acceptsFirstResponder: Bool { true }

    override func keyDown(with event: NSEvent) {
        guard editingActivated else {
            super.keyDown(with: event)
            return
        }
        if inputContext?.handleEvent(event) == true { return }
        interpretKeyEvents([event])
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        setAccessibilityRole(.image)
        setAccessibilityLabel("H.264 原生视频显示区")
    }

    required init?(coder: NSCoder) {
        fatalError("VideoSurfaceView does not support NSCoder")
    }

    deinit {
        invalidateDecoder()
    }

    func apply(snapshot: Snapshot) {
        let epochChanged = inputEpoch != nil && inputEpoch != snapshot.epoch
        inputEpoch = snapshot.epoch
        inputControlMode = snapshot.controlMode
        let contextChanged = inputSessionId != nil && inputSessionId != snapshot.sessionId
        if epochChanged || contextChanged || snapshot.controlMode != "control" {
            pendingScrolls.removeAll()
            releaseHeldInput()
        }
        if snapshot.connectionState == "stopped" || snapshot.connectionState == "error" || snapshot.released {
            releaseHeldInput()
            endEditing(cancelRemote: false)
            reset()
        }
        if snapshot.connectionState == "connected" || snapshot.connectionState == "connecting" || snapshot.state == "playing" || snapshot.state == "starting" {
            activeGeneration = snapshot.generation
            if let sessionId = snapshot.sessionId {
                activeSessionID = sessionId
            }
        }
        applyEditorContext(snapshot.editorContext)
        if let editing = snapshot.editingOutcome {
            consume(editing: editing)
        }
        flushPendingScrolls(snapshot: snapshot)
        needsDisplay = true
    }

    func reset() {
        pendingScrolls.removeAll()
        releaseHeldInput()
        inputEpoch = nil
        inputControlMode = nil
        invalidateDecoder(clearParameterSets: false)
        image = nil
        displayedViewport = nil
        visibleWidth = 0
        visibleHeight = 0
        activeSessionID = nil
        activeGeneration = nil
        needsDisplay = true
    }

    private func releaseHeldInput() {
        guard let pendingPointer else { return }
        self.pendingPointer = nil
        guard let epoch = inputEpoch else { return }
        let cancel = editingEvent(
            binding: json([("type", "gesture"), ("gesture_session_id", pendingPointer.operationId)]),
            event: json([("type", "pointer_cancel"), ("pointer_id", String(pendingPointer.pointerId))]),
            operationId: pendingPointer.operationId,
            eventSequence: pendingPointer.sequence + 1,
            epoch: epoch
        )
        _ = onEdit?(cancel)
    }

    func accept(header: FrameHeader, bytes: Data) {
        guard header.generation == activeGeneration else { return }
        guard bytes.count == header.byte_length && bytes.count > 0 && bytes.count <= maximumFrameBytes else {
            onDecodeError?(header.ticket, "FRAME_SIZE")
            return
        }
        guard header.coded_width >= 2 && header.coded_height >= 2 && header.coded_width % 2 == 0 && header.coded_height % 2 == 0,
              header.visible_width > 0 && header.visible_height > 0,
              header.visible_width <= header.coded_width && header.visible_height <= header.coded_height,
              header.coded_width - header.visible_width <= 1 && header.coded_height - header.visible_height <= 1 else {
            onDecodeError?(header.ticket, "FRAME_DIMENSIONS")
            return
        }
        if let activeSessionID, activeSessionID != header.session_id {
            onDecodeError?(header.ticket, "FRAME_SESSION_MISMATCH")
            return
        }
        activeSessionID = header.session_id
        do {
            try decode(header: header, bytes: bytes)
        } catch {
            onDecodeError?(header.ticket, "H264_DECODE_\(error)")
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.black.setFill()
        dirtyRect.fill()
        guard let image else {
            let message = activeGeneration == nil ? "等待连接 Host" : "等待 H.264 画面确认"
            let attributes: [NSAttributedString.Key: Any] = [
                .foregroundColor: NSColor.lightGray,
                .font: NSFont.systemFont(ofSize: 16),
            ]
            let size = message.size(withAttributes: attributes)
            let point = NSPoint(x: max(12, (bounds.width - size.width) / 2), y: max(12, (bounds.height - size.height) / 2))
            message.draw(at: point, withAttributes: attributes)
            return
        }
        let sourceWidth = CGFloat(image.width)
        let sourceHeight = CGFloat(image.height)
        guard sourceWidth > 0 && sourceHeight > 0 else { return }
        let scale = min(bounds.width / sourceWidth, bounds.height / sourceHeight)
        let width = sourceWidth * scale
        let height = sourceHeight * scale
        let destination = NSRect(x: (bounds.width - width) / 2, y: (bounds.height - height) / 2, width: width, height: height)
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.saveGState()
        context.translateBy(x: destination.minX, y: destination.maxY)
        context.scaleBy(x: 1, y: -1)
        context.draw(image, in: CGRect(origin: .zero, size: destination.size))
        context.restoreGState()
    }

    override func mouseDown(with event: NSEvent) {
        guard nativeVideoInputEnabled?() == true, inputReady?() == true, let epoch = currentEpoch?() else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard let coordinates = pageCoordinates(point) else { return }
        let operationId = nextOperationId(prefix: "pointer")
        let pointerId: UInt64 = 1
        pendingPointer = PendingPointer(
            operationId: operationId,
            pointerId: pointerId,
            buttons: 1,
            startX: coordinates.x,
            startY: coordinates.y,
            lastX: coordinates.x,
            lastY: coordinates.y,
            sequence: 0
        )
        let raw = editingEvent(
            binding: json([("type", "gesture"), ("gesture_session_id", operationId)]),
            event: pointerEvent(type: "pointer_down", pointerId: pointerId, x: coordinates.x, y: coordinates.y, button: "left", buttons: 1),
            operationId: operationId,
            eventSequence: 0,
            epoch: epoch
        )
        if refused(onEdit?(raw)) {
            pendingPointer = nil
        }
    }

    override func mouseDragged(with event: NSEvent) {
        guard nativeVideoInputEnabled?() == true, inputReady?() == true, var pending = pendingPointer, let epoch = currentEpoch?() else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard let coordinates = pageCoordinates(point) else { return }
        guard coordinates.x != pending.lastX || coordinates.y != pending.lastY else {
            pendingPointer = pending
            return
        }
        pending.lastX = coordinates.x
        pending.lastY = coordinates.y
        pending.sequence += 1
        pendingPointer = pending
        let raw = editingEvent(
            binding: json([("type", "gesture"), ("gesture_session_id", pending.operationId)]),
            event: json([
                ("type", "pointer_move"),
                ("pointer_id", String(pending.pointerId)),
                ("x", String(format: "%.3f", coordinates.x)),
                ("y", String(format: "%.3f", coordinates.y)),
                ("buttons", "1"),
                ("modifiers", "0"),
            ]),
            operationId: pending.operationId,
            eventSequence: pending.sequence,
            epoch: epoch
        )
        if refused(onEdit?(raw)) {
            pendingPointer = nil
        }
    }

    override func mouseUp(with event: NSEvent) {
        guard nativeVideoInputEnabled?() == true, inputReady?() == true, let epoch = currentEpoch?() else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard let coordinates = pageCoordinates(point) else {
            releaseHeldInput()
            return
        }
        if let pending = pendingPointer {
            pendingPointer = nil
            let sequence = pending.sequence + 1
            let raw = editingEvent(
                binding: json([("type", "gesture"), ("gesture_session_id", pending.operationId)]),
                event: pointerEvent(type: "pointer_up", pointerId: pending.pointerId, x: coordinates.x, y: coordinates.y, button: "left", buttons: 0),
                operationId: pending.operationId,
                eventSequence: sequence,
                epoch: epoch
            )
            _ = onEdit?(raw)
        } else {
            onInput?("{\"op\":\"click\",\"epoch\":\(epoch),\"x\":\(coordinates.x),\"y\":\(coordinates.y)}")
        }
    }

    override func scrollWheel(with event: NSEvent) {
        guard nativeVideoInputEnabled?() == true else { return }
        let ready = inputReady?() == true
        guard let epoch = currentEpoch?() else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard let coordinates = pageCoordinates(point), event.scrollingDeltaX.isFinite, event.scrollingDeltaY.isFinite else {
            return
        }
        // AppKit reports the physical wheel direction; browser wheel input
        // uses positive deltas to advance the page toward right/down.
        let dx = String(format: "%.4f", -event.scrollingDeltaX)
        let dy = String(format: "%.4f", -event.scrollingDeltaY)
        let raw = "{\"op\":\"scroll\",\"epoch\":\(epoch),\"x\":\(coordinates.x),\"y\":\(coordinates.y),\"dx\":\(dx),\"dy\":\(dy)}"
        if ready {
            onInput?(raw)
        } else if inputControlMode == "control" {
            pendingScrolls.append(PendingScroll(epoch: epoch, raw: raw))
        }
    }

    private func flushPendingScrolls(snapshot: Snapshot) {
        guard snapshot.inputReady, snapshot.controlMode == "control",
              let epoch = snapshot.epoch, let pending = pendingScrolls.first,
              pending.epoch == epoch, let onInput else { return }
        pendingScrolls.removeFirst()
        onInput(pending.raw)
    }

    private func pageCoordinates(_ point: NSPoint) -> (x: Double, y: Double)? {
        guard let image, image.width > 0, image.height > 0, bounds.contains(point) else { return nil }
        let scale = min(bounds.width / CGFloat(image.width), bounds.height / CGFloat(image.height))
        guard scale > 0 else { return nil }
        let width = CGFloat(image.width) * scale
        let height = CGFloat(image.height) * scale
        let origin = NSPoint(x: (bounds.width - width) / 2, y: (bounds.height - height) / 2)
        guard NSRect(origin: origin, size: CGSize(width: width, height: height)).contains(point) else { return nil }
        let x = (point.x - origin.x) / scale
        let y = (point.y - origin.y) / scale
        return (Double(x), Double(y))
    }

    private func viewPoint(pageX: Double, pageY: Double) -> NSPoint? {
        guard let image, image.width > 0, image.height > 0 else { return nil }
        let scale = min(bounds.width / CGFloat(image.width), bounds.height / CGFloat(image.height))
        guard scale > 0 else { return nil }
        let width = CGFloat(image.width) * scale
        let height = CGFloat(image.height) * scale
        let origin = NSPoint(x: (bounds.width - width) / 2, y: (bounds.height - height) / 2)
        return NSPoint(x: origin.x + CGFloat(pageX) * scale, y: origin.y + CGFloat(pageY) * scale)
    }

    private func nextOperationId(prefix: String) -> String {
        editSequenceCounter += 1
        return "\(prefix)-\(editSessionId)-\(editSequenceCounter)"
    }

    private func pointerEvent(type: String, pointerId: UInt64, x: Double, y: Double, button: String, buttons: UInt64) -> String {
        var fields: [(String, String)] = [
            ("type", type),
            ("pointer_id", String(pointerId)),
            ("x", String(format: "%.3f", x)),
            ("y", String(format: "%.3f", y)),
        ]
        if type != "pointer_cancel" {
            fields.append(("button", button))
            fields.append(("buttons", String(buttons)))
            fields.append(("modifiers", "0"))
        }
        return json(fields)
    }

    private func editingEvent(binding: String, event: String, operationId: String, eventSequence: UInt64, epoch: UInt64) -> String {
        json([
            ("op", "editing"),
            ("epoch", String(epoch)),
            ("operation_id", operationId),
            ("event_sequence", String(eventSequence)),
            ("binding", binding),
            ("event", event),
        ])
    }

    private var editingActive: Bool {
        guard let context = editorContext else { return false }
        return context.editable && !context.readOnly && inputControlMode == "control" && inputReady?() == true
    }

    private func applyEditorContext(_ context: EditorContext?) {
        guard let context else {
            if editorContext != nil || editTargetId != nil {
                endEditing(cancelRemote: false)
                editorContext = nil
                editTargetId = nil
                editValue = ""
                editValueOffset = 0
                editSelection = NSRange(location: 0, length: 0)
            }
            return
        }
        if editTargetId != context.targetId {
            endEditing(cancelRemote: false)
            editTargetId = context.targetId
            // `surrounding` is a relative UTF-16 window around the caret, and
            // password targets never carry plaintext. Only that window is kept
            // locally; remote offsets stay authoritative.
            editValue = context.password ? "" : (context.surrounding?.text ?? "")
            editValueOffset = context.password ? 0 : (context.surrounding?.offset ?? 0)
            editComposing = false
            editMarkedRange = NSRange(location: NSNotFound, length: 0)
            editOperationId = nil
            editEventSequence = 0
        }
        editorContext = context
        inputSessionId = sessionIdIfKnown
        if let selection = context.selection {
            editSelection = localRange(start: selection.start, end: selection.end)
        }
        if !editComposing, let surrounding = context.surrounding {
            editValue = surrounding.text
            editValueOffset = surrounding.offset
        }
        if let composition = context.composition {
            editComposing = true
            editMarkedRange = localRange(start: composition.start, end: composition.end)
        } else if !editComposing {
            editMarkedRange = NSRange(location: NSNotFound, length: 0)
        }
        if editingActive {
            activateEditing()
        } else {
            deactivateEditing()
        }
    }

    private func consume(editing: EditingEnvelope) {
        guard editing.type == "editor", let receipt = editing.receipt else { return }
        switch receipt.state {
        case "applied", "pending", "cancelled":
            if let selection = receipt.selection {
                editSelection = localRange(start: selection.start, end: selection.end)
            }
            if let composition = receipt.composition {
                editComposing = true
                editMarkedRange = localRange(start: composition.start, end: composition.end)
            } else {
                editComposing = false
                editMarkedRange = NSRange(location: NSNotFound, length: 0)
            }
            if receipt.state != "pending" {
                editOperationId = nil
                editEventSequence = 0
            }
        default:
            editComposing = false
            editMarkedRange = NSRange(location: NSNotFound, length: 0)
            editOperationId = nil
            editEventSequence = 0
        }
    }

    private func localRange(start: UInt64, end: UInt64) -> NSRange {
        let units = editValue.utf16.count
        let windowEnd = editValueOffset + UInt64(units)
        let clampedStart = max(start, editValueOffset)
        let clampedEnd = min(max(end, clampedStart), windowEnd)
        let location = min(Int(clampedStart - editValueOffset), units)
        let length = min(Int(clampedEnd - clampedStart), units - location)
        return NSRange(location: location, length: max(0, length))
    }

    /// Terminates the local IME session.
    ///
    /// `cancelRemote` is only true for an explicit local/system cancel while
    /// the same editor context is still authoritative. Context, epoch,
    /// generation, focus or connection invalidation must not emit an editing
    /// event for the stale binding; the remote owner already ended or will
    /// fence that composition, and replaying it would risk writing old state.
    private func endEditing(cancelRemote: Bool) {
        if cancelRemote, editComposing, editingActive {
            _ = submitEditComposition(cancel: true)
        }
        editComposing = false
        editMarkedData = ""
        editMarkedRange = NSRange(location: NSNotFound, length: 0)
        editOperationId = nil
        editEventSequence = 0
        deactivateEditing()
    }

    private var sessionIdIfKnown: String? { activeSessionID }

    private func activateEditing() {
        guard editingActive, !editingActivated else { return }
        editingActivated = true
        if window?.firstResponder !== self {
            window?.makeFirstResponder(self)
        }
        inputContext?.activate()
    }

    private func deactivateEditing() {
        guard editingActivated else { return }
        editingActivated = false
        if window?.firstResponder === self {
            window?.makeFirstResponder(nil)
        }
    }

    private func submitEditComposition(cancel: Bool, data: String? = nil) -> String? {
        guard let context = editorContext else { return nil }
        let operationId: String
        let sequence: UInt64
        if let existing = editOperationId {
            operationId = existing
            editEventSequence += 1
            sequence = editEventSequence
        } else {
            operationId = nextOperationId(prefix: "composition")
            editEventSequence = 0
            sequence = 0
            editOperationId = operationId
        }
        let event = cancel ? json([("type", "composition_cancel")])
            : json([("type", "composition_commit"), ("data", data ?? markedData())])
        let raw = editingEvent(
            binding: editorBinding(context: context),
            event: event,
            operationId: operationId,
            eventSequence: sequence,
            epoch: inputEpoch ?? 0
        )
        return onEdit?(raw)
    }

    /// A transport/Host refusal is not an accepted edit. Swift keeps the
    /// optimistic marked-text window only until the real `EditingReceipt`
    /// arrives; an explicit `{"rejection": ...}` means the binding was fenced
    /// or invalid, so local composition state must be dropped rather than
    /// displayed as committed.
    private func refused(_ response: String?) -> Bool {
        guard let response, let data = response.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return object["rejection"] != nil
    }

    private func markedData() -> String {
        return editMarkedData
    }

    private func editorBinding(context: EditorContext) -> String {
        json([
            ("type", "editor"),
            ("edit_session_id", editSessionId),
            ("target_id", context.targetId),
            ("focus_revision", String(context.focusRevision)),
            ("editor_revision", String(context.editorRevision)),
        ])
    }

    private func submitStandaloneEdit(event: String) -> String? {
        guard let context = editorContext else { return nil }
        let operationId = nextOperationId(prefix: "edit")
        editOperationId = nil
        editEventSequence = 0
        let raw = editingEvent(
            binding: editorBinding(context: context),
            event: event,
            operationId: operationId,
            eventSequence: 0,
            epoch: inputEpoch ?? 0
        )
        let response = onEdit?(raw)
        if refused(response) {
            endEditing(cancelRemote: false)
        }
        return response
    }

    private func decode(header: FrameHeader, bytes: Data) throws {
        let nalUnits = try annexBNALUnits(bytes)
        guard !nalUnits.isEmpty else { throw DecodeError("ANNEX_B_EMPTY") }
        var idr = false
        for nal in nalUnits {
            guard let first = nal.first, first & 0x80 == 0 else { throw DecodeError("NAL_HEADER") }
            switch first & 0x1f {
            case 7: sps = nal
            case 8: pps = nal
            case 5: idr = true
            case 6, 9: break
            default: throw DecodeError("NAL_TYPE")
            }
        }
        guard !sps.isEmpty && !pps.isEmpty && idr else { throw DecodeError("SELF_CONTAINED_IDR_REQUIRED") }
        try ensureDecoder(codedWidth: header.coded_width, codedHeight: header.coded_height)
        let sampleData = try lengthPrefixed(nalUnits)
        try decodeSample(sampleData, header: header)
    }

    private func ensureDecoder(codedWidth: Int, codedHeight: Int) throws {
        if let formatDescription, CMVideoFormatDescriptionGetDimensions(formatDescription).width == codedWidth,
           CMVideoFormatDescriptionGetDimensions(formatDescription).height == codedHeight {
            return
        }
        invalidateDecoder(clearParameterSets: false)
        var description: CMVideoFormatDescription?
        let status = sps.withUnsafeBytes { spsBytes in
            pps.withUnsafeBytes { ppsBytes in
                guard let spsBase = spsBytes.bindMemory(to: UInt8.self).baseAddress,
                      let ppsBase = ppsBytes.bindMemory(to: UInt8.self).baseAddress else { return OSStatus(-1) }
                var pointers: [UnsafePointer<UInt8>] = [spsBase, ppsBase]
                var sizes = [sps.count, pps.count]
                return CMVideoFormatDescriptionCreateFromH264ParameterSets(
                    allocator: kCFAllocatorDefault,
                    parameterSetCount: 2,
                    parameterSetPointers: &pointers,
                    parameterSetSizes: &sizes,
                    nalUnitHeaderLength: 4,
                    formatDescriptionOut: &description
                )
            }
        }
        guard status == noErr, let description else { throw DecodeError("FORMAT_\(status)") }
        var callback = VTDecompressionOutputCallbackRecord(
            decompressionOutputCallback: videoToolboxCallback,
            decompressionOutputRefCon: Unmanaged.passUnretained(self).toOpaque()
        )
        var session: VTDecompressionSession?
        let sessionStatus = VTDecompressionSessionCreate(
            allocator: kCFAllocatorDefault,
            formatDescription: description,
            decoderSpecification: nil,
            imageBufferAttributes: nil,
            outputCallback: &callback,
            decompressionSessionOut: &session
        )
        guard sessionStatus == noErr, let session else { throw DecodeError("SESSION_\(sessionStatus)") }
        formatDescription = description
        decompressionSession = session
    }

    private func decodeSample(_ data: Data, header: FrameHeader) throws {
        guard let formatDescription, let decompressionSession else { throw DecodeError("DECODER_NOT_READY") }
        var blockBuffer: CMBlockBuffer?
        let blockStatus = CMBlockBufferCreateWithMemoryBlock(
            allocator: kCFAllocatorDefault,
            memoryBlock: nil,
            blockLength: data.count,
            blockAllocator: kCFAllocatorDefault,
            customBlockSource: nil,
            offsetToData: 0,
            dataLength: data.count,
            flags: 0,
            blockBufferOut: &blockBuffer
        )
        guard blockStatus == kCMBlockBufferNoErr, let blockBuffer else { throw DecodeError("BLOCK_\(blockStatus)") }
        let copyStatus = data.withUnsafeBytes { bytes in
            guard let baseAddress = bytes.baseAddress else { return OSStatus(-1) }
            return CMBlockBufferReplaceDataBytes(
                with: baseAddress,
                blockBuffer: blockBuffer,
                offsetIntoDestination: 0,
                dataLength: data.count
            )
        }
        guard copyStatus == kCMBlockBufferNoErr else { throw DecodeError("BLOCK_COPY_\(copyStatus)") }
        var timing = CMSampleTimingInfo(
            duration: CMTime.invalid,
            presentationTimeStamp: CMTime(value: CMTimeValue(header.pts_us), timescale: 1_000_000),
            decodeTimeStamp: CMTime.invalid
        )
        var sampleBuffer: CMSampleBuffer?
        let sampleStatus = CMSampleBufferCreateReady(
            allocator: kCFAllocatorDefault,
            dataBuffer: blockBuffer,
            formatDescription: formatDescription,
            sampleCount: 1,
            sampleTimingEntryCount: 1,
            sampleTimingArray: &timing,
            sampleSizeEntryCount: 1,
            sampleSizeArray: [data.count],
            sampleBufferOut: &sampleBuffer
        )
        guard sampleStatus == noErr, let sampleBuffer else { throw DecodeError("SAMPLE_\(sampleStatus)") }
        let token = Unmanaged.passRetained(FrameToken(
            ticket: header.ticket,
            generation: header.generation,
            visibleWidth: header.visible_width,
            visibleHeight: header.visible_height
        )).toOpaque()
        var flags = VTDecodeInfoFlags(rawValue: 0)
        let decodeStatus = VTDecompressionSessionDecodeFrame(
            decompressionSession,
            sampleBuffer: sampleBuffer,
            flags: [],
            frameRefcon: token,
            infoFlagsOut: &flags
        )
        if decodeStatus != noErr {
            Unmanaged<FrameToken>.fromOpaque(token).release()
            throw DecodeError("FRAME_\(decodeStatus)")
        }
    }

    func decoded(status: OSStatus, imageBuffer: CVImageBuffer?, presentationTimeStamp: CMTime, token: FrameToken?) {
        guard let token else { return }
        guard status == noErr, let imageBuffer else {
            DispatchQueue.main.async { [weak self] in
                self?.onDecodeError?(token.ticket, "OUTPUT_\(status)")
            }
            return
        }
        let source = CIImage(cvImageBuffer: imageBuffer)
        let crop = CGRect(x: 0, y: 0, width: token.visibleWidth, height: token.visibleHeight)
        guard source.extent.contains(crop), let cgImage = ciContext.createCGImage(source, from: crop) else {
            DispatchQueue.main.async { [weak self] in
                self?.onDecodeError?(token.ticket, "PIXEL_BUFFER_CONVERSION")
            }
            return
        }
        DispatchQueue.main.async { [weak self] in
            guard let self, self.activeGeneration == token.generation else { return }
            self.image = cgImage
            self.displayedViewport = nil
            self.visibleWidth = token.visibleWidth
            self.visibleHeight = token.visibleHeight
            self.needsDisplay = true
            self.onDisplayed?(token.ticket)
        }
        _ = presentationTimeStamp
    }

    func hasFrame(matching viewport: NSRect) -> Bool {
        return image != nil && displayedViewport == viewport
    }

    func recordDisplayed(viewport: NSRect) {
        guard image != nil else { return }
        displayedViewport = viewport
    }

    func setPresentationInteractive(_ active: Bool) {
        if active {
            if editingActive {
                activateEditing()
            }
        } else {
            deactivateEditing()
        }
    }

    private func invalidateDecoder(clearParameterSets: Bool = true) {
        if let decompressionSession {
            VTDecompressionSessionWaitForAsynchronousFrames(decompressionSession)
            VTDecompressionSessionInvalidate(decompressionSession)
        }
        decompressionSession = nil
        formatDescription = nil
        if clearParameterSets {
            sps = Data()
            pps = Data()
        }
    }

    private func annexBNALUnits(_ data: Data) throws -> [Data] {
        let bytes = [UInt8](data)
        var starts: [(offset: Int, length: Int)] = []
        var index = 0
        while index + 3 < bytes.count {
            if bytes[index] == 0 && bytes[index + 1] == 0 && bytes[index + 2] == 1 {
                starts.append((index, 3))
                index += 3
            } else if index + 4 <= bytes.count && bytes[index] == 0 && bytes[index + 1] == 0 && bytes[index + 2] == 0 && bytes[index + 3] == 1 {
                starts.append((index, 4))
                index += 4
            } else {
                index += 1
            }
        }
        guard let first = starts.first, first.offset == 0 else { throw DecodeError("ANNEX_B_START_CODE") }
        var units: [Data] = []
        for position in starts.indices {
            let current = starts[position]
            let start = current.offset + current.length
            let end = position + 1 < starts.count ? starts[position + 1].offset : bytes.count
            guard end > start else { throw DecodeError("ANNEX_B_EMPTY_NAL") }
            units.append(Data(bytes[start..<end]))
        }
        return units
    }

    private func lengthPrefixed(_ units: [Data]) throws -> Data {
        var result = Data()
        for unit in units {
            guard unit.count <= Int(UInt32.max) else { throw DecodeError("NAL_TOO_LARGE") }
            var length = UInt32(unit.count).bigEndian
            withUnsafeBytes(of: &length) { result.append(contentsOf: $0) }
            result.append(unit)
        }
        return result
    }
}

private extension VideoSurfaceView {
    func insertText(_ string: Any, replacementRange: NSRange) {
        guard editingActive else { return }
        let text: String
        if let value = string as? String {
            text = value
        } else if let attributed = string as? NSAttributedString {
            text = attributed.string
        } else {
            return
        }
        if editComposing {
            // IME confirmation delivers the final text through insertText; the
            // Host commits the open composition with this data and ends it.
            _ = submitEditComposition(cancel: false, data: text)
            editComposing = false
            editMarkedData = ""
            editMarkedRange = NSRange(location: NSNotFound, length: 0)
            return
        }
        guard !text.isEmpty else { return }
        let event = json([
            ("type", "replace_text"),
            ("text", text),
            ("input_type", editorContext?.inputType ?? "insertText"),
        ])
        _ = submitStandaloneEdit(event: event)
    }

    func doCommandBySelector(_ command: Selector) {
        guard editingActive else { return }
        if command == #selector(insertNewline(_:)) {
            // Enter is a real key event; the remote editor decides whether it
            // inserts a line break (textarea/contenteditable) or does nothing
            // (single-line input) instead of the client forcing a newline.
            _ = submitStandaloneEdit(event: json([
                ("type", "key_down"),
                ("key", "Enter"),
                ("code", "Enter"),
                ("modifiers", "0"),
            ]))
        } else if command == #selector(deleteBackward(_:)) {
            _ = submitStandaloneEdit(event: json([("type", "editor_action"), ("action", "delete_backward")]))
        } else if command == #selector(deleteForward(_:)) {
            _ = submitStandaloneEdit(event: json([("type", "editor_action"), ("action", "delete_forward")]))
        } else if command == #selector(moveLeft(_:)) {
            moveCaret(by: -1, extend: false)
        } else if command == #selector(moveRight(_:)) {
            moveCaret(by: 1, extend: false)
        } else if command == #selector(moveLeftAndModifySelection(_:)) {
            moveCaret(by: -1, extend: true)
        } else if command == #selector(moveRightAndModifySelection(_:)) {
            moveCaret(by: 1, extend: true)
        } else if command == #selector(moveToBeginningOfLine(_:)) || command == #selector(moveToBeginningOfDocument(_:)) {
            moveCaret(to: 0, extend: false)
        } else if command == #selector(moveToEndOfLine(_:)) || command == #selector(moveToEndOfDocument(_:)) {
            moveCaret(to: editValue.utf16.count, extend: false)
        } else if command == #selector(deleteWordBackward(_:)) {
            deleteWord(backward: true)
        } else if command == #selector(deleteWordForward(_:)) {
            deleteWord(backward: false)
        } else if command == #selector(selectAll(_:)) {
            emitSelection(start: 0, end: editValue.utf16.count, direction: "forward")
        } else if command == #selector(cancelOperation(_:)) {
            // Escape during composition must cancel the remote composition,
            // not commit the current candidate.
            if editComposing {
                _ = submitEditComposition(cancel: true)
                editComposing = false
                editMarkedData = ""
                editMarkedRange = NSRange(location: NSNotFound, length: 0)
            }
        } else if command == Selector("paste:") {
            guard let text = NSPasteboard.general.string(forType: .string), !text.isEmpty else { return }
            _ = submitStandaloneEdit(event: json([
                ("type", "replace_text"),
                ("text", text),
                ("input_type", "insertFromPaste"),
            ]))
        }
    }

    private func moveCaret(by delta: Int, extend: Bool) {
        let units = editValue.utf16.count
        let lower = max(0, min(editSelection.location, units))
        let upper = max(lower, min(editSelection.location + editSelection.length, units))
        if !extend {
            let caret = delta < 0 ? lower : upper
            let target = delta < 0 ? stepBack(from: caret) : stepForward(from: upper)
            emitSelection(start: target, end: target, direction: "none")
            return
        }
        let start = delta < 0 ? stepBack(from: lower) : lower
        let end = delta < 0 ? upper : stepForward(from: upper)
        emitSelection(start: start, end: end, direction: delta < 0 ? "backward" : "forward")
    }

    private func moveCaret(to local: Int, extend: Bool) {
        let units = editValue.utf16.count
        let target = max(0, min(local, units))
        if extend {
            let lower = max(0, min(editSelection.location, units))
            let upper = max(lower, min(editSelection.location + editSelection.length, units))
            let start = min(lower, target)
            let end = max(upper, target)
            emitSelection(start: start, end: end, direction: target < upper ? "backward" : "forward")
        } else {
            emitSelection(start: target, end: target, direction: "none")
        }
    }

    private func stepBack(from index: Int) -> Int {
        guard index > 0 else { return 0 }
        let units = Array(editValue.utf16)
        var target = index - 1
        if target > 0, units[target] >= 0xDC00, units[target] <= 0xDFFF,
           units[target - 1] >= 0xD800, units[target - 1] <= 0xDBFF {
            target -= 1
        }
        return target
    }

    private func stepForward(from index: Int) -> Int {
        let units = Array(editValue.utf16)
        guard index < units.count else { return units.count }
        var target = index + 1
        if units[index] >= 0xD800, units[index] <= 0xDBFF,
           target < units.count, units[target] >= 0xDC00, units[target] <= 0xDFFF {
            target += 1
        }
        return target
    }

    private func deleteWord(backward: Bool) {
        let units = Array(editValue.utf16)
        let lower = max(0, min(editSelection.location, units.count))
        let upper = max(lower, min(editSelection.location + editSelection.length, units.count))
        let isWord = { (unit: UInt16) -> Bool in
            switch unit {
            case 0x30...0x39, 0x41...0x5A, 0x61...0x7A, 0x5F: return true
            default: return unit > 0x7F
            }
        }
        if lower != upper {
            _ = submitStandaloneEdit(event: json([("type", "delete_range"), ("start", String(editValueOffset + UInt64(lower))), ("end", String(editValueOffset + UInt64(upper)))]))
            return
        }
        if backward {
            var target = lower
            while target > 0, !isWord(units[target - 1]) {
                target -= 1
            }
            while target > 0, isWord(units[target - 1]) {
                target -= 1
            }
            guard target < lower else { return }
            _ = submitStandaloneEdit(event: json([("type", "delete_range"), ("start", String(editValueOffset + UInt64(target))), ("end", String(editValueOffset + UInt64(lower)))]))
        } else {
            var target = upper
            while target < units.count, !isWord(units[target]) {
                target += 1
            }
            while target < units.count, isWord(units[target]) {
                target += 1
            }
            guard target > upper else { return }
            _ = submitStandaloneEdit(event: json([("type", "delete_range"), ("start", String(editValueOffset + UInt64(upper))), ("end", String(editValueOffset + UInt64(target)))]))
        }
    }

    private func emitSelection(start: Int, end: Int, direction: String) {
        guard let context = editorContext else { return }
        let operationId = nextOperationId(prefix: "select")
        let raw = json([
            ("op", "select"),
            ("epoch", String(inputEpoch ?? 0)),
            ("operation_id", operationId),
            ("event_sequence", "0"),
            ("edit_session_id", editSessionId),
            ("target_id", context.targetId),
            ("focus_revision", String(context.focusRevision)),
            ("editor_revision", String(context.editorRevision)),
            ("start", String(editValueOffset + UInt64(max(0, start)))),
            ("end", String(editValueOffset + UInt64(max(0, end)))),
            ("direction", direction),
        ])
        if refused(onEdit?(raw)) {
            endEditing(cancelRemote: false)
        }
    }

    func setMarkedText(_ string: Any, selectedRange: NSRange, replacementRange: NSRange) {
        guard editingActive, let context = editorContext else { return }
        let text: String
        if let value = string as? String {
            text = value
        } else if let attributed = string as? NSAttributedString {
            text = attributed.string
        } else {
            return
        }
        let wasComposing = editComposing
        let operationId: String
        let sequence: UInt64
        if wasComposing, let existing = editOperationId {
            operationId = existing
            editEventSequence += 1
            sequence = editEventSequence
        } else {
            operationId = nextOperationId(prefix: "composition")
            editEventSequence = 0
            sequence = 0
            editOperationId = operationId
        }
        editComposing = true
        editMarkedData = text
        if !text.isEmpty {
            editMarkedRange = NSRange(location: replacementRange.location == NSNotFound ? (editSelection.location + editSelection.length) : replacementRange.location, length: text.utf16.count)
        } else {
            editMarkedRange = NSRange(location: NSNotFound, length: 0)
        }
        let event = json([
            ("type", wasComposing ? "composition_update" : "composition_begin"),
            ("data", text),
        ])
        let raw = editingEvent(
            binding: editorBinding(context: context),
            event: event,
            operationId: operationId,
            eventSequence: sequence,
            epoch: inputEpoch ?? 0
        )
        if refused(onEdit?(raw)) {
            endEditing(cancelRemote: false)
        }
    }

    func unmarkText() {
        guard editingActive else { return }
        if editComposing {
            _ = submitEditComposition(cancel: false, data: editMarkedData)
        }
        editComposing = false
        editMarkedData = ""
        editMarkedRange = NSRange(location: NSNotFound, length: 0)
    }

    func selectedRange() -> NSRange { editSelection }

    func markedRange() -> NSRange { editMarkedRange }

    func hasMarkedText() -> Bool { editMarkedRange.location != NSNotFound }

    func attributedSubstring(forProposedRange range: NSRange, actualRange: NSRangePointer?) -> NSAttributedString? {
        let units = Array(editValue.utf16)
        let start = min(range.location, units.count)
        let end = min(start + range.length, units.count)
        actualRange?.pointee = NSRange(location: start, length: end - start)
        guard start < end else { return nil }
        return NSAttributedString(string: String(decoding: units[start..<end], as: UTF16.self))
    }

    func validAttributesForMarkedText() -> [NSAttributedString.Key] {
        return []
    }

    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer?) -> NSRect {
        actualRange?.pointee = range
        guard let caret = editorContext?.caret else { return .zero }
        // Convert the page-space caret rect through the same display transform
        // used for pointer input, then to screen space for the AppKit candidate
        // window. Never invert the transform separately for IME geometry.
        guard let localPoint = viewPoint(pageX: caret.x, pageY: caret.y) else { return .zero }
        let screenPoint = window?.convertPoint(toScreen: convert(localPoint, to: nil)) ?? localPoint
        let width = max(caret.width, 1)
        let height = max(caret.height, 1)
        return NSRect(x: screenPoint.x, y: screenPoint.y, width: width, height: height)
    }

    func characterIndex(for point: NSPoint) -> Int {
        return editSelection.location
    }
}

// MARK: - Local provisioning producer

private let hostServiceLauncherResource = "host-service/host-service"
private let hostServiceRootEnvironment = "AGENTBROWSER_HOST_SERVICE_ROOT"
private let hostServiceLauncherEnvironment = "AGENTBROWSER_HOST_SERVICE_LAUNCHER"
private let hostServiceAdminSocketEnvironment = "AGENTBROWSER_MAC_ADMIN_SOCKET"

private struct HostProvisioningError: Error {
    let code: String
    let message: String

    init(_ code: String, _ message: String) {
        self.code = code
        self.message = message
    }
}

/// The single explicit locator for the project's existing service launcher and
/// service root. The launcher is packaged inside the app bundle; the root is
/// the service's own declared default or an explicit override. No fallback
/// search, no mirrored service config, and no second lifecycle are introduced.
private struct HostServiceLocator {
    let launcherURL: URL?
    let root: String

    static func resolve(environment: [String: String] = ProcessInfo.processInfo.environment) -> HostServiceLocator {
        let launcher: URL?
        if let override = environment[hostServiceLauncherEnvironment], !override.isEmpty {
            launcher = URL(fileURLWithPath: override)
        } else if let resources = Bundle.main.resourceURL {
            launcher = resources.appendingPathComponent(hostServiceLauncherResource)
        } else {
            launcher = nil
        }
        let root: String
        if let override = environment[hostServiceRootEnvironment], !override.isEmpty {
            root = override
        } else {
            root = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/AgentBrowser/host-service")
                .path
        }
        return HostServiceLocator(launcherURL: launcher, root: root)
    }
}

/// Private native bridge to the frozen `host-service` CLI. It owns no lifecycle
/// and mirrors no service config: it only invokes the declared typed commands
/// and returns their exact result. A command the local service has not bound
/// yet fails explicitly; nothing here fabricates a receipt or a bundle.
private final class HostProvisioningBridge {
    private let launcherURL: URL?
    private let root: String

    init(locator: HostServiceLocator) {
        self.launcherURL = locator.launcherURL
        self.root = locator.root
    }

    /// Read the service-owned socket directory from the service's own typed
    /// `status` result. The Mac never guesses the runtime path and never reads
    /// the service config or state files directly.
    func adminSocketPath() -> String? {
        guard case .success(let status) = invoke(["status"], timeout: 15),
              status["state"] as? String == "running",
              let socketDir = status["socket_dir"] as? String,
              !socketDir.isEmpty else {
            return nil
        }
        return (socketDir as NSString).appendingPathComponent("host-admin.sock")
    }

    /// Initialize the endpoint locator exactly once. The service generates and
    /// owns the access token, so the Mac never reads it, shows it, logs it, or
    /// stores it.
    func initTrust(endpointHost: String, endpointPort: Int) -> Result<[String: Any], HostProvisioningError> {
        return invoke(
            ["trust", "init", "--endpoint-host", endpointHost, "--endpoint-port", String(endpointPort)],
            timeout: 60
        )
    }

    /// Re-bind the endpoint and record the applied receipt the export requires.
    func refreshEndpoint(timeout: Int) -> Result<[String: Any], HostProvisioningError> {
        return invoke(["trust", "endpoint-refresh", "--timeout", String(timeout)], timeout: Double(timeout) + 10)
    }

    func endpointStatus() -> Result<[String: Any], HostProvisioningError> {
        return invoke(["trust", "status"], timeout: 20)
    }

    /// Write the one connection file. The bundle is the only artifact the phone
    /// needs, and the token inside it is the only credential on the path.
    func exportBundle(outputPath: String) -> Result<[String: Any], HostProvisioningError> {
        return invoke(["trust", "export", "--output", outputPath], timeout: 60)
    }

    private func invoke(_ arguments: [String], timeout: TimeInterval) -> Result<[String: Any], HostProvisioningError> {
        guard let launcherURL, FileManager.default.isExecutableFile(atPath: launcherURL.path) else {
            return .failure(HostProvisioningError("HOST_SERVICE_LOCATOR_UNAVAILABLE", "host-service launcher is not packaged"))
        }
        let process = Process()
        // Launch env directly, with the packaged launcher as argv[0]. This
        // avoids relying on shell/open inherited state and binds the command
        // to the app's Resources directory and dedicated service root.
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [launcherURL.path] + arguments + ["--root", root]
        process.currentDirectoryURL = launcherURL.deletingLastPathComponent().deletingLastPathComponent()
        var environment = ProcessInfo.processInfo.environment
        environment[hostServiceRootEnvironment] = root
        environment[hostServiceLauncherEnvironment] = launcherURL.path
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
        process.environment = environment
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.standardOutput = standardOutput
        process.standardError = standardError
        process.standardInput = FileHandle.nullDevice
        // Put launch itself behind a bounded gate. This keeps a platform launch
        // stall from blocking the provisioning worker before its deadline.
        let launchGate = DispatchSemaphore(value: 0)
        var launchError: Error?
        DispatchQueue.global(qos: .userInitiated).async {
            do { try process.run() }
            catch { launchError = error }
            launchGate.signal()
        }
        if launchGate.wait(timeout: .now() + 5.0) == .timedOut {
            if process.isRunning { process.terminate() }
            return .failure(HostProvisioningError("HOST_SERVICE_TIMEOUT", "host-service launch timed out"))
        }
        if launchError != nil {
            return .failure(HostProvisioningError("HOST_SERVICE_LAUNCH_FAILED", "cannot start host-service"))
        }
        // Drain both pipes off the caller thread so a full pipe cannot deadlock
        // a command that is still running.
        let buffer = ServiceOutputBuffer()
        let drain = DispatchGroup()
        // Drain stdout and stderr independently. Reading one pipe to EOF before
        // the other can deadlock a valid Host command when either pipe fills.
        drain.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            buffer.stdout = (try? standardOutput.fileHandleForReading.readToEnd()) ?? Data()
            drain.leave()
        }
        drain.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            buffer.stderr = (try? standardError.fileHandleForReading.readToEnd()) ?? Data()
            drain.leave()
        }
        let deadline = Date(timeIntervalSinceNow: timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        let timedOut = process.isRunning
        if timedOut {
            process.terminate()
        }
        if timedOut {
            // Do not let waitUntilExit defeat the deadline. Give graceful
            // termination a short window, then force-kill the exact child PID.
            let reapDeadline = Date(timeIntervalSinceNow: 2.0)
            while process.isRunning && Date() < reapDeadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
            while process.isRunning && Date() < Date(timeIntervalSinceNow: 0.2) {
                Thread.sleep(forTimeInterval: 0.02)
            }
        } else {
            process.waitUntilExit()
        }
        // A launcher or descendant may retain a pipe descriptor after the
        // parent exits. Never let cleanup defeat the command timeout or leave
        // the provisioning sheet busy forever.
        _ = drain.wait(timeout: .now() + 2.0)
        if timedOut {
            return .failure(HostProvisioningError("HOST_SERVICE_TIMEOUT", "host-service did not return"))
        }
        let stdoutText = String(data: buffer.stdout, encoding: .utf8) ?? ""
        let stderrText = String(data: buffer.stderr, encoding: .utf8) ?? ""
        if process.terminationStatus != 0 {
            if let typed = HostProvisioningBridge.parseServiceError(stderrText) {
                return .failure(typed)
            }
            if process.terminationStatus == 2 {
                return .failure(HostProvisioningError("HOST_SERVICE_COMMAND_UNAVAILABLE", firstLine(stderrText)))
            }
            return .failure(HostProvisioningError("HOST_SERVICE_COMMAND_FAILED", firstLine(stderrText)))
        }
        guard let data = stdoutText.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return .failure(HostProvisioningError("HOST_SERVICE_OUTPUT_MALFORMED", "host-service did not return a JSON object"))
        }
        return .success(object)
    }

    private static func parseServiceError(_ text: String) -> HostProvisioningError? {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let error = object["error"] as? [String: Any],
              let code = error["code"] as? String, !code.isEmpty else {
            return nil
        }
        return HostProvisioningError(code, (error["message"] as? String) ?? code)
    }
}

private final class ServiceOutputBuffer {
    var stdout = Data()
    var stderr = Data()
}

private func firstLine(_ text: String) -> String {
    for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            return trimmed
        }
    }
    return "host-service returned no result"
}

/// Native AppKit sheet that produces the one connection file. It initializes
/// the endpoint locator if needed, re-binds the endpoint, and writes the
/// `.abconnection` bundle through `NSSavePanel`. The service generates and owns
/// the access token, so the Mac never reads, shows, logs, or stores it.
private final class ProvisioningSheetController: NSWindowController, NSWindowDelegate {
    private let provisioning: HostProvisioningBridge
    private let provisioningAvailable: Bool
    private let onClose: () -> Void

    private var statusLabel: NSTextField!
    private var hostField: NSTextField!
    private var portField: NSTextField!
    private var exportButton: NSButton!

    private var busy = false
    private var closed = false

    init(provisioning: HostProvisioningBridge, available: Bool, onClose: @escaping () -> Void) {
        self.provisioning = provisioning
        self.provisioningAvailable = available
        self.onClose = onClose
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 220),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "生成连接文件"
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        buildContent()
        begin()
    }

    required init?(coder: NSCoder) {
        fatalError("ProvisioningSheetController is created in code")
    }

    private func buildContent() {
        guard let window else { return }
        statusLabel = NSTextField(wrappingLabelWithString: "")
        statusLabel.preferredMaxLayoutWidth = 380

        hostField = NSTextField(string: "127.0.0.1")
        hostField.placeholderString = "手机可访问的主机名或 IP"

        portField = NSTextField(string: "8443")
        portField.placeholderString = "端口"

        exportButton = NSButton(title: "生成连接文件…", target: self, action: #selector(beginExport(_:)))
        let cancelButton = NSButton(title: "取消", target: self, action: #selector(cancel(_:)))
        cancelButton.keyEquivalent = "\u{1b}"

        let grid = NSGridView(views: [
            [gridLabel("状态"), statusLabel],
            [gridLabel("端点主机"), hostField],
            [gridLabel("端点端口"), portField],
        ])
        grid.rowSpacing = 10
        grid.columnSpacing = 12
        grid.column(at: 0).xPlacement = .trailing
        grid.column(at: 1).xPlacement = .fill

        let buttonRow = NSStackView(views: [exportButton, cancelButton])
        buttonRow.orientation = .horizontal
        buttonRow.spacing = 10
        buttonRow.distribution = .fillEqually

        let stack = NSStackView(views: [grid, buttonRow])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 18
        stack.translatesAutoresizingMaskIntoConstraints = false

        let content = NSView(frame: window.contentLayoutRect)
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
        ])
        window.contentView = content
    }

    private func gridLabel(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.alignment = .right
        return label
    }

    private func begin() {
        guard provisioningAvailable else {
            setStatus("本机管理服务不可用。请先安装并运行 Host 服务。", code: "HOST_PROVISIONING_UNAVAILABLE")
            setBusy(false)
            return
        }
        setStatus("填写手机可访问的端点，然后生成连接文件。", code: nil)
        setBusy(false)
    }

    @objc private func beginExport(_ sender: Any?) {
        let host = hostField.stringValue.trimmingCharacters(in: .whitespaces)
        guard !host.isEmpty else {
            setStatus("端点主机不能为空。", code: "ENDPOINT_HOST_REQUIRED")
            return
        }
        guard let port = Int(portField.stringValue.trimmingCharacters(in: .whitespaces)),
              (1...65535).contains(port) else {
            setStatus("端点端口必须是 1 到 65535。", code: "ENDPOINT_PORT_INVALID")
            return
        }
        guard let outputPath = chooseOutputPath() else { return }
        setBusy(true)
        setStatus("正在生成连接文件…", code: nil)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.runExport(host: host, port: port, outputPath: outputPath)
        }
    }

    private func runExport(host: String, port: Int, outputPath: String) {
        // `trust status` is the service's own typed locator read. A missing
        // locator is the only state that may initialize it; `trust init` itself
        // refuses to re-initialize, so a later failure is reported, not retried.
        if case .failure = provisioning.endpointStatus() {
            guard unwrap(provisioning.initTrust(endpointHost: host, endpointPort: port), step: "初始化端点") != nil else {
                return
            }
        }
        guard unwrap(provisioning.refreshEndpoint(timeout: 30), step: "绑定端点") != nil else { return }
        guard let exported = unwrap(provisioning.exportBundle(outputPath: outputPath), step: "导出连接文件") else {
            return
        }
        let endpoint = (exported["endpoint"] as? String) ?? "\(host):\(port)"
        DispatchQueue.main.async { [weak self] in
            self?.setBusy(false)
            self?.setStatus("已写入连接文件。\n端点：\(endpoint)\n路径：\(outputPath)", code: nil)
        }
    }

    private func unwrap(_ result: Result<[String: Any], HostProvisioningError>, step: String) -> [String: Any]? {
        switch result {
        case .success(let payload):
            return payload
        case .failure(let error):
            fail(step, error.code)
            return nil
        }
    }

    private func fail(_ step: String, _ code: String) {
        DispatchQueue.main.async { [weak self] in
            self?.setBusy(false)
            self?.setStatus("\(step)失败。", code: code)
        }
    }

    private func chooseOutputPath() -> String? {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "agentbrowser.abconnection"
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }

    private func setStatus(_ text: String, code: String?) {
        if let code {
            statusLabel.stringValue = "\(text)\n\(code)"
        } else {
            statusLabel.stringValue = text
        }
    }

    private func setBusy(_ busy: Bool) {
        self.busy = busy
        exportButton.isEnabled = !busy && provisioningAvailable
        hostField.isEnabled = !busy
        portField.isEnabled = !busy
    }

    @objc private func cancel(_ sender: Any?) {
        requestClose()
    }

    func windowWillClose(_ notification: Notification) {
        requestClose()
    }

    private func requestClose() {
        guard !closed else { return }
        closed = true
        onClose()
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    private static let chromeHeightPoints: CGFloat = 56

    private var window: NSWindow!
    private var contentView: BrowserContentView!
    private var webView: WKWebView!
    private var videoView: VideoSurfaceView!
    private var bridge: NativeBridge?
    private var hostProvisioning: HostProvisioningBridge?
    private var provisioningSheet: ProvisioningSheetController?
    private var provisioningMenuItem: NSMenuItem?
    private var latestSnapshot: Snapshot?
    private var presentationState: PresentationState = .browsing
    private var layoutRevision: UInt64 = 0
    private var appliedLayout: AppliedLayout?
    private var lastAdvertisedViewport: NSRect?
    private var viewportTimer: Timer?
    private var webViewAboveVideo = true
    private var connectionStatusSurface: NSTextField!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        installMainMenu()
        let pairingDirectory = commandLineValue("--pairing-dir")
        let helperURL = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent("AgentBrowserMacBridge")
        // The local-provisioning capability is projected from the service's own
        // typed `status` socket directory and a real `host-admin.sock` connect
        // probe inside the bridge; it is never inferred from directory
        // existence and never replaces the remote pairing truth.
        let hostProvisioning = HostProvisioningBridge(locator: HostServiceLocator.resolve())
        self.hostProvisioning = hostProvisioning
        bridge = try? NativeBridge(
            helperURL: helperURL,
            pairingDirectory: pairingDirectory,
            adminSocketPath: hostProvisioning.adminSocketPath()
        )

        videoView = VideoSurfaceView(frame: .zero)
        videoView.onDisplayed = { [weak self] ticket in
            guard let self else { return }
            self.videoView.recordDisplayed(viewport: self.appliedLayout?.presentedContentRect ?? .zero)
            self.applyLayout(revision: true)
            _ = self.bridge?.request("{\"op\":\"ack_frame\",\"ticket\":\(ticket)}")
        }
        videoView.onDecodeError = { [weak self] ticket, error in
            guard let self else { return }
            _ = self.bridge?.request("{\"op\":\"nack_frame\",\"ticket\":\(ticket),\"error\":\"\(escapeJSON(error))\"}")
        }
        videoView.onInput = { [weak self] raw in
            guard let self else { return }
            _ = self.bridge?.request(raw)
        }
        videoView.onEdit = { [weak self] raw in
            guard let self else { return nil }
            return self.bridge?.request(raw)
        }
        videoView.currentEpoch = { [weak self] in self?.currentEpoch() ?? 0 }
        videoView.inputReady = { [weak self] in self?.isInputReady() ?? false }
        videoView.nativeVideoInputEnabled = { [weak self] in self?.shouldRouteNativeVideoInput() ?? false }

        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptEnabled = true
        let userScriptSource = "window.ProbeNative={request:function(raw){var value=window.prompt('AgentBrowser.Native',raw);return value===null?JSON.stringify({rejection:'NATIVE_PROMPT_CANCELLED'}):value;}};window.addEventListener('error',function(e){try{window.ProbeNative.request(JSON.stringify({op:'pageerror',message:String(e.message||e.error||e)}));}catch(_){}});window.addEventListener('unhandledrejection',function(e){try{window.ProbeNative.request(JSON.stringify({op:'pageerror',message:String(e.reason||e)}));}catch(_){}});"
        let userScript = WKUserScript(source: userScriptSource, injectionTime: .atDocumentStart, forMainFrameOnly: true)
        configuration.userContentController.addUserScript(userScript)
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        macDiagnostic("MAC_WEBVIEW_CREATED frame=\(webView.frame) hidden=\(webView.isHidden) window=\(webView.window != nil) superview=\(webView.superview != nil)")

        contentView = BrowserContentView(frame: .zero)
        contentView.onLayout = { [weak self] in self?.applyLayout(revision: true) }
        contentView.addSubview(videoView)
        contentView.addSubview(webView)
        macDiagnostic("MAC_WEBVIEW_ADDED frame=\(webView.frame) hidden=\(webView.isHidden) window=\(webView.window != nil) superview=\(webView.superview != nil)")
        connectionStatusSurface = NSTextField(labelWithString: "Connection: idle | Transport: unknown | Path: unknown")
        connectionStatusSurface.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        connectionStatusSurface.textColor = .labelColor
        connectionStatusSurface.backgroundColor = NSColor.windowBackgroundColor.withAlphaComponent(0.92)
        connectionStatusSurface.drawsBackground = true
        connectionStatusSurface.wantsLayer = true
        connectionStatusSurface.layer?.cornerRadius = 6
        connectionStatusSurface.frame = NSRect(x: 18, y: 18, width: 560, height: 28)
        connectionStatusSurface.autoresizingMask = [.maxXMargin, .minYMargin]
        contentView.addSubview(connectionStatusSurface)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 760), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.contentView = contentView
        window.title = "AgentBrowser"
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        contentView.layoutSubtreeIfNeeded()
        applyLayout(revision: true)

        bridge?.onFrame = { [weak self] header, bytes in
            DispatchQueue.main.async { self?.videoView.accept(header: header, bytes: bytes) }
        }
        bridge?.onSnapshot = { [weak self] raw in
            guard let data = raw.data(using: .utf8) else { return }
            guard let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) else {
                macDiagnostic("MAC_SNAPSHOT_DECODE_FAILED raw=\(raw)\\n")
                return
            }
            DispatchQueue.main.async {
                self?.latestSnapshot = snapshot
                let state = snapshot.connectionState ?? "idle"
                let transport = snapshot.transport ?? "unknown"
                let path = snapshot.path ?? "unknown"
                let error = snapshot.error.map { " | Error: \($0)" } ?? ""
                self?.connectionStatusSurface?.stringValue = "Connection: \(state) | Transport: \(transport) | Path: \(path)\(error)"
                self?.provisioningMenuItem?.isEnabled = snapshot.provisioningAvailable ?? false
                self?.videoView.apply(snapshot: snapshot)
                self?.applyLayout(revision: true)
            }
        }
        bridge?.responseTransform = { [weak self] raw in
            guard let self, let data = raw.data(using: .utf8),
                  var object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  object["state"] != nil,
                  let layout = self.layoutProjection() else {
                return raw
            }
            object["layout"] = layout
            object["macStatusSurface"] = [
                "connectionState": object["connectionState"] ?? NSNull(),
                "transport": object["transport"] ?? "unknown",
                "path": object["path"] ?? "unknown",
                "error": object["error"] ?? NSNull()
            ]
            guard let projected = try? JSONSerialization.data(withJSONObject: object),
                  let encoded = String(data: projected, encoding: .utf8) else {
                return raw
            }
            return encoded
        }

        guard let uiURL = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "ui") else {
            macDiagnostic("MAC_WEBVIEW_ASSET_MISSING bundle=\(Bundle.main.bundlePath)\\n")
            webView.loadHTMLString("<h1>Mac UI asset missing</h1>", baseURL: nil)
            return
        }
        macDiagnostic("MAC_WEBVIEW_LOAD_REQUEST url=\(uiURL.absoluteString) exists=\(FileManager.default.fileExists(atPath: uiURL.path)) frame=\(webView.frame) hidden=\(webView.isHidden) window=\(webView.window != nil) superview=\(webView.superview != nil)\\n")
        webView.loadFileURL(uiURL, allowingReadAccessTo: uiURL.deletingLastPathComponent())
        applyLayout(revision: true)
        let timer = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in self?.updateViewport() }
        viewportTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        viewportTimer?.invalidate()
        viewportTimer = nil
        bridge?.close()
        return .terminateNow
    }

    private func installMainMenu() {
        let mainMenu = NSMenu()

        let applicationItem = NSMenuItem()
        mainMenu.addItem(applicationItem)
        let applicationMenu = NSMenu()
        applicationMenu.addItem(withTitle: "关于 AgentBrowser", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "退出 AgentBrowser", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu

        let connectionItem = NSMenuItem()
        mainMenu.addItem(connectionItem)
        let connectionMenu = NSMenu(title: "连接")
        let provisionItem = NSMenuItem(title: "配置手机连接…", action: #selector(beginProvisioning(_:)), keyEquivalent: "p")
        provisionItem.target = self
        // Disabled until the real declared local authority probe reports
        // available; the bridge never sets this true from a guessed path.
        provisionItem.isEnabled = false
        connectionMenu.addItem(provisionItem)
        connectionItem.submenu = connectionMenu
        provisioningMenuItem = provisionItem

        NSApplication.shared.mainMenu = mainMenu
    }

    @objc private func beginProvisioning(_ sender: Any?) {
        guard let window, let hostProvisioning, provisioningSheet == nil else { return }
        let available = latestSnapshot?.provisioningAvailable ?? false
        let controller = ProvisioningSheetController(provisioning: hostProvisioning, available: available) { [weak self] in
            guard let self, let sheet = self.provisioningSheet?.window else { return }
            self.window.endSheet(sheet)
            self.provisioningSheet = nil
        }
        provisioningSheet = controller
        guard let sheet = controller.window else { return }
        window.beginSheet(sheet, completionHandler: nil)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard prompt == nativePrompt, let defaultText else {
            completionHandler(nil)
            return
        }
        if let request = parsePresentationRequest(defaultText) {
            guard let result = applyPresentation(request) else {
                completionHandler(presentationRejection(id: request.id, reason: "GEOMETRY_UNAVAILABLE"))
                return
            }
            completionHandler(result)
            return
        }
        // Stay on the main thread so the presentation projection in
        // `responseTransform` only ever reads AppKit state on its owning thread.
        completionHandler(bridge?.request(defaultText) ?? rejection("NATIVE_BRIDGE_UNAVAILABLE"))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        macDiagnostic("MAC_WEBVIEW_LOAD_FINISH url=" + (webView.url?.absoluteString ?? "nil"))
        webView.evaluateJavaScript("document.readyState+'|'+location.href+'|rootChildren='+(document.getElementById('root')?.childElementCount??-1)+'|bodyText='+document.body.innerText.slice(0,300)+'|bodyChildren='+document.body.children.length+'|rootSize='+((document.getElementById('root')?.offsetWidth)||-1)+'x'+((document.getElementById('root')?.offsetHeight)||-1)") { value, error in
            macDiagnostic("MAC_WEBVIEW_DOM value=" + String(describing: value) + " error=" + String(describing: error))
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        macDiagnostic("MAC_WEBVIEW_LOAD_FAIL url=" + (webView.url?.absoluteString ?? "nil") + " error=" + String(describing: error))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        macDiagnostic("MAC_WEBVIEW_PROVISIONAL_FAIL url=" + (webView.url?.absoluteString ?? "nil") + " error=" + String(describing: error))
    }


    private func updateViewport() {
        guard let bridge, let layout = appliedLayout else { return }
        // A disconnected probe owns no Host viewport; clearing the fence lets the
        // first declaration after reconnect through instead of being suppressed.
        guard latestSnapshot?.sessionId != nil else {
            lastAdvertisedViewport = nil
            return
        }
        let viewport = layout.advertisedViewportRect
        guard viewport.width > 0, viewport.height > 0, viewport != lastAdvertisedViewport else { return }
        let width = max(1, Int(viewport.width.rounded()))
        let height = max(1, Int(viewport.height.rounded()))
        let orientation = width >= height ? "landscape" : "portrait"
        _ = bridge.request("{\"op\":\"viewport\",\"width\":\(width),\"height\":\(height),\"orientation\":\"\(orientation)\"}")
        lastAdvertisedViewport = viewport
    }

    private func applyLayout(revision: Bool) {
        guard let contentView, contentView.bounds.width > 0, contentView.bounds.height > 0 else { return }
        let drawable = contentView.bounds
        // Mac has no system bars or soft keyboard: the drawable content view is
        // the safe content once, and advertised == presented in AppKit points.
        let safeContent = drawable
        // Thin chrome matches the frozen UI's `.browser-chrome` 56pt bar.
        let chromeHeight = min(Self.chromeHeightPoints, safeContent.height)
        let chromeRect = NSRect(x: safeContent.minX, y: safeContent.minY, width: safeContent.width, height: chromeHeight)
        let contentRect = NSRect(
            x: safeContent.minX,
            y: safeContent.minY + chromeHeight,
            width: safeContent.width,
            height: max(0, safeContent.height - chromeHeight)
        )
        let nativeVideo = nativeVideoVisible(for: contentRect)
        let videoVisible = nativeVideo && presentationState == .browsing
        // No valid matching displayed frame, geometry mismatch or overlay: the
        // WebView owns the full drawable content. Browsing native video: the
        // WebView shrinks to the thin chrome and the video fills the content.
        let webRect = videoVisible ? chromeRect : drawable
        let layout = AppliedLayout(
            drawableWindowRect: drawable,
            safeContentRect: safeContent,
            advertisedViewportRect: contentRect,
            presentedContentRect: contentRect,
            chromeRect: chromeRect,
            webRect: webRect,
            presentation: presentationState
        )
        if appliedLayout != layout, revision {
            layoutRevision += 1
        }
        appliedLayout = layout
        videoView.frame = contentRect
        webView.frame = webRect
        videoView.isHidden = !videoVisible
        setWebViewAboveVideo(!videoVisible)
        videoView.setPresentationInteractive(videoVisible)
    }

    private func setWebViewAboveVideo(_ above: Bool) {
        guard webViewAboveVideo != above else { return }
        webViewAboveVideo = above
        contentView.addSubview(webView, positioned: above ? .above : .below, relativeTo: videoView)
    }

    /// Visibility is owned by the displayed frame identity plus the local
    /// geometry match. `inputReady`/`controlMode`/pending operations never enter
    /// this decision; the connection lifecycle only supplies the outer fence.
    private func nativeVideoVisible(for contentRect: NSRect) -> Bool {
        guard let snapshot = latestSnapshot, snapshot.connectionState == "connected" else { return false }
        guard snapshot.viewportPending != true else { return false }
        guard let displayedGeneration = snapshot.displayedGeneration,
              let displayedSessionId = snapshot.displayedSessionId,
              let displayedTabId = snapshot.displayedTabId,
              let activeTabId = snapshot.activeTabId,
              let documentRevision = snapshot.documentRevision,
              let viewportRevision = snapshot.viewportRevision else {
            return false
        }
        guard displayedGeneration == snapshot.generation,
              displayedSessionId == snapshot.sessionId,
              displayedTabId == activeTabId,
              snapshot.displayedDocumentRevision == documentRevision,
              snapshot.displayedViewportRevision == viewportRevision else {
            return false
        }
        return videoView.hasFrame(matching: contentRect)
    }

    private func shouldUseNativeVideo() -> Bool {
        guard let contentRect = appliedLayout?.presentedContentRect else { return false }
        return nativeVideoVisible(for: contentRect)
    }

    private func shouldRouteNativeVideoInput() -> Bool {
        return presentationState == .browsing && shouldUseNativeVideo()
    }

    private func parsePresentationRequest(_ raw: String) -> PresentationRequest? {
        guard let data = raw.data(using: .utf8),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              value["op"] as? String == "set_presentation",
              let id = value["id"] as? String,
              let stateRaw = value["state"] as? String,
              let state = PresentationState(rawValue: stateRaw) else {
            return nil
        }
        return PresentationRequest(id: id, state: state)
    }

    private func layoutProjection() -> [String: Any]? {
        guard let layout = appliedLayout else { return nil }
        let nativeVideo = nativeVideoVisible(for: layout.presentedContentRect)
        var projection: [String: Any] = [
            "drawableWindowRect": rectJSON(layout.drawableWindowRect),
            "safeContentRect": rectJSON(layout.safeContentRect),
            "advertisedViewportRect": rectJSON(layout.advertisedViewportRect),
            "presentedContentRect": rectJSON(layout.presentedContentRect),
            "chromeRect": rectJSON(layout.chromeRect),
            "webRect": rectJSON(layout.webRect),
            "visibleContent": nativeVideo ? "native-video" : "webview",
            "currentTabId": latestSnapshot?.activeTabId as Any? ?? NSNull(),
            "presentationViewport": rectJSON(layout.presentedContentRect),
            "geometryMatches": nativeVideo,
            "presentation": layout.presentation.rawValue,
            "revision": layoutRevision,
        ]
        if let displayedFrame = displayedFrameProjection() {
            projection["displayedFrame"] = displayedFrame
        }
        return projection
    }

    private func displayedFrameProjection() -> [String: Any]? {
        guard let snapshot = latestSnapshot,
              let generation = snapshot.displayedGeneration,
              let sessionId = snapshot.displayedSessionId,
              let tabId = snapshot.displayedTabId else {
            return nil
        }
        return [
            "generation": generation,
            "sessionId": sessionId,
            "tabId": tabId,
            "documentRevision": snapshot.displayedDocumentRevision as Any? ?? UInt64(0),
            "viewportRevision": snapshot.displayedViewportRevision as Any? ?? UInt64(0),
        ]
    }

    private func applyPresentation(_ request: PresentationRequest) -> String? {
        guard contentView != nil, window != nil else { return nil }
        presentationState = request.state
        applyLayout(revision: true)
        guard let layout = appliedLayout else { return nil }
        let result: [String: Any] = [
            "id": request.id,
            "op": "set_presentation",
            "status": "applied",
            "geometry": [
                "webRect": rectJSON(layout.webRect),
                "revision": layoutRevision,
            ],
        ]
        return encodeJSON(result)
    }

    private func presentationRejection(id: String, reason: String) -> String {
        return encodeJSON([
            "id": id,
            "op": "set_presentation",
            "status": "rejected",
            "reason": reason,
        ]) ?? rejection(reason)
    }

    private func encodeJSON(_ object: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func rectJSON(_ rect: NSRect) -> [String: Double] {
        return [
            "x": Double(rect.minX),
            "y": Double(rect.minY),
            "width": Double(rect.width),
            "height": Double(rect.height),
        ]
    }

    private func currentEpoch() -> UInt64 {
        return latestSnapshot?.epoch ?? 0
    }

    private func isInputReady() -> Bool {
        return latestSnapshot?.inputReady ?? false
    }

    private func commandLineValue(_ name: String) -> String? {
        guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else { return nil }
        return CommandLine.arguments[index + 1]
    }
}

private func escapeJSON(_ value: String) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: [value]), let raw = String(data: data, encoding: .utf8) else { return "decode error" }
    return String(raw.dropFirst().dropLast())
}

private func json(_ values: [(String, String)]) -> String {
    var out = "{"
    for (index, entry) in values.enumerated() {
        if index > 0 { out += "," }
        out += "\"" + escapeJSON(entry.0) + "\":\"" + escapeJSON(entry.1) + "\""
    }
    return out + "}"
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
