use agentbrowser_connection::{decode_video, Failure};

fn wire(packet: &serde_json::Value, payload: &[u8]) -> Vec<u8> {
    let header = serde_json::to_vec(packet).unwrap();
    let mut bytes = (header.len() as u32).to_be_bytes().to_vec();
    bytes.extend(header); bytes.extend(payload); bytes
}

fn frame() -> serde_json::Value {
    serde_json::json!({"type":"access_unit","source":{
        "session_id":"s","tab_id":"tab-a","presentation_revision":1,"sequence":1,"document_revision":1,"viewport_revision":1,
        "width":391,"height":845,"stride":1564,"byte_length":1321580,
        "pixel_format":"premultiplied_rgba8"},"encoder_id":"e","pts_us":1,
        "coded_width":392,"coded_height":846,"codec":"h264_annex_b","keyframe":true,"byte_length":5})
}

#[test]
fn validates_raw_dimensions_padding_and_payload_bounds() {
    let original = frame();
    let payload = [0, 0, 0, 1, 0x65];
    assert!(decode_video(&wire(&original, &payload)).is_ok());
    for field in ["coded_width", "coded_height", "byte_length"] {
        let mut packet = original.clone(); packet[field] = 1.into();
        assert!(decode_video(&wire(&packet, &payload)).is_err(), "{field}");
    }
    for field in ["width", "height", "stride", "byte_length"] {
        let mut packet = original.clone(); packet["source"][field] = u32::MAX.into();
        assert!(decode_video(&wire(&packet, &payload)).is_err(), "{field}");
    }
    assert!(decode_video(&wire(&original, &[1, 2, 3, 4, 5])).is_err());
    assert!(decode_video(&wire(&original, &[0; 4 * 1024 * 1024 + 1])).is_err());
}

#[test]
fn rejects_session_encoder_and_revision_changes_without_poisoning_last_frame() {
    use agentbrowser_connection::MediaSequence;
    let mut sequence = MediaSequence::new("s".into());
    let original = frame(); let payload = [0, 0, 0, 1, 0x65];
    let video = decode_video(&wire(&original, &payload)).unwrap();
    sequence.accept(&video).unwrap();
    assert!(sequence.accept(&video).is_err());
    let mut next = original.clone(); next["source"]["sequence"] = 2.into(); next["pts_us"] = 2.into();
    let mut foreign = next.clone(); foreign["source"]["session_id"] = "other".into();
    assert!(sequence.accept(&decode_video(&wire(&foreign, &payload)).unwrap()).is_err());
    let mut encoder = next.clone(); encoder["encoder_id"] = "other".into();
    assert!(sequence.accept(&decode_video(&wire(&encoder, &payload)).unwrap()).is_err());
    let mut stale = next.clone(); stale["source"]["viewport_revision"] = 0.into();
    assert!(sequence.accept(&decode_video(&wire(&stale, &payload)).unwrap()).is_err());
    sequence.accept(&decode_video(&wire(&next, &payload)).unwrap()).unwrap();
    let mut tab_b = next.clone();
    tab_b["source"]["tab_id"] = "tab-b".into();
    tab_b["source"]["presentation_revision"] = 2.into();
    tab_b["source"]["sequence"] = 3.into();
    tab_b["pts_us"] = 3.into();
    sequence.accept(&decode_video(&wire(&tab_b, &payload)).unwrap()).unwrap();
    let mut stale_a = tab_b.clone();
    stale_a["source"]["tab_id"] = "tab-a".into();
    stale_a["source"]["presentation_revision"] = 1.into();
    stale_a["source"]["sequence"] = 4.into();
    stale_a["pts_us"] = 4.into();
    assert!(
        sequence
            .accept(&decode_video(&wire(&stale_a, &payload)).unwrap())
            .is_err(),
        "A -> B -> A with a stale A presentation must be rejected"
    );
    let mut new_a = tab_b.clone();
    new_a["source"]["tab_id"] = "tab-a".into();
    new_a["source"]["presentation_revision"] = 3.into();
    new_a["source"]["sequence"] = 4.into();
    new_a["pts_us"] = 4.into();
    sequence
        .accept(&decode_video(&wire(&new_a, &payload)).unwrap())
        .unwrap();
    sequence.accept(&decode_video(&wire(&serde_json::json!({"type":"closed","session_id":"s"}), &[])).unwrap()).unwrap();
    assert!(sequence.accept(&video).is_err());
}

#[test]
fn host11_frame_identity_rejects_missing_or_unknown_fields() {
    let payload = [0, 0, 0, 1, 0x65];
    let mut missing_tab = frame();
    missing_tab["source"].as_object_mut().unwrap().remove("tab_id");
    assert!(decode_video(&wire(&missing_tab, &payload)).is_err());

    let mut missing_presentation = frame();
    missing_presentation["source"]
        .as_object_mut()
        .unwrap()
        .remove("presentation_revision");
    assert!(decode_video(&wire(&missing_presentation, &payload)).is_err());

    let mut unknown = frame();
    unknown["source"]["native_guess"] = true.into();
    assert!(decode_video(&wire(&unknown, &payload)).is_err());
}

#[test]
fn encoder_unavailable_is_typed_terminal_error_while_unavailable_recovers() {
    use agentbrowser_connection::MediaSequence;

    let mut sequence = MediaSequence::new("s".into());
    let unavailable = decode_video(&wire(
        &serde_json::json!({
            "type": "unavailable",
            "session_id": "s",
            "message": "capture temporarily unavailable"
        }),
        &[],
    ))
    .unwrap();
    sequence.accept(&unavailable).unwrap();

    let terminal = decode_video(&wire(
        &serde_json::json!({
            "type": "encoder_unavailable",
            "session_id": "s",
            "message": "configured encoder exited"
        }),
        &[],
    ))
    .unwrap();
    assert!(matches!(
        sequence.accept(&terminal),
        Err(Failure::Host { ref code, ref message })
            if code == "ENCODER_UNAVAILABLE"
                && message == "Encoder for session s is unavailable: configured encoder exited"
    ));

    let next = decode_video(&wire(&frame(), &[0, 0, 0, 1, 0x65])).unwrap();
    let mut recovered = MediaSequence::new("s".into());
    recovered.accept(&unavailable).unwrap();
    recovered.accept(&next).unwrap();
}

#[test]
fn rejects_unbounded_and_mismatched_network_packets() {
    assert!(matches!(decode_video(&[]), Err(Failure::Protocol(_))));
    assert!(decode_video(&u32::MAX.to_be_bytes()).is_err());
    let header = br#"{"type":"waiting","session_id":"s"}"#;
    let mut bytes = (header.len() as u32).to_be_bytes().to_vec(); bytes.extend_from_slice(header);
    assert!(decode_video(&bytes).is_ok());
    bytes.push(0);
    assert!(decode_video(&bytes).is_err(), "state packets cannot smuggle trailing media");
}
