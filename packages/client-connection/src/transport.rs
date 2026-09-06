use crate::{
    decode_video,
    protocol::{
        Command, Mode, Operation, Request, Response, ResultValue, SessionStatus,
        ViewportDeclaration,
    },
    relay_backend::RelayBackend,
    webrtc::{WebRtcBackend, WebRtcConfig},
    Failure, MediaSequence, Video,
};
use futures_util::{SinkExt, StreamExt};
use std::{future::Future, pin::Pin, sync::Arc, time::Duration};
use tokio::{
    net::TcpStream,
    sync::{mpsc, oneshot, watch},
};
use tokio_rustls::{
    rustls::{
        self,
        pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer, ServerName},
    },
    TlsConnector,
};
use tokio_tungstenite::{
    client_async_with_config,
    tungstenite::{client::IntoClientRequest, protocol::WebSocketConfig, Message},
    WebSocketStream,
};

pub(crate) type Socket = WebSocketStream<tokio_rustls::client::TlsStream<TcpStream>>;
type Answer = oneshot::Sender<Result<ResultValue, Failure>>;
type Work = (Action, Answer);

pub(crate) type BackendFuture<'a, T> =
    Pin<Box<dyn Future<Output = Result<T, Failure>> + Send + 'a>>;

pub(crate) trait Backend: Send {
    fn request<'a>(&'a mut self, request: Request) -> BackendFuture<'a, Response>;
    /// Receive one complete video item. The future must be cancellation-safe:
    /// dropping it before completion cannot consume a partial protocol item or
    /// leave the next call positioned in the middle of a frame.
    fn next_video<'a>(&'a mut self) -> BackendFuture<'a, Video>;
    fn close<'a>(&'a mut self) -> BackendFuture<'a, ()>;
}

/// Explicit prepaired direct endpoint; credentials remain in the native owner.
pub struct Pairing {
    pub endpoint: String,
    pub server_ca_der: Vec<u8>,
    pub client_cert_der: Vec<u8>,
    pub client_key_pkcs8_der: Vec<u8>,
}

#[derive(Clone, Debug)]
pub struct DisplayedFrame {
    pub generation: u64,
    pub session_id: String,
    pub document_revision: u64,
    pub viewport_revision: u64,
}

pub enum Input {
    Click {
        x: f64,
        y: f64,
    },
    Text(String),
    Scroll {
        x: f64,
        y: f64,
        delta_x: f64,
        delta_y: f64,
    },
}

enum Action {
    Status,
    DeclareViewport(ViewportDeclaration),
    Takeover(u64),
    Release(u64),
    Navigate(String, u64),
    Input(Input, DisplayedFrame, u64),
}

pub struct Connector {
    generation: watch::Sender<u64>,
}
impl Default for Connector {
    fn default() -> Self {
        Self {
            generation: watch::channel(0).0,
        }
    }
}

pub struct Connection {
    pub generation: u64,
    pub initial_status: SessionStatus,
    pub media: watch::Receiver<Option<Arc<Video>>>,
    pub failure: watch::Receiver<Option<Failure>>,
    work: mpsc::Sender<Work>,
    stop: Option<oneshot::Sender<()>>,
}

impl Drop for Connection {
    fn drop(&mut self) {
        self.stop.take();
    }
}

impl Connection {
    async fn call(&self, action: Action) -> Result<ResultValue, Failure> {
        if let Some(error) = self.failure.borrow().clone() {
            return Err(error);
        }
        let mutating = !matches!(action, Action::Status);
        let (send, recv) = oneshot::channel();
        self.work
            .send((action, send))
            .await
            .map_err(|_| Failure::Closed)?;
        recv.await.unwrap_or(Err(if mutating {
            Failure::OutcomeUnknown
        } else {
            Failure::Closed
        }))
    }
    pub async fn status(&self) -> Result<SessionStatus, Failure> {
        status(self.call(Action::Status).await?)
    }
    pub async fn declare_viewport(
        &self,
        viewport: ViewportDeclaration,
    ) -> Result<SessionStatus, Failure> {
        status(self.call(Action::DeclareViewport(viewport)).await?)
    }
    pub async fn takeover(&self, epoch: u64) -> Result<SessionStatus, Failure> {
        status(self.call(Action::Takeover(epoch)).await?)
    }
    pub async fn release(&self, epoch: u64) -> Result<SessionStatus, Failure> {
        status(self.call(Action::Release(epoch)).await?)
    }
    pub async fn navigate(&self, url: String, epoch: u64) -> Result<SessionStatus, Failure> {
        status(self.call(Action::Navigate(url, epoch)).await?)
    }
    pub async fn input(
        &self,
        input: Input,
        displayed: DisplayedFrame,
        epoch: u64,
    ) -> Result<(), Failure> {
        match self.call(Action::Input(input, displayed, epoch)).await? {
            ResultValue::Input { .. } => Ok(()),
            _ => Err(Failure::Protocol("Expected input receipt".into())),
        }
    }
    /// Closing the native connection releases sockets; it does not close Host.
    pub fn close(mut self) {
        self.stop.take();
    }
}

