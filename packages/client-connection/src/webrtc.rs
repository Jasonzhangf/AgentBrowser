//! WebRTC direct backend for the shared native connection owner.
//!
//! mTLS WSS remains the bootstrap/signaling and binding liveness channel. Once
//! the peer is authenticated, browser requests and responses use the single
//! typed DataChannel and video uses the negotiated H.264 RTP track.

use std::{
    collections::BTreeMap,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::Arc,
    time::{Duration, Instant},
};

use futures_util::{SinkExt, StreamExt};
use rtc::{
    media::io::sample_builder::SampleBuilder,
    peer_connection::configuration::media_engine::MIME_TYPE_H264,
    peer_connection::transport::RTCIceProtocol,
    rtp::codec::h264::H264Packet,
    rtp_transceiver::{
        rtp_sender::{RTCRtpCodec, RtpCodecKind},
        RTCRtpTransceiverDirection, RTCRtpTransceiverInit,
    },
};
use tokio::{
    sync::{mpsc, Notify},
    time::{sleep, timeout},
};
use tokio_tungstenite::tungstenite::Message;
use webrtc_rs::{
    data_channel::{DataChannel, DataChannelEvent},
    media_stream::track_remote::{TrackRemote, TrackRemoteEvent},
    peer_connection::{
        PeerConnection, PeerConnectionBuilder, PeerConnectionEventHandler, RTCConfiguration,
        RTCIceGatheringState, RTCSessionDescription,
    },
};

use crate::{
    media::{from_webrtc, MAX_ACCESS_UNIT},
    protocol::{
        Request, Response, SessionStatus, WebRtcCapability, WebRtcControlMessage,
        WebRtcSessionBinding, WebRtcSignal, WebRtcTransport, WebRtcVideoCodec, WebRtcVideoFrame,
        WEBRTC_CONTROL_LABEL, WEBRTC_H264_FMTP, WEBRTC_PROTOCOL_VERSION,
    },
    transport::{Backend, BackendFuture, Socket},
    Failure, Video,
};

const DEADLINE: Duration = Duration::from_secs(20);
const H264_CLOCK_RATE: u32 = 90_000;
const H264_PAYLOAD_TYPE: u8 = 102;
const MAX_DC_MESSAGE: usize = 1024 * 1024;
const MAX_ASSOCIATIONS: usize = 64;

#[derive(Clone, Copy, Debug)]
pub struct WebRtcConfig {
    /// Local address used for the explicit UDP ICE candidate. Use the
    /// interface address selected by the platform for LAN/Tailscale paths.
    pub bind_ip: IpAddr,
}

impl Default for WebRtcConfig {
    fn default() -> Self {
        Self {
            bind_ip: IpAddr::V4(Ipv4Addr::LOCALHOST),
        }
    }
}

pub(crate) struct WebRtcBackend {
    signaling: Socket,
    peer: Arc<dyn PeerConnection>,
    data_channel: Arc<dyn DataChannel>,
    track: Option<Arc<dyn TrackRemote>>,
    track_rx: mpsc::Receiver<Arc<dyn TrackRemote>>,
    builder: SampleBuilder<H264Packet>,
    descriptors: BTreeMap<u32, WebRtcVideoFrame>,
    samples: BTreeMap<u32, Vec<u8>>,
    session_id: String,
}

/// Close a peer if WebRTC setup is cancelled after the driver has started.
/// `Drop` cannot await the async peer API, so it schedules the same explicit
/// close used by a live backend. The normal connection path still awaits close
/// before publishing its terminal state.
struct PeerGuard(Option<Arc<dyn PeerConnection>>);

impl PeerGuard {
    fn new(peer: Arc<dyn PeerConnection>) -> Self {
        Self(Some(peer))
    }

    fn disarm(mut self) -> Arc<dyn PeerConnection> {
        self.0.take().expect("peer guard must own a peer")
    }
}

impl Drop for PeerGuard {
    fn drop(&mut self) {
        let Some(peer) = self.0.take() else {
            return;
        };
        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            return;
        };
        handle.spawn(async move {
            let _ = peer.close().await;
        });
    }
}

