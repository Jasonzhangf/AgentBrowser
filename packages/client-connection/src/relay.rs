//! Authenticated client/Host adapter for the AgentBrowser Relay v0 ABI.
//!
//! This module owns client credentials, Relay directory projection, connection
//! generations, and the two opaque tunnel channels. Browser operation and media
//! semantics stay in their owning protocol/Host adapters.

use std::{
    collections::{HashSet, VecDeque},
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
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use futures_util::{
    stream::{SplitSink, SplitStream},
    SinkExt, StreamExt,
};
use rand_core::OsRng;
use reqwest::Method;
use rustls::{
    pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer, ServerName},
    ClientConfig, RootCertStore,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt, DuplexStream, ReadHalf, WriteHalf},
    net::TcpStream,
    sync::{watch, Mutex},
    task::JoinHandle,
};
use tokio_rustls::{TlsAcceptor, TlsConnector, TlsStream};
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
const MAX_PENDING_OFFERS: usize = 128;
const RELAY_ABI_ID: &str = "agentbrowser-relay-v0";
const TUNNEL_HELLO_VERSION: u64 = 0;
const CLIENT_CONTROL_PATH: &str = "/v2/control/client";
const TUNNEL_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_TUNNEL_HELLO: usize = 16 * 1024;
const INNER_BRIDGE_BUFFER: usize = 256 * 1024;
const TUNNEL_EXPORTER_LABEL: &[u8] = b"EXPORTER-AgentBrowser-Relay-v0-TunnelHello";

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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RelayRejectReason {
    UnknownPeer,
    Capacity,
}