fn transport(error: impl std::fmt::Display) -> Failure {
    Failure::Transport(error.to_string())
}
pub(crate) fn status(value: ResultValue) -> Result<SessionStatus, Failure> {
    match value {
        ResultValue::Status(status) => Ok(status),
        _ => Err(Failure::Protocol("Expected status".into())),
    }
}

impl Connector {
    pub async fn connect(
        &mut self,
        pairing: Pairing,
        viewport: Option<ViewportDeclaration>,
    ) -> Result<Connection, Failure> {
        let generation = self.begin_generation()?;
        let (control, media, initial_status, id) =
            tokio::time::timeout(Duration::from_secs(15), establish(pairing, viewport))
                .await
                .map_err(transport)??;
        Ok(spawn_connection(
            generation,
            initial_status,
            DirectBackend { control, media },
            id,
            self.generation.subscribe(),
        ))
    }

    /// Connect the same Host attachment through the explicitly negotiated
    /// WebRTC H.264/DataChannel backend. WSS remains bootstrap/signaling only.
    pub async fn connect_webrtc(
        &mut self,
        pairing: Pairing,
        viewport: Option<ViewportDeclaration>,
    ) -> Result<Connection, Failure> {
        self.connect_webrtc_with_config(pairing, viewport, WebRtcConfig::default())
            .await
    }

    pub async fn connect_webrtc_with_config(
        &mut self,
        pairing: Pairing,
        viewport: Option<ViewportDeclaration>,
        config: WebRtcConfig,
    ) -> Result<Connection, Failure> {
        let generation = self.begin_generation()?;
        let bootstrap = tokio::time::timeout(
            Duration::from_secs(15),
            establish_control(pairing, viewport),
        )
        .await
        .map_err(transport)??;
        let (backend, id) = tokio::time::timeout(
            Duration::from_secs(30),
            WebRtcBackend::connect(bootstrap.control, &bootstrap.status, bootstrap.id, config),
        )
        .await
        .map_err(transport)??;
        Ok(spawn_connection(
            generation,
            bootstrap.status,
            backend,
            id,
            self.generation.subscribe(),
        ))
    }

    /// Connect an authenticated Relay v2 tunnel through the same browser
    /// action and media pump used by direct and WebRTC backends.
    pub async fn connect_relay(
        &mut self,
        relay: crate::relay::RelayClient,
        device: crate::relay::RegisteredDevice,
        host_id: &str,
        session_id: &str,
        peer: crate::relay::RelayPeerBinding,
        tls: crate::relay::RelayTlsClientIdentity,
        viewport: Option<ViewportDeclaration>,
    ) -> Result<Connection, Failure> {
        let generation = self.begin_generation()?;
        let (backend, initial_status, id) = tokio::time::timeout(
            Duration::from_secs(60),
            RelayBackend::connect(relay, device, host_id, session_id, peer, tls, viewport),
        )
        .await
        .map_err(transport)??;
        Ok(spawn_connection(
            generation,
            initial_status,
            backend,
            id,
            self.generation.subscribe(),
        ))
    }

    fn begin_generation(&self) -> Result<u64, Failure> {
        let generation = self
            .generation
            .borrow()
            .checked_add(1)
            .ok_or_else(|| Failure::Protocol("Connection generation exhausted".into()))?;
        // Fence the previous sockets before performing a new handshake, including
        // a failing handshake. No input replay or implicit route fallback.
        self.generation.send_replace(generation);
        Ok(generation)
    }
}