impl WebRtcBackend {
    pub(crate) async fn connect(
        mut signaling: Socket,
        status: &SessionStatus,
        mut id: u64,
        config: WebRtcConfig,
    ) -> Result<(Self, u64), Failure> {
        let session_id = status.session_id.clone();
        let attachment_id = status
            .attachment_id
            .ok_or_else(|| Failure::Protocol("WebRTC requires an attached Host session".into()))?;
        let capability = capability();

        let gathered = Arc::new(Notify::new());
        let (track_tx, track_rx) = mpsc::channel(1);
        let peer: Arc<dyn PeerConnection> = Arc::new(
            PeerConnectionBuilder::new()
                .with_configuration(RTCConfiguration::default())
                .with_media_engine(h264_media_engine()?)
                .with_handler(Arc::new(ReceiverHandler {
                    gathered: Arc::clone(&gathered),
                    track_tx,
                }))
                .with_udp_addrs(vec![SocketAddr::new(config.bind_ip, 0)])
                .build()
                .await
                .map_err(transport)?,
        );
        let peer_guard = PeerGuard::new(Arc::clone(&peer));
        peer.add_transceiver_from_kind(
            RtpCodecKind::Video,
            Some(RTCRtpTransceiverInit {
                direction: RTCRtpTransceiverDirection::Recvonly,
                ..Default::default()
            }),
        )
        .await
        .map_err(transport)?;
        let data_channel = peer
            .create_data_channel(WEBRTC_CONTROL_LABEL, None)
            .await
            .map_err(transport)?;
        let offer = peer.create_offer(None).await.map_err(transport)?;
        peer.set_local_description(offer).await.map_err(transport)?;
        wait_for_notify(&gathered, "client WebRTC ICE gathering").await?;
        let offer = peer
            .local_description()
            .await
            .ok_or_else(|| Failure::Protocol("WebRTC offer missing after ICE gathering".into()))?;

        id = next_id(id)?;
        send_signal(
            &mut signaling,
            &WebRtcSignal::Offer {
                id,
                capability: capability.clone(),
                sdp: offer.sdp,
            },
        )
        .await?;
        let answer = next_signal(&mut signaling).await?;
        let WebRtcSignal::Answer {
            id: answer_id,
            capability: answer_capability,
            binding,
            sdp,
        } = answer
        else {
            return Err(Failure::Protocol("Expected WebRTC signaling answer".into()));
        };
        if answer_id != id || answer_capability != capability {
            return Err(Failure::Protocol("WebRTC signaling answer mismatch".into()));
        }
        if binding.session_id != session_id
            || binding.attachment_id != attachment_id
            || binding.auth_binding.is_empty()
        {
            return Err(Failure::Protocol("WebRTC session binding mismatch".into()));
        }
        peer.set_remote_description(RTCSessionDescription::answer(sdp).map_err(transport)?)
            .await
            .map_err(transport)?;
        wait_for_open(&data_channel).await?;
        wait_for_udp_candidate_pair(&peer).await?;
        send_control(
            &data_channel,
            &WebRtcControlMessage::Hello {
                capability: capability.clone(),
                binding: binding.clone(),
            },
        )
        .await?;
        wait_for_hello_ack(&data_channel, &capability, &binding).await?;

        Ok((
            Self {
                signaling,
                peer: peer_guard.disarm(),
                data_channel,
                track: None,
                track_rx,
                builder: SampleBuilder::new(256, H264Packet::default(), H264_CLOCK_RATE)
                    .with_max_time_delay(Duration::from_secs(2)),
                descriptors: BTreeMap::new(),
                samples: BTreeMap::new(),
                session_id,
            },
            id,
        ))
    }

    async fn request_inner(&mut self, request: Request) -> Result<Response, Failure> {
        let encoded = serde_json::to_string(&WebRtcControlMessage::BrowserRequest {
            request: request.clone(),
        })
        .map_err(transport)?;
        if encoded.len() > MAX_DC_MESSAGE {
            return Err(Failure::Protocol("WebRTC browser request too large".into()));
        }
        self.data_channel
            .send_text(&encoded)
            .await
            .map_err(transport)?;
        loop {
            tokio::select! {
                event = self.data_channel.poll() => {
                    let event = event.ok_or(Failure::Closed)?;
                    if let Some(response) = self.handle_data_event(event, Some(request.id)).await? {
                        return Ok(response);
                    }
                }
                message = self.signaling.next() => self.handle_signaling_event(message).await?,
            }
        }
    }

