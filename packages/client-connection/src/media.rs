use crate::{
    protocol::{VideoCodec, VideoPacket, WebRtcVideoCodec, WebRtcVideoFrame},
    Failure,
};

const MAX_HEADER: usize = 4095;
pub(crate) const MAX_ACCESS_UNIT: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Video {
    pub packet: VideoPacket,
    pub bytes: Vec<u8>,
}

fn require(ok: bool, message: &str) -> Result<(), Failure> {
    if ok {
        Ok(())
    } else {
        Err(Failure::Protocol(message.into()))
    }
}

fn validate_access_unit(packet: &VideoPacket, payload: &[u8]) -> Result<(), Failure> {
    let VideoPacket::AccessUnit {
        source,
        encoder_id,
        coded_width,
        coded_height,
        codec,
        keyframe,
        byte_length,
        ..
    } = packet
    else {
        return Err(Failure::Protocol("Expected an H.264 access unit".into()));
    };
    require(
        matches!(codec, VideoCodec::H264AnnexB),
        "Expected H.264 Annex B",
    )?;
    require(
        *byte_length > 0
            && *byte_length <= MAX_ACCESS_UNIT as u64
            && *byte_length == payload.len() as u64,
        "Invalid access unit length",
    )?;
    require(
        !encoder_id.is_empty() && encoder_id.len() <= 128 && *keyframe,
        "Expected independently decodable frame and encoder identity",
    )?;
    let width = source.width as u64;
    let height = source.height as u64;
    require(
        width > 0 && height > 0 && width * height <= 4 * 1024 * 1024,
        "Invalid visible dimensions",
    )?;
    require(
        source.stride as u64 == width * 4 && source.byte_length == width * height * 4,
        "Invalid raw frame descriptor",
    )?;
    require(
        *coded_width as u64 == (width + 1) & !1 && *coded_height as u64 == (height + 1) & !1,
        "Invalid padded dimensions",
    )?;
    require(
        payload.starts_with(&[0, 0, 0, 1]) || payload.starts_with(&[0, 0, 1]),
        "Expected Annex B start code",
    )
}

/// Validate framing before allocating the encoded payload. Native decoders may
/// impose a smaller explicit limit; they must reject, never truncate, an AU.
pub fn decode_video(wire: &[u8]) -> Result<Video, Failure> {
    require(wire.len() >= 4, "Missing media header length")?;
    let size = u32::from_be_bytes(wire[..4].try_into().unwrap()) as usize;
    require(
        size > 0 && size <= MAX_HEADER && size <= wire.len() - 4,
        "Invalid media header length",
    )?;
    let packet: VideoPacket = serde_json::from_slice(&wire[4..4 + size])
        .map_err(|error| Failure::Protocol(error.to_string()))?;
    let payload = &wire[4 + size..];
    match &packet {
        VideoPacket::AccessUnit { .. } => validate_access_unit(&packet, payload)?,
        _ => require(payload.is_empty(), "State packet contains media bytes")?,
    }
    Ok(Video {
        packet,
        bytes: payload.to_vec(),
    })
}

/// Rebuild one access unit from the typed DC descriptor and the matching RTP
/// sample. The descriptor supplies all source identity; no revision or encoder
/// field is inferred from RTP, timing, or local state.
pub(crate) fn from_webrtc(
    descriptor: WebRtcVideoFrame,
    payload: Vec<u8>,
) -> Result<Video, Failure> {
    require(
        matches!(descriptor.codec, WebRtcVideoCodec::H264AnnexB),
        "Expected H.264 Annex B WebRTC media",
    )?;
    let packet = VideoPacket::AccessUnit {
        source: descriptor.source,
        encoder_id: descriptor.encoder_id,
        pts_us: descriptor.pts_us,
        coded_width: descriptor.coded_width,
        coded_height: descriptor.coded_height,
        codec: VideoCodec::H264AnnexB,
        keyframe: descriptor.keyframe,
        byte_length: descriptor.access_unit_bytes,
    };
    validate_access_unit(&packet, &payload)?;
    Ok(Video {
        packet,
        bytes: payload,
    })
}

/// Per-connection media continuity. Failure leaves the last accepted identity
/// intact. The transport must close the connection on any validation failure.
pub struct MediaSequence {
    session: String,
    last: Option<(String, u64, u64, u64, u64, u32, u32)>,
    closed: bool,
}

impl MediaSequence {
    pub fn new(session: String) -> Self {
        Self {
            session,
            last: None,
            closed: false,
        }
    }

    pub fn accept(&mut self, video: &Video) -> Result<(), Failure> {
        require(!self.closed, "Media after session close")?;
        match &video.packet {
            VideoPacket::AccessUnit {
                source,
                encoder_id,
                pts_us,
                ..
            } => {
                require(source.session_id == self.session, "Media session mismatch")?;
                if let Some((encoder, sequence, pts, document, viewport, width, height)) =
                    &self.last
                {
                    require(
                        encoder == encoder_id
                            && source.sequence > *sequence
                            && pts_us > pts
                            && source.document_revision >= *document
                            && source.viewport_revision >= *viewport,
                        "Media identity or revisions regressed",
                    )?;
                    require(
                        source.viewport_revision != *viewport
                            || (source.width == *width && source.height == *height),
                        "Dimensions changed without viewport revision",
                    )?;
                }
                self.last = Some((
                    encoder_id.clone(),
                    source.sequence,
                    *pts_us,
                    source.document_revision,
                    source.viewport_revision,
                    source.width,
                    source.height,
                ));
            }
            VideoPacket::Waiting { session_id }
            | VideoPacket::Unavailable { session_id, .. }
            | VideoPacket::Closed { session_id } => {
                require(*session_id == self.session, "Media session mismatch")?;
                self.closed = matches!(video.packet, VideoPacket::Closed { .. });
            }
        }
        Ok(())
    }
}
