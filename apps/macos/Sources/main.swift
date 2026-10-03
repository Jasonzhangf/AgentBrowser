import AppKit
import CoreImage
import CoreMedia
import CoreVideo
import VideoToolbox
import WebKit

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
    let displayedTicket: UInt64?
    let epoch: UInt64?
    let inputReady: Bool
    let editorContext: EditorContext?
    let inputLease: InputLease?
    let editingOutcome: EditingEnvelope?
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

    init(helperURL: URL, pairingDirectory: String?) throws {
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
        process.environment = environment
        try process.run()
        startReader()
    }

    deinit {
        close()
    }

    func request(_ raw: String) -> String {
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
        onSnapshot?(value)
        return value
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

    var onDisplayed: ((UInt64) -> Void)?
    var onDecodeError: ((UInt64, String) -> Void)?
    var onInput: ((String) -> Void)?
    var onEdit: ((String) -> String?)?
    var currentEpoch: (() -> UInt64)?
    var inputReady: (() -> Bool)?

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
        guard inputReady?() == true, let epoch = currentEpoch?() else { return }
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
        guard inputReady?() == true, var pending = pendingPointer, let epoch = currentEpoch?() else { return }
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
        guard inputReady?() == true, let epoch = currentEpoch?() else { return }
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
            self.visibleWidth = token.visibleWidth
            self.visibleHeight = token.visibleHeight
            self.needsDisplay = true
            self.onDisplayed?(token.ticket)
        }
        _ = presentationTimeStamp
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

private final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var videoView: VideoSurfaceView!
    private var bridge: NativeBridge?
    private var latestSnapshot: Snapshot?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        let pairingDirectory = commandLineValue("--pairing-dir")
        let helperURL = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent("AgentBrowserMacBridge")
        bridge = try? NativeBridge(helperURL: helperURL, pairingDirectory: pairingDirectory)

        videoView = VideoSurfaceView(frame: .zero)
        videoView.onDisplayed = { [weak self] ticket in
            guard let self else { return }
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

        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptEnabled = true
        let userScriptSource = "window.ProbeNative={request:function(raw){var value=window.prompt('AgentBrowser.Native',raw);return value===null?JSON.stringify({rejection:'NATIVE_PROMPT_CANCELLED'}):value;}};"
        let userScript = WKUserScript(source: userScriptSource, injectionTime: .atDocumentStart, forMainFrameOnly: true)
        configuration.userContentController.addUserScript(userScript)
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        let split = NSSplitViewController()
        let videoController = NSViewController()
        videoController.view = videoView
        let webController = NSViewController()
        webController.view = webView
        let videoItem = NSSplitViewItem(viewController: videoController)
        let webItem = NSSplitViewItem(viewController: webController)
        videoItem.minimumThickness = 320
        webItem.minimumThickness = 320
        split.addSplitViewItem(videoItem)
        split.addSplitViewItem(webItem)

        window = NSWindow(contentViewController: split)
        window.title = "AgentBrowser"
        window.setContentSize(NSSize(width: 1180, height: 760))
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)

        bridge?.onFrame = { [weak self] header, bytes in
            DispatchQueue.main.async { self?.videoView.accept(header: header, bytes: bytes) }
        }
        bridge?.onSnapshot = { [weak self] raw in
            guard let data = raw.data(using: .utf8), let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
            DispatchQueue.main.async {
                self?.latestSnapshot = snapshot
                self?.videoView.apply(snapshot: snapshot)
            }
        }

        guard let uiURL = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "ui") else {
            webView.loadHTMLString("<h1>Mac UI asset missing</h1>", baseURL: nil)
            return
        }
        webView.loadFileURL(uiURL, allowingReadAccessTo: uiURL.deletingLastPathComponent())
        let timer = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in self?.updateViewport() }
        RunLoop.main.add(timer, forMode: .common)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        bridge?.close()
        return .terminateNow
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard prompt == nativePrompt, let defaultText else {
            completionHandler(nil)
            return
        }
        completionHandler(bridge?.request(defaultText) ?? rejection("NATIVE_BRIDGE_UNAVAILABLE"))
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
    }

    private func updateViewport() {
        guard let bridge, videoView.bounds.width > 0, videoView.bounds.height > 0 else { return }
        let scale = window.backingScaleFactor
        let width = max(1, Int((videoView.bounds.width * scale).rounded()))
        let height = max(1, Int((videoView.bounds.height * scale).rounded()))
        let orientation = width >= height ? "landscape" : "portrait"
        _ = bridge.request("{\"op\":\"viewport\",\"width\":\(width),\"height\":\(height),\"orientation\":\"\(orientation)\"}")
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