fn spawn_connection<B: Backend + 'static>(
    generation: u64,
    initial_status: SessionStatus,
    backend: B,
    mut id: u64,
    mut changed: watch::Receiver<u64>,
) -> Connection {
    let session = initial_status.session_id.clone();
    let attachment = initial_status.attachment_id;
    let (work, mut requests) = mpsc::channel::<Work>(1);
    let (latest, media) = watch::channel(None);
    let (failed, failure) = watch::channel(None);
    let (stop, mut stopped) = oneshot::channel();
    tokio::spawn(async move {
        let mut backend = backend;
        let mut sequence = MediaSequence::new(session.clone());
        let result: Result<(), Failure> = loop {
            tokio::select! {
                biased;
                _ = changed.changed() => break Err(Failure::Closed),
                _ = &mut stopped => break Err(Failure::Closed),
                Some((action, answer)) = requests.recv() => {
                    // A cancelled request that never started cannot mutate Host.
                    if answer.is_closed() { continue; }
                    let result = execute(&mut backend, &mut id, action, generation, &session, attachment).await;
                    let fatal = matches!(&result, Err(Failure::Transport(_) | Failure::Protocol(_) | Failure::OutcomeUnknown | Failure::Closed));
                    let error = result.as_ref().err().cloned();
                    let _ = answer.send(result);
                    if fatal { break Err(error.unwrap()); }
                }
                value = backend.next_video() => {
                    let video = match value {
                        Ok(video) => video,
                        Err(error) => break Err(error),
                    };
                    if let Err(error) = sequence.accept(&video) {
                        break Err(error);
                    }
                    let closed = matches!(video.packet, crate::protocol::VideoPacket::Closed { .. });
                    latest.send_replace(Some(Arc::new(video)));
                    if closed { break Err(Failure::Closed); }
                }
                else => break Err(Failure::Closed),
            }
        };
        let close_result = backend.close().await;
        let failure = result.err().or_else(|| close_result.err());
        failed.send_replace(failure);
        // Dropping one backend closes its entire transport; stale control cannot
        // retain a human holder after a media error or generation change.
    });
    Connection {
        generation,
        initial_status,
        media,
        failure,
        work,
        stop: Some(stop),
    }
}

struct DirectBackend {
    control: Socket,
    media: Socket,
}

impl Backend for DirectBackend {
    fn request<'a>(&'a mut self, request: Request) -> BackendFuture<'a, Response> {
        Box::pin(direct_request(&mut self.control, request))
    }

    fn next_video<'a>(&'a mut self) -> BackendFuture<'a, Video> {
        Box::pin(async move {
            loop {
                match next(&mut self.media).await? {
                    Message::Binary(bytes) => return decode_video(&bytes),
                    _ => return Err(Failure::Protocol("Expected media binary".into())),
                }
            }
        })
    }

    fn close<'a>(&'a mut self) -> BackendFuture<'a, ()> {
        Box::pin(async move {
            let control = self.control.close(None).await.map_err(transport);
            let media = self.media.close(None).await.map_err(transport);
            control.and(media)
        })
    }
}

struct Bootstrap {
    base: url::Url,
    tls: TlsConnector,
    control: Socket,
    media_token: Option<String>,
    status: SessionStatus,
    id: u64,
}

async fn establish(
    pairing: Pairing,
    viewport: Option<ViewportDeclaration>,
) -> Result<(Socket, Socket, SessionStatus, u64), Failure> {
    let bootstrap = establish_control(pairing, viewport).await?;
    let token = bootstrap
        .media_token
        .as_deref()
        .ok_or_else(|| Failure::Protocol("Missing media binding".into()))?;
    let (media, _) = open(&bootstrap.base, "/media", &bootstrap.tls, Some(token)).await?;
    Ok((bootstrap.control, media, bootstrap.status, bootstrap.id))
}

async fn establish_control(
    pairing: Pairing,
    viewport: Option<ViewportDeclaration>,
) -> Result<Bootstrap, Failure> {
    let base = url::Url::parse(&pairing.endpoint).map_err(transport)?;
    if base.scheme() != "wss"
        || base.host_str().is_none()
        || !base.username().is_empty()
        || base.password().is_some()
        || base.query().is_some()
        || base.fragment().is_some()
        || !matches!(base.path(), "" | "/")
    {
        return Err(Failure::Protocol(
            "Expected plain wss endpoint origin".into(),
        ));
    }
    let mut roots = rustls::RootCertStore::empty();
    roots
        .add(CertificateDer::from(pairing.server_ca_der))
        .map_err(transport)?;
    let config = rustls::ClientConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .map_err(transport)?
    .with_root_certificates(roots)
    .with_client_auth_cert(
        vec![CertificateDer::from(pairing.client_cert_der)],
        PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(pairing.client_key_pkcs8_der)),
    )
    .map_err(transport)?;
    let tls = TlsConnector::from(Arc::new(config));
    let (mut control, token) = open(&base, "/control", &tls, None).await?;
    let session = match response(&mut control).await? {
        Response::Ready {
            version: 4,
            session_id,
        } if !session_id.is_empty() => session_id,
        _ => return Err(Failure::Protocol("Expected Host protocol version 4".into())),
    };
    let mut id = 0;
    let attached = status(
        exchange(
            &mut control,
            &mut id,
            Command::Attach {
                mode: Mode::Observe,
                viewport,
            },
            None,
        )
        .await?,
    )?;
    if attached.session_id != session
        || attached.attachment_id.is_none()
        || attached.mode != Some(Mode::Observe)
    {
        return Err(Failure::Protocol("Invalid observation attachment".into()));
    }
    Ok(Bootstrap {
        base,
        tls,
        control,
        media_token: token,
        status: attached,
        id,
    })
}