    async fn close_inner(&mut self) -> Result<(), Failure> {
        let peer = self.peer.close().await.map_err(transport);
        let signaling = self.signaling.close(None).await.map_err(transport);
        peer.and(signaling)
    }

    async fn next_video_inner(&mut self) -> Result<Video, Failure> {
        loop {
            if let Some(video) = self.take_matching_video()? {
                return Ok(video);
            }

            if let Some(track) = &self.track {
                let track = Arc::clone(track);
                tokio::select! {
                    event = track.poll() => self.handle_track_event(event)?,
                    event = self.data_channel.poll() => {
                        let event = event.ok_or(Failure::Closed)?;
                        if self.handle_data_event(event, None).await?.is_some() {
                            return Err(Failure::Protocol("Unsolicited WebRTC browser response".into()));
                        }
                    }
                    message = self.signaling.next() => self.handle_signaling_event(message).await?,
                }
            } else {
                tokio::select! {
                    track = self.track_rx.recv() => {
                        self.track = Some(track.ok_or(Failure::Closed)?);
                    }
                    event = self.data_channel.poll() => {
                        let event = event.ok_or(Failure::Closed)?;
                        if self.handle_data_event(event, None).await?.is_some() {
                            return Err(Failure::Protocol("Unsolicited WebRTC browser response".into()));
                        }
                    }
                    message = self.signaling.next() => self.handle_signaling_event(message).await?,
                }
            }
        }
    }

    async fn handle_data_event(
        &mut self,
        event: DataChannelEvent,
        expected_response: Option<u64>,
    ) -> Result<Option<Response>, Failure> {
        match event {
            DataChannelEvent::OnMessage(message) => {
                if message.data.len() > MAX_DC_MESSAGE {
                    return Err(Failure::Protocol("WebRTC control message too large".into()));
                }
                let control: WebRtcControlMessage = serde_json::from_slice(&message.data)
                    .map_err(|error| Failure::Protocol(error.to_string()))?;
                match control {
                    WebRtcControlMessage::VideoFrame { descriptor } => {
                        self.accept_descriptor(descriptor)?;
                        Ok(None)
                    }
                    WebRtcControlMessage::Ping { request_id } => {
                        send_control(
                            &self.data_channel,
                            &WebRtcControlMessage::Pong { request_id },
                        )
                        .await?;
                        Ok(None)
                    }
                    WebRtcControlMessage::BrowserResponse { response } => {
                        let Some(expected) = expected_response else {
                            return Err(Failure::Protocol(
                                "Unsolicited WebRTC browser response".into(),
                            ));
                        };
                        if response_id(&response) != Some(expected) {
                            return Err(Failure::Protocol(
                                "WebRTC browser response ID mismatch".into(),
                            ));
                        }
                        Ok(Some(response))
                    }
                    WebRtcControlMessage::Error { code, message } => Err(Failure::Protocol(
                        format!("WebRTC endpoint {code}: {message}"),
                    )),
                    WebRtcControlMessage::Hello { .. }
                    | WebRtcControlMessage::HelloAck { .. }
                    | WebRtcControlMessage::BrowserRequest { .. }
                    | WebRtcControlMessage::Pong { .. } => Err(Failure::Protocol(
                        "Unexpected WebRTC control message".into(),
                    )),
                }
            }
            DataChannelEvent::OnError => Err(Failure::Transport("WebRTC DataChannel error".into())),
            DataChannelEvent::OnClose | DataChannelEvent::OnClosing => Err(Failure::Closed),
            DataChannelEvent::OnOpen => Ok(None),
            _ => Ok(None),
        }
    }

