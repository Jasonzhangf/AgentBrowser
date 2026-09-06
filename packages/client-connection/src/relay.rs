//! Authenticated client-side adapter for the AgentBrowser Relay v1 ABI.
//!
//! This module owns client credentials, Relay directory projection, connection
//! generations, and the two opaque tunnel channels. Browser operation and media
//! semantics stay in their owning protocol/Host adapters.

use std::{
    collections::HashSet,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex as StdMutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use base64::{
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
    Engine as _,
};
use ed25519_dalek::{Signer, SigningKey};
use futures_util::{SinkExt, StreamExt};
use rand_core::OsRng;
use reqwest::Method;
use rustls::{
    pki_types::{CertificateDer, ServerName},
    ClientConfig, RootCertStore,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::{
    net::TcpStream,
    sync::{watch, Mutex},
};
use tokio_rustls::TlsConnector;
use tokio_tungstenite::{
    client_async_with_config,
    tungstenite::{
        client::IntoClientRequest,
        http::header::{HeaderValue, AUTHORIZATION},
        protocol::WebSocketConfig,
        Message,
    },
    WebSocketStream,
};
use url::Url;

const MAX_HTTP_BODY: usize = 16 * 1024;
const MAX_WIRE_MESSAGE: usize = 1024 * 1024;
const MAX_CONTROL_PAYLOAD: usize = 64 * 1024;
const MAX_MEDIA_PAYLOAD: usize = 1024 * 1024;
const MAX_ID: usize = 128;
const CONTROL_PATH: &str = "/v1/control/client";
const TUNNEL_TIMEOUT: Duration = Duration::from_secs(15);

type Socket = WebSocketStream<tokio_rustls::client::TlsStream<TcpStream>>;

pub type Result<T> = std::result::Result<T, RelayFailure>;

#[derive(Debug, Error)]
pub enum RelayFailure {
    #[error("relay configuration: {0}")]
    Configuration(String),
    #[error("relay transport: {0}")]
    Transport(String),
    #[error("relay {status} {code}: {message}")]
    Remote {
        status: u16,
        code: String,
        message: String,
    },
    #[error("relay protocol: {0}")]
    Protocol(String),
    #[error("relay authentication expired")]
    Expired,
    #[error("relay authentication revoked")]
    Revoked,
    #[error("relay connection generation superseded")]
    Superseded,
    #[error("relay connection closed")]
    Closed,
    #[error("relay limit: {0}")]
    Limit(String),
    #[error("device identity belongs to another relay session")]
    IdentityMismatch,
}

#[derive(Clone)]
pub struct RelayConfig {
    inner: Arc<RelayConfigInner>,
}

struct RelayConfigInner {
    https_origin: Url,
    wss_origin: Url,
    http: reqwest::Client,
    tls: Arc<ClientConfig>,
}

impl RelayConfig {
    /// Build a TLS-only configuration from an HTTPS origin and an explicit CA.
    /// No insecure or plaintext mode exists in this adapter.
    pub fn new(origin: &str, server_ca_der: Vec<u8>) -> Result<Self> {
        let https_origin =
            Url::parse(origin).map_err(|error| RelayFailure::Configuration(error.to_string()))?;
        if https_origin.scheme() != "https"
            || https_origin.host_str().is_none()
            || !https_origin.username().is_empty()
            || https_origin.password().is_some()
            || https_origin.query().is_some()
            || https_origin.fragment().is_some()
            || !matches!(https_origin.path(), "" | "/")
        {
            return Err(RelayFailure::Configuration(
                "origin must be a plain https origin".into(),
            ));
        }
        if server_ca_der.is_empty() || server_ca_der.len() > 128 * 1024 {
            return Err(RelayFailure::Configuration("invalid CA certificate".into()));
        }

        let certificate = reqwest::Certificate::from_der(&server_ca_der)
            .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
        let http = reqwest::Client::builder()
            .use_rustls_tls()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .timeout(TUNNEL_TIMEOUT)
            .tls_built_in_root_certs(false)
            .add_root_certificate(certificate)
            .build()
            .map_err(|error| RelayFailure::Configuration(error.to_string()))?;

        let mut roots = RootCertStore::empty();
        roots
            .add(CertificateDer::from(server_ca_der))
            .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let tls = ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|error| RelayFailure::Configuration(error.to_string()))?
            .with_root_certificates(roots)
            .with_no_client_auth();

        let mut wss_origin = https_origin.clone();
        wss_origin
            .set_scheme("wss")
            .map_err(|_| RelayFailure::Configuration("cannot derive wss origin".into()))?;
        Ok(Self {
            inner: Arc::new(RelayConfigInner {
                https_origin,
                wss_origin,
                http,
                tls: Arc::new(tls),
            }),
        })
    }

    fn http_url(&self, path: &str) -> Result<Url> {
        if !path.starts_with('/') || path.contains('?') || path.contains('#') {
            return Err(RelayFailure::Protocol("invalid Relay HTTP path".into()));
        }
        let mut url = self.inner.https_origin.clone();
        url.set_path(path);
        Ok(url)
    }

    fn ws_url(&self, path: &str) -> Result<Url> {
        if !path.starts_with('/')
            || path.contains('?')
            || path.contains('#')
            || (path.starts_with("/v1/tunnel/") && path.len() > MAX_ID * 4)
        {
            return Err(RelayFailure::Protocol(
                "invalid Relay WebSocket path".into(),
            ));
        }
        let mut url = self.inner.wss_origin.clone();
        url.set_path(path);
        Ok(url)
    }

    async fn open_ws(&self, path: &str, bearer: Option<&str>) -> Result<Socket> {
        let url = self.ws_url(path)?;
        let host = self
            .inner
            .https_origin
            .host_str()
            .ok_or_else(|| RelayFailure::Configuration("Relay host is missing".into()))?;
        let port = self.inner.https_origin.port().unwrap_or(443);
        let address = if host.contains(':') {
            format!("[{host}]:{port}")
        } else {
            format!("{host}:{port}")
        };
        let tcp = tokio::time::timeout(TUNNEL_TIMEOUT, TcpStream::connect(address))
            .await
            .map_err(|_| RelayFailure::Transport("Relay TCP connect timed out".into()))?
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        let server_name = ServerName::try_from(host.to_owned())
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        let tls = tokio::time::timeout(
            TUNNEL_TIMEOUT,
            TlsConnector::from(self.inner.tls.clone()).connect(server_name, tcp),
        )
        .await
        .map_err(|_| RelayFailure::Transport("Relay TLS handshake timed out".into()))?
        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        let mut request = url
            .as_str()
            .into_client_request()
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        if let Some(bearer) = bearer {
            let value = HeaderValue::from_str(&format!("Bearer {bearer}"))
                .map_err(|error| RelayFailure::Protocol(error.to_string()))?;
            request.headers_mut().insert(AUTHORIZATION, value);
        }
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_WIRE_MESSAGE))
            .max_frame_size(Some(MAX_WIRE_MESSAGE));
        tokio::time::timeout(
            TUNNEL_TIMEOUT,
            client_async_with_config(request, tls, Some(config)),
        )
        .await
        .map_err(|_| RelayFailure::Transport("Relay WebSocket handshake timed out".into()))?
        .map(|(socket, _)| socket)
        .map_err(|error| RelayFailure::Transport(error.to_string()))
    }

    async fn http_json<T, B>(
        &self,
        method: Method,
        path: &str,
        bearer: Option<&str>,
        body: Option<&B>,
    ) -> Result<T>
    where
        T: DeserializeOwned,
        B: Serialize + ?Sized,
    {
        let mut request = self.inner.http.request(method, self.http_url(path)?);
        if let Some(bearer) = bearer {
            request = request.bearer_auth(bearer);
        }
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        let status = response.status().as_u16();
        let mut stream = response.bytes_stream();
        let mut bytes = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|error| RelayFailure::Transport(error.to_string()))?;
            if bytes.len() + chunk.len() > MAX_HTTP_BODY {
                return Err(RelayFailure::Protocol("Relay response too large".into()));
            }
            bytes.extend_from_slice(&chunk);
        }
        if !(200..300).contains(&status) {
            let error = serde_json::from_slice::<HttpErrorEnvelope>(&bytes)
                .map_err(|_| RelayFailure::Protocol("Malformed Relay error response".into()))?;
            return Err(RelayFailure::Remote {
                status,
                code: error.error.code,
                message: error.error.message,
            });
        }
        serde_json::from_slice(&bytes)
            .map_err(|error| RelayFailure::Protocol(format!("Malformed Relay response: {error}")))
    }
}

