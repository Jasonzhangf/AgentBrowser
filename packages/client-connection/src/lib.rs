//! Native connection boundary. Browser ABI remains owned by Obscura.
pub use obscura_host_protocol as protocol;
// Relay v2 account/device and opaque channels; Browser ABI stays in protocol.
pub mod relay;
mod media;
pub use media::{decode_video, MediaSequence, Video};
mod transport;
pub use transport::{Connection, Connector, DisplayedFrame, Input, Pairing};
mod webrtc;
pub use webrtc::WebRtcConfig;

#[derive(Debug, Clone, thiserror::Error)]
pub enum Failure {
    #[error("Protocol: {0}")]
    Protocol(String),
    #[error("Transport: {0}")]
    Transport(String),
    #[error("Host {code}: {message}")]
    Host { code: String, message: String },
    #[error("Connection ended or was superseded")]
    Closed,
    #[error("Operation outcome unknown; do not replay")]
    OutcomeUnknown,
}