    fn handle_track_event(&mut self, event: Option<TrackRemoteEvent>) -> Result<(), Failure> {
        match event.ok_or(Failure::Closed)? {
            TrackRemoteEvent::OnRtpPacket(packet) => {
                self.builder.push(Instant::now(), packet);
                while let Some(sample) = self.builder.pop(Instant::now()) {
                    if sample.data.is_empty() || sample.data.len() > MAX_ACCESS_UNIT {
                        return Err(Failure::Protocol(
                            "Invalid reassembled WebRTC H.264 access unit".into(),
                        ));
                    }
                    if self
                        .samples
                        .insert(sample.packet_timestamp, sample.data.to_vec())
                        .is_some()
                    {
                        return Err(Failure::Protocol(
                            "Duplicate WebRTC RTP timestamp sample".into(),
                        ));
                    }
                    if self.samples.len() > MAX_ASSOCIATIONS {
                        return Err(Failure::Protocol(
                            "WebRTC RTP association backlog exceeded 64".into(),
                        ));
                    }
                }
                Ok(())
            }
            TrackRemoteEvent::OnError => Err(Failure::Transport("WebRTC H.264 track error".into())),
            TrackRemoteEvent::OnEnded => Err(Failure::Closed),
            TrackRemoteEvent::OnOpen(_) => Ok(()),
            TrackRemoteEvent::OnRtcpPacket(_) => Ok(()),
            _ => Ok(()),
        }
    }

    fn accept_descriptor(&mut self, descriptor: WebRtcVideoFrame) -> Result<(), Failure> {
        if descriptor.source.session_id != self.session_id {
            return Err(Failure::Protocol("WebRTC frame session mismatch".into()));
        }
        if self
            .descriptors
            .insert(descriptor.rtp_timestamp, descriptor)
            .is_some()
        {
            return Err(Failure::Protocol(
                "Duplicate WebRTC RTP timestamp descriptor".into(),
            ));
        }
        if self.descriptors.len() > MAX_ASSOCIATIONS {
            return Err(Failure::Protocol(
                "WebRTC descriptor association backlog exceeded 64".into(),
            ));
        }
        Ok(())
    }

    fn take_matching_video(&mut self) -> Result<Option<Video>, Failure> {
        let timestamp = self
            .samples
            .keys()
            .copied()
            .find(|timestamp| self.descriptors.contains_key(timestamp));
        let Some(timestamp) = timestamp else {
            return Ok(None);
        };
        let payload = self.samples.remove(&timestamp).ok_or_else(|| {
            Failure::Protocol("WebRTC RTP sample disappeared before association".into())
        })?;
        let descriptor = self.descriptors.remove(&timestamp).ok_or_else(|| {
            Failure::Protocol("WebRTC frame descriptor disappeared before association".into())
        })?;
        Ok(Some(from_webrtc(descriptor, payload)?))
    }

    async fn handle_signaling_event(
        &mut self,
        message: Option<Result<Message, tokio_tungstenite::tungstenite::Error>>,
    ) -> Result<(), Failure> {
        match message.ok_or(Failure::Closed)?.map_err(transport)? {
            Message::Ping(bytes) => {
                self.signaling
                    .send(Message::Pong(bytes))
                    .await
                    .map_err(transport)?;
                Ok(())
            }
            Message::Pong(_) => Ok(()),
            Message::Close(_) => Err(Failure::Closed),
            Message::Text(text) => match serde_json::from_str::<WebRtcSignal>(&text)
                .map_err(|error| Failure::Protocol(error.to_string()))?
            {
                WebRtcSignal::Error { code, message, .. } => Err(Failure::Protocol(format!(
                    "WebRTC signaling {code}: {message}"
                ))),
                _ => Err(Failure::Protocol(
                    "Unexpected WebRTC signaling message".into(),
                )),
            },
            _ => Err(Failure::Protocol(
                "Unexpected WebRTC signaling frame".into(),
            )),
        }
    }
}

impl Backend for WebRtcBackend {
    fn request<'a>(&'a mut self, request: Request) -> BackendFuture<'a, Response> {
        Box::pin(self.request_inner(request))
    }

    fn next_video<'a>(&'a mut self) -> BackendFuture<'a, Video> {
        Box::pin(self.next_video_inner())
    }

    fn close<'a>(&'a mut self) -> BackendFuture<'a, ()> {
        Box::pin(self.close_inner())
    }
}

fn capability() -> WebRtcCapability {
    WebRtcCapability {
        protocol_version: WEBRTC_PROTOCOL_VERSION,
        transport: WebRtcTransport::Udp,
        video_codec: WebRtcVideoCodec::H264AnnexB,
        data_channel_label: WEBRTC_CONTROL_LABEL.into(),
        max_access_unit: MAX_ACCESS_UNIT as u64,
    }
}