async fn open(
    base: &url::Url,
    path: &str,
    tls: &TlsConnector,
    token: Option<&str>,
) -> Result<(Socket, Option<String>), Failure> {
    let host = base.host_str().unwrap().trim_matches(['[', ']']);
    let tcp = TcpStream::connect((host, base.port().unwrap_or(443)))
        .await
        .map_err(transport)?;
    let stream = tls
        .connect(
            ServerName::try_from(host.to_owned()).map_err(transport)?,
            tcp,
        )
        .await
        .map_err(transport)?;
    let mut url = base.clone();
    url.set_path(path);
    let mut request = url.as_str().into_client_request().map_err(transport)?;
    if let Some(token) = token {
        request.headers_mut().insert(
            "authorization",
            format!("Bearer {token}").parse().map_err(transport)?,
        );
    }
    let config = WebSocketConfig::default()
        .max_message_size(Some(4 * 1024 * 1024 + 4099))
        .max_frame_size(Some(4 * 1024 * 1024 + 4099));
    let (socket, reply) = client_async_with_config(request, stream, Some(config))
        .await
        .map_err(transport)?;
    let token = reply
        .headers()
        .get("x-obscura-media-token")
        .map(|value| value.to_str().map(str::to_owned))
        .transpose()
        .map_err(transport)?;
    Ok((socket, token))
}

async fn next(socket: &mut Socket) -> Result<Message, Failure> {
    loop {
        match socket
            .next()
            .await
            .ok_or(Failure::Closed)?
            .map_err(transport)?
        {
            Message::Ping(data) => {
                tokio::time::timeout(Duration::from_secs(2), socket.send(Message::Pong(data)))
                    .await
                    .map_err(transport)?
                    .map_err(transport)?;
            }
            Message::Pong(_) => {}
            Message::Close(_) => return Err(Failure::Closed),
            message => return Ok(message),
        }
    }
}

async fn response(socket: &mut Socket) -> Result<Response, Failure> {
    match next(socket).await? {
        Message::Text(text) if text.len() <= 1024 * 1024 => {
            serde_json::from_str(&text).map_err(|error| Failure::Protocol(error.to_string()))
        }
        _ => Err(Failure::Protocol("Expected bounded control JSON".into())),
    }
}

async fn direct_request(socket: &mut Socket, request: Request) -> Result<Response, Failure> {
    let bytes = serde_json::to_string(&request).map_err(transport)?;
    if bytes.len() > 1024 * 1024 {
        return Err(Failure::Protocol("Control request too large".into()));
    }
    tokio::time::timeout(Duration::from_secs(17), async {
        socket
            .send(Message::Text(bytes.into()))
            .await
            .map_err(transport)?;
        response(socket).await
    })
    .await
    .map_err(transport)?
}

pub(crate) fn response_value(request_id: u64, response: Response) -> Result<ResultValue, Failure> {
    match response {
        Response::Result { id, value } if id == request_id => Ok(value),
        Response::Error { id, code, message } if id == request_id => {
            Err(Failure::Host { code, message })
        }
        _ => Err(Failure::Protocol("Control reply ID mismatch".into())),
    }
}

async fn exchange(
    socket: &mut Socket,
    id: &mut u64,
    command: Command,
    operation: Option<Operation>,
) -> Result<ResultValue, Failure> {
    *id = id
        .checked_add(1)
        .ok_or_else(|| Failure::Protocol("Request ID exhausted".into()))?;
    let mutation = !matches!(command, Command::Status {} | Command::Attach { .. });
    let request = Request {
        id: *id,
        command,
        operation,
    };
    let result = direct_request(socket, request.clone())
        .await
        .and_then(|response| response_value(request.id, response));
    match result {
        Err(Failure::Host { code, message }) => Err(Failure::Host { code, message }),
        Err(_) if mutation => Err(Failure::OutcomeUnknown),
        value => value,
    }
}