struct AuthState {
    token: String,
    expires_at_ms: u64,
    revoked: AtomicBool,
}

impl AuthState {
    fn token(&self) -> Result<String> {
        if self.revoked.load(Ordering::Acquire) {
            return Err(RelayFailure::Revoked);
        }
        if now_ms() >= self.expires_at_ms {
            return Err(RelayFailure::Expired);
        }
        Ok(self.token.clone())
    }
}

#[derive(Clone)]
pub struct RelayClient {
    config: RelayConfig,
    auth: Arc<AuthState>,
}

impl RelayClient {
    pub async fn login(config: RelayConfig, username: &str, password: &str) -> Result<Self> {
        let response: LoginResponse = config
            .http_json(
                Method::POST,
                "/v1/login",
                None,
                Some(&LoginRequest { username, password }),
            )
            .await?;
        validate_bearer(&response.token, "token", 256)?;
        if response.expires_at_ms <= now_ms() {
            return Err(RelayFailure::Protocol(
                "Relay returned an expired token".into(),
            ));
        }
        Ok(Self {
            config,
            auth: Arc::new(AuthState {
                token: response.token,
                expires_at_ms: response.expires_at_ms,
                revoked: AtomicBool::new(false),
            }),
        })
    }

    pub fn expires_at_ms(&self) -> u64 {
        self.auth.expires_at_ms
    }

