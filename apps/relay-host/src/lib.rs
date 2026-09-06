//! Thin Relay Host network adapter.
//!
//! This crate owns Relay Host registration, the inner Relay TLS boundary, and
//! byte-preserving forwarding to an already-running Obscura endpoint. It does
//! not read Host sockets, interpret browser operations, or own Session state.

use std::{sync::Arc, time::Duration};

use agentbrowser_connection::{
    protocol::{Command, Mode, Request, Response, ResultValue},
    relay::{
        HostSnapshot, RelayClient, RelayEndpoint, RelayFailure, RelayNetwork, RelayPeerBinding,
        RelayTlsServerIdentity, SecureRelayChannel, SecureRelayTunnel,
    },
};
use futures_util::{stream::SplitSink, stream::SplitStream, SinkExt, StreamExt};
use rustls::{
    pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer, ServerName},
    ClientConfig, RootCertStore,
};
use thiserror::Error;
use tokio::{
    net::TcpStream,
    sync::{mpsc, Mutex},
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

const ENDPOINT_TIMEOUT: Duration = Duration::from_secs(15);
const DIRECTORY_REFRESH: Duration = Duration::from_secs(10);
const MAX_CONTROL_MESSAGE: usize = 64 * 1024;
const MAX_MEDIA_MESSAGE: usize = 4 * 1024 * 1024 + 4096;

type EndpointSocket = WebSocketStream<tokio_rustls::client::TlsStream<TcpStream>>;

#[derive(Debug, Error)]
pub enum RelayHostError {
    #[error(transparent)]
    Relay(#[from] RelayFailure),
    #[error("relay-host configuration: {0}")]
    Configuration(String),
    #[error("relay-host transport: {0}")]
    Transport(String),
    #[error("relay-host protocol: {0}")]
    Protocol(String),
}

pub type Result<T> = std::result::Result<T, RelayHostError>;

/// All bytes here are deployment inputs. Credentials and private keys are not
/// persisted or copied into Relay state.
pub struct RelayHostSettings {
    pub relay_origin: String,
    pub relay_ca_der: Vec<u8>,
    pub relay_username: String,
    pub relay_password: String,
    pub device_name: String,
    pub endpoint_url: String,
    pub endpoint_network: RelayNetwork,
    pub endpoint_ca_der: Vec<u8>,
    pub endpoint_client_cert_der: Vec<u8>,
    pub endpoint_client_key_pkcs8_der: Vec<u8>,
    pub inner_server: RelayTlsServerIdentity,
    pub peer: RelayPeerBinding,
    pub device_identity: agentbrowser_connection::relay::DeviceIdentity,
}

#[derive(Clone)]
struct EndpointTlsIdentity {
    ca_der: Vec<u8>,
    client_cert_der: Vec<u8>,
    client_key_pkcs8_der: Vec<u8>,
}

struct EndpointChannel {
    sink: Mutex<SplitSink<EndpointSocket, Message>>,
    stream: Mutex<SplitStream<EndpointSocket>>,
}

pub async fn run(settings: RelayHostSettings) -> Result<()> {
    let relay_config = agentbrowser_connection::relay::RelayConfig::new(
        &settings.relay_origin,
        settings.relay_ca_der,
    )?;
    let relay = RelayClient::login(
        relay_config,
        &settings.relay_username,
        &settings.relay_password,
    )
    .await?;
    let device = relay
        .register_device(&settings.device_name, settings.device_identity)
        .await?;
    let host = relay.register_host(&device).await?;
    let host_connection = Arc::new(relay.connector().connect_host(&host).await?);
    let endpoint_tls = EndpointTlsIdentity {
        ca_der: settings.endpoint_ca_der,
        client_cert_der: settings.endpoint_client_cert_der,
        client_key_pkcs8_der: settings.endpoint_client_key_pkcs8_der,
    };
    let snapshot = probe_endpoint(
        &settings.endpoint_url,
        &endpoint_tls,
        settings.endpoint_network,
    )
    .await?;
    host_connection.publish(snapshot.clone()).await?;

    let (heartbeat_error_tx, mut heartbeat_error_rx) = mpsc::channel(1);
    let heartbeat_connection = Arc::clone(&host_connection);
    let heartbeat_endpoint_url = settings.endpoint_url.clone();
    let heartbeat_endpoint_tls = endpoint_tls.clone();
    let heartbeat_network = settings.endpoint_network;
    let heartbeat = tokio::spawn(async move {
        let result: Result<()> = async move {
            let mut revision = snapshot.revision;
            let mut interval = tokio::time::interval(DIRECTORY_REFRESH);
            interval.tick().await;
            loop {
                interval.tick().await;
                let mut next = probe_endpoint(
                    &heartbeat_endpoint_url,
                    &heartbeat_endpoint_tls,
                    heartbeat_network,
                )
                .await?;
                revision = revision.checked_add(1).ok_or_else(|| {
                    RelayHostError::Protocol("Relay snapshot revision exhausted".into())
                })?;
                next.revision = revision;
                heartbeat_connection.publish(next).await?;
            }
        }
        .await;
        if let Err(error) = result {
            let _ = heartbeat_error_tx.send(error).await;
        }
    });

    let result = loop {
        tokio::select! {
            error = heartbeat_error_rx.recv() => {
                break Err(error.unwrap_or_else(|| RelayHostError::Transport("Relay snapshot refresh stopped".into())));
            }
            offer = host_connection.next_offer() => {
                let offer = match offer {
                    Ok(offer) => offer,
                    Err(error) => break Err(error.into()),
                };
                let tunnel = match host_connection
                    .accept_secure_offer(offer, settings.peer.clone(), settings.inner_server.clone())
                    .await
                {
                    Ok(tunnel) => tunnel,
                    Err(error) => break Err(error.into()),
                };
                if let Err(error) = forward_tunnel(&tunnel, &settings.endpoint_url, &endpoint_tls).await {
                    eprintln!("relay-host tunnel ended: {error}");
                }
            }
        }
    };
    heartbeat.abort();
    result
}

async fn probe_endpoint(
    endpoint_url: &str,
    identity: &EndpointTlsIdentity,
    network: RelayNetwork,
) -> Result<HostSnapshot> {
    let (mut control, _) = connect_endpoint(endpoint_url, identity, "/control", None).await?;
    let ready = next_endpoint_message(&mut control).await?;
    let session_id = match ready {
        Message::Text(text) => match serde_json::from_str::<Response>(&text) {
            Ok(Response::Ready {
                version: 4,
                session_id,
            }) if !session_id.is_empty() => session_id,
            Ok(Response::Ready { version, .. }) => {
                return Err(RelayHostError::Protocol(format!(
                    "unsupported Obscura endpoint version {version}"
                )))
            }
            Ok(other) => {
                return Err(RelayHostError::Protocol(format!(
                    "expected Obscura Ready response, got {other:?}"
                )))
            }
            Err(error) => {
                return Err(RelayHostError::Protocol(format!(
                    "invalid Obscura Ready response: {error}"
                )))
            }
        },
        other => {
            return Err(RelayHostError::Protocol(format!(
                "expected Obscura control text, got {other:?}"
            )))
        }
    };
    let attach = Request {
        id: 1,
        command: Command::Attach {
            mode: Mode::Observe,
            viewport: None,
        },
        operation: None,
    };
    send_endpoint_text(&mut control, &attach).await?;
    match next_endpoint_message(&mut control).await? {
        Message::Text(text) => match serde_json::from_str::<Response>(&text)
            .map_err(|error| RelayHostError::Protocol(error.to_string()))?
        {
            Response::Result {
                id: 1,
                value: ResultValue::Status(status),
            } if status.session_id == session_id && status.attachment_id.is_some() => {}
            other => {
                return Err(RelayHostError::Protocol(format!(
                    "expected Obscura Attach response, got {other:?}"
                )))
            }
        },
        other => {
            return Err(RelayHostError::Protocol(format!(
                "expected Obscura control text, got {other:?}"
            )))
        }
    }
    let request = Request {
        id: 2,
        command: Command::Status {},
        operation: None,
    };
    send_endpoint_text(&mut control, &request).await?;
    let status = match next_endpoint_message(&mut control).await? {
        Message::Text(text) => match serde_json::from_str::<Response>(&text)
            .map_err(|error| RelayHostError::Protocol(error.to_string()))?
        {
            Response::Result {
                id: 2,
                value: ResultValue::Status(status),
            } => status,
            other => {
                return Err(RelayHostError::Protocol(format!(
                    "expected Obscura Status response, got {other:?}"
                )))
            }
        },
        other => {
            return Err(RelayHostError::Protocol(format!(
                "expected Obscura control text, got {other:?}"
            )))
        }
    };
    if status.session_id != session_id {
        return Err(RelayHostError::Protocol(
            "Obscura Ready and Status session mismatch".into(),
        ));
    }
    let snapshot = HostSnapshot {
        incarnation: session_id.clone(),
        // Relay revision fences directory projections, not Browser document state.
        revision: 1,
        endpoints: vec![RelayEndpoint {
            network,
            url: endpoint_url.to_owned(),
        }],
        sessions: vec![agentbrowser_connection::relay::RelaySession { id: session_id }],
    };
    tokio::time::timeout(ENDPOINT_TIMEOUT, control.close(None))
        .await
        .map_err(|_| RelayHostError::Transport("Obscura probe close timed out".into()))?
        .map_err(|error| RelayHostError::Transport(error.to_string()))?;
    Ok(snapshot)
}

fn endpoint_url(origin: &str) -> Result<Url> {
    let url =
        Url::parse(origin).map_err(|error| RelayHostError::Configuration(error.to_string()))?;
    if url.scheme() != "wss"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        return Err(RelayHostError::Configuration(
            "Obscura endpoint must be a plain wss origin".into(),
        ));
    }
    Ok(url)
}

fn endpoint_tls_config(identity: &EndpointTlsIdentity) -> Result<Arc<ClientConfig>> {
    if identity.ca_der.is_empty()
        || identity.client_cert_der.is_empty()
        || identity.client_key_pkcs8_der.is_empty()
    {
        return Err(RelayHostError::Configuration(
            "Obscura endpoint TLS credentials are incomplete".into(),
        ));
    }
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from(identity.ca_der.clone()))
        .map_err(|error| RelayHostError::Configuration(error.to_string()))?;
    let config =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .map_err(|error| RelayHostError::Configuration(error.to_string()))?
            .with_root_certificates(roots)
            .with_client_auth_cert(
                vec![CertificateDer::from(identity.client_cert_der.clone())],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
                    identity.client_key_pkcs8_der.clone(),
                )),
            )
            .map_err(|error| RelayHostError::Configuration(error.to_string()))?;
    Ok(Arc::new(config))
}

