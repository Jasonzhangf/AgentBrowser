//! Mac native bridge process.
//!
//! The WebView only sees bounded JSON snapshots. Encoded media uses a separate
//! framed native pipe and is acknowledged only after VideoToolbox displays it.
//! Browser ABI, TLS and Host control remain owned by client-connection/Obscura.

use std::{
    io::{self, BufRead, BufReader, BufWriter, Write},
    path::{Path, PathBuf},
    sync::{mpsc as std_mpsc, Arc},
    thread,
};

use agentbrowser_connection::{
    protocol::{
        ControlPhase, Device, Orientation, SessionStatus, VideoPacket, ViewportDeclaration,
    },
    Connection, Connector, DisplayedFrame, Failure, Input, Pairing, Video,
};
use tokio::{sync::mpsc, task::JoinHandle};

const MAX_COMMAND_BYTES: usize = 64 * 1024;
const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;

struct BridgeRequest {
    id: u64,
    command: String,
}

enum Output {
    Response { id: u64, value: String },
    Frame(FrameOutput),
}

struct FrameOutput {
    ticket: u64,
    generation: u64,
    session_id: String,
    sequence: u64,
    document_revision: u64,
    viewport_revision: u64,
    coded_width: u32,
    coded_height: u32,
    visible_width: u32,
    visible_height: u32,
    pts_us: u64,
    bytes: Vec<u8>,
}

enum Event {
    Connected {
        generation: u64,
        connector: Connector,
        connection: Connection,
    },
    ConnectFailed {
        generation: u64,
        error: String,
    },
    Media {
        generation: u64,
        video: Arc<Video>,
    },
    MediaEnded {
        generation: u64,
        error: String,
    },
}

struct Session {
    // Dropping Connector fences the Connection's generation source.
    _connector: Connector,
    connection: Connection,
    client_generation: u64,
    status: SessionStatus,
    pending: Option<PendingFrame>,
    queued: Option<Arc<Video>>,
    displayed: Option<DisplayedRecord>,
    rendered_frames: u64,
    next_ticket: u64,
    viewport: Option<ViewportDeclaration>,
    error: Option<String>,
}

struct PendingFrame {
    ticket: u64,
    video: Arc<Video>,
}

struct DisplayedRecord {
    ticket: u64,
    pts_us: u64,
    frame: DisplayedFrame,
}

#[derive(Clone, Copy)]
enum Lifecycle {
    Idle,
    Connecting,
    Connected,
    Stopped,
    Error,
}

struct AppState {
    session: Option<Session>,
    pending_connect: Option<JoinHandle<()>>,
    generation: u64,
    lifecycle: Lifecycle,
    error: Option<String>,
    pairing_available: bool,
}