    pub async fn register_device(
        &self,
        name: &str,
        identity: DeviceIdentity,
    ) -> Result<RegisteredDevice> {
        validate_text(name, "device name", 64)?;
        let token = self.auth.token()?;
        let response: IdResponse = self
            .config
            .http_json(
                Method::POST,
                "/v1/devices",
                Some(&token),
                Some(&RegisterDeviceRequest {
                    name,
                    public_key: identity.public_key_pem(),
                }),
            )
            .await?;
        validate_text(&response.id, "device id", MAX_ID)?;
        Ok(RegisteredDevice {
            id: response.id,
            identity,
            auth: self.auth.clone(),
        })
    }

    pub async fn list_directory(&self) -> Result<Vec<DirectoryHost>> {
        let token = self.auth.token()?;
        let response: DirectoryResponse = self
            .config
            .http_json(
                Method::GET,
                "/v1/directory",
                Some(&token),
                Option::<&()>::None,
            )
            .await?;
        parse_directory(response)
    }

    /// Revoke the server token and poison all objects sharing this session.
    /// A failed HTTP request does not claim revocation succeeded.
    pub async fn revoke(&self) -> Result<()> {
        let token = self.auth.token()?;
        let response: RevokeResponse = self
            .config
            .http_json(
                Method::DELETE,
                "/v1/token",
                Some(&token),
                Option::<&()>::None,
            )
            .await?;
        if !response.revoked {
            return Err(RelayFailure::Protocol(
                "Relay did not confirm revocation".into(),
            ));
        }
        self.auth.revoked.store(true, Ordering::Release);
        Ok(())
    }

    pub fn connector(&self) -> RelayConnector {
        RelayConnector::new(self.clone())
    }
}

pub struct DeviceIdentity {
    signing_key: SigningKey,
}

impl DeviceIdentity {
    pub fn generate() -> Self {
        Self {
            signing_key: SigningKey::generate(&mut OsRng),
        }
    }

    pub fn from_signing_key(signing_key: SigningKey) -> Self {
        Self { signing_key }
    }

    fn public_key_pem(&self) -> String {
        let der = subject_public_key_info(&self.signing_key.verifying_key().to_bytes());
        let encoded = STANDARD.encode(der);
        let mut pem = String::from("-----BEGIN PUBLIC KEY-----\n");
        for chunk in encoded.as_bytes().chunks(64) {
            pem.push_str(std::str::from_utf8(chunk).expect("base64 is UTF-8"));
            pem.push('\n');
        }
        pem.push_str("-----END PUBLIC KEY-----\n");
        pem
    }

    fn sign(&self, nonce: &str, path: &str, device_id: &str, token: &str) -> String {
        let token_digest = hex_lower(&Sha256::digest(token.as_bytes()));
        let transcript = serde_json::to_vec(&[
            "agentbrowser-relay-v1",
            nonce,
            path,
            device_id,
            &token_digest,
        ])
        .expect("fixed transcript is serializable");
        URL_SAFE_NO_PAD.encode(self.signing_key.sign(&transcript).to_bytes())
    }
}

pub struct RegisteredDevice {
    id: String,
    identity: DeviceIdentity,
    auth: Arc<AuthState>,
}

impl RegisteredDevice {
    pub fn id(&self) -> &str {
        &self.id
    }
}

pub struct RelayConnector {
    relay: RelayClient,
    generation: watch::Sender<u64>,
    next_generation: StdMutex<u64>,
}