async fn exchange_backend<B: Backend>(
    backend: &mut B,
    id: &mut u64,
    command: Command,
    operation: Option<Operation>,
) -> Result<ResultValue, Failure> {
    *id = id
        .checked_add(1)
        .ok_or_else(|| Failure::Protocol("Request ID exhausted".into()))?;
    let mutation = !matches!(command, Command::Status {} | Command::Attach { .. });
    let request = Request {
        id: *id,
        command,
        operation,
    };
    let result = tokio::time::timeout(Duration::from_secs(17), backend.request(request.clone()))
        .await
        .map_err(transport)
        .and_then(|value| value)
        .and_then(|response| response_value(request.id, response));
    match result {
        Err(Failure::Host { code, message }) => Err(Failure::Host { code, message }),
        Err(_) if mutation => Err(Failure::OutcomeUnknown),
        value => value,
    }
}

async fn execute<B: Backend>(
    backend: &mut B,
    id: &mut u64,
    action: Action,
    generation: u64,
    session: &str,
    attachment: Option<u64>,
) -> Result<ResultValue, Failure> {
    let (command, operation) = match action {
        Action::Status => (Command::Status {}, None),
        Action::DeclareViewport(viewport) => (Command::DeclareViewport { viewport }, None),
        Action::Takeover(epoch) => (Command::RequestTakeover { epoch }, None),
        Action::Release(epoch) => (Command::ReleaseControl { epoch }, None),
        Action::Navigate(url, epoch) => {
            let current = status(exchange_backend(backend, id, Command::Status {}, None).await?)?;
            if current.session_id != session || current.attachment_id != attachment {
                return Err(Failure::Protocol("Control attachment changed".into()));
            }
            if current.viewport_pending {
                return Err(Failure::Host {
                    code: "VIEWPORT_PENDING".into(),
                    message: "Viewport layout is pending".into(),
                });
            }
            (
                Command::Navigate { url },
                Some(Operation {
                    session_id: session.into(),
                    attachment_id: attachment.unwrap(),
                    sequence: current.next_sequence,
                    control_epoch: epoch,
                    viewport_revision: current.viewport_revision,
                    document_revision: current.document_revision,
                }),
            )
        }
        Action::Input(input, frame, epoch) => {
            if frame.generation != generation || frame.session_id != session {
                return Err(Failure::Protocol(
                    "Input belongs to another displayed connection".into(),
                ));
            }
            let current = status(exchange_backend(backend, id, Command::Status {}, None).await?)?;
            if current.session_id != session || current.attachment_id != attachment {
                return Err(Failure::Protocol("Control attachment changed".into()));
            }
            if current.viewport_pending {
                return Err(Failure::Host {
                    code: "VIEWPORT_PENDING".into(),
                    message: "Viewport layout is pending".into(),
                });
            }
            let command = match input {
                Input::Click { x, y } => Command::Click { x, y },
                Input::Text(text) => Command::InputText { text },
                Input::Scroll {
                    x,
                    y,
                    delta_x,
                    delta_y,
                } => Command::Scroll {
                    x,
                    y,
                    delta_x,
                    delta_y,
                },
            };
            (
                command,
                Some(Operation {
                    session_id: session.into(),
                    attachment_id: attachment.unwrap(),
                    sequence: current.next_sequence,
                    control_epoch: epoch,
                    viewport_revision: frame.viewport_revision,
                    document_revision: frame.document_revision,
                }),
            )
        }
    };
    let value = exchange_backend(backend, id, command, operation).await?;
    if let ResultValue::Status(status) = &value {
        if status.session_id != session || status.attachment_id != attachment {
            return Err(Failure::Protocol("Control attachment changed".into()));
        }
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct PendingVideoBackend {
        status: SessionStatus,
        requests: Arc<AtomicUsize>,
        closed: Arc<AtomicUsize>,
    }

    impl Backend for PendingVideoBackend {
        fn request<'a>(&'a mut self, request: Request) -> BackendFuture<'a, Response> {
            self.requests.fetch_add(1, Ordering::Relaxed);
            let status = self.status.clone();
            Box::pin(async move {
                Ok(Response::Result {
                    id: request.id,
                    value: ResultValue::Status(status),
                })
            })
        }

        fn next_video<'a>(&'a mut self) -> BackendFuture<'a, Video> {
            Box::pin(std::future::pending())
        }

        fn close<'a>(&'a mut self) -> BackendFuture<'a, ()> {
            self.closed.fetch_add(1, Ordering::Relaxed);
            Box::pin(async { Ok(()) })
        }
    }

    fn pending_video_status() -> SessionStatus {
        SessionStatus {
            session_id: "session".into(),
            attachment_id: Some(7),
            mode: Some(Mode::Observe),
            attachments: 1,
            agent_attached: false,
            operation_running: false,
            control: crate::protocol::Control {
                epoch: 0,
                phase: crate::protocol::ControlPhase::Agent,
            },
            fault: None,
            next_sequence: 1,
            viewport_revision: 1,
            document_revision: 1,
            viewport: None,
            viewport_owner: None,
            viewport_pending: false,
        }
    }

    #[tokio::test]
    async fn action_is_serviced_while_video_receive_is_pending() {
        let requests = Arc::new(AtomicUsize::new(0));
        let closed = Arc::new(AtomicUsize::new(0));
        let status = pending_video_status();
        let (_generation_sender, changed) = watch::channel(1);
        let connection = spawn_connection(
            1,
            status.clone(),
            PendingVideoBackend {
                status: status.clone(),
                requests: Arc::clone(&requests),
                closed: Arc::clone(&closed),
            },
            0,
            changed,
        );

        let received = tokio::time::timeout(Duration::from_secs(1), connection.status())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(received.session_id, status.session_id);
        assert_eq!(requests.load(Ordering::Relaxed), 1);

        let mut failure = connection.failure.clone();
        connection.close();
        tokio::time::timeout(
            Duration::from_secs(1),
            failure.wait_for(|value| value.is_some()),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(closed.load(Ordering::Relaxed), 1);
    }

    async fn pair() -> (
        Socket,
        WebSocketStream<tokio_rustls::server::TlsStream<TcpStream>>,
    ) {
        let cert = rcgen::generate_simple_self_signed(vec!["localhost".into()]).unwrap();
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let server = rustls::ServerConfig::builder_with_provider(provider.clone())
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(
                vec![cert.cert.der().clone()],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(cert.key_pair.serialize_der())),
            )
            .unwrap();
        let mut roots = rustls::RootCertStore::empty();
        roots.add(cert.cert.der().clone()).unwrap();
        let client = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let tls = tokio_rustls::TlsAcceptor::from(Arc::new(server))
                .accept(tcp)
                .await
                .unwrap();
            tokio_tungstenite::accept_async(tls).await.unwrap()
        };
        let client = async move {
            let tcp = TcpStream::connect(address).await.unwrap();
            let tls = TlsConnector::from(Arc::new(client))
                .connect(ServerName::try_from("localhost").unwrap(), tcp)
                .await
                .unwrap();
            tokio_tungstenite::client_async("wss://localhost/control", tls)
                .await
                .unwrap()
                .0
        };
        tokio::join!(client, server)
    }

    #[tokio::test]
    async fn lost_mutation_reply_is_unknown_without_retry() {
        let (mut client, mut server) = pair().await;
        let peer = async move {
            let message = server.next().await.unwrap().unwrap();
            let request: Request = serde_json::from_str(message.to_text().unwrap()).unwrap();
            assert_eq!(request.id, 1);
            assert!(matches!(
                request.command,
                Command::RequestTakeover { epoch: 7 }
            ));
            // Fault injection: accepted wire request, connection lost before reply.
            drop(server);
        };
        let caller = async move {
            let mut id = 0;
            assert!(matches!(
                exchange(
                    &mut client,
                    &mut id,
                    Command::RequestTakeover { epoch: 7 },
                    None
                )
                .await,
                Err(Failure::OutcomeUnknown)
            ));
            assert_eq!(id, 1, "No second request or retry");
        };
        tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(peer, caller);
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn mismatched_read_reply_is_protocol_failure() {
        let (mut client, mut server) = pair().await;
        let peer = async move {
            server.next().await.unwrap().unwrap();
            let error = Response::Error {
                id: 99,
                code: "WRONG".into(),
                message: "wrong correlation".into(),
            };
            server
                .send(Message::text(serde_json::to_string(&error).unwrap()))
                .await
                .unwrap();
        };
        let caller = async move {
            assert!(matches!(
                exchange(&mut client, &mut 0, Command::Status {}, None).await,
                Err(Failure::Protocol(_))
            ));
        };
        tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(peer, caller);
        })
        .await
        .unwrap();
    }
}