fn response_id(response: &Response) -> Option<u64> {
    match response {
        Response::Result { id, .. } | Response::Error { id, .. } => Some(*id),
        Response::Ready { .. } => None,
    }
}

fn next_id(id: u64) -> Result<u64, Failure> {
    id.checked_add(1)
        .ok_or_else(|| Failure::Protocol("Request ID exhausted".into()))
}

async fn send_signal(signaling: &mut Socket, signal: &WebRtcSignal) -> Result<(), Failure> {
    let text = serde_json::to_string(signal).map_err(transport)?;
    if text.len() > MAX_DC_MESSAGE {
        return Err(Failure::Protocol(
            "WebRTC signaling message too large".into(),
        ));
    }
    signaling
        .send(Message::Text(text.into()))
        .await
        .map_err(transport)
}

async fn next_signal(signaling: &mut Socket) -> Result<WebRtcSignal, Failure> {
    loop {
        match signaling
            .next()
            .await
            .ok_or(Failure::Closed)?
            .map_err(transport)?
        {
            Message::Ping(bytes) => {
                signaling
                    .send(Message::Pong(bytes))
                    .await
                    .map_err(transport)?;
            }
            Message::Pong(_) => {}
            Message::Close(_) => return Err(Failure::Closed),
            Message::Text(text) => {
                if text.len() > MAX_DC_MESSAGE {
                    return Err(Failure::Protocol(
                        "WebRTC signaling message too large".into(),
                    ));
                }
                return serde_json::from_str(&text)
                    .map_err(|error| Failure::Protocol(error.to_string()));
            }
            _ => {
                return Err(Failure::Protocol(
                    "Unexpected WebRTC signaling frame".into(),
                ))
            }
        }
    }
}

async fn send_control(
    data_channel: &Arc<dyn DataChannel>,
    message: &WebRtcControlMessage,
) -> Result<(), Failure> {
    let text = serde_json::to_string(message).map_err(transport)?;
    if text.len() > MAX_DC_MESSAGE {
        return Err(Failure::Protocol("WebRTC control message too large".into()));
    }
    data_channel.send_text(&text).await.map_err(transport)
}

async fn wait_for_hello_ack(
    data_channel: &Arc<dyn DataChannel>,
    capability: &WebRtcCapability,
    binding: &WebRtcSessionBinding,
) -> Result<(), Failure> {
    timeout(DEADLINE, async {
        loop {
            let event = data_channel.poll().await.ok_or(Failure::Closed)?;
            match event {
                DataChannelEvent::OnMessage(message) => {
                    if message.data.len() > MAX_DC_MESSAGE {
                        return Err(Failure::Protocol("WebRTC control message too large".into()));
                    }
                    match serde_json::from_slice::<WebRtcControlMessage>(&message.data)
                        .map_err(|error| Failure::Protocol(error.to_string()))?
                    {
                        WebRtcControlMessage::HelloAck {
                            capability: ack_capability,
                            binding: ack_binding,
                        } => {
                            if ack_capability != *capability || ack_binding != *binding {
                                return Err(Failure::Protocol(
                                    "WebRTC HelloAck binding mismatch".into(),
                                ));
                            }
                            return Ok(());
                        }
                        WebRtcControlMessage::Error { code, message } => {
                            return Err(Failure::Protocol(format!(
                                "WebRTC endpoint {code}: {message}"
                            )));
                        }
                        _ => {
                            return Err(Failure::Protocol(
                                "Unexpected WebRTC Hello response".into(),
                            ))
                        }
                    }
                }
                DataChannelEvent::OnError => {
                    return Err(Failure::Transport("WebRTC DataChannel error".into()))
                }
                DataChannelEvent::OnClose | DataChannelEvent::OnClosing => {
                    return Err(Failure::Closed)
                }
                _ => {}
            }
        }
    })
    .await
    .map_err(transport)??;
    Ok(())
}