impl RelayConnector {
    pub fn new(relay: RelayClient) -> Self {
        Self {
            relay,
            generation: watch::channel(0).0,
            next_generation: StdMutex::new(0),
        }
    }

    pub async fn connect(&self, device: &RegisteredDevice) -> Result<RelayConnection> {
        if !Arc::ptr_eq(&self.relay.auth, &device.auth) {
            return Err(RelayFailure::IdentityMismatch);
        }
        let (generation, current) = reserve_generation(&self.next_generation, &self.generation)?;
        let token = self.relay.auth.token()?;
        let socket = establish_control(&self.relay, device, &token).await?;
        let closed = Arc::new(AtomicBool::new(false));
        let guard = GenerationGuard {
            generation,
            current,
            auth: self.relay.auth.clone(),
            config: self.relay.config.clone(),
            closed,
        };
        Ok(RelayConnection {
            generation,
            guard,
            control: Mutex::new(socket),
        })
    }
}

fn reserve_generation(
    next_generation: &StdMutex<u64>,
    generation: &watch::Sender<u64>,
) -> Result<(u64, watch::Receiver<u64>)> {
    let mut last = next_generation
        .lock()
        .map_err(|_| RelayFailure::Protocol("connection generation lock poisoned".into()))?;
    let next = last
        .checked_add(1)
        .ok_or_else(|| RelayFailure::Protocol("connection generation exhausted".into()))?;
    *last = next;
    // Publish while holding the same lock as allocation so concurrent
    // handshakes cannot make the watch value move backwards.
    generation.send_replace(next);
    Ok((next, generation.subscribe()))
}

#[derive(Clone)]
struct GenerationGuard {
    generation: u64,
    current: watch::Receiver<u64>,
    auth: Arc<AuthState>,
    config: RelayConfig,
    closed: Arc<AtomicBool>,
}

impl GenerationGuard {
    fn check(&self) -> Result<String> {
        if self.closed.load(Ordering::Acquire) {
            return Err(RelayFailure::Closed);
        }
        if *self.current.borrow() != self.generation {
            return Err(RelayFailure::Superseded);
        }
        self.auth.token()
    }
}

pub struct RelayConnection {
    generation: u64,
    guard: GenerationGuard,
    control: Mutex<Socket>,
}

impl RelayConnection {
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// Request one server-authorized tunnel. The returned control and media
    /// channels have separate sockets, queues, and size ceilings.
    pub async fn open_tunnel(&self, host_id: &str) -> Result<RelayTunnel> {
        validate_text(host_id, "host id", MAX_ID)?;
        self.guard.check()?;
        let offer = {
            let mut socket = self.control.lock().await;
            send_json(
                &mut socket,
                &TunnelOpenRequest {
                    kind: "tunnel.open",
                    host_id,
                },
            )
            .await?;
            loop {
                let message = tokio::time::timeout(TUNNEL_TIMEOUT, next_message(&mut socket))
                    .await
                    .map_err(|_| RelayFailure::Transport("tunnel offer timed out".into()))??;
                let Message::Text(text) = message else {
                    return Err(RelayFailure::Protocol(
                        "Relay control requires JSON messages".into(),
                    ));
                };
                let kind = message_type(&text)?;
                match kind.as_str() {
                    "directory.snapshot" => {
                        let directory = serde_json::from_str::<DirectoryEvent>(&text)
                            .map_err(|error| RelayFailure::Protocol(error.to_string()))?;
                        let _ = parse_directory(DirectoryResponse {
                            hosts: directory.hosts,
                        })?;
                    }
                    "tunnel.offer" => break parse_tunnel_offer(&text)?,
                    "error" => return Err(parse_remote_error(&text, 400)?),
                    other => {
                        return Err(RelayFailure::Protocol(format!(
                            "unexpected Relay control event: {other}"
                        )))
                    }
                }
            }
        };
        self.guard.check()?;
        let tunnel_id = offer.tunnel_id.clone();
        let peer_device_id = offer.peer_device_id.clone();
        let control = open_channel(
            self.guard.clone(),
            tunnel_id.clone(),
            RelayChannelKind::Control,
            offer.channels.control,
        );
        let media = open_channel(
            self.guard.clone(),
            tunnel_id.clone(),
            RelayChannelKind::Media,
            offer.channels.media,
        );
        let (control, media) = tokio::try_join!(control, media)?;
        Ok(RelayTunnel {
            tunnel_id,
            peer_device_id,
            control,
            media,
        })
    }