async fn connect_endpoint(
    origin: &str,
    identity: &EndpointTlsIdentity,
    path: &str,
    bearer: Option<&str>,
) -> Result<(EndpointSocket, Option<String>)> {
    let origin = endpoint_url(origin)?;
    let host = origin
        .host_str()
        .ok_or_else(|| RelayHostError::Configuration("endpoint host is missing".into()))?;
    let port = origin.port().unwrap_or(443);
    let address = if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    };
    let tcp = tokio::time::timeout(ENDPOINT_TIMEOUT, TcpStream::connect(address))
        .await
        .map_err(|_| RelayHostError::Transport("Obscura TCP connect timed out".into()))?
        .map_err(|error| RelayHostError::Transport(error.to_string()))?;
    let server_name = ServerName::try_from(host.to_owned())
        .map_err(|error| RelayHostError::Configuration(error.to_string()))?;
    let tls = TlsConnector::from(endpoint_tls_config(identity)?);
    let stream = tokio::time::timeout(ENDPOINT_TIMEOUT, tls.connect(server_name, tcp))
        .await
        .map_err(|_| RelayHostError::Transport("Obscura TLS handshake timed out".into()))?
        .map_err(|error| RelayHostError::Transport(error.to_string()))?;
    let mut url = origin;
    url.set_path(path);
    let mut request = url
        .as_str()
        .into_client_request()
        .map_err(|error| RelayHostError::Transport(error.to_string()))?;
    if let Some(bearer) = bearer {
        let value = HeaderValue::from_str(&format!("Bearer {bearer}"))
            .map_err(|error| RelayHostError::Protocol(error.to_string()))?;
        request.headers_mut().insert(AUTHORIZATION, value);
    }
    let limit = if path == "/control" {
        MAX_CONTROL_MESSAGE
    } else {
        MAX_MEDIA_MESSAGE
    };
    let config = WebSocketConfig::default()
        .max_message_size(Some(limit))
        .max_frame_size(Some(limit));
    let (socket, response) = tokio::time::timeout(
        ENDPOINT_TIMEOUT,
        client_async_with_config(request, stream, Some(config)),
    )
    .await
    .map_err(|_| RelayHostError::Transport("Obscura WebSocket handshake timed out".into()))?
    .map_err(|error| RelayHostError::Transport(error.to_string()))?;
    let token = response
        .headers()
        .get("x-obscura-media-token")
        .map(|value| {
            value
                .to_str()
                .map(str::to_owned)
                .map_err(|error| RelayHostError::Protocol(error.to_string()))
        })
        .transpose()?;
    Ok((socket, token))
}

