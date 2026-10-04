#![cfg(unix)]
use std::{path::PathBuf, process::Stdio, time::Duration, os::unix::fs::{DirBuilderExt, PermissionsExt}};
use agentbrowser_connection::{Connector, DisplayedFrame, EditingInput, EditingOutcome, Failure, Input, Pairing, PresentedFrame, protocol::{Command, ControlPhase, EditingState, EditorContext, InputBinding, InputEnvelope, InputEvent, InputLeaseKind, InputReceipt, InputState, Mode, Operation, PresentedFrameIdentity, Request, Response, ResultValue, SessionStatus, VideoPacket}};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::test]
async fn real_host_observe_input_and_reconnect() {
    tokio::time::timeout(Duration::from_secs(40), exercise()).await.unwrap();
}

async fn exercise() {
    let bin = PathBuf::from(std::env::var("OBSCURA_BIN_DIR").expect("Set OBSCURA_BIN_DIR to validated Host/media/endpoint binaries"));
    let root = PathBuf::from(format!("/tmp/ac-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
    let ca_key = rcgen::KeyPair::generate().unwrap();
    let mut params = rcgen::CertificateParams::new(vec![]).unwrap();
    params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    params.distinguished_name.push(rcgen::DnType::CommonName, "Connection test CA");
    let ca = params.self_signed(&ca_key).unwrap();
    let server_key = rcgen::KeyPair::generate().unwrap();
    let server = rcgen::CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap().signed_by(&server_key, &ca, &ca_key).unwrap();
    let key = rcgen::KeyPair::generate().unwrap();
    let mut params = rcgen::CertificateParams::new(vec![]).unwrap();
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    let client = params.signed_by(&key, &ca, &ca_key).unwrap();
    std::fs::write(root.join("server.der"), server.der()).unwrap();
    std::fs::write(root.join("key.der"), server_key.serialize_der()).unwrap();
    std::fs::set_permissions(root.join("key.der"), std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(root.join("ca.der"), ca.der()).unwrap();
    let mut host = tokio::process::Command::new(bin.join("obscura-host")).arg("--socket-dir").arg(root.join("host"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn().unwrap();
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(tokio::net::UnixStream::connect(root.join("host/host.sock")).await.unwrap());
    let mut line = String::new(); local.read_line(&mut line).await.unwrap();
    let mut id = 0;
    let attached = state(local_call(&mut local, &mut id, Command::Attach { mode: Mode::Agent, viewport: None }, None).await);
    let navigated = state(local_call(&mut local, &mut id, Command::Navigate { url:
        "data:text/html,<button style='width:200px;height:100px' onclick='window.clicked=(window.clicked||0)+1'>click</button>".into()
    }, Some(identity(&attached))).await);
    let reservation = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = reservation.local_addr().unwrap(); drop(reservation);
    let mut endpoint = tokio::process::Command::new(bin.join("obscura-endpoint"))
        .arg("--listen").arg(address.to_string()).arg("--host-dir").arg(root.join("host"))
        .arg("--socket-dir").arg(root.join("endpoint"))
        .arg("--server-cert").arg(root.join("server.der")).arg("--server-key").arg(root.join("key.der"))
        .arg("--client-ca").arg(root.join("ca.der")).arg("--media-bin").arg(bin.join("obscura-media"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn().unwrap();
    wait(root.join("endpoint/encoded.sock")).await;
    let pairing = || Pairing { endpoint: format!("wss://{address}"), server_ca_der: ca.der().to_vec(),
        client_cert_der: client.der().to_vec(), client_key_pkcs8_der: key.serialize_der() };
    let mut connector = Connector::default();
    let mut untrusted = pairing();
    untrusted.server_ca_der = rcgen::generate_simple_self_signed(vec!["localhost".into()]).unwrap().cert.der().to_vec();
    assert!(connector.connect(untrusted, None).await.is_err(), "Server trust cannot be bypassed");
    let mut connection = connector.connect(pairing(), None).await.unwrap();
    assert_eq!(connection.initial_status.session_id, navigated.session_id);
    let displayed = loop {
        connection.media.changed().await.unwrap();
        let video = connection.media.borrow_and_update().clone().unwrap();
        if let VideoPacket::AccessUnit { source, .. } = &video.packet {
            assert!(!video.bytes.is_empty());
            break DisplayedFrame { generation: connection.generation, session_id: source.session_id.clone(),
                tab_id: source.tab_id.clone(), presentation_revision: source.presentation_revision,
                document_revision: source.document_revision, viewport_revision: source.viewport_revision };
        }
    };
    let observed = connection.status().await.unwrap();
    assert!(matches!(connection.input(Input::Click { x:30., y:30. }, displayed.clone(), observed.control.epoch).await,
        Err(Failure::Host { .. })), "Observer cannot input");
    assert!(matches!(connection.navigate("data:text/html,<title>observer</title>".into(), observed.control.epoch).await,
        Err(Failure::Host { ref code, .. }) if code == "CONTROL_REQUIRED"), "Observer navigation must reach Host control arbitration");
    let human = connection.takeover(observed.control.epoch).await.unwrap();
    assert!(matches!(human.control.phase, ControlPhase::Human { .. }));
    assert!(matches!(connection.navigate("about:blank".into(), observed.control.epoch).await,
        Err(Failure::Host { ref code, .. }) if code == "STALE_CONTROL"), "Stale navigation epoch must reach Host validation");
    let navigated = connection.navigate("data:text/html,<title>navigated</title><button style='width:200px;height:100px' onclick='window.navigatedClicked=(window.navigatedClicked||0)+1'>new</button>".into(), human.control.epoch).await.unwrap();
    assert_eq!(navigated.document_revision, human.document_revision + 1);
    let navigated_displayed = loop {
        connection.media.changed().await.unwrap();
        let video = connection.media.borrow_and_update().clone().unwrap();
        if let VideoPacket::AccessUnit { source, .. } = &video.packet {
            if source.document_revision == navigated.document_revision {
                break DisplayedFrame { generation: connection.generation, session_id: source.session_id.clone(),
                    tab_id: source.tab_id.clone(), presentation_revision: source.presentation_revision,
                    document_revision: source.document_revision, viewport_revision: source.viewport_revision };
            }
        }
    };
    assert!(matches!(connection.input(Input::Click { x:30., y:30. }, displayed.clone(), human.control.epoch).await,
        Err(Failure::Host { .. })), "Old displayed document must be rejected after navigation");
    connection.input(Input::Click { x:30., y:30. }, navigated_displayed, human.control.epoch).await.unwrap();
    connection.release(human.control.epoch).await.unwrap();
    let latest = state(local_call(&mut local, &mut id, Command::Status {}, None).await);
    let effect = local_call(&mut local, &mut id, Command::Evaluate { expression: "window.navigatedClicked".into() }, Some(identity(&latest))).await;
    match effect { ResultValue::Evaluation { result } => assert_eq!(result.value.unwrap().as_f64(), Some(1.0)), _ => panic!("Expected DOM evidence") }
    let observed = connection.status().await.unwrap();
    connection.takeover(observed.control.epoch).await.unwrap();
    let replacement = connector.connect(pairing(), None).await.unwrap();
    connection.failure.wait_for(|failure| failure.is_some()).await.unwrap();
    assert!(connection.status().await.is_err());
    let after = replacement.status().await.unwrap();
    assert_eq!(after.session_id, displayed.session_id);
    assert_ne!(after.attachment_id, observed.attachment_id);
    assert!(matches!(after.control.phase, ControlPhase::Paused), "Disconnected human must leave Host paused");
    assert!(replacement.input(Input::Click { x:30., y:30. }, displayed, after.control.epoch).await.is_err());
    // A failed explicit new attempt must still fence the previous sockets.
    let mut invalid = pairing(); invalid.endpoint = "http://localhost".into();
    assert!(connector.connect(invalid, None).await.is_err());
    drop(replacement); connection.close();
    endpoint.kill().await.unwrap(); endpoint.wait().await.unwrap();
    host.kill().await.unwrap(); host.wait().await.unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

fn state(value: ResultValue) -> SessionStatus { match value { ResultValue::Status(status) => status, _ => panic!("Expected status") } }
fn identity(state: &SessionStatus) -> Operation {
    Operation { operation_id: format!("legacy-{}", state.next_sequence), session_id: state.session_id.clone(), attachment_id: state.attachment_id.unwrap(), sequence: state.next_sequence,
        control_epoch: state.control.epoch, viewport_revision: state.viewport_revision, document_revision: state.document_revision }
}
async fn local_call(local: &mut BufReader<tokio::net::UnixStream>, id: &mut u64, command: Command, operation: Option<Operation>) -> ResultValue {
    *id += 1;
    let mut bytes = serde_json::to_vec(&Request { id: *id, command, operation }).unwrap(); bytes.push(b'\n');
    local.get_mut().write_all(&bytes).await.unwrap();
    let mut line = String::new(); local.read_line(&mut line).await.unwrap();
    match serde_json::from_str::<Response>(&line).unwrap() {
        Response::Result { id: reply_id, value } if reply_id == *id => value,
        other => panic!("Host error: {other:?}"),
    }
}
async fn wait(path: PathBuf) {
    tokio::time::timeout(Duration::from_secs(10), async {
        while !path.exists() { tokio::time::sleep(Duration::from_millis(20)).await; }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }).await.unwrap();
}

const EDIT_PAGE: &str = "data:text/html,<style>body{margin:0}input{position:absolute;left:10px;top:10px;width:250px;height:40px}textarea{position:absolute;left:10px;top:70px;width:250px;height:60px}div{position:absolute;left:10px;top:150px;width:250px;height:60px}</style><input id=field><textarea id=area></textarea><div id=rich contenteditable=true>seed</div><script>window.events=[];['field','area','rich'].forEach(function(id){var e=document.getElementById(id);['beforeinput','input','compositionstart','compositionupdate','compositionend'].forEach(function(t){e.addEventListener(t,function(ev){window.events.push({type:ev.type,data:ev.data||'',inputType:ev.inputType||'',target:ev.target&&ev.target.id||''})})})})</script>";

#[tokio::test]
async fn real_host_typed_editing_lease_and_fencing() {
    tokio::time::timeout(Duration::from_secs(90), exercise_editing()).await.unwrap();
}

async fn exercise_editing() {
    let bin = PathBuf::from(std::env::var("OBSCURA_BIN_DIR").expect("Set OBSCURA_BIN_DIR to validated Host/media/endpoint binaries"));
    let root = PathBuf::from(format!("/tmp/ac-edit-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
    let ca_key = rcgen::KeyPair::generate().unwrap();
    let mut params = rcgen::CertificateParams::new(vec![]).unwrap();
    params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    params.distinguished_name.push(rcgen::DnType::CommonName, "Typed editing test CA");
    let ca = params.self_signed(&ca_key).unwrap();
    let server_key = rcgen::KeyPair::generate().unwrap();
    let server = rcgen::CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap().signed_by(&server_key, &ca, &ca_key).unwrap();
    let key = rcgen::KeyPair::generate().unwrap();
    let mut params = rcgen::CertificateParams::new(vec![]).unwrap();
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    let client = params.signed_by(&key, &ca, &ca_key).unwrap();
    std::fs::write(root.join("server.der"), server.der()).unwrap();
    std::fs::write(root.join("key.der"), server_key.serialize_der()).unwrap();
    std::fs::set_permissions(root.join("key.der"), std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(root.join("ca.der"), ca.der()).unwrap();
    let mut host = tokio::process::Command::new(bin.join("obscura-host")).arg("--socket-dir").arg(root.join("host"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn().unwrap();
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(tokio::net::UnixStream::connect(root.join("host/host.sock")).await.unwrap());
    let mut line = String::new(); local.read_line(&mut line).await.unwrap();
    let mut id = 0;
    let attached = state(local_call(&mut local, &mut id, Command::Attach { mode: Mode::Agent, viewport: None }, None).await);
    local_call(&mut local, &mut id, Command::Navigate { url: EDIT_PAGE.into() }, Some(identity(&attached))).await;
    let reservation = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = reservation.local_addr().unwrap(); drop(reservation);
    let mut endpoint = tokio::process::Command::new(bin.join("obscura-endpoint"))
        .arg("--listen").arg(address.to_string()).arg("--host-dir").arg(root.join("host"))
        .arg("--socket-dir").arg(root.join("endpoint"))
        .arg("--server-cert").arg(root.join("server.der")).arg("--server-key").arg(root.join("key.der"))
        .arg("--client-ca").arg(root.join("ca.der")).arg("--media-bin").arg(bin.join("obscura-media"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn().unwrap();
    wait(root.join("endpoint/encoded.sock")).await;
    let pairing = || Pairing { endpoint: format!("wss://{address}"), server_ca_der: ca.der().to_vec(),
        client_cert_der: client.der().to_vec(), client_key_pkcs8_der: key.serialize_der() };
    let mut connector = Connector::default();
    let connection = connector.connect(pairing(), None).await.unwrap();
    let observed = connection.status().await.unwrap();
    let observe_epoch = observed.control.epoch;
    let frame = next_frame(&connection).await;

    // BB03: an observer attachment cannot inject typed input.
    assert!(matches!(gesture_event(&connection, "observe-gesture", 0, InputEvent::PointerDown { pointer_id: 1, x: 20.0, y: 20.0, button: "left".into(), buttons: 1, modifiers: 0 }, &frame, observe_epoch).await,
        Err(Failure::Host { ref code, .. }) if code == "CONTROL_REQUIRED"), "Observer cannot inject typed input");

    let human = connection.takeover(observe_epoch).await.unwrap();
    assert!(matches!(human.control.phase, ControlPhase::Human { .. }));
    let epoch = human.control.epoch;

    // BB06: a typed gesture tap focuses the input and publishes a target-bound context.
    let up = tap(&connection, "focus-field", 20.0, 20.0, &frame, epoch).await;
    assert!(matches!(up, EditingOutcome::Gesture(InputReceipt { state: InputState::Succeeded })), "focus tap: {up:?}");
    let context = editable_context(&connection).await;
    assert!(context.target_id.len() > 0 && context.editable && !context.read_only);

    // BB09: replace text, reject a surrogate-splitting range, delete, select and replace selection.
    let binding = editor_binding(&context, "edit-field");
    let applied = connection.editing(EditingInput { operation_id: "replace-1".into(), event_sequence: 0, binding: binding.clone(),
        event: InputEvent::ReplaceText { text: "A😀中".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(applied, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)), "replace: {applied:?}");
    let edited = editable_context(&connection).await;
    assert_eq!(edited.editor_revision, 1);
    assert!(matches!(connection.editing(EditingInput { operation_id: "delete-split".into(), event_sequence: 0, binding: editor_binding(&edited, "edit-field"),
        event: InputEvent::DeleteRange { start: 1, end: 2 }, frame: frame.clone(), epoch }).await,
        Err(Failure::Host { ref code, .. }) if code == "INVALID_UTF16_RANGE"), "Surrogate split must be rejected");
    let deleted = connection.editing(EditingInput { operation_id: "delete-2".into(), event_sequence: 0, binding: editor_binding(&edited, "edit-field"),
        event: InputEvent::DeleteRange { start: 1, end: 3 }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(deleted, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)), "delete: {deleted:?}");
    let after_delete = editable_context(&connection).await;
    let selected = connection.editing(EditingInput { operation_id: "select-1".into(), event_sequence: 0, binding: editor_binding(&after_delete, "edit-field"),
        event: InputEvent::SetSelection { start: 0, end: 2, direction: agentbrowser_connection::protocol::SelectionDirection::Forward }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(selected, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)), "select: {selected:?}");
    let after_select = editable_context(&connection).await;
    let replaced = connection.editing(EditingInput { operation_id: "replace-selection".into(), event_sequence: 0, binding: editor_binding(&after_select, "edit-field"),
        event: InputEvent::ReplaceText { text: "X".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(replaced, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)), "replace selection: {replaced:?}");

    // BB10: stale control epoch, target and editor revision are rejected by Host.
    let stale = editable_context(&connection).await;
    assert!(matches!(connection.editing(EditingInput { operation_id: "stale-epoch".into(), event_sequence: 0, binding: editor_binding(&stale, "stale-epoch"),
        event: InputEvent::ReplaceText { text: "blocked".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch: observe_epoch }).await,
        Err(Failure::Host { ref code, .. }) if code == "STALE_CONTROL"), "Stale epoch must be rejected");
    let wrong_target = InputBinding::Editor { edit_session_id: "stale-target".into(), target_id: "node-does-not-exist".into(),
        focus_revision: stale.focus_revision, editor_revision: stale.editor_revision };
    assert!(matches!(connection.editing(EditingInput { operation_id: "stale-target".into(), event_sequence: 0, binding: wrong_target,
        event: InputEvent::ReplaceText { text: "blocked".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch }).await,
        Err(Failure::Host { ref code, .. }) if code == "STALE_EDITOR_TARGET"), "Stale target must be rejected");
    let old_context = editor_binding(&context, "stale-revision");
    assert!(matches!(connection.editing(EditingInput { operation_id: "stale-revision".into(), event_sequence: 0, binding: old_context,
        event: InputEvent::ReplaceText { text: "blocked".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch }).await,
        Err(Failure::Host { ref code, .. }) if code == "STALE_EDITOR_REVISION"), "Stale editor revision must be rejected");

    // BB06: focus the textarea with a typed gesture and drive a UTF-16 composition.
    assert!(matches!(tap(&connection, "focus-area", 20.0, 80.0, &frame, epoch).await,
        EditingOutcome::Gesture(InputReceipt { state: InputState::Succeeded })));
    let area = editable_context(&connection).await;
    assert!(matches!(area.kind, agentbrowser_connection::protocol::EditorKind::Textarea));
    // BB07: cancel an open composition without committing text.
    let cancel_binding = editor_binding(&area, "compose-cancel");
    let begin = connection.editing(EditingInput { operation_id: "compose-cancel".into(), event_sequence: 0, binding: cancel_binding.clone(),
        event: InputEvent::CompositionBegin { data: "x".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(begin, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Pending)), "cancel begin: {begin:?}");
    let cancelled = connection.editing(EditingInput { operation_id: "compose-cancel".into(), event_sequence: 1, binding: cancel_binding,
        event: InputEvent::CompositionCancel {}, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(cancelled, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Cancelled)), "composition cancel: {cancelled:?}");

    // BB07: begin/update/commit a UTF-16 composition and replay the terminal commit.
    let area = editable_context(&connection).await;
    let composition = editor_binding(&area, "compose-1");
    let begin = connection.editing(EditingInput { operation_id: "compose-1".into(), event_sequence: 0, binding: composition.clone(),
        event: InputEvent::CompositionBegin { data: "中".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(begin, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Pending)), "composition begin: {begin:?}");
    let lease = connection.status().await.unwrap().input_lease.expect("composition lease");
    assert!(matches!(lease.kind, InputLeaseKind::Composition) && lease.operation_id == "compose-1");
    let update = connection.editing(EditingInput { operation_id: "compose-1".into(), event_sequence: 1, binding: composition.clone(),
        event: InputEvent::CompositionUpdate { data: "中文".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(update, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Pending)), "composition update: {update:?}");
    assert_eq!(editable_context(&connection).await.surrounding.expect("composition surrounding").text, "中文");
    let commit = connection.editing(EditingInput { operation_id: "compose-1".into(), event_sequence: 2, binding: composition.clone(),
        event: InputEvent::CompositionCommit { data: "中文".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(commit, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)), "composition commit: {commit:?}");
    let replay = connection.editing(EditingInput { operation_id: "compose-1".into(), event_sequence: 2, binding: composition.clone(),
        event: InputEvent::CompositionCommit { data: "中文".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert_eq!(replay_receipt(&replay), replay_receipt(&commit), "Replayed commit must return the cached receipt");

    // BB06: contenteditable target accepts a typed replacement.
    assert!(matches!(tap(&connection, "focus-rich", 20.0, 160.0, &frame, epoch).await,
        EditingOutcome::Gesture(InputReceipt { state: InputState::Succeeded })));
    let rich = editable_context(&connection).await;
    assert!(matches!(rich.kind, agentbrowser_connection::protocol::EditorKind::ContentEditable));
    let rich_receipt = connection.editing(EditingInput { operation_id: "rich-1".into(), event_sequence: 0, binding: editor_binding(&rich, "rich-1"),
        event: InputEvent::ReplaceText { text: "富文本".into(), input_type: "insertText".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(rich_receipt, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Applied)));

    // BB05: a human takeover from the Agent phase waits for the open lease.
    // The Agent attachment owns Agent-phase control, so it opens the typed
    // gesture and a second observer requests takeover.
    let agent_phase = connection.release(epoch).await.unwrap();
    assert!(matches!(agent_phase.control.phase, ControlPhase::Agent));
    let mut observer_connector = Connector::default();
    let observer = observer_connector.connect(pairing(), None).await.unwrap();
    let agent = state(local_call(&mut local, &mut id, Command::Status {}, None).await);
    let agent_frame = PresentedFrameIdentity { session_id: agent.session_id.clone(),
        tab_id: agent.active_tab_id.clone(), presentation_revision: agent.presentation_revision, frame_sequence: 0,
        document_revision: agent.document_revision, viewport_revision: agent.viewport_revision };
    let gesture = Operation { operation_id: "agent-wait".into(), session_id: agent.session_id.clone(),
        attachment_id: agent.attachment_id.unwrap(), sequence: agent.next_sequence, control_epoch: agent.control.epoch,
        viewport_revision: agent.viewport_revision, document_revision: agent.document_revision };
    let down = local_call(&mut local, &mut id, Command::Input { envelope: InputEnvelope { event_sequence: 0,
        frame: agent_frame.clone(), binding: InputBinding::Gesture { gesture_session_id: "agent-wait".into() },
        event: InputEvent::PointerDown { pointer_id: 1, x: 20.0, y: 20.0, button: "left".into(), buttons: 1, modifiers: 0 } } },
        Some(gesture.clone())).await;
    assert!(matches!(down, ResultValue::Input { input: InputReceipt { state: InputState::Pending } }), "agent gesture down: {down:?}");
    let waiting = observer.takeover(observer.status().await.unwrap().control.epoch).await.unwrap();
    assert!(matches!(waiting.control.phase, ControlPhase::Waiting { .. }) && waiting.operation_running, "Takeover must wait for the lease: {waiting:?}");
    // The Agent must terminate its own lease before the takeover can complete.
    let up = local_call(&mut local, &mut id, Command::Input { envelope: InputEnvelope { event_sequence: 1,
        frame: agent_frame, binding: InputBinding::Gesture { gesture_session_id: "agent-wait".into() },
        event: InputEvent::PointerUp { pointer_id: 1, x: 20.0, y: 20.0, button: "left".into(), buttons: 0, modifiers: 0 } } },
        Some(gesture)).await;
    assert!(matches!(up, ResultValue::Input { input: InputReceipt { state: InputState::Succeeded } }), "agent gesture up: {up:?}");
    let handed = observer.status().await.unwrap();
    assert!(matches!(handed.control.phase, ControlPhase::Human { .. }) && !handed.operation_running,
        "Takeover must complete after the terminal event: {handed:?}");
    // Hand control back to the Agent phase so the local attachment can read the DOM.
    let released = observer.release(handed.control.epoch).await.unwrap();
    assert!(matches!(released.control.phase, ControlPhase::Agent));

    // Real DOM side effects, observed read-only by the local Agent attachment.
    let after_release = state(local_call(&mut local, &mut id, Command::Status {}, None).await);
    let effect = local_call(&mut local, &mut id, Command::Evaluate {
        expression: "[document.getElementById('field').value,document.getElementById('area').value,document.getElementById('rich').textContent,window.events.some(e=>e.target==='rich'&&e.type==='beforeinput')]".into()
    }, Some(identity(&after_release))).await;
    match effect { ResultValue::Evaluation { result } => {
        let value = result.value.unwrap();
        assert_eq!(value[0], "X");
        assert_eq!(value[1], "中文");
        assert_eq!(value[2], "seed富文本");
        assert_eq!(value[3].as_bool(), Some(true));
    }, other => panic!("Expected DOM evidence, got {other:?}") }

    let human_again = connection.takeover(released.control.epoch).await.unwrap();
    assert!(matches!(human_again.control.phase, ControlPhase::Human { .. }));

    // BB13: disconnect cancels the open lease; the new generation rejects the
    // old frame, and the client never replays the lost operation.
    let epoch = human_again.control.epoch;
    assert!(matches!(tap(&connection, "focus-field-2", 20.0, 20.0, &frame, epoch).await,
        EditingOutcome::Gesture(InputReceipt { state: InputState::Succeeded })));
    let disconnect_binding = editor_binding(&editable_context(&connection).await, "disconnect-compose");
    let open = connection.editing(EditingInput { operation_id: "disconnect-compose".into(), event_sequence: 0, binding: disconnect_binding,
        event: InputEvent::CompositionBegin { data: "z".into() }, frame: frame.clone(), epoch }).await.unwrap();
    assert!(matches!(open, EditingOutcome::Editor(ref receipt) if matches!(receipt.state, EditingState::Pending)));
    assert!(connection.status().await.unwrap().input_lease.is_some(), "lease must be held before disconnect");
    connection.close();
    let replacement = connector.connect(pairing(), None).await.unwrap();
    let after = replacement.status().await.unwrap();
    assert!(after.input_lease.is_none(), "Disconnect must not leak an open lease");
    assert!(matches!(replacement.editing(EditingInput { operation_id: "old-generation".into(), event_sequence: 0,
        binding: InputBinding::Gesture { gesture_session_id: "old".into() },
        event: InputEvent::PointerDown { pointer_id: 1, x: 20.0, y: 20.0, button: "left".into(), buttons: 1, modifiers: 0 },
        frame: frame.clone(), epoch: after.control.epoch }).await,
        Err(Failure::Protocol(_))), "Old generation frame must be rejected locally");

    drop(replacement); drop(observer);
    endpoint.kill().await.unwrap(); endpoint.wait().await.unwrap();
    host.kill().await.unwrap(); host.wait().await.unwrap();
    tokio::time::sleep(Duration::from_millis(200)).await;
    std::fs::remove_dir_all(root).unwrap();
}

fn editor_binding(context: &EditorContext, edit_session_id: &str) -> InputBinding {
    InputBinding::Editor { edit_session_id: edit_session_id.into(), target_id: context.target_id.clone(),
        focus_revision: context.focus_revision, editor_revision: context.editor_revision }
}

async fn editable_context(connection: &agentbrowser_connection::Connection) -> EditorContext {
    connection.status().await.unwrap().editor_context.expect("Host must publish an editable context after focus")
}

async fn gesture_event(connection: &agentbrowser_connection::Connection, operation_id: &str, event_sequence: u64,
    event: InputEvent, frame: &PresentedFrame, epoch: u64) -> Result<EditingOutcome, Failure> {
    connection.editing(EditingInput { operation_id: operation_id.into(), event_sequence,
        binding: InputBinding::Gesture { gesture_session_id: format!("{operation_id}-gesture") }, event, frame: frame.clone(), epoch }).await
}

async fn tap(connection: &agentbrowser_connection::Connection, operation_id: &str, x: f64, y: f64, frame: &PresentedFrame, epoch: u64) -> EditingOutcome {
    let down = gesture_event(connection, operation_id, 0, InputEvent::PointerDown { pointer_id: 1, x, y, button: "left".into(), buttons: 1, modifiers: 0 }, frame, epoch).await.unwrap();
    assert!(matches!(down, EditingOutcome::Gesture(InputReceipt { state: InputState::Pending })), "pointer down: {down:?}");
    gesture_event(connection, operation_id, 1, InputEvent::PointerUp { pointer_id: 1, x, y, button: "left".into(), buttons: 0, modifiers: 0 }, frame, epoch).await.unwrap()
}

fn replay_receipt(outcome: &EditingOutcome) -> (String, u64, u64, String) {
    match outcome {
        EditingOutcome::Editor(receipt) => (receipt.operation_id.clone(), receipt.event_sequence, receipt.editor_revision, receipt.target_id.clone()),
        other => panic!("Expected editing receipt, got {other:?}"),
    }
}

async fn next_frame(connection: &agentbrowser_connection::Connection) -> PresentedFrame {
    let mut media = connection.media.clone();
    loop {
        media.changed().await.unwrap();
        let video = media.borrow_and_update().clone().unwrap();
        if let VideoPacket::AccessUnit { source, .. } = &video.packet {
            return PresentedFrame { generation: connection.generation, session_id: source.session_id.clone(),
                tab_id: source.tab_id.clone(), presentation_revision: source.presentation_revision,
                frame_sequence: source.sequence, document_revision: source.document_revision, viewport_revision: source.viewport_revision };
        }
    }
}