impl AppState {
    fn new() -> Self {
        Self {
            session: None,
            pending_connect: None,
            generation: 0,
            lifecycle: Lifecycle::Idle,
            error: None,
            pairing_available: pairing_dir().is_some_and(|path| path.is_dir()),
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (request_tx, request_rx) = mpsc::channel(32);
    let (event_tx, event_rx) = mpsc::channel(32);
    let (output_tx, output_rx) = std_mpsc::channel();

    thread::spawn(move || read_requests(request_tx));
    thread::spawn(move || write_outputs(output_rx));

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    runtime.block_on(run(request_rx, event_rx, event_tx, output_tx));
    Ok(())
}

fn read_requests(sender: mpsc::Sender<BridgeRequest>) {
    let stdin = io::stdin();
    for line in BufReader::new(stdin.lock()).lines() {
        let Ok(line) = line else { break };
        if line.len() > MAX_COMMAND_BYTES {
            eprintln!("bridge request rejected: COMMAND_SIZE");
            continue;
        }
        let value = match serde_json::from_str::<serde_json::Value>(&line) {
            Ok(value) => value,
            Err(error) => {
                eprintln!("bridge request rejected: INVALID_REQUEST:{error}");
                continue;
            }
        };
        let Some(id) = value.get("id").and_then(serde_json::Value::as_u64) else {
            eprintln!("bridge request rejected: MISSING_REQUEST_ID");
            continue;
        };
        let Some(command) = value.get("command").and_then(serde_json::Value::as_str) else {
            eprintln!("bridge request rejected: MISSING_COMMAND");
            continue;
        };
        if command.len() > MAX_COMMAND_BYTES {
            eprintln!("bridge request rejected: COMMAND_SIZE");
            continue;
        }
        if sender
            .blocking_send(BridgeRequest {
                id,
                command: command.to_owned(),
            })
            .is_err()
        {
            break;
        }
    }
}

fn write_outputs(receiver: std_mpsc::Receiver<Output>) {
    let stdout = io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    for message in receiver {
        let (kind, header, bytes) = match message {
            Output::Response { id, value } => {
                let header = serde_json::json!({"type":"response", "id":id, "value":value});
                (
                    0u8,
                    serde_json::to_vec(&header).expect("response serialization"),
                    Vec::new(),
                )
            }
            Output::Frame(frame) => {
                if frame.bytes.len() > MAX_FRAME_BYTES {
                    eprintln!("bridge frame rejected: FRAME_SIZE");
                    continue;
                }
                let header = serde_json::json!({
                    "type":"frame",
                    "ticket":frame.ticket,
                    "generation":frame.generation,
                    "session_id":frame.session_id,
                    "sequence":frame.sequence,
                    "document_revision":frame.document_revision,
                    "viewport_revision":frame.viewport_revision,
                    "coded_width":frame.coded_width,
                    "coded_height":frame.coded_height,
                    "visible_width":frame.visible_width,
                    "visible_height":frame.visible_height,
                    "pts_us":frame.pts_us,
                    "byte_length":frame.bytes.len(),
                });
                (
                    1u8,
                    serde_json::to_vec(&header).expect("frame serialization"),
                    frame.bytes,
                )
            }
        };
        if header.len() > MAX_RESPONSE_BYTES || bytes.len() > MAX_FRAME_BYTES {
            eprintln!("bridge output rejected: OUTPUT_SIZE");
            continue;
        }
        let Ok(header_len) = u32::try_from(header.len()) else {
            continue;
        };
        let Ok(bytes_len) = u32::try_from(bytes.len()) else {
            continue;
        };
        if output.write_all(&[kind]).is_err()
            || output.write_all(&header_len.to_be_bytes()).is_err()
            || output.write_all(&header).is_err()
            || output.write_all(&bytes_len.to_be_bytes()).is_err()
            || output.write_all(&bytes).is_err()
            || output.flush().is_err()
        {
            break;
        }
    }
}

async fn run(
    mut requests: mpsc::Receiver<BridgeRequest>,
    mut events: mpsc::Receiver<Event>,
    event_sender: mpsc::Sender<Event>,
    output: std_mpsc::Sender<Output>,
) {
    let mut app = AppState::new();
    loop {
        tokio::select! {
            request = requests.recv() => {
                let Some(request) = request else { break };
                handle_request(&mut app, request, &event_sender, &output).await;
            }
            event = events.recv() => {
                let Some(event) = event else { break };
                handle_event(&mut app, event, &event_sender, &output);
            }
        }
    }
}

async fn handle_request(
    app: &mut AppState,
    request: BridgeRequest,
    event_sender: &mpsc::Sender<Event>,
    output: &std_mpsc::Sender<Output>,
) {
    let value = match dispatch(app, &request.command, event_sender, output).await {
        Ok(value) => value,
        Err(error) => rejection(&error),
    };
    let _ = output.send(Output::Response {
        id: request.id,
        value,
    });
}

fn handle_event(
    app: &mut AppState,
    event: Event,
    event_sender: &mpsc::Sender<Event>,
    output: &std_mpsc::Sender<Output>,
) {
    match event {
        Event::Connected {
            generation,
            connector,
            connection,
        } => {
            if app.generation != generation || !matches!(app.lifecycle, Lifecycle::Connecting) {
                drop(connection);
                drop(connector);
                return;
            }
            app.pending_connect = None;
            let status = connection.initial_status.clone();
            let actual_generation = connection.generation;
            spawn_media_pump(&connection, generation, event_sender.clone());
            app.session = Some(Session {
                _connector: connector,
                connection,
                client_generation: generation,
                status,
                pending: None,
                queued: None,
                displayed: None,
                rendered_frames: 0,
                next_ticket: 0,
                viewport: None,
                error: None,
            });
            app.lifecycle = Lifecycle::Connected;
            app.error = None;
            debug_assert!(actual_generation > 0);
        }
        Event::ConnectFailed { generation, error } => {
            if app.generation == generation && matches!(app.lifecycle, Lifecycle::Connecting) {
                app.pending_connect = None;
                app.lifecycle = Lifecycle::Error;
                app.error = Some(error);
            }
        }
        Event::Media { generation, video } => {
            handle_media(app, generation, video, output);
        }
        Event::MediaEnded { generation, error } => {
            let current = app
                .session
                .as_ref()
                .map(|session| session.client_generation == generation)
                .unwrap_or(false);
            if current && matches!(app.lifecycle, Lifecycle::Connected) {
                app.lifecycle = Lifecycle::Error;
                app.error = Some(error);
            }
        }
    }
}

fn spawn_media_pump(connection: &Connection, generation: u64, sender: mpsc::Sender<Event>) {
    let mut media = connection.media.clone();
    tokio::spawn(async move {
        loop {
            if media.changed().await.is_err() {
                let _ = sender
                    .send(Event::MediaEnded {
                        generation,
                        error: "MEDIA_STREAM_CLOSED".into(),
                    })
                    .await;
                break;
            }
            let Some(video) = media.borrow_and_update().clone() else {
                continue;
            };
            if sender
                .send(Event::Media { generation, video })
                .await
                .is_err()
            {
                break;
            }
        }
    });
}

fn handle_media(
    app: &mut AppState,
    generation: u64,
    video: Arc<Video>,
    output: &std_mpsc::Sender<Output>,
) {
    let Some(session) = app.session.as_mut() else {
        return;
    };
    if session.client_generation != generation {
        return;
    }
    match &video.packet {
        VideoPacket::AccessUnit { .. } => {
            if session.pending.is_some() {
                session.queued = Some(video);
            } else if let Err(error) = offer_frame(session, video, output) {
                app.lifecycle = Lifecycle::Error;
                app.error = Some(error);
            }
        }
        VideoPacket::Waiting { .. } => {}
        VideoPacket::Unavailable { message, .. } => {
            app.lifecycle = Lifecycle::Error;
            app.error = Some(format!("Host media unavailable: {message}"));
        }
        VideoPacket::Closed { .. } => {
            app.lifecycle = Lifecycle::Error;
            app.error = Some("Host media closed".into());
        }
    }
}

fn offer_frame(
    session: &mut Session,
    video: Arc<Video>,
    output: &std_mpsc::Sender<Output>,
) -> Result<(), String> {
    let VideoPacket::AccessUnit {
        source,
        pts_us,
        coded_width,
        coded_height,
        ..
    } = &video.packet
    else {
        return Ok(());
    };
    session.next_ticket = session
        .next_ticket
        .checked_add(1)
        .ok_or("FRAME_TICKET_EXHAUSTED")?;
    let ticket = session.next_ticket;
    output
        .send(Output::Frame(FrameOutput {
            ticket,
            generation: session.client_generation,
            session_id: source.session_id.clone(),
            sequence: source.sequence,
            document_revision: source.document_revision,
            viewport_revision: source.viewport_revision,
            coded_width: *coded_width,
            coded_height: *coded_height,
            visible_width: source.width,
            visible_height: source.height,
            pts_us: *pts_us,
            bytes: video.bytes.clone(),
        }))
        .map_err(|_| "NATIVE_OUTPUT_CLOSED".to_string())?;
    session.pending = Some(PendingFrame { ticket, video });
    Ok(())
}

async fn dispatch(
    app: &mut AppState,
    raw: &str,
    event_sender: &mpsc::Sender<Event>,
    output: &std_mpsc::Sender<Output>,
) -> Result<String, String> {
    if raw.len() > MAX_COMMAND_BYTES {
        return Err("COMMAND_SIZE".into());
    }
    let value = serde_json::from_str::<serde_json::Value>(raw)
        .map_err(|_| "INVALID_COMMAND_JSON".to_string())?;
    let object = value.as_object().ok_or("COMMAND_OBJECT_REQUIRED")?;
    let op = object
        .get("op")
        .and_then(serde_json::Value::as_str)
        .ok_or("MISSING_COMMAND")?;
    match op {
        "account_status"
        | "account_login"
        | "account_register_device"
        | "account_refresh"
        | "account_logout" => account_command(object),
        "status" => {
            fields(object, &["op"], &["op"])?;
            refresh_status(app).await;
            Ok(snapshot(app))
        }
        "connect" => {
            fields(object, &["op"], &["op"])?;
            start_connect(app, event_sender).await?;
            Ok(snapshot(app))
        }
        "disconnect" => {
            fields(object, &["op"], &["op"])?;
            disconnect(app)?;
            Ok(snapshot(app))
        }
        "observe" => {
            fields(object, &["op"], &["op"])?;
            // Attach starts in Host observation mode. No local shadow state or
            // second attach path is invented for this command.
            Ok(snapshot(app))
        }
        "takeover" => {
            fields(object, &["op", "epoch"], &["op", "epoch"])?;
            let epoch = u64_value(object, "epoch")?;
            let result = {
                let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
                clear_session_error(session);
                session.connection.takeover(epoch).await
            };
            let status = match result {
                Ok(status) => status,
                Err(error) => return Err(command_failure(app, error)),
            };
            let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
            session.status = status;
            Ok(snapshot(app))
        }
        "release" => {
            fields(object, &["op", "epoch"], &["op", "epoch"])?;
            let epoch = u64_value(object, "epoch")?;
            let result = {
                let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
                clear_session_error(session);
                session.connection.release(epoch).await
            };
            let status = match result {
                Ok(status) => status,
                Err(error) => return Err(command_failure(app, error)),
            };
            let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
            session.status = status;
            Ok(snapshot(app))
        }
        "navigate" => {
            fields(
                object,
                &["op", "epoch", "url"],
                &["op", "epoch", "url"],
            )?;
            let epoch = u64_value(object, "epoch")?;
            let url = object
                .get("url")
                .and_then(serde_json::Value::as_str)
                .ok_or("URL_REQUIRED")?;
            let result = {
                let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
                clear_session_error(session);
                session.connection.navigate(url.to_owned(), epoch).await
            };
            let status = match result {
                Ok(status) => status,
                Err(error) => return Err(command_failure(app, error)),
            };
            let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
            session.status = status;
            Ok(snapshot(app))
        }
        "input_text" => {
            fields(object, &["op", "epoch", "text"], &["op", "epoch", "text"])?;
            let epoch = u64_value(object, "epoch")?;
            let text = object
                .get("text")
                .and_then(serde_json::Value::as_str)
                .ok_or("TEXT_REQUIRED")?;
            if text.len() > 4096 {
                return Err("INPUT_TEXT_LIMIT".into());
            }
            submit_input(app, Input::Text(text.to_owned()), epoch).await?;
            Ok(snapshot(app))
        }
        "click" => {
            fields(
                object,
                &["op", "epoch", "x", "y"],
                &["op", "epoch", "x", "y"],
            )?;
            let epoch = u64_value(object, "epoch")?;
            let x = finite_value(object, "x")?;
            let y = finite_value(object, "y")?;
            submit_input(app, Input::Click { x, y }, epoch).await?;
            Ok(snapshot(app))
        }
        "scroll" => {
            fields(
                object,
                &["op", "epoch", "x", "y", "dx", "dy"],
                &["op", "epoch", "x", "y", "dx", "dy"],
            )?;
            let epoch = u64_value(object, "epoch")?;
            let input = Input::Scroll {
                x: finite_value(object, "x")?,
                y: finite_value(object, "y")?,
                delta_x: finite_value(object, "dx")?,
                delta_y: finite_value(object, "dy")?,
            };
            submit_input(app, input, epoch).await?;
            Ok(snapshot(app))
        }
        "viewport" => {
            fields(
                object,
                &["op", "width", "height", "orientation"],
                &["op", "width", "height", "orientation"],
            )?;
            let width = bounded_dimension(object, "width")?;
            let height = bounded_dimension(object, "height")?;
            if u64::from(width) * u64::from(height) > 4_194_304 {
                return Err("INVALID_VIEWPORT".into());
            }
            let orientation = match object
                .get("orientation")
                .and_then(serde_json::Value::as_str)
            {
                Some("portrait") => Orientation::Portrait,
                Some("landscape") => Orientation::Landscape,
                _ => return Err("INVALID_ORIENTATION".into()),
            };
            declare_viewport(
                app,
                ViewportDeclaration {
                    device: Device::Desktop,
                    css_width: width,
                    css_height: height,
                    orientation,
                },
            )
            .await?;
            Ok(snapshot(app))
        }
        "ack_frame" => {
            fields(object, &["op", "ticket"], &["op", "ticket"])?;
            acknowledge_frame(app, u64_value(object, "ticket")?, output)?;
            Ok(snapshot(app))
        }
        "nack_frame" => {
            fields(
                object,
                &["op", "ticket", "error"],
                &["op", "ticket", "error"],
            )?;
            let ticket = u64_value(object, "ticket")?;
            let error = object
                .get("error")
                .and_then(serde_json::Value::as_str)
                .ok_or("FRAME_ERROR_REQUIRED")?;
            reject_frame(app, ticket, error)?;
            Ok(snapshot(app))
        }
        "play" => {
            fields(object, &["op", "sample"], &["op", "sample"])?;
            Err("MAC_LOCAL_MEDIA_UNSUPPORTED".into())
        }
        "stop" => {
            fields(object, &["op"], &["op"])?;
            disconnect(app)?;
            Ok(snapshot(app))
        }
        _ => Err("UNKNOWN_COMMAND".into()),
    }
}

async fn start_connect(
    app: &mut AppState,
    event_sender: &mpsc::Sender<Event>,
) -> Result<(), String> {
    if app.session.is_some() || app.pending_connect.is_some() {
        return Err("NETWORK_BUSY".into());
    }
    let pairing = match load_pairing() {
        Ok(pairing) => pairing,
        Err(error) => {
            app.lifecycle = Lifecycle::Error;
            app.error = Some(error.clone());
            return Err(error);
        }
    };
    app.generation = next_generation(app.generation)?;
    let generation = app.generation;
    app.lifecycle = Lifecycle::Connecting;
    app.error = None;
    let sender = event_sender.clone();
    app.pending_connect = Some(tokio::spawn(async move {
        let mut connector = Connector::default();
        match connector.connect(pairing, None).await {
            Ok(connection) => {
                let _ = sender
                    .send(Event::Connected {
                        generation,
                        connector,
                        connection,
                    })
                    .await;
            }
            Err(error) => {
                let _ = sender
                    .send(Event::ConnectFailed {
                        generation,
                        error: error.to_string(),
                    })
                    .await;
            }
        }
    }));
    Ok(())
}

fn disconnect(app: &mut AppState) -> Result<(), String> {
    let had_connection = app.session.take().is_some() || app.pending_connect.take().is_some();
    if had_connection {
        app.generation = next_generation(app.generation)?;
    }
    app.lifecycle = Lifecycle::Stopped;
    app.error = None;
    Ok(())
}

async fn declare_viewport(app: &mut AppState, viewport: ViewportDeclaration) -> Result<(), String> {
    let already_declared = app
        .session
        .as_ref()
        .ok_or("NETWORK_NOT_CONNECTED")?
        .viewport
        == Some(viewport)
        && !app.session.as_ref().unwrap().status.viewport_pending;
    if already_declared {
        return Ok(());
    }
    let result = {
        let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
        clear_session_error(session);
        session.connection.declare_viewport(viewport).await
    };
    let status = match result {
        Ok(status) => status,
        Err(error) => return Err(command_failure(app, error)),
    };
    let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
    session.viewport = Some(viewport);
    session.status = status;
    Ok(())
}

async fn submit_input(app: &mut AppState, input: Input, epoch: u64) -> Result<(), String> {
    let result = {
        let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
        if !input_ready(session) {
            return Err("DISPLAY_NOT_READY_OR_CONTROL_NOT_GRANTED".into());
        }
        let displayed = session.displayed.as_ref().ok_or("DISPLAY_NOT_READY")?;
        let frame = displayed.frame.clone();
        clear_session_error(session);
        session.connection.input(input, frame, epoch).await
    };
    if let Err(error) = result {
        return Err(command_failure(app, error));
    }
    let status = {
        let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
        session.connection.status().await
    };
    let status = match status {
        Ok(status) => status,
        Err(error) => return Err(command_failure(app, error)),
    };
    app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?.status = status;
    Ok(())
}

fn acknowledge_frame(
    app: &mut AppState,
    ticket: u64,
    output: &std_mpsc::Sender<Output>,
) -> Result<(), String> {
    let session = app.session.as_mut().ok_or("NETWORK_NOT_CONNECTED")?;
    let pending = session
        .pending
        .as_ref()
        .ok_or("STALE_DISPLAYED_FRAME_ACK")?;
    if pending.ticket != ticket {
        return Err("STALE_DISPLAYED_FRAME_ACK".into());
    }
    let pending = session.pending.take().ok_or("STALE_DISPLAYED_FRAME_ACK")?;
    let VideoPacket::AccessUnit { source, pts_us, .. } = &pending.video.packet else {
        return Err("INVALID_DISPLAYED_FRAME".into());
    };
    session.displayed = Some(DisplayedRecord {
        ticket,
        pts_us: *pts_us,
        frame: DisplayedFrame {
            generation: session.connection.generation,
            session_id: source.session_id.clone(),
            document_revision: source.document_revision,
            viewport_revision: source.viewport_revision,
        },
    });
    session.rendered_frames = session
        .rendered_frames
        .checked_add(1)
        .ok_or("FRAME_COUNT_EXHAUSTED")?;
    if let Some(next) = session.queued.take() {
        offer_frame(session, next, output)?;
    }
    Ok(())
}

fn reject_frame(app: &mut AppState, ticket: u64, error: &str) -> Result<(), String> {
    let session = app.session.as_ref().ok_or("NETWORK_NOT_CONNECTED")?;
    if session.pending.as_ref().map(|frame| frame.ticket) != Some(ticket) {
        return Err("STALE_DISPLAYED_FRAME_ACK".into());
    }
    app.session.take();
    app.lifecycle = Lifecycle::Error;
    app.error = Some(format!("NATIVE_DECODE_FAILED:{error}"));
    Ok(())
}

async fn refresh_status(app: &mut AppState) {
    let Some(session) = app.session.as_mut() else {
        return;
    };
    if let Some(error) = session.connection.failure.borrow().clone() {
        app.lifecycle = Lifecycle::Error;
        app.error = Some(error.to_string());
        return;
    }
    let result = session.connection.status().await;
    match result {
        Ok(status) => session.status = status,
        Err(error) => {
            app.lifecycle = Lifecycle::Error;
            app.error = Some(error.to_string());
            session.error = Some(error.to_string());
        }
    }
}

fn clear_session_error(session: &mut Session) {
    session.error = None;
}

fn command_failure(app: &mut AppState, error: Failure) -> String {
    let text = error.to_string();
    let fatal = matches!(
        &error,
        Failure::Transport(_) | Failure::Protocol(_) | Failure::Closed | Failure::OutcomeUnknown
    );
    if let Some(session) = app.session.as_mut() {
        session.error = Some(text.clone());
    }
    if fatal {
        app.lifecycle = Lifecycle::Error;
        app.error = Some(text.clone());
    }
    text
}

fn snapshot(app: &AppState) -> String {
    let (
        state,
        connection_state,
        released,
        codec,
        rendered_frames,
        control_mode,
        epoch,
        input_ready,
        pending,
        session_id,
        document_revision,
        viewport_revision,
        displayed_pts_us,
        displayed_ticket,
        displayed_document_revision,
        displayed_viewport_revision,
        session_error,
    ) = match app.session.as_ref() {
        Some(session) => {
            let mode = control_mode(session);
            let pending = if session.pending.is_some() || session.queued.is_some() {
                Some("frame_ack")
            } else {
                None
            };
            (
                ui_state(app, session.rendered_frames),
                connection_state(app),
                false,
                if session.rendered_frames > 0 {
                    "h264_annex_b"
                } else {
                    ""
                },
                session.rendered_frames,
                mode,
                session.status.control.epoch,
                input_ready(session),
                pending,
                Some(session.status.session_id.as_str()),
                Some(session.status.document_revision),
                Some(session.status.viewport_revision),
                session.displayed.as_ref().map(|frame| frame.pts_us),
                session.displayed.as_ref().map(|frame| frame.ticket),
                session
                    .displayed
                    .as_ref()
                    .map(|frame| frame.frame.document_revision),
                session
                    .displayed
                    .as_ref()
                    .map(|frame| frame.frame.viewport_revision),
                session.error.as_deref(),
            )
        }
        None => (
            no_session_state(app),
            no_session_connection_state(app),
            app.pending_connect.is_none(),
            "",
            0,
            "observe",
            0,
            false,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    };
    let mut value = serde_json::json!({
        "state": state,
        "generation": app.generation,
        "renderedFrames": rendered_frames,
        "released": released,
        "codec": codec,
        "error": session_error.or(app.error.as_deref()),
        "source": "network",
        "connectionState": connection_state,
        "controlMode": control_mode,
        "pending": pending,
        "networkConfigured": app.pairing_available,
        "inputReady": input_ready,
        "epoch": epoch,
        "sessionId": session_id,
        "documentRevision": document_revision,
        "viewportRevision": viewport_revision,
        "displayedPtsUs": displayed_pts_us,
        "displayedTicket": displayed_ticket,
        "displayedDocumentRevision": displayed_document_revision,
        "displayedViewportRevision": displayed_viewport_revision,
    });
    omit_optional_nulls(&mut value);
    value.to_string()
}

fn omit_optional_nulls(value: &mut serde_json::Value) {
    if let Some(object) = value.as_object_mut() {
        for field in [
            "sessionId",
            "documentRevision",
            "viewportRevision",
            "displayedPtsUs",
            "displayedTicket",
            "displayedDocumentRevision",
            "displayedViewportRevision",
        ] {
            if object
                .get(field)
                .map(serde_json::Value::is_null)
                .unwrap_or(false)
            {
                object.remove(field);
            }
        }
    }
}

fn ui_state(app: &AppState, rendered_frames: u64) -> &'static str {
    match app.lifecycle {
        Lifecycle::Idle => "idle",
        Lifecycle::Connecting => "starting",
        Lifecycle::Connected if rendered_frames > 0 => "playing",
        Lifecycle::Connected => "starting",
        Lifecycle::Stopped => "stopped",
        Lifecycle::Error => "error",
    }
}

fn no_session_state(app: &AppState) -> &'static str {
    match app.lifecycle {
        Lifecycle::Idle => "idle",
        Lifecycle::Stopped => "stopped",
        Lifecycle::Connecting => "starting",
        Lifecycle::Connected => "starting",
        Lifecycle::Error => "error",
    }
}

fn connection_state(app: &AppState) -> &'static str {
    match app.lifecycle {
        Lifecycle::Connected => "connected",
        Lifecycle::Error => "error",
        Lifecycle::Connecting => "connecting",
        Lifecycle::Stopped => "stopped",
        Lifecycle::Idle => "idle",
    }
}