async fn next_endpoint_message(socket: &mut EndpointSocket) -> Result<Message> {
    loop {
        match socket
            .next()
            .await
            .ok_or_else(|| RelayHostError::Transport("Obscura endpoint closed".into()))?
        {
            Ok(Message::Ping(bytes)) => {
                tokio::time::timeout(ENDPOINT_TIMEOUT, socket.send(Message::Pong(bytes)))
                    .await
                    .map_err(|_| RelayHostError::Transport("Obscura Pong timed out".into()))?
                    .map_err(|error| RelayHostError::Transport(error.to_string()))?;
            }
            Ok(Message::Pong(_)) => {}
            Ok(Message::Close(_)) => {
                return Err(RelayHostError::Transport("Obscura endpoint closed".into()))
            }
            Ok(message) => return Ok(message),
            Err(error) => return Err(RelayHostError::Transport(error.to_string())),
        }
    }
}

async fn send_endpoint_text<T: serde::Serialize>(
    socket: &mut EndpointSocket,
    value: &T,
) -> Result<()> {
    let text = serde_json::to_string(value)
        .map_err(|error| RelayHostError::Protocol(error.to_string()))?;
    if text.len() > MAX_CONTROL_MESSAGE {
        return Err(RelayHostError::Protocol(
            "Obscura control request exceeds limit".into(),
        ));
    }
    tokio::time::timeout(ENDPOINT_TIMEOUT, socket.send(Message::Text(text.into())))
        .await
        .map_err(|_| RelayHostError::Transport("Obscura control send timed out".into()))?
        .map_err(|error| RelayHostError::Transport(error.to_string()))
}