impl RelayRejectReason {
    fn closed_code(self) -> &'static str {
        match self {
            Self::UnknownPeer => "HOST_REJECTED_UNKNOWN_PEER",
            Self::Capacity => "HOST_REJECTED_CAPACITY",
        }
    }
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
            || (path.starts_with("/v2/tunnel/") && path.len() > MAX_ID * 4)
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
                "/v2/login",
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
                "/v2/devices",
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

    pub async fn register_host(&self, device: &RegisteredDevice) -> Result<RegisteredHost> {
        if !Arc::ptr_eq(&self.auth, &device.auth) {
            return Err(RelayFailure::IdentityMismatch);
        }
        let token = self.auth.token()?;
        let response: IdResponse = self
            .config
            .http_json(
                Method::POST,
                "/v2/hosts",
                Some(&token),
                Some(&RegisterHostRequest {
                    device_id: &device.id,
                }),
            )
            .await?;
        validate_text(&response.id, "host id", MAX_ID)?;
        Ok(RegisteredHost {
            id: response.id,
            device: device.clone(),
        })
    }

    pub async fn list_directory(&self) -> Result<Vec<DirectoryHost>> {
        let token = self.auth.token()?;
        let response: DirectoryResponse = self
            .config
            .http_json(
                Method::GET,
                "/v2/directory",
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
                "/v2/token",
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

#[derive(Clone)]
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

    pub fn from_seed(seed: [u8; 32]) -> Self {
        Self::from_signing_key(SigningKey::from_bytes(&seed))
    }

    pub fn public_key_bytes(&self) -> [u8; 32] {
        self.signing_key.verifying_key().to_bytes()
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
        let transcript = serde_json::to_vec(&[RELAY_ABI_ID, nonce, path, device_id, &token_digest])
            .expect("fixed transcript is serializable");
        URL_SAFE_NO_PAD.encode(self.signing_key.sign(&transcript).to_bytes())
    }

    fn sign_bytes(&self, transcript: &[u8]) -> String {
        URL_SAFE_NO_PAD.encode(self.signing_key.sign(transcript).to_bytes())
    }
}

#[derive(Clone)]
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

#[derive(Clone)]
pub struct RegisteredHost {
    id: String,
    device: RegisteredDevice,
}

impl RegisteredHost {
    pub fn id(&self) -> &str {
        &self.id
    }

    pub fn device_id(&self) -> &str {
        self.device.id()
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
        let socket = establish_control(&self.relay, device, &token, CLIENT_CONTROL_PATH).await?;
        let closed = Arc::new(AtomicBool::new(false));
        let guard = GenerationGuard {
            generation,
            current,
            _source: self.generation.clone(),
            auth: self.relay.auth.clone(),
            config: self.relay.config.clone(),
            closed,
        };
        Ok(RelayConnection {
            generation,
            device: device.clone(),
            guard,
            control: Mutex::new(socket),
            tunnel_open: Mutex::new(()),
        })
    }

    pub async fn connect_host(&self, host: &RegisteredHost) -> Result<RelayHostConnection> {
        if !Arc::ptr_eq(&self.relay.auth, &host.device.auth) {
            return Err(RelayFailure::IdentityMismatch);
        }
        validate_text(&host.id, "host id", MAX_ID)?;
        let (generation, current) = reserve_generation(&self.next_generation, &self.generation)?;
        let token = self.relay.auth.token()?;
        let path = format!("/v2/control/host/{}", host.id);
        let socket = establish_control(&self.relay, &host.device, &token, &path).await?;
        let (sink, stream) = socket.split();
        let closed = Arc::new(AtomicBool::new(false));
        let guard = GenerationGuard {
            generation,
            current,
            _source: self.generation.clone(),
            auth: self.relay.auth.clone(),
            config: self.relay.config.clone(),
            closed,
        };
        Ok(RelayHostConnection {
            generation,
            host_id: host.id.clone(),
            device_id: host.device.id.clone(),
            device: host.device.clone(),
            guard,
            control: RelayControlSocket {
                sink: Mutex::new(sink),
                stream: Mutex::new(stream),
            },
            pending_offers: Mutex::new(VecDeque::new()),
            snapshot: Mutex::new(None),
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
    _source: watch::Sender<u64>,
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
    device: RegisteredDevice,
    guard: GenerationGuard,
    control: Mutex<Socket>,
    tunnel_open: Mutex<()>,
}

impl RelayConnection {
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// Request one server-authorized tunnel. The returned control and media
    /// channels have separate sockets, queues, and size ceilings.
    pub async fn open_tunnel(&self, host_id: &str, session_id: &str) -> Result<RelayTunnel> {
        validate_text(host_id, "host id", MAX_ID)?;
        validate_text(session_id, "session id", MAX_ID)?;
        let _tunnel_open = self.tunnel_open.lock().await;
        self.guard.check()?;
        let offer = {
            let mut socket = self.control.lock().await;
            send_json(
                &mut socket,
                &TunnelOpenRequest {
                    kind: "tunnel.open",
                    abi: RELAY_ABI_ID,
                    host_id,
                    session_id,
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
                        let directory: DirectoryEvent = parse_typed(&text, "directory.snapshot")?;
                        let _ = parse_directory(DirectoryResponse {
                            hosts: directory.hosts,
                        })?;
                    }
                    "tunnel.offer" => {
                        break parse_tunnel_offer(&text, 0, host_id, Some(session_id))?
                    }
                    "tunnel.closed" => {
                        let closed = parse_tunnel_closed(&text)?;
                        if is_host_rejection(&closed.reason) {
                            return Err(RelayFailure::Remote {
                                status: 409,
                                code: closed.reason,
                                message: "Host rejected Relay tunnel offer".into(),
                            });
                        }
                    }
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
            0,
        );
        let media = open_channel(
            self.guard.clone(),
            tunnel_id.clone(),
            RelayChannelKind::Media,
            offer.channels.media,
            0,
        );
        let mut channels = Box::pin(async move { tokio::try_join!(control, media) });
        let (control, media) = loop {
            self.guard.check()?;
            tokio::select! {
                biased;
                message = async {
                    let mut socket = self.control.lock().await;
                    tokio::time::timeout(TUNNEL_TIMEOUT, next_message(&mut socket))
                        .await
                        .map_err(|_| RelayFailure::Transport("Relay control event timed out".into()))?
                } => {
                    let message = message?;
                    let Message::Text(text) = message else {
                        return Err(RelayFailure::Protocol("Relay control requires JSON messages".into()));
                    };
                    match message_type(&text)?.as_str() {
                        "directory.snapshot" => {
                            let directory: DirectoryEvent =
                                parse_typed(&text, "directory.snapshot")?;
                            let _ = parse_directory(DirectoryResponse { hosts: directory.hosts })?;
                        }
                        "tunnel.closed" => {
                            let closed = parse_tunnel_closed(&text)?;
                            if closed.tunnel_id == tunnel_id {
                                self.guard.check()?;
                                return Err(RelayFailure::Remote {
                                    status: 409,
                                    code: closed.reason,
                                    message: "Relay tunnel closed before channels opened".into(),
                                });
                            }
                        }
                        "error" => return Err(parse_remote_error(&text, 400)?),
                        other => {
                            return Err(RelayFailure::Protocol(format!(
                                "unexpected Relay control event: {other}"
                            )))
                        }
                    }
                }
                result = &mut channels => break result?,
            }
        };
        Ok(RelayTunnel {
            tunnel_id,
            host_id: offer.host_id,
            session_id: offer.session_id,
            peer_device_id,
            control,
            media,
        })
    }

    pub async fn open_secure_tunnel(
        &self,
        host_id: &str,
        session_id: &str,
        peer: RelayPeerBinding,
        tls: RelayTlsClientIdentity,
    ) -> Result<SecureRelayTunnel> {
        let tunnel = self.open_tunnel(host_id, session_id).await?;
        if tunnel.peer_device_id != peer.relay_device_id {
            return Err(RelayFailure::IdentityMismatch);
        }
        secure_tunnel(
            tunnel,
            &self.device,
            &peer,
            RelayRole::Client,
            RelayTlsIdentity::Client(tls),
        )
        .await
    }

    pub fn close(self) {}
}

impl Drop for RelayConnection {
    fn drop(&mut self) {
        self.guard.closed.store(true, Ordering::Release);
    }
}

pub struct RelayHostConnection {
    generation: u64,
    host_id: String,
    device_id: String,
    device: RegisteredDevice,
    guard: GenerationGuard,
    control: RelayControlSocket,
    pending_offers: Mutex<VecDeque<RelayTunnelOffer>>,
    snapshot: Mutex<Option<HostSnapshot>>,
}

impl RelayHostConnection {
    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn host_id(&self) -> &str {
        &self.host_id
    }

    pub fn device_id(&self) -> &str {
        &self.device_id
    }

    pub async fn publish(&self, snapshot: HostSnapshot) -> Result<()> {
        validate_host_snapshot(&snapshot)?;
        self.guard.check()?;
        let mut sink = self.control.sink.lock().await;
        send_json_sink(
            &mut sink,
            &HostPublishRequest {
                kind: "host.publish",
                abi: RELAY_ABI_ID,
                host_id: &self.host_id,
                snapshot: snapshot.clone(),
            },
        )
        .await?;
        *self.snapshot.lock().await = Some(snapshot);
        Ok(())
    }

    async fn parse_offer(&self, text: &str) -> Result<RelayTunnelOffer> {
        let mut offer = parse_tunnel_offer(text, 1, &self.host_id, None)?;
        offer.generation = self.generation;
        let snapshot = self.snapshot.lock().await;
        if snapshot.as_ref().is_none_or(|value| {
            !value
                .sessions
                .iter()
                .any(|session| session.id == offer.session_id)
        }) {
            return Err(RelayFailure::Protocol(
                "Relay offered an unpublished Host session".into(),
            ));
        }
        Ok(offer)
    }

    pub async fn next_offer(&self) -> Result<RelayTunnelOffer> {
        self.guard.check()?;
        if let Some(offer) = self.pending_offers.lock().await.pop_front() {
            return Ok(offer);
        }
        loop {
            let message = {
                let mut stream = self.control.stream.lock().await;
                tokio::time::timeout(TUNNEL_TIMEOUT, stream.next())
                    .await
                    .map_err(|_| RelayFailure::Transport("host control event timed out".into()))?
                    .ok_or(RelayFailure::Closed)?
                    .map_err(|error| RelayFailure::Transport(error.to_string()))?
            };
            let text = match message {
                Message::Ping(bytes) => {
                    let mut sink = self.control.sink.lock().await;
                    sink.send(Message::Pong(bytes))
                        .await
                        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
                    continue;
                }
                Message::Pong(_) => continue,
                Message::Close(_) => return Err(RelayFailure::Closed),
                Message::Binary(_) => {
                    return Err(RelayFailure::Protocol(
                        "Relay host control requires JSON messages".into(),
                    ))
                }
                Message::Text(text) => text,
                _ => continue,
            };
            match message_type(&text)?.as_str() {
                "directory.snapshot" => {
                    let directory: DirectoryEvent = parse_typed(&text, "directory.snapshot")?;
                    let _ = parse_directory(DirectoryResponse {
                        hosts: directory.hosts,
                    })?;
                }
                "tunnel.offer" => {
                    return self.parse_offer(&text).await;
                }
                "tunnel.closed" => {
                    let _ = parse_tunnel_closed(&text)?;
                }
                "error" => return Err(parse_remote_error(&text, 400)?),
                other => {
                    return Err(RelayFailure::Protocol(format!(
                        "unexpected Relay host control event: {other}"
                    )))
                }
            }
        }
    }

    /// Reject one pending offer and wait for Relay's matching close receipt.
    /// The offer is consumed even when Relay rejects the request.
    pub async fn reject_offer(
        &self,
        offer: RelayTunnelOffer,
        reason: RelayRejectReason,
    ) -> Result<()> {
        self.guard.check()?;
        if offer.generation != self.generation || offer.host_id != self.host_id {
            return Err(RelayFailure::IdentityMismatch);
        }
        let expected = reason.closed_code();
        {
            let mut sink = self.control.sink.lock().await;
            send_json_sink(
                &mut sink,
                &TunnelRejectRequest {
                    kind: "tunnel.reject",
                    abi: RELAY_ABI_ID,
                    tunnel_id: &offer.tunnel_id,
                    reason,
                },
            )
            .await?;
        }
        self.guard.check()?;
        tokio::time::timeout(TUNNEL_TIMEOUT, async {
            loop {
                self.guard.check()?;
                let message = {
                    let mut stream = self.control.stream.lock().await;
                    stream
                        .next()
                        .await
                        .ok_or(RelayFailure::Closed)?
                        .map_err(|error| RelayFailure::Transport(error.to_string()))?
                };
                let text = match message {
                    Message::Ping(bytes) => {
                        let mut sink = self.control.sink.lock().await;
                        sink.send(Message::Pong(bytes))
                            .await
                            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
                        continue;
                    }
                    Message::Pong(_) => continue,
                    Message::Close(_) => return Err(RelayFailure::Closed),
                    Message::Binary(_) => {
                        return Err(RelayFailure::Protocol(
                            "Relay host control requires JSON messages".into(),
                        ))
                    }
                    Message::Text(text) => text,
                    _ => continue,
                };
                match message_type(&text)?.as_str() {
                    "directory.snapshot" => {
                        let directory: DirectoryEvent = parse_typed(&text, "directory.snapshot")?;
                        let _ = parse_directory(DirectoryResponse {
                            hosts: directory.hosts,
                        })?;
                    }
                    "tunnel.offer" => {
                        let next = self.parse_offer(&text).await?;
                        let mut pending = self.pending_offers.lock().await;
                        if pending.len() >= MAX_PENDING_OFFERS {
                            return Err(RelayFailure::Limit(
                                "pending Relay offer queue exceeded".into(),
                            ));
                        }
                        pending.push_back(next);
                    }
                    "tunnel.closed" => {
                        let closed = parse_tunnel_closed(&text)?;
                        if closed.tunnel_id != offer.tunnel_id {
                            continue;
                        }
                        self.guard.check()?;
                        if closed.reason == expected {
                            return Ok(());
                        }
                        return Err(RelayFailure::Remote {
                            status: 409,
                            code: closed.reason,
                            message: "Relay tunnel closed before requested rejection".into(),
                        });
                    }
                    "error" => return Err(parse_remote_error(&text, 400)?),
                    other => {
                        return Err(RelayFailure::Protocol(format!(
                            "unexpected Relay host control event: {other}"
                        )))
                    }
                }
            }
        })
        .await
        .map_err(|_| RelayFailure::Transport("tunnel rejection timed out".into()))?
    }

    pub async fn accept_offer(&self, offer: RelayTunnelOffer) -> Result<RelayTunnel> {
        self.guard.check()?;
        let tunnel_id = offer.tunnel_id.clone();
        let peer_device_id = offer.peer_device_id.clone();
        let control = open_channel(
            self.guard.clone(),
            tunnel_id.clone(),
            RelayChannelKind::Control,
            offer.channels.control,
            1,
        );
        let media = open_channel(
            self.guard.clone(),
            tunnel_id.clone(),
            RelayChannelKind::Media,
            offer.channels.media,
            1,
        );
        let (control, media) = tokio::try_join!(control, media)?;
        Ok(RelayTunnel {
            tunnel_id,
            host_id: offer.host_id,
            session_id: offer.session_id,
            peer_device_id,
            control,
            media,
        })
    }

    pub async fn accept_secure_offer(
        &self,
        offer: RelayTunnelOffer,
        peer: RelayPeerBinding,
        tls: RelayTlsServerIdentity,
    ) -> Result<SecureRelayTunnel> {
        if offer.peer_device_id != peer.relay_device_id {
            return Err(RelayFailure::IdentityMismatch);
        }
        let tunnel = self.accept_offer(offer).await?;
        secure_tunnel(
            tunnel,
            &self.device,
            &peer,
            RelayRole::Host,
            RelayTlsIdentity::Server(tls),
        )
        .await
    }
}

impl Drop for RelayHostConnection {
    fn drop(&mut self) {
        self.guard.closed.store(true, Ordering::Release);
    }
}

pub struct RelayTunnel {
    tunnel_id: String,
    host_id: String,
    session_id: String,
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

    pub fn host_id(&self) -> &str {
        &self.host_id
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn control(&self) -> &RelayChannel {
        &self.control
    }

    pub fn media(&self) -> &RelayChannel {
        &self.media
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RelayPeerBinding {
    pub relay_device_id: String,
    pub auth_public_key: [u8; 32],
    pub certificate_sha256: [u8; 32],
}

impl RelayPeerBinding {
    pub fn new(
        relay_device_id: impl Into<String>,
        auth_public_key: [u8; 32],
        certificate_sha256: [u8; 32],
    ) -> Self {
        Self {
            relay_device_id: relay_device_id.into(),
            auth_public_key,
            certificate_sha256,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RelayTlsClientIdentity {
    pub server_name: String,
    pub server_ca_der: Vec<u8>,
    pub client_cert_der: Vec<u8>,
    pub client_key_pkcs8_der: Vec<u8>,
}

impl RelayTlsClientIdentity {
    pub fn new(
        server_name: impl Into<String>,
        server_ca_der: Vec<u8>,
        client_cert_der: Vec<u8>,
        client_key_pkcs8_der: Vec<u8>,
    ) -> Self {
        Self {
            server_name: server_name.into(),
            server_ca_der,
            client_cert_der,
            client_key_pkcs8_der,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RelayTlsServerIdentity {
    pub server_cert_der: Vec<u8>,
    pub server_key_pkcs8_der: Vec<u8>,
    pub client_ca_der: Vec<u8>,
}

impl RelayTlsServerIdentity {
    pub fn new(
        server_cert_der: Vec<u8>,
        server_key_pkcs8_der: Vec<u8>,
        client_ca_der: Vec<u8>,
    ) -> Self {
        Self {
            server_cert_der,
            server_key_pkcs8_der,
            client_ca_der,
        }
    }
}

pub struct SecureRelayTunnel {
    tunnel_id: String,
    host_id: String,
    session_id: String,
    peer_device_id: String,
    control: SecureRelayChannel,
    media: SecureRelayChannel,
}

impl SecureRelayTunnel {
    pub fn tunnel_id(&self) -> &str {
        &self.tunnel_id
    }

    pub fn host_id(&self) -> &str {
        &self.host_id
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn peer_device_id(&self) -> &str {
        &self.peer_device_id
    }

    pub fn control(&self) -> &SecureRelayChannel {
        &self.control
    }

    pub fn media(&self) -> &SecureRelayChannel {
        &self.media
    }
}

struct SecureFrameReader<R> {
    reader: R,
    header: [u8; 4],
    header_filled: usize,
    payload: Vec<u8>,
    payload_filled: usize,
}

impl<R: AsyncRead + Unpin> SecureFrameReader<R> {
    fn new(reader: R) -> Self {
        Self {
            reader,
            header: [0; 4],
            header_filled: 0,
            payload: Vec::new(),
            payload_filled: 0,
        }
    }

    async fn read_frame(&mut self, kind: RelayChannelKind) -> Result<Vec<u8>> {
        while self.header_filled < self.header.len() {
            let read = self
                .reader
                .read(&mut self.header[self.header_filled..])
                .await
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            if read == 0 {
                if self.header_filled == 0 {
                    return Err(RelayFailure::Closed);
                }
                return Err(RelayFailure::Transport(
                    "secure frame ended before length header".into(),
                ));
            }
            self.header_filled += read;
        }
        if self.payload.is_empty() && self.payload_filled == 0 {
            let length = u32::from_be_bytes(self.header) as usize;
            if length > channel_limit(kind) {
                return Err(RelayFailure::Limit(format!(
                    "{} payload exceeds limit",
                    channel_name(kind)
                )));
            }
            self.payload = vec![0; length];
        }
        while self.payload_filled < self.payload.len() {
            let read = self
                .reader
                .read(&mut self.payload[self.payload_filled..])
                .await
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            if read == 0 {
                return Err(RelayFailure::Transport(
                    "secure frame ended before payload".into(),
                ));
            }
            self.payload_filled += read;
        }
        self.header_filled = 0;
        self.payload_filled = 0;
        Ok(std::mem::take(&mut self.payload))
    }
}

pub struct SecureRelayChannel {
    kind: RelayChannelKind,
    writer: Mutex<WriteHalf<TlsStream<DuplexStream>>>,
    reader: Mutex<SecureFrameReader<ReadHalf<TlsStream<DuplexStream>>>>,
    _bridge: Arc<RelayBridge>,
    guard: GenerationGuard,
}

impl SecureRelayChannel {
    pub fn kind(&self) -> RelayChannelKind {
        self.kind
    }

    pub async fn send(&self, bytes: &[u8]) -> Result<()> {
        if bytes.len() > channel_limit(self.kind) {
            return Err(RelayFailure::Limit(format!(
                "{} payload exceeds limit",
                channel_name(self.kind)
            )));
        }
        self.guard.check()?;
        let length = u32::try_from(bytes.len())
            .map_err(|_| RelayFailure::Limit("secure frame is too large".into()))?;
        let mut writer = self.writer.lock().await;
        writer
            .write_all(&length.to_be_bytes())
            .await
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        writer
            .write_all(bytes)
            .await
            .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        writer
            .flush()
            .await
            .map_err(|error| RelayFailure::Transport(error.to_string()))
    }

    pub async fn recv(&self) -> Result<Vec<u8>> {
        self.guard.check()?;
        let mut reader = self.reader.lock().await;
        reader.read_frame(self.kind).await
    }

    /// Close the inner TLS stream only after the bridge has forwarded every
    /// encrypted byte already written to it to the Relay channel.
    pub async fn shutdown(&self) -> Result<()> {
        {
            let mut writer = self.writer.lock().await;
            writer
                .shutdown()
                .await
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
        }
        let mut done = self._bridge.outbound_done.clone();
        tokio::time::timeout(TUNNEL_TIMEOUT, async {
            while !*done.borrow() {
                done.changed().await.map_err(|_| {
                    RelayFailure::Transport("relay bridge ended before outbound drain".into())
                })?;
            }
            Ok::<(), RelayFailure>(())
        })
        .await
        .map_err(|_| RelayFailure::Transport("relay bridge outbound drain timed out".into()))??;
        if let Some(error) = self
            ._bridge
            .outbound_error
            .lock()
            .expect("relay bridge error owner")
            .clone()
        {
            return Err(RelayFailure::Transport(format!(
                "relay bridge outbound failed: {error}"
            )));
        }
        Ok(())
    }
}

struct RelayBridge {
    relay_to_tls: JoinHandle<()>,
    tls_to_relay: JoinHandle<()>,
    outbound_done: watch::Receiver<bool>,
    outbound_error: Arc<StdMutex<Option<String>>>,
}

impl Drop for RelayBridge {
    fn drop(&mut self) {
        self.relay_to_tls.abort();
        self.tls_to_relay.abort();
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelayChannelKind {
    Control,
    Media,
}

struct RelayChannelSocket {
    sink: Mutex<SplitSink<Socket, Message>>,
    stream: Mutex<SplitStream<Socket>>,
}

struct RelayControlSocket {
    sink: Mutex<SplitSink<Socket, Message>>,
    stream: Mutex<SplitStream<Socket>>,
}

#[derive(Clone)]
pub struct RelayChannel {
    kind: RelayChannelKind,
    socket: Arc<RelayChannelSocket>,
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
        let mut socket = self.socket.sink.lock().await;
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
            let message = {
                let mut socket = self.socket.stream.lock().await;
                tokio::select! {
                    changed = current.changed() => {
                        changed.map_err(|_| RelayFailure::Closed)?;
                        return Err(RelayFailure::Superseded);
                    }
                    message = socket.next() => message,
                }
            };
            let message = message
                .ok_or(RelayFailure::Closed)?
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            match message {
                Message::Ping(bytes) => {
                    let mut socket = self.socket.sink.lock().await;
                    socket
                        .send(Message::Pong(bytes))
                        .await
                        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
                }
                Message::Pong(_) => {}
                Message::Close(_) => return Err(RelayFailure::Closed),
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
    path: &str,
) -> Result<Socket> {
    let mut socket = relay.config.open_ws(path, None).await?;
    let challenge_text = next_text(&mut socket).await?;
    let challenge: AuthChallenge = parse_typed(&challenge_text, "auth.challenge")?;
    if challenge.path != path {
        return Err(RelayFailure::Protocol(format!(
            "Relay auth challenge path mismatch: expected {path}, got {}",
            challenge.path
        )));
    }
    validate_text(&challenge.nonce, "auth nonce", 256)?;
    send_json(
        &mut socket,
        &AuthProve {
            kind: "auth.prove",
            abi: RELAY_ABI_ID,
            token,
            device_id: &device.id,
            signature: device
                .identity
                .sign(&challenge.nonce, path, &device.id, token),
        },
    )
    .await?;
    let ready_text = next_text(&mut socket).await?;
    let ready: AuthOk = parse_typed(&ready_text, "auth.ok")?;
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
    side: u8,
) -> Result<RelayChannel> {
    guard.check()?;
    validate_tunnel_channel(&ticket, &tunnel_id, channel_name(kind), side)?;
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
                let (sink, stream) = socket.split();
                return Ok(RelayChannel {
                    kind,
                    socket: Arc::new(RelayChannelSocket {
                        sink: Mutex::new(sink),
                        stream: Mutex::new(stream),
                    }),
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RelayRole {
    Client,
    Host,
}

impl RelayRole {
    fn as_str(self) -> &'static str {
        match self {
            Self::Client => "client",
            Self::Host => "host",
        }
    }

    fn peer(self) -> Self {
        match self {
            Self::Client => Self::Host,
            Self::Host => Self::Client,
        }
    }
}

#[derive(Clone)]
enum RelayTlsIdentity {
    Client(RelayTlsClientIdentity),
    Server(RelayTlsServerIdentity),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TunnelHello {
    #[serde(rename = "type")]
    kind: String,
    version: u64,
    role: String,
    tunnel_id: String,
    host_id: String,
    session_id: String,
    channel: String,
    device_id: String,
    peer_device_id: String,
    cert_sha256: String,
    peer_cert_sha256: String,
    signature: String,
}

async fn secure_tunnel(
    tunnel: RelayTunnel,
    local: &RegisteredDevice,
    peer: &RelayPeerBinding,
    role: RelayRole,
    identity: RelayTlsIdentity,
) -> Result<SecureRelayTunnel> {
    if tunnel.peer_device_id != peer.relay_device_id {
        return Err(RelayFailure::IdentityMismatch);
    }
    let control = secure_channel(
        tunnel.control,
        &tunnel.tunnel_id,
        &tunnel.host_id,
        &tunnel.session_id,
        local,
        peer,
        RelayChannelKind::Control,
        role,
        identity.clone(),
    )
    .await?;
    let media = secure_channel(
        tunnel.media,
        &tunnel.tunnel_id,
        &tunnel.host_id,
        &tunnel.session_id,
        local,
        peer,
        RelayChannelKind::Media,
        role,
        identity,
    )
    .await?;
    Ok(SecureRelayTunnel {
        tunnel_id: tunnel.tunnel_id,
        host_id: tunnel.host_id,
        session_id: tunnel.session_id,
        peer_device_id: tunnel.peer_device_id,
        control,
        media,
    })
}

async fn secure_channel(
    channel: RelayChannel,
    tunnel_id: &str,
    host_id: &str,
    session_id: &str,
    local: &RegisteredDevice,
    peer: &RelayPeerBinding,
    kind: RelayChannelKind,
    role: RelayRole,
    identity: RelayTlsIdentity,
) -> Result<SecureRelayChannel> {
    let local_cert_sha256 = tls_identity_certificate_sha256(&identity)?;
    let guard = channel.guard.clone();
    let (io, bridge) = bridge_channel(channel);
    let (mut stream, exporter) = match identity {
        RelayTlsIdentity::Client(identity) => {
            let server_name = ServerName::try_from(identity.server_name.clone())
                .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
            let config = build_inner_client_config(&identity)?;
            let handshake = TlsConnector::from(config).connect(server_name, io);
            let stream = tokio::time::timeout(TUNNEL_TIMEOUT, handshake)
                .await
                .map_err(|_| RelayFailure::Transport("inner TLS handshake timed out".into()))?
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            let exporter = stream
                .get_ref()
                .1
                .export_keying_material([0u8; 32], TUNNEL_EXPORTER_LABEL, None)
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            (TlsStream::Client(stream), exporter)
        }
        RelayTlsIdentity::Server(identity) => {
            let config = build_inner_server_config(&identity)?;
            let handshake = TlsAcceptor::from(config).accept(io);
            let stream = tokio::time::timeout(TUNNEL_TIMEOUT, handshake)
                .await
                .map_err(|_| RelayFailure::Transport("inner TLS handshake timed out".into()))?
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            let exporter = stream
                .get_ref()
                .1
                .export_keying_material([0u8; 32], TUNNEL_EXPORTER_LABEL, None)
                .map_err(|error| RelayFailure::Transport(error.to_string()))?;
            (TlsStream::Server(stream), exporter)
        }
    };
    let peer_cert_sha256 = tls_peer_certificate_sha256(&stream)?;
    if peer_cert_sha256 != peer.certificate_sha256 {
        return Err(RelayFailure::IdentityMismatch);
    }
    let hello = make_tunnel_hello(
        local,
        peer,
        role,
        tunnel_id,
        host_id,
        session_id,
        kind,
        local_cert_sha256,
        &exporter,
    )?;
    send_tunnel_hello(&mut stream, &hello).await?;
    let received = receive_tunnel_hello(&mut stream).await?;
    verify_tunnel_hello(
        &received,
        local,
        peer,
        role,
        tunnel_id,
        host_id,
        session_id,
        kind,
        local_cert_sha256,
        &exporter,
    )?;
    let (reader, writer) = tokio::io::split(stream);
    Ok(SecureRelayChannel {
        kind,
        writer: Mutex::new(writer),
        reader: Mutex::new(SecureFrameReader::new(reader)),
        _bridge: bridge,
        guard,
    })
}

fn bridge_channel(channel: RelayChannel) -> (DuplexStream, Arc<RelayBridge>) {
    let (tls_io, bridge_io) = tokio::io::duplex(INNER_BRIDGE_BUFFER);
    let (mut bridge_reader, mut bridge_writer) = tokio::io::split(bridge_io);
    let (outbound_done_tx, outbound_done) = watch::channel(false);
    let outbound_error = Arc::new(StdMutex::new(None));
    let inbound = channel.clone();
    let outbound = channel;
    let outbound_error_owner = Arc::clone(&outbound_error);
    let relay_to_tls = tokio::spawn(async move {
        while let Ok(bytes) = inbound.recv().await {
            if bridge_writer.write_all(&bytes).await.is_err() {
                break;
            }
        }
    });
    let tls_to_relay = tokio::spawn(async move {
        let mut bytes = vec![0u8; 16 * 1024];
        let error = loop {
            let read = match bridge_reader.read(&mut bytes).await {
                Ok(0) => break None,
                Err(error) => break Some(error.to_string()),
                Ok(read) => read,
            };
            if let Err(error) = outbound.send(&bytes[..read]).await {
                break Some(error.to_string());
            }
        };
        *outbound_error_owner
            .lock()
            .expect("relay bridge error owner") = error;
        let _ = outbound_done_tx.send(true);
    });
    let bridge = Arc::new(RelayBridge {
        relay_to_tls,
        tls_to_relay,
        outbound_done,
        outbound_error,
    });
    (tls_io, bridge)
}

fn build_inner_client_config(identity: &RelayTlsClientIdentity) -> Result<Arc<ClientConfig>> {
    validate_tls_bytes(&identity.server_ca_der, "inner server CA")?;
    validate_tls_bytes(&identity.client_cert_der, "inner client certificate")?;
    validate_tls_bytes(&identity.client_key_pkcs8_der, "inner client key")?;
    let roots = inner_root_store(&identity.server_ca_der)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|error| RelayFailure::Configuration(error.to_string()))?
        .with_root_certificates(roots)
        .with_client_auth_cert(
            vec![CertificateDer::from(identity.client_cert_der.clone())],
            PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
                identity.client_key_pkcs8_der.clone(),
            )),
        )
        .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
    Ok(Arc::new(config))
}

fn build_inner_server_config(
    identity: &RelayTlsServerIdentity,
) -> Result<Arc<rustls::ServerConfig>> {
    validate_tls_bytes(&identity.server_cert_der, "inner server certificate")?;
    validate_tls_bytes(&identity.server_key_pkcs8_der, "inner server key")?;
    validate_tls_bytes(&identity.client_ca_der, "inner client CA")?;
    let roots = inner_root_store(&identity.client_ca_der)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
        Arc::new(roots),
        provider.clone(),
    )
    .build()
    .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
    let config = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|error| RelayFailure::Configuration(error.to_string()))?
        .with_client_cert_verifier(verifier)
        .with_single_cert(
            vec![CertificateDer::from(identity.server_cert_der.clone())],
            PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
                identity.server_key_pkcs8_der.clone(),
            )),
        )
        .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
    Ok(Arc::new(config))
}

fn inner_root_store(der: &[u8]) -> Result<RootCertStore> {
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from(der.to_vec()))
        .map_err(|error| RelayFailure::Configuration(error.to_string()))?;
    Ok(roots)
}

fn validate_tls_bytes(bytes: &[u8], name: &str) -> Result<()> {
    if bytes.is_empty() || bytes.len() > 1024 * 1024 {
        return Err(RelayFailure::Configuration(format!("invalid {name}")));
    }
    Ok(())
}

fn tls_identity_certificate_sha256(identity: &RelayTlsIdentity) -> Result<[u8; 32]> {
    let der = match identity {
        RelayTlsIdentity::Client(identity) => &identity.client_cert_der,
        RelayTlsIdentity::Server(identity) => &identity.server_cert_der,
    };
    validate_tls_bytes(der, "inner local certificate")?;
    Ok(sha256_array(der))
}

fn tls_peer_certificate_sha256(stream: &TlsStream<DuplexStream>) -> Result<[u8; 32]> {
    let certificate = stream
        .get_ref()
        .1
        .peer_certificates()
        .and_then(|certificates| certificates.first())
        .ok_or(RelayFailure::IdentityMismatch)?;
    Ok(sha256_array(certificate.as_ref()))
}

fn sha256_array(bytes: &[u8]) -> [u8; 32] {
    let digest = Sha256::digest(bytes);
    let mut output = [0u8; 32];
    output.copy_from_slice(&digest);
    output
}

fn make_tunnel_hello(
    local: &RegisteredDevice,
    peer: &RelayPeerBinding,
    role: RelayRole,
    tunnel_id: &str,
    host_id: &str,
    session_id: &str,
    kind: RelayChannelKind,
    local_cert_sha256: [u8; 32],
    exporter: &[u8; 32],
) -> Result<TunnelHello> {
    let mut hello = TunnelHello {
        kind: "tunnel.hello".into(),
        version: TUNNEL_HELLO_VERSION,
        role: role.as_str().into(),
        tunnel_id: tunnel_id.into(),
        host_id: host_id.into(),
        session_id: session_id.into(),
        channel: channel_name(kind).into(),
        device_id: local.id.clone(),
        peer_device_id: peer.relay_device_id.clone(),
        cert_sha256: hex_lower(&local_cert_sha256),
        peer_cert_sha256: hex_lower(&peer.certificate_sha256),
        signature: String::new(),
    };
    let transcript = tunnel_hello_transcript(&hello, exporter);
    hello.signature = local.identity.sign_bytes(&transcript);
    Ok(hello)
}

fn tunnel_hello_transcript(hello: &TunnelHello, exporter: &[u8; 32]) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!([
        "agentbrowser-relay-v0-tunnel-hello",
        hello.version,
        hello.role,
        hello.tunnel_id,
        hello.host_id,
        hello.session_id,
        hello.channel,
        hello.device_id,
        hello.peer_device_id,
        hello.cert_sha256,
        hello.peer_cert_sha256,
        URL_SAFE_NO_PAD.encode(exporter),
    ]))
    .expect("TunnelHello transcript is serializable")
}

async fn send_tunnel_hello(
    stream: &mut TlsStream<DuplexStream>,
    hello: &TunnelHello,
) -> Result<()> {
    let bytes =
        serde_json::to_vec(hello).map_err(|error| RelayFailure::Protocol(error.to_string()))?;
    if bytes.len() > MAX_TUNNEL_HELLO {
        return Err(RelayFailure::Limit("TunnelHello is too large".into()));
    }
    let length = u32::try_from(bytes.len())
        .map_err(|_| RelayFailure::Limit("TunnelHello is too large".into()))?;
    stream
        .write_all(&length.to_be_bytes())
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
    stream
        .write_all(&bytes)
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
    stream
        .flush()
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))
}

async fn receive_tunnel_hello(stream: &mut TlsStream<DuplexStream>) -> Result<TunnelHello> {
    let mut length = [0u8; 4];
    stream
        .read_exact(&mut length)
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
    let length = u32::from_be_bytes(length) as usize;
    if length == 0 || length > MAX_TUNNEL_HELLO {
        return Err(RelayFailure::Protocol("invalid TunnelHello length".into()));
    }
    let mut bytes = vec![0u8; length];
    stream
        .read_exact(&mut bytes)
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| RelayFailure::Protocol(format!("invalid TunnelHello: {error}")))
}

fn verify_tunnel_hello(
    hello: &TunnelHello,
    local: &RegisteredDevice,
    peer: &RelayPeerBinding,
    role: RelayRole,
    tunnel_id: &str,
    host_id: &str,
    session_id: &str,
    kind: RelayChannelKind,
    local_cert_sha256: [u8; 32],
    exporter: &[u8; 32],
) -> Result<()> {
    if hello.kind != "tunnel.hello"
        || hello.version != TUNNEL_HELLO_VERSION
        || hello.role != role.peer().as_str()
        || hello.tunnel_id != tunnel_id
        || hello.host_id != host_id
        || hello.session_id != session_id
        || hello.channel != channel_name(kind)
        || hello.device_id != peer.relay_device_id
        || hello.peer_device_id != local.id
        || hello.cert_sha256 != hex_lower(&peer.certificate_sha256)
        || hello.peer_cert_sha256 != hex_lower(&local_cert_sha256)
    {
        return Err(RelayFailure::IdentityMismatch);
    }
    validate_text(&hello.signature, "TunnelHello signature", 128)?;
    let signature_bytes = URL_SAFE_NO_PAD
        .decode(&hello.signature)
        .map_err(|_| RelayFailure::IdentityMismatch)?;
    let signature =
        Signature::from_slice(&signature_bytes).map_err(|_| RelayFailure::IdentityMismatch)?;
    let key = VerifyingKey::from_bytes(&peer.auth_public_key)
        .map_err(|_| RelayFailure::IdentityMismatch)?;
    let transcript = tunnel_hello_transcript(hello, exporter);
    key.verify(&transcript, &signature)
        .map_err(|_| RelayFailure::IdentityMismatch)
}

async fn send_json<T: Serialize>(socket: &mut Socket, value: &T) -> Result<()> {
    let text = json_text(value)?;
    socket
        .send(Message::Text(text.into()))
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))
}

async fn send_json_sink<T: Serialize>(
    sink: &mut SplitSink<Socket, Message>,
    value: &T,
) -> Result<()> {
    let text = json_text(value)?;
    sink.send(Message::Text(text.into()))
        .await
        .map_err(|error| RelayFailure::Transport(error.to_string()))
}

fn json_text<T: Serialize>(value: &T) -> Result<String> {
    let text =
        serde_json::to_string(value).map_err(|error| RelayFailure::Protocol(error.to_string()))?;
    if text.len() > MAX_WIRE_MESSAGE {
        return Err(RelayFailure::Limit(
            "Relay control message is too large".into(),
        ));
    }
    Ok(text)
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
    let value: Value = serde_json::from_str(text)
        .map_err(|error| RelayFailure::Protocol(format!("invalid Relay JSON: {error}")))?;
    if value.get("type").and_then(Value::as_str) != Some(expected) {
        return Err(RelayFailure::Protocol(format!(
            "expected {expected} message"
        )));
    }
    match value.get("abi").and_then(Value::as_str) {
        Some(RELAY_ABI_ID) => {}
        Some(_) => return Err(RelayFailure::Protocol("unsupported Relay ABI".into())),
        None => return Err(RelayFailure::Protocol("Relay ABI is missing".into())),
    }
    serde_json::from_value(value).map_err(|error| RelayFailure::Protocol(error.to_string()))
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

fn parse_tunnel_closed(text: &str) -> Result<TunnelClosed> {
    let closed: TunnelClosed = parse_typed(text, "tunnel.closed")?;
    validate_text(&closed.tunnel_id, "tunnel id", MAX_ID)?;
    validate_text(&closed.reason, "tunnel close reason", MAX_ID)?;
    Ok(closed)
}

fn is_host_rejection(reason: &str) -> bool {
    matches!(
        reason,
        "HOST_REJECTED_UNKNOWN_PEER" | "HOST_REJECTED_CAPACITY"
    )
}

fn parse_tunnel_offer(
    text: &str,
    expected_side: u8,
    expected_host_id: &str,
    expected_session_id: Option<&str>,
) -> Result<RelayTunnelOffer> {
    let offer: TunnelOfferWire = parse_typed(text, "tunnel.offer")?;
    validate_text(&offer.tunnel_id, "tunnel id", MAX_ID)?;
    validate_text(&offer.host_id, "host id", MAX_ID)?;
    validate_text(&offer.session_id, "session id", MAX_ID)?;
    validate_text(&offer.peer_device_id, "peer device id", MAX_ID)?;
    if offer.host_id != expected_host_id
        || expected_session_id.is_some_and(|value| value != offer.session_id)
    {
        return Err(RelayFailure::Protocol(
            "Relay tunnel offer identity mismatch".into(),
        ));
    }
    if offer.side != expected_side {
        return Err(RelayFailure::Protocol("Relay tunnel side mismatch".into()));
    }
    if offer.expires_at_ms <= now_ms() {
        return Err(RelayFailure::Remote {
            status: 401,
            code: "TUNNEL_EXPIRED".into(),
            message: "Relay tunnel offer expired".into(),
        });
    }
    validate_tunnel_channel(
        &offer.channels.control,
        &offer.tunnel_id,
        "control",
        expected_side,
    )?;
    validate_tunnel_channel(
        &offer.channels.media,
        &offer.tunnel_id,
        "media",
        expected_side,
    )?;
    Ok(RelayTunnelOffer {
        tunnel_id: offer.tunnel_id,
        host_id: offer.host_id,
        session_id: offer.session_id,
        peer_device_id: offer.peer_device_id,
        generation: 0,
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

fn validate_tunnel_channel(
    channel: &TunnelChannel,
    tunnel_id: &str,
    name: &str,
    side: u8,
) -> Result<()> {
    let segments = channel.path.split('/').collect::<Vec<_>>();
    if segments.len() != 6
        || segments[1] != "v2"
        || segments[2] != "tunnel"
        || segments[3] != tunnel_id
        || segments[4] != name
        || segments[5] != side.to_string()
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

fn validate_host_snapshot(snapshot: &HostSnapshot) -> Result<()> {
    validate_text(&snapshot.incarnation, "host incarnation", MAX_ID)?;
    if snapshot.endpoints.len() > 16 || snapshot.sessions.len() > 64 {
        return Err(RelayFailure::Limit("host snapshot limit exceeded".into()));
    }
    for endpoint in &snapshot.endpoints {
        validate_text(&endpoint.url, "endpoint", 1024)?;
        let url = Url::parse(&endpoint.url)
            .map_err(|_| RelayFailure::Protocol("invalid Relay endpoint".into()))?;
        if !matches!(url.scheme(), "wss" | "https" | "udp")
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(RelayFailure::Protocol("invalid Relay endpoint".into()));
        }
    }
    let mut sessions = HashSet::new();
    for session in &snapshot.sessions {
        validate_text(&session.id, "session id", MAX_ID)?;
        if !sessions.insert(&session.id) {
            return Err(RelayFailure::Protocol("duplicate Relay session".into()));
        }
    }
    Ok(())
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

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RegisterHostRequest<'a> {
    device_id: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HostPublishRequest<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    abi: &'static str,
    #[serde(rename = "hostId")]
    host_id: &'a str,
    snapshot: HostSnapshot,
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
    #[serde(rename = "abi")]
    _abi: String,
    nonce: String,
    path: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AuthProve<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    abi: &'static str,
    token: &'a str,
    device_id: &'a str,
    signature: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthOk {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "abi")]
    _abi: String,
    #[serde(rename = "deviceId")]
    device_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TunnelOpenRequest<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    abi: &'static str,
    host_id: &'a str,
    session_id: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TunnelRejectRequest<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    abi: &'static str,
    tunnel_id: &'a str,
    reason: RelayRejectReason,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireError {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "abi")]
    _abi: String,
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
    #[serde(rename = "abi")]
    _abi: String,
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
    #[serde(rename = "abi")]
    _abi: String,
    #[serde(rename = "tunnelId")]
    tunnel_id: String,
    #[serde(rename = "hostId")]
    host_id: String,
    #[serde(rename = "sessionId")]
    session_id: String,
    #[serde(rename = "peerDeviceId")]
    peer_device_id: String,
    side: u8,
    #[serde(rename = "expiresAt")]
    expires_at_ms: u64,
    channels: TunnelChannels,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct TunnelChannels {
    control: TunnelChannel,
    media: TunnelChannel,
}

#[derive(Debug, Clone, Deserialize)]
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
    #[serde(rename = "abi")]
    _abi: String,
    #[serde(rename = "tunnelId")]
    tunnel_id: String,
    channel: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TunnelClosed {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(rename = "abi")]
    _abi: String,
    #[serde(rename = "tunnelId")]
    tunnel_id: String,
    #[serde(rename = "reason")]
    reason: String,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct DirectoryHost {
    pub host_id: String,
    pub device_id: String,
    pub device_name: String,
    pub snapshot: HostSnapshot,
}

#[derive(Debug, Clone, Eq, PartialEq, Serialize)]
pub struct HostSnapshot {
    pub incarnation: String,
    pub revision: u64,
    pub endpoints: Vec<RelayEndpoint>,
    pub sessions: Vec<RelaySession>,
}

#[derive(Debug, Clone, Eq, PartialEq, Serialize)]
pub struct RelayEndpoint {
    pub network: RelayNetwork,
    pub url: String,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RelayNetwork {
    Lan,
    Public,
    Tailscale,
}

#[derive(Debug, Clone, Eq, PartialEq, Serialize)]
pub struct RelaySession {
    pub id: String,
}

#[derive(Debug, Clone)]
pub struct RelayTunnelOffer {
    tunnel_id: String,
    host_id: String,
    session_id: String,
    peer_device_id: String,
    generation: u64,
    channels: TunnelChannels,
}

impl RelayTunnelOffer {
    pub fn id(&self) -> &str {
        &self.tunnel_id
    }

    pub fn peer_device_id(&self) -> &str {
        &self.peer_device_id
    }

    pub fn host_id(&self) -> &str {
        &self.host_id
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }
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

    #[tokio::test]
    async fn secure_frame_reader_preserves_partial_frames_after_cancellation() {
        use tokio::io::AsyncWriteExt;

        let (mut writer, reader) = tokio::io::duplex(64);
        let mut framed = SecureFrameReader::new(reader);
        writer.write_all(&[0, 0]).await.expect("partial header");
        assert!(
            tokio::time::timeout(
                Duration::from_millis(50),
                framed.read_frame(RelayChannelKind::Control),
            )
            .await
            .is_err(),
            "partial header must wait for the rest of the frame"
        );
        writer
            .write_all(&[0, 3, b'a'])
            .await
            .expect("partial payload");
        assert!(
            tokio::time::timeout(
                Duration::from_millis(50),
                framed.read_frame(RelayChannelKind::Control),
            )
            .await
            .is_err(),
            "partial payload must wait for the rest of the frame"
        );
        writer.write_all(b"bc").await.expect("remaining payload");
        assert_eq!(
            framed
                .read_frame(RelayChannelKind::Control)
                .await
                .expect("complete frame"),
            b"abc"
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

    #[test]
    fn relay_control_wire_is_v0_and_legacy_auth_is_rejected() {
        let open = json_text(&TunnelOpenRequest {
            kind: "tunnel.open",
            abi: RELAY_ABI_ID,
            host_id: "host-1",
            session_id: "session-1",
        })
        .expect("serialize v0 tunnel.open");
        assert_eq!(
            open,
            r#"{"type":"tunnel.open","abi":"agentbrowser-relay-v0","hostId":"host-1","sessionId":"session-1"}"#
        );
        let publish = json_text(&HostPublishRequest {
            kind: "host.publish",
            abi: RELAY_ABI_ID,
            host_id: "host-1",
            snapshot: HostSnapshot {
                incarnation: "boot-1".into(),
                revision: 0,
                endpoints: vec![],
                sessions: vec![],
            },
        })
        .expect("serialize v0 host.publish");
        assert!(publish.contains(r#""abi":"agentbrowser-relay-v0""#));
        assert!(publish.contains(r#""hostId":"host-1""#));
        let reject = json_text(&TunnelRejectRequest {
            kind: "tunnel.reject",
            abi: RELAY_ABI_ID,
            tunnel_id: "tunnel-1",
            reason: RelayRejectReason::Capacity,
        })
        .expect("serialize v0 tunnel.reject");
        assert_eq!(
            reject,
            r#"{"type":"tunnel.reject","abi":"agentbrowser-relay-v0","tunnelId":"tunnel-1","reason":"CAPACITY"}"#
        );
        let prove = json_text(&AuthProve {
            kind: "auth.prove",
            abi: RELAY_ABI_ID,
            token: "token-1",
            device_id: "device-1",
            signature: "signature-1".into(),
        })
        .expect("serialize v0 auth.prove");
        assert_eq!(
            prove,
            r#"{"type":"auth.prove","abi":"agentbrowser-relay-v0","token":"token-1","deviceId":"device-1","signature":"signature-1"}"#
        );
        assert!(
            parse_typed::<AuthOk>(r#"{"type":"auth.ok","deviceId":"device-1"}"#, "auth.ok")
                .is_err()
        );
        assert!(
            parse_typed::<AuthOk>(r#"{"type":"auth.ready","deviceId":"device-1"}"#, "auth.ok")
                .is_err()
        );
        assert!(parse_typed::<AuthOk>(
            r#"{"type":"auth.ok","abi":"agentbrowser-relay-v1","deviceId":"device-1"}"#,
            "auth.ok"
        )
        .is_err());
    }
}