    pub fn close(self) {}
}

impl Drop for RelayConnection {
    fn drop(&mut self) {
        self.guard.closed.store(true, Ordering::Release);
    }
}

pub struct RelayTunnel {
    tunnel_id: String,
    peer_device_id: String,
    control: RelayChannel,
    media: RelayChannel,
}

impl RelayTunnel {
    pub fn id(&self) -> &str {
        &self.tunnel_id
    }

    pub fn peer_device_id(&self) -> &str {
        &self.peer_device_id
    }

    pub fn control(&self) -> &RelayChannel {
        &self.control
    }

    pub fn media(&self) -> &RelayChannel {
        &self.media
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelayChannelKind {
    Control,
    Media,
}

pub struct RelayChannel {
    kind: RelayChannelKind,
    socket: Mutex<Socket>,
    guard: GenerationGuard,
}

impl RelayChannel {
    pub fn kind(&self) -> RelayChannelKind {
        self.kind
    }

    pub async fn send(&self, bytes: &[u8]) -> Result<()> {
        self.guard.check()?;
        let limit = channel_limit(self.kind);
        if bytes.len() > limit {
            return Err(RelayFailure::Limit(format!(
                "{} channel payload exceeds {limit} bytes",
                channel_name(self.kind)
            )));
        }
        let mut socket = self.socket.lock().await;
        tokio::time::timeout(
            TUNNEL_TIMEOUT,
            socket.send(Message::Binary(bytes.to_vec().into())),
        )
        .await
        .map_err(|_| RelayFailure::Transport("Relay channel send timed out".into()))?
        .map_err(|error| RelayFailure::Transport(error.to_string()))
    }

    pub async fn recv(&self) -> Result<Vec<u8>> {
        let mut current = self.guard.current.clone();
        loop {
            self.guard.check()?;
            let mut socket = self.socket.lock().await;
            let message = tokio::select! {
                changed = current.changed() => {
                    changed.map_err(|_| RelayFailure::Closed)?;
                    return Err(RelayFailure::Superseded);
                }
                message = next_message(&mut socket) => message?,
            };
            match message {
                Message::Binary(bytes) => {
                    if bytes.len() > channel_limit(self.kind) {
                        return Err(RelayFailure::Limit(format!(
                            "{} channel frame exceeds {} bytes",
                            channel_name(self.kind),
                            channel_limit(self.kind)
                        )));
                    }
                    return Ok(bytes.to_vec());
                }
                Message::Text(_) => {
                    return Err(RelayFailure::Protocol(format!(
                        "{} channel received text",
                        channel_name(self.kind)
                    )))
                }
                _ => continue,
            }
        }
    }
}

async fn establish_control(
    relay: &RelayClient,
    device: &RegisteredDevice,
    token: &str,
) -> Result<Socket> {
    let mut socket = relay.config.open_ws(CONTROL_PATH, None).await?;
    let challenge_text = next_text(&mut socket).await?;
    let challenge: AuthChallenge = parse_typed(&challenge_text, "auth.challenge")?;
    if challenge.version != 1 {
        return Err(RelayFailure::Protocol(format!(
            "unsupported Relay auth version {}",
            challenge.version
        )));
    }
    validate_text(&challenge.nonce, "auth nonce", 256)?;
    send_json(
        &mut socket,
        &AuthProve {
            kind: "auth.prove",
            token,
            device_id: &device.id,
            signature: device
                .identity
                .sign(&challenge.nonce, CONTROL_PATH, &device.id, token),
        },
    )
    .await?;
    let ready_text = next_text(&mut socket).await?;
    let ready: AuthReady = parse_typed(&ready_text, "auth.ready")?;
    if ready.device_id != device.id {
        return Err(RelayFailure::Protocol(
            "Relay returned another device identity".into(),
        ));
    }
    Ok(socket)
}

async fn open_channel(
    guard: GenerationGuard,
    tunnel_id: String,
    kind: RelayChannelKind,
    ticket: TunnelChannel,
) -> Result<RelayChannel> {
    guard.check()?;
    let mut socket = guard
        .config
        .open_ws(&ticket.path, Some(&ticket.ticket))
        .await?;
    loop {
        let message = tokio::time::timeout(TUNNEL_TIMEOUT, next_message(&mut socket))
            .await
            .map_err(|_| RelayFailure::Transport("Relay channel ready timed out".into()))??;
        let Message::Text(text) = message else {
            return Err(RelayFailure::Protocol(
                "Relay channel requires channel.ready JSON".into(),
            ));
        };
        match message_type(&text)?.as_str() {
            "channel.ready" => {
                let ready: ChannelReady = parse_typed(&text, "channel.ready")?;
                if ready.tunnel_id != tunnel_id || ready.channel != channel_name(kind) {
                    return Err(RelayFailure::Protocol(
                        "Relay channel identity mismatch".into(),
                    ));
                }
                return Ok(RelayChannel {
                    kind,
                    socket: Mutex::new(socket),
                    guard,
                });
            }
            "error" => return Err(parse_remote_error(&text, 401)?),
            other => {
                return Err(RelayFailure::Protocol(format!(
                    "unexpected Relay channel event: {other}"
                )))
            }
        }
    }
}

async fn send_json<T: Serialize>(socket: &mut Socket, value: &T) -> Result<()> {
    let text =
        serde_json::to_string(value).map_err(|error| RelayFailure::Protocol(error.to_string()))?;
    if text.len() > MAX_WIRE_MESSAGE {
        return Err(RelayFailure::Limit(
            "Relay control message is too large".into(),
        ));
    }
    socket
        .send(Message::Text(text.into()))
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))
}