async fn forward_tunnel(
    tunnel: &SecureRelayTunnel,
    endpoint_url: &str,
    endpoint_tls: &EndpointTlsIdentity,
) -> Result<()> {
    let (mut control, media_token) =
        connect_endpoint(endpoint_url, endpoint_tls, "/control", None).await?;
    let ready = next_endpoint_message(&mut control).await?;
    let Message::Text(ready) = ready else {
        return Err(RelayHostError::Protocol(
            "Obscura control Ready must be text".into(),
        ));
    };
    tunnel
        .control()
        .send(ready.as_ref())
        .await
        .map_err(RelayHostError::from)?;
    let (control_sink, control_stream) = control.split();
    let control = EndpointChannel {
        sink: Mutex::new(control_sink),
        stream: Mutex::new(control_stream),
    };
    forward_control_until_attached(tunnel.control(), &control).await?;
    let media_token = media_token.ok_or_else(|| {
        RelayHostError::Protocol("Obscura control did not return media token".into())
    })?;
    let (media, _) =
        connect_endpoint(endpoint_url, endpoint_tls, "/media", Some(&media_token)).await?;
    let (media_sink, media_stream) = media.split();
    let media = EndpointChannel {
        sink: Mutex::new(media_sink),
        stream: Mutex::new(media_stream),
    };
    let control_result = forward_control(tunnel.control(), control);
    let media_result = forward_media(tunnel.media(), media);
    tokio::select! {
        result = control_result => result,
        result = media_result => result,
    }
}