fn no_session_connection_state(app: &AppState) -> &'static str {
    connection_state(app)
}

fn control_mode(session: &Session) -> &'static str {
    match session.status.control.phase {
        ControlPhase::Human { attachment_id }
            if session.status.attachment_id == Some(attachment_id) =>
        {
            "control"
        }
        ControlPhase::Waiting { attachment_id }
            if session.status.attachment_id == Some(attachment_id) =>
        {
            "waiting"
        }
        _ => "observe",
    }
}

fn input_ready(session: &Session) -> bool {
    let Some(displayed) = session.displayed.as_ref() else {
        return false;
    };
    if session.pending.is_some()
        || session.queued.is_some()
        || session.viewport.is_none()
        || session.status.viewport_pending
    {
        return false;
    }
    if control_mode(session) != "control" || displayed.frame.session_id != session.status.session_id
    {
        return false;
    }
    displayed.frame.document_revision == session.status.document_revision
        && displayed.frame.viewport_revision == session.status.viewport_revision
}

fn fields(
    object: &serde_json::Map<String, serde_json::Value>,
    allowed: &[&str],
    required: &[&str],
) -> Result<(), String> {
    if object.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err("UNKNOWN_COMMAND_FIELD".into());
    }
    if required.iter().any(|key| !object.contains_key(*key)) {
        return Err("MISSING_COMMAND_FIELD".into());
    }
    Ok(())
}