async fn next_text(socket: &mut Socket) -> Result<String> {
    loop {
        match next_message(socket).await? {
            Message::Text(text) => {
                if text.len() > MAX_WIRE_MESSAGE {
                    return Err(RelayFailure::Protocol(
                        "Relay JSON message is too large".into(),
                    ));
                }
                return Ok(text.to_string());
            }
            Message::Binary(_) => {
                return Err(RelayFailure::Protocol("Relay control requires JSON".into()))
            }
            _ => continue,
        }
    }
}

async fn next_message(socket: &mut Socket) -> Result<Message> {
    loop {
        match socket.next().await.ok_or(RelayFailure::Closed)? {
            Ok(Message::Ping(data)) => {
                socket
                    .send(Message::Pong(data))
                    .await
                    .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            }
            Ok(Message::Pong(_)) => {}
            Ok(Message::Close(_)) => return Err(RelayFailure::Closed),
            Ok(message) => return Ok(message),
            Err(error) => return Err(RelayFailure::Transport(error.to_string())),
        }
    }
}

fn parse_typed<T: DeserializeOwned>(text: &str, expected: &str) -> Result<T> {
    if message_type(text)? != expected {
        return Err(RelayFailure::Protocol(format!(
            "expected {expected} message"
        )));
    }
    serde_json::from_str(text).map_err(|error| RelayFailure::Protocol(error.to_string()))
}

fn message_type(text: &str) -> Result<String> {
    let value: Value = serde_json::from_str(text)
        .map_err(|error| RelayFailure::Protocol(format!("invalid Relay JSON: {error}")))?;
    value
        .get("type")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| RelayFailure::Protocol("Relay message type is missing".into()))
}

fn parse_remote_error(text: &str, status: u16) -> Result<RelayFailure> {
    let error: WireError = parse_typed(text, "error")?;
    validate_text(&error.code, "error code", MAX_ID)?;
    if error.message.len() > MAX_HTTP_BODY {
        return Err(RelayFailure::Protocol(
            "Relay error message is too large".into(),
        ));
    }
    Ok(RelayFailure::Remote {
        status,
        code: error.code,
        message: error.message,
    })
}

fn parse_tunnel_offer(text: &str) -> Result<TunnelOffer> {
    let offer: TunnelOfferWire = parse_typed(text, "tunnel.offer")?;
    validate_text(&offer.tunnel_id, "tunnel id", MAX_ID)?;
    validate_text(&offer.peer_device_id, "peer device id", MAX_ID)?;
    if offer.expires_at_ms <= now_ms() {
        return Err(RelayFailure::Remote {
            status: 401,
            code: "TUNNEL_EXPIRED".into(),
            message: "Relay tunnel offer expired".into(),
        });
    }
    validate_tunnel_channel(&offer.channels.control, &offer.tunnel_id, "control")?;
    validate_tunnel_channel(&offer.channels.media, &offer.tunnel_id, "media")?;
    Ok(TunnelOffer {
        tunnel_id: offer.tunnel_id,
        peer_device_id: offer.peer_device_id,
        channels: offer.channels,
    })
}