async fn endpoint_recv(endpoint: &EndpointChannel) -> Result<Option<Message>> {
    let mut stream = endpoint.stream.lock().await;
    match stream.next().await {
        None => Ok(None),
        Some(Ok(message)) => Ok(Some(message)),
        Some(Err(error)) => Err(RelayHostError::Transport(error.to_string())),
    }
}

async fn endpoint_send(endpoint: &EndpointChannel, message: Message) -> Result<()> {
    let mut sink = endpoint.sink.lock().await;
    tokio::time::timeout(ENDPOINT_TIMEOUT, sink.send(message))
        .await
        .map_err(|_| RelayHostError::Transport("Obscura endpoint send timed out".into()))?
        .map_err(|error| RelayHostError::Transport(error.to_string()))
}

async fn forward_control_until_attached(
    secure: &SecureRelayChannel,
    endpoint: &EndpointChannel,
) -> Result<()> {
    let mut pending_attach_id = None;
    loop {
        tokio::select! {
            frame = secure.recv() => {
                let bytes = frame.map_err(RelayHostError::from)?;
                let text = String::from_utf8(bytes)
                    .map_err(|_| RelayHostError::Protocol("Relay control frame is not UTF-8".into()))?;
                if text.len() > MAX_CONTROL_MESSAGE {
                    return Err(RelayHostError::Protocol("Relay control frame exceeds limit".into()));
                }
                let request = serde_json::from_str::<Request>(&text)
                    .map_err(|error| RelayHostError::Protocol(format!("invalid Relay control request: {error}")))?;
                if matches!(request.command, Command::Attach { .. }) {
                    pending_attach_id = Some(request.id);
                }
                endpoint_send(endpoint, Message::Text(text.into())).await?;
            }
            message = endpoint_recv(endpoint) => {
                match message? {
                    None | Some(Message::Close(_)) => {
                        return Err(RelayHostError::Transport(
                            "Obscura control closed before attachment".into(),
                        ))
                    }
                    Some(Message::Ping(bytes)) => endpoint_send(endpoint, Message::Pong(bytes)).await?,
                    Some(Message::Pong(_)) => {}
                    Some(Message::Text(text)) => {
                        let attached = attachment_response(&mut pending_attach_id, text.as_ref())?;
                        secure.send(text.as_bytes()).await.map_err(RelayHostError::from)?;
                        if attached {
                            return Ok(());
                        }
                    }
                    Some(Message::Binary(_)) => {
                        return Err(RelayHostError::Protocol(
                            "Obscura control sent binary".into(),
                        ))
                    }
                    Some(_) => {
                        return Err(RelayHostError::Protocol(
                            "unexpected Obscura control frame".into(),
                        ))
                    }
                }
            }
        }
    }
}

fn attachment_response(pending_attach_id: &mut Option<u64>, text: &str) -> Result<bool> {
    let Some(request_id) = *pending_attach_id else {
        return Ok(false);
    };
    let response = serde_json::from_str::<Response>(text).map_err(|error| {
        RelayHostError::Protocol(format!(
            "invalid Obscura control response while waiting for Attach: {error}"
        ))
    })?;
    match response {
        Response::Result { id, value } if id == request_id => {
            *pending_attach_id = None;
            Ok(matches!(
                value,
                ResultValue::Status(status)
                    if status.attachment_id.is_some() && status.mode.is_some()
            ))
        }
        Response::Error { id, .. } if id == request_id => {
            *pending_attach_id = None;
            Ok(false)
        }
        _ => Ok(false),
    }
}

