use crate::{
    decode_video,
    protocol::{Command, Mode, Request, Response, SessionStatus, ViewportDeclaration},
    relay::{
        RegisteredDevice, RelayClient, RelayConnection, RelayFailure, RelayPeerBinding,
        RelayTlsClientIdentity, SecureRelayTunnel,
    },
    transport::{response_value, status, Backend, BackendFuture},
    Failure, Video,
};
use std::time::Duration;

const CONTROL_TIMEOUT: Duration = Duration::from_secs(17);
const MEDIA_TIMEOUT: Duration = Duration::from_secs(20);

/// Relay v2 adapter for the shared browser `Connection` action/media pump.
///
/// The Relay control object must remain owned for as long as the tunnel does:
/// its generation guard is shared by both secure channels and dropping it
/// would fence an otherwise live tunnel.
pub(crate) struct RelayBackend {
    connection: Option<RelayConnection>,
    tunnel: Option<SecureRelayTunnel>,
}

impl RelayBackend {
    pub(crate) async fn connect(
        relay: RelayClient,
        device: RegisteredDevice,
        host_id: &str,
        session_id: &str,
        peer: RelayPeerBinding,
        tls: RelayTlsClientIdentity,
        viewport: Option<ViewportDeclaration>,
    ) -> Result<(Self, SessionStatus, u64), Failure> {
        let connection = relay
            .connector()
            .connect(&device)
            .await
            .map_err(map_relay_failure)?;
        let tunnel = connection
            .open_secure_tunnel(host_id, session_id, peer, tls)
            .await
            .map_err(map_relay_failure)?;
        if tunnel.session_id() != session_id {
            return Err(Failure::Protocol(
                "Relay tunnel session does not match requested session".into(),
            ));
        }

        let mut backend = Self {
            connection: Some(connection),
            tunnel: Some(tunnel),
        };
        let ready = backend.receive_control().await?;
        let ready_session = match ready {
            Response::Ready {
                version: 4,
                session_id,
            } => session_id,
            Response::Ready { version, .. } => {
                return Err(Failure::Protocol(format!(
                    "Expected Host protocol version 4, got {version}"
                )))
            }
            other => {
                return Err(Failure::Protocol(format!(
                    "Expected Host Ready response, got {other:?}"
                )))
            }
        };
        if ready_session != session_id {
            return Err(Failure::Protocol(
                "Host Ready session does not match Relay tunnel session".into(),
            ));
        }

        let attached = status(response_value(
            1,
            backend
                .request_inner(Request {
                    id: 1,
                    command: Command::Attach {
                        mode: Mode::Observe,
                        viewport,
                    },
                    operation: None,
                })
                .await?,
        )?)?;
        if attached.session_id != session_id
            || attached.attachment_id.is_none()
            || attached.mode != Some(Mode::Observe)
        {
            return Err(Failure::Protocol(
                "Invalid Relay observation attachment".into(),
            ));
        }
        Ok((backend, attached, 1))
    }

    async fn request_inner(&mut self, request: Request) -> Result<Response, Failure> {
        let tunnel = self.tunnel.as_ref().ok_or(Failure::Closed)?;
        let bytes = serde_json::to_vec(&request).map_err(protocol_error)?;
        tokio::time::timeout(CONTROL_TIMEOUT, tunnel.control().send(&bytes))
            .await
            .map_err(|_| Failure::Transport("Relay control send timed out".into()))?
            .map_err(map_relay_failure)?;
        let response = self.receive_control().await?;
        Ok(response)
    }

    async fn receive_control(&self) -> Result<Response, Failure> {
        let tunnel = self.tunnel.as_ref().ok_or(Failure::Closed)?;
        let bytes = tokio::time::timeout(CONTROL_TIMEOUT, tunnel.control().recv())
            .await
            .map_err(|_| Failure::Transport("Relay control receive timed out".into()))?
            .map_err(map_relay_failure)?;
        serde_json::from_slice(&bytes).map_err(protocol_error)
    }

    async fn next_video_inner(&self) -> Result<Video, Failure> {
        let tunnel = self.tunnel.as_ref().ok_or(Failure::Closed)?;
        let bytes = tokio::time::timeout(MEDIA_TIMEOUT, tunnel.media().recv())
            .await
            .map_err(|_| Failure::Transport("Relay media receive timed out".into()))?
            .map_err(map_relay_failure)?;
        decode_video(&bytes)
    }
}

impl Backend for RelayBackend {
    fn request<'a>(&'a mut self, request: Request) -> BackendFuture<'a, Response> {
        Box::pin(self.request_inner(request))
    }

    fn next_video<'a>(&'a mut self) -> BackendFuture<'a, Video> {
        Box::pin(self.next_video_inner())
    }

    fn close<'a>(&'a mut self) -> BackendFuture<'a, ()> {
        Box::pin(async move {
            let (media_result, control_result) = if let Some(tunnel) = self.tunnel.as_ref() {
                // Close media first so the Obscura endpoint can emit its
                // terminal media marker before control attachment teardown.
                let media_result = tunnel.media().shutdown().await.map_err(map_relay_failure);
                let control_result = tunnel.control().shutdown().await.map_err(map_relay_failure);
                (media_result, control_result)
            } else {
                (Ok(()), Ok(()))
            };
            self.tunnel.take();
            self.connection.take();
            match (media_result, control_result) {
                (Ok(()), Ok(())) => Ok(()),
                (Err(error), Ok(())) | (Ok(()), Err(error)) => Err(error),
                (Err(media), Err(control)) => Err(Failure::Transport(format!(
                    "media close failed: {media}; control close failed: {control}"
                ))),
            }
        })
    }
}

fn protocol_error(error: impl std::fmt::Display) -> Failure {
    Failure::Protocol(error.to_string())
}

fn map_relay_failure(error: RelayFailure) -> Failure {
    match error {
        RelayFailure::Closed | RelayFailure::Superseded => Failure::Closed,
        RelayFailure::Protocol(message) | RelayFailure::Limit(message) => {
            Failure::Protocol(message)
        }
        RelayFailure::IdentityMismatch => Failure::Protocol("Relay identity mismatch".into()),
        other => Failure::Transport(other.to_string()),
    }
}