fn parse_directory(response: DirectoryResponse) -> Result<Vec<DirectoryHost>> {
    if response.hosts.len() > 128 {
        return Err(RelayFailure::Limit("directory host limit exceeded".into()));
    }
    let mut seen = HashSet::new();
    response
        .hosts
        .into_iter()
        .map(|host| {
            validate_text(&host.host_id, "host id", MAX_ID)?;
            validate_text(&host.device_id, "device id", MAX_ID)?;
            validate_text(&host.device_name, "device name", 64)?;
            if !seen.insert(host.host_id.clone()) {
                return Err(RelayFailure::Protocol("duplicate Relay host".into()));
            }
            Ok(DirectoryHost {
                host_id: host.host_id,
                device_id: host.device_id,
                device_name: host.device_name,
                snapshot: parse_snapshot(host.snapshot)?,
            })
        })
        .collect()
}

fn validate_tunnel_channel(channel: &TunnelChannel, tunnel_id: &str, name: &str) -> Result<()> {
    let segments = channel.path.split('/').collect::<Vec<_>>();
    if segments.len() != 6
        || segments[1] != "v1"
        || segments[2] != "tunnel"
        || segments[3] != tunnel_id
        || segments[4] != name
        || segments[5] != "0"
        || channel.path.len() > MAX_ID * 4
        || channel.path.contains('?')
        || channel.path.contains('#')
    {
        return Err(RelayFailure::Protocol("invalid Relay tunnel path".into()));
    }
    validate_bearer(&channel.ticket, "tunnel ticket", 256)
}

fn validate_bearer(value: &str, name: &str, max: usize) -> Result<()> {
    validate_text(value, name, max)?;
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(RelayFailure::Protocol(format!("invalid {name}")));
    }
    Ok(())
}