fn u64_value(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<u64, String> {
    object
        .get(key)
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| format!("INVALID_{key}"))
}

fn bounded_dimension(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<u32, String> {
    let value = u64_value(object, key)?;
    if !(1..=4096).contains(&value) {
        return Err("INVALID_VIEWPORT".into());
    }
    Ok(value as u32)
}

fn finite_value(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<f64, String> {
    let value = object
        .get(key)
        .and_then(serde_json::Value::as_f64)
        .ok_or_else(|| format!("INVALID_{key}"))?;
    if !value.is_finite() || value.abs() > 1_000_000.0 {
        return Err(format!("INVALID_{key}"));
    }
    Ok(value)
}

fn next_generation(value: u64) -> Result<u64, String> {
    value
        .checked_add(1)
        .ok_or_else(|| "GENERATION_EXHAUSTED".into())
}

fn rejection(error: &str) -> String {
    serde_json::json!({"rejection": error}).to_string()
}

fn account_command(object: &serde_json::Map<String, serde_json::Value>) -> Result<String, String> {
    let op = object
        .get("op")
        .and_then(serde_json::Value::as_str)
        .ok_or("MISSING_COMMAND")?;
    match op {
        "account_status" => {
            fields(object, &["op"], &["op"])?;
            Ok(account_snapshot("signed_out", None))
        }
        "account_login" => {
            fields(
                object,
                &["op", "username", "password"],
                &["op", "username", "password"],
            )?;
            account_text(object, "username", 64)?;
            account_text(object, "password", 1024)?;
            Ok(account_snapshot("error", Some("ACCOUNT_NOT_CONFIGURED")))
        }
        "account_register_device" => {
            fields(object, &["op", "name"], &["op", "name"])?;
            account_text(object, "name", 64)?;
            Ok(account_snapshot("error", Some("ACCOUNT_NOT_CONFIGURED")))
        }
        "account_refresh" | "account_logout" => {
            fields(object, &["op"], &["op"])?;
            Ok(account_snapshot("error", Some("ACCOUNT_NOT_CONFIGURED")))
        }
        _ => Err("UNKNOWN_ACCOUNT_COMMAND".into()),
    }
}

fn account_text(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    max: usize,
) -> Result<(), String> {
    let value = object
        .get(key)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("INVALID_ACCOUNT_{key}"))?;
    if value.is_empty() || value.len() > max {
        return Err(format!("INVALID_ACCOUNT_{key}"));
    }
    Ok(())
}

fn account_snapshot(state: &str, error: Option<&str>) -> String {
    serde_json::json!({
        "accountState": state,
        "generation": 0,
        "pending": false,
        "error": error,
        "expiresAtMs": 0,
        "deviceId": null,
        "directoryState": "empty",
        "hosts": [],
    })
    .to_string()
}

fn pairing_dir() -> Option<PathBuf> {
    std::env::var_os("AGENTBROWSER_MAC_PAIRING").map(PathBuf::from)
}

fn load_pairing() -> Result<Pairing, String> {
    let root = pairing_dir().ok_or_else(|| "PAIRING_NOT_CONFIGURED".to_string())?;
    if !root.is_dir() {
        return Err("PAIRING_NOT_CONFIGURED".into());
    }
    let endpoint = String::from_utf8(read_pairing_file(&root, "endpoint.txt", 4096)?)
        .map_err(|_| "PAIRING_ENDPOINT_ENCODING".to_string())?;
    Ok(Pairing {
        endpoint: endpoint.trim().to_owned(),
        server_ca_der: read_pairing_file(&root, "ca.der", 65536)?,
        client_cert_der: read_pairing_file(&root, "client.der", 65536)?,
        client_key_pkcs8_der: read_pairing_file(&root, "key.der", 65536)?,
    })
}

fn read_pairing_file(root: &Path, name: &str, max: u64) -> Result<Vec<u8>, String> {
    let path = root.join(name);
    let metadata = std::fs::metadata(&path).map_err(|_| format!("PAIRING_FILE_MISSING:{name}"))?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > max {
        return Err(format!("PAIRING_FILE_INVALID:{name}"));
    }
    #[cfg(unix)]
    if name == "key.der"
        && std::os::unix::fs::PermissionsExt::mode(&metadata.permissions()) & 0o077 != 0
    {
        return Err("PAIRING_KEY_PERMISSIONS".into());
    }
    std::fs::read(path).map_err(|_| format!("PAIRING_FILE_READ_FAILED:{name}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_command_fields_are_closed() {
        let value = serde_json::json!({"op":"connect", "extra":true});
        assert_eq!(
            fields(value.as_object().unwrap(), &["op"], &["op"]),
            Err("UNKNOWN_COMMAND_FIELD".into())
        );
    }

    #[test]
    fn navigate_command_requires_only_typed_fields() {
        let value = serde_json::json!({
            "op":"navigate",
            "epoch":7,
            "url":"data:text/html,navigate"
        });
        assert!(fields(
            value.as_object().unwrap(),
            &["op", "epoch", "url"],
            &["op", "epoch", "url"]
        )
        .is_ok());
        let extra = serde_json::json!({
            "op":"navigate",
            "epoch":7,
            "url":"data:text/html,navigate",
            "metadata":"control must not be mirrored"
        });
        assert_eq!(
            fields(
                extra.as_object().unwrap(),
                &["op", "epoch", "url"],
                &["op", "epoch", "url"]
            ),
            Err("UNKNOWN_COMMAND_FIELD".into())
        );
    }

    #[test]
    fn nonfinite_input_is_rejected() {
        let value = serde_json::json!({"x": "not-a-number"});
        assert_eq!(
            finite_value(value.as_object().unwrap(), "x"),
            Err("INVALID_x".into())
        );
    }

    #[test]
    fn probe_snapshot_omits_null_optional_fields() {
        let mut value = serde_json::json!({
            "error": null,
            "sessionId": null,
            "documentRevision": null,
            "viewportRevision": null,
            "displayedPtsUs": null,
            "displayedTicket": null,
            "displayedDocumentRevision": null,
            "displayedViewportRevision": null,
        });
        omit_optional_nulls(&mut value);
        assert_eq!(value.get("error"), Some(&serde_json::Value::Null));
        for field in [
            "sessionId",
            "documentRevision",
            "viewportRevision",
            "displayedPtsUs",
            "displayedTicket",
            "displayedDocumentRevision",
            "displayedViewportRevision",
        ] {
            assert!(
                value.get(field).is_none(),
                "{field} must be omitted when unavailable"
            );
        }
    }

    #[test]
    fn account_status_snapshot_matches_client_domain_abi() {
        let command = serde_json::json!({"op":"account_status"});
        let value = account_command(command.as_object().unwrap()).expect("account status");
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&value).unwrap(),
            serde_json::json!({
                "accountState": "signed_out",
                "generation": 0,
                "pending": false,
                "error": null,
                "expiresAtMs": 0,
                "deviceId": null,
                "directoryState": "empty",
                "hosts": [],
            })
        );
    }

    #[test]
    fn unconfigured_account_mutations_return_typed_errors() {
        let commands = [
            serde_json::json!({"op":"account_login", "username":"alice", "password":"secret"}),
            serde_json::json!({"op":"account_register_device", "name":"Mac"}),
            serde_json::json!({"op":"account_refresh"}),
            serde_json::json!({"op":"account_logout"}),
        ];
        for command in commands {
            let value = account_command(command.as_object().unwrap()).expect("account mutation");
            let snapshot = serde_json::from_str::<serde_json::Value>(&value).unwrap();
            assert_eq!(snapshot["accountState"], "error");
            assert_eq!(snapshot["error"], "ACCOUNT_NOT_CONFIGURED");
            assert_eq!(snapshot["pending"], false);
            assert_eq!(snapshot["hosts"], serde_json::json!([]));
        }
    }
}