async fn forward_control(secure: &SecureRelayChannel, endpoint: EndpointChannel) -> Result<()> {
    loop {
        tokio::select! {
            frame = secure.recv() => {
                let bytes = frame.map_err(RelayHostError::from)?;
                let text = String::from_utf8(bytes)
                    .map_err(|_| RelayHostError::Protocol("Relay control frame is not UTF-8".into()))?;
                if text.len() > MAX_CONTROL_MESSAGE {
                    return Err(RelayHostError::Protocol("Relay control frame exceeds limit".into()));
                }
                endpoint_send(&endpoint, Message::Text(text.into())).await?;
            }
            message = endpoint_recv(&endpoint) => {
                match message? {
                    None | Some(Message::Close(_)) => return Ok(()),
                    Some(Message::Ping(bytes)) => endpoint_send(&endpoint, Message::Pong(bytes)).await?,
                    Some(Message::Pong(_)) => {},
                    Some(Message::Text(text)) => secure.send(text.as_bytes()).await.map_err(RelayHostError::from)?,
                    Some(Message::Binary(_)) => return Err(RelayHostError::Protocol("Obscura control sent binary".into())),
                    Some(_) => return Err(RelayHostError::Protocol("unexpected Obscura control frame".into())),
                }
            }
        }
    }
}

async fn forward_media(secure: &SecureRelayChannel, endpoint: EndpointChannel) -> Result<()> {
    loop {
        tokio::select! {
            frame = secure.recv() => {
                frame.map_err(RelayHostError::from)?;
                return Err(RelayHostError::Protocol("Relay media channel is read-only".into()));
            }
            message = endpoint_recv(&endpoint) => {
                match message? {
                    None | Some(Message::Close(_)) => return Ok(()),
                    Some(Message::Ping(bytes)) => endpoint_send(&endpoint, Message::Pong(bytes)).await?,
                    Some(Message::Pong(_)) => {},
                    Some(Message::Binary(bytes)) => secure.send(&bytes).await.map_err(RelayHostError::from)?,
                    Some(Message::Text(_)) => return Err(RelayHostError::Protocol("Obscura media sent text".into())),
                    Some(_) => return Err(RelayHostError::Protocol("unexpected Obscura media frame".into())),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn status_response(id: u64, attachment_id: Option<u64>, mode: Option<&str>) -> String {
        serde_json::json!({
            "type": "result",
            "id": id,
            "value": {
                "session_id": "session",
                "attachment_id": attachment_id,
                "mode": mode,
                "attachments": 1,
                "agent_attached": false,
                "operation_running": false,
                "control": {"epoch": 0, "phase": {"type": "waiting", "attachment_id": 3}},
                "fault": null,
                "next_sequence": 0,
                "viewport_revision": 0,
                "document_revision": 0,
                "viewport": null,
                "viewport_owner": null,
                "viewport_pending": false,
            }
        })
        .to_string()
    }

    #[test]
    fn media_gate_waits_for_matching_successful_attach() {
        let mut pending = Some(7);
        assert!(
            !attachment_response(&mut pending, &status_response(8, Some(3), Some("observe")),)
                .expect("parse unrelated response")
        );
        assert_eq!(pending, Some(7));
        assert!(
            !attachment_response(&mut pending, &status_response(7, None, None),)
                .expect("parse unsuccessful response")
        );
        assert_eq!(pending, None);

        let mut pending = Some(9);
        assert!(
            attachment_response(&mut pending, &status_response(9, Some(4), Some("observe")),)
                .expect("parse successful response")
        );
        assert_eq!(pending, None);
    }
}