fn parse_snapshot(snapshot: SnapshotWire) -> Result<HostSnapshot> {
    validate_text(&snapshot.incarnation, "host incarnation", MAX_ID)?;
    if snapshot.endpoints.len() > 16 || snapshot.sessions.len() > 64 {
        return Err(RelayFailure::Limit("host snapshot limit exceeded".into()));
    }
    let endpoints = snapshot
        .endpoints
        .into_iter()
        .map(|endpoint| {
            let network = match endpoint.network.as_str() {
                "lan" => RelayNetwork::Lan,
                "public" => RelayNetwork::Public,
                "tailscale" => RelayNetwork::Tailscale,
                _ => return Err(RelayFailure::Protocol("invalid Relay network".into())),
            };
            validate_text(&endpoint.url, "endpoint", 1024)?;
            let url = Url::parse(&endpoint.url)
                .map_err(|_| RelayFailure::Protocol("invalid Relay endpoint".into()))?;
            if !matches!(url.scheme(), "wss" | "https" | "udp")
                || url.host_str().is_none()
                || url.username() != ""
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
            {
                return Err(RelayFailure::Protocol("invalid Relay endpoint".into()));
            }
            Ok(RelayEndpoint {
                network,
                url: endpoint.url,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let mut seen = HashSet::new();
    let sessions = snapshot
        .sessions
        .into_iter()
        .map(|session| {
            validate_text(&session.id, "session id", MAX_ID)?;
            if !seen.insert(session.id.clone()) {
                return Err(RelayFailure::Protocol("duplicate Relay session".into()));
            }
            Ok(RelaySession { id: session.id })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(HostSnapshot {
        incarnation: snapshot.incarnation,
        revision: snapshot.revision,
        endpoints,
        sessions,
    })
}

fn validate_text(value: &str, name: &str, max: usize) -> Result<()> {
    if value.is_empty() || value.len() > max {
        return Err(RelayFailure::Protocol(format!("invalid {name}")));
    }
    Ok(())
}

fn channel_limit(kind: RelayChannelKind) -> usize {
    match kind {
        RelayChannelKind::Control => MAX_CONTROL_PAYLOAD,
        RelayChannelKind::Media => MAX_MEDIA_PAYLOAD,
    }
}

fn channel_name(kind: RelayChannelKind) -> &'static str {
    match kind {
        RelayChannelKind::Control => "control",
        RelayChannelKind::Media => "media",
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before Unix epoch")
        .as_millis()
        .try_into()
        .expect("system time overflow")
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn subject_public_key_info(key: &[u8; 32]) -> Vec<u8> {
    let mut der = vec![
        0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
    ];
    der.extend_from_slice(key);
    der
}

#[derive(Serialize)]
struct LoginRequest<'a> {
    username: &'a str,
    password: &'a str,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginResponse {
    token: String,
    #[serde(rename = "expiresAt")]
    expires_at_ms: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RegisterDeviceRequest<'a> {
    name: &'a str,
    public_key: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IdResponse {
    id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RevokeResponse {
    revoked: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HttpErrorEnvelope {
    error: HttpError,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HttpError {
    code: String,
    message: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthChallenge {
    #[serde(rename = "type")]
    _kind: String,
    version: u64,
    nonce: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AuthProve<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    token: &'a str,
    device_id: &'a str,
    signature: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthReady {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "deviceId")]
    device_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TunnelOpenRequest<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    host_id: &'a str,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireError {
    #[serde(rename = "type")]
    _kind: String,
    code: String,
    message: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DirectoryResponse {
    hosts: Vec<DirectoryHostWire>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DirectoryEvent {
    #[serde(rename = "type")]
    _kind: String,
    hosts: Vec<DirectoryHostWire>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DirectoryHostWire {
    #[serde(rename = "hostId")]
    host_id: String,
    #[serde(rename = "deviceId")]
    device_id: String,
    #[serde(rename = "deviceName")]
    device_name: String,
    snapshot: SnapshotWire,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SnapshotWire {
    incarnation: String,
    revision: u64,
    endpoints: Vec<EndpointWire>,
    sessions: Vec<SessionWire>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EndpointWire {
    network: String,
    url: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SessionWire {
    id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TunnelOfferWire {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "tunnelId")]
    tunnel_id: String,
    #[serde(rename = "peerDeviceId")]
    peer_device_id: String,
    #[serde(rename = "expiresAt")]
    expires_at_ms: u64,
    channels: TunnelChannels,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TunnelChannels {
    control: TunnelChannel,
    media: TunnelChannel,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TunnelChannel {
    path: String,
    ticket: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChannelReady {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "tunnelId")]
    tunnel_id: String,
    channel: String,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct DirectoryHost {
    pub host_id: String,
    pub device_id: String,
    pub device_name: String,
    pub snapshot: HostSnapshot,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct HostSnapshot {
    pub incarnation: String,
    pub revision: u64,
    pub endpoints: Vec<RelayEndpoint>,
    pub sessions: Vec<RelaySession>,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct RelayEndpoint {
    pub network: RelayNetwork,
    pub url: String,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum RelayNetwork {
    Lan,
    Public,
    Tailscale,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct RelaySession {
    pub id: String,
}

struct TunnelOffer {
    tunnel_id: String,
    peer_device_id: String,
    channels: TunnelChannels,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_rejects_plaintext_and_embedded_credentials() {
        assert!(matches!(
            RelayConfig::new("http://127.0.0.1:1", vec![1]),
            Err(RelayFailure::Configuration(_))
        ));
        assert!(matches!(
            RelayConfig::new("https://user:pass@127.0.0.1:1", vec![1]),
            Err(RelayFailure::Configuration(_))
        ));
        assert!(matches!(
            RelayConfig::new("https://127.0.0.1:1/relay", vec![1]),
            Err(RelayFailure::Configuration(_))
        ));
    }

    #[test]
    fn device_public_key_uses_ed25519_spki() {
        let identity = DeviceIdentity::generate();
        let pem = identity.public_key_pem();
        assert!(pem.starts_with("-----BEGIN PUBLIC KEY-----\n"));
        assert!(pem.ends_with("-----END PUBLIC KEY-----\n"));
    }

    #[test]
    fn media_and_control_limits_are_separate() {
        assert_eq!(channel_limit(RelayChannelKind::Control), 64 * 1024);
        assert_eq!(channel_limit(RelayChannelKind::Media), 1024 * 1024);
        assert_ne!(
            channel_name(RelayChannelKind::Control),
            channel_name(RelayChannelKind::Media)
        );
    }

    #[test]
    fn generation_reservation_is_unique_and_published_in_order() {
        let counter = Arc::new(StdMutex::new(0));
        let (sender, receiver) = watch::channel(0);
        drop(receiver);
        let mut workers = Vec::new();
        for _ in 0..16 {
            let counter = Arc::clone(&counter);
            let sender = sender.clone();
            workers.push(std::thread::spawn(move || {
                reserve_generation(&counter, &sender).map(|(value, _)| value)
            }));
        }
        let mut values = workers
            .into_iter()
            .map(|worker| {
                worker
                    .join()
                    .expect("generation worker")
                    .expect("generation")
            })
            .collect::<Vec<_>>();
        values.sort_unstable();
        assert_eq!(values, (1..=16).collect::<Vec<_>>());
        assert_eq!(*sender.borrow(), 16);
    }
}