async fn wait_for_open(data_channel: &Arc<dyn DataChannel>) -> Result<(), Failure> {
    let label = data_channel.label().await.map_err(transport)?;
    if label != WEBRTC_CONTROL_LABEL {
        return Err(Failure::Protocol(
            "WebRTC DataChannel label mismatch".into(),
        ));
    }
    timeout(DEADLINE, async {
        loop {
            match data_channel.poll().await.ok_or(Failure::Closed)? {
                DataChannelEvent::OnOpen => return Ok(()),
                DataChannelEvent::OnError => {
                    return Err(Failure::Transport("WebRTC DataChannel error".into()))
                }
                DataChannelEvent::OnClose | DataChannelEvent::OnClosing => {
                    return Err(Failure::Closed)
                }
                _ => {}
            }
        }
    })
    .await
    .map_err(transport)??;
    Ok(())
}

async fn wait_for_udp_candidate_pair(peer: &Arc<dyn PeerConnection>) -> Result<(), Failure> {
    let sctp = peer
        .sctp()
        .await
        .ok_or_else(|| Failure::Protocol("WebRTC SCTP transport missing".into()))?;
    let ice = sctp.transport().ice_transport();
    timeout(DEADLINE, async {
        loop {
            if let Some(pair) = ice.get_selected_candidate_pair().await.map_err(transport)? {
                if pair.local().protocol != RTCIceProtocol::Udp
                    || pair.remote().protocol != RTCIceProtocol::Udp
                {
                    return Err(Failure::Protocol(
                        "WebRTC selected non-UDP candidate pair".into(),
                    ));
                }
                return Ok(());
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .map_err(transport)??;
    Ok(())
}

async fn wait_for_notify(notify: &Notify, description: &str) -> Result<(), Failure> {
    timeout(DEADLINE, notify.notified())
        .await
        .map_err(|_| Failure::Transport(format!("Timed out waiting for {description}")))?;
    Ok(())
}

fn h264_media_engine() -> Result<webrtc_rs::peer_connection::MediaEngine, Failure> {
    let mut media_engine = webrtc_rs::peer_connection::MediaEngine::default();
    media_engine
        .register_codec(
            rtc::rtp_transceiver::rtp_sender::RTCRtpCodecParameters {
                rtp_codec: RTCRtpCodec {
                    mime_type: MIME_TYPE_H264.to_owned(),
                    clock_rate: H264_CLOCK_RATE,
                    channels: 0,
                    sdp_fmtp_line: WEBRTC_H264_FMTP.to_owned(),
                    rtcp_feedback: vec![],
                },
                payload_type: H264_PAYLOAD_TYPE,
            },
            RtpCodecKind::Video,
        )
        .map_err(transport)?;
    Ok(media_engine)
}

fn transport(error: impl std::fmt::Display) -> Failure {
    Failure::Transport(error.to_string())
}

struct ReceiverHandler {
    gathered: Arc<Notify>,
    track_tx: mpsc::Sender<Arc<dyn TrackRemote>>,
}

#[async_trait::async_trait]
impl PeerConnectionEventHandler for ReceiverHandler {
    async fn on_ice_gathering_state_change(&self, state: RTCIceGatheringState) {
        if state == RTCIceGatheringState::Complete {
            self.gathered.notify_one();
        }
    }

    async fn on_track(&self, track: Arc<dyn TrackRemote>) {
        let _ = self.track_tx.send(track).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::UdpSocket;

    #[tokio::test]
    async fn peer_guard_releases_udp_driver_after_setup_cancellation() {
        let address = reserve_udp_address();
        let gathered = Arc::new(Notify::new());
        let (track_tx, _track_rx) = mpsc::channel(1);
        let peer: Arc<dyn PeerConnection> = Arc::new(
            PeerConnectionBuilder::new()
                .with_configuration(RTCConfiguration::default())
                .with_media_engine(h264_media_engine().unwrap())
                .with_handler(Arc::new(ReceiverHandler { gathered, track_tx }))
                .with_udp_addrs(vec![address])
                .build()
                .await
                .unwrap(),
        );
        assert!(UdpSocket::bind(address).is_err());

        // This is the same ownership boundary used when WebRTC setup is
        // cancelled by a timeout before a backend is returned.
        drop(PeerGuard::new(Arc::clone(&peer)));
        wait_for_udp_release(address).await;
        drop(peer);
    }

    fn reserve_udp_address() -> SocketAddr {
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        socket.local_addr().unwrap()
    }

    async fn wait_for_udp_release(address: SocketAddr) {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(socket) = UdpSocket::bind(address) {
                    drop(socket);
                    return;
                }
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
    }
}
