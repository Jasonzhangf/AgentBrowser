#![cfg(unix)]
use std::{path::PathBuf, process::Stdio, time::Duration, os::unix::fs::{DirBuilderExt, PermissionsExt}};
use agentbrowser_connection::{Connector, DisplayedFrame, Failure, Input, Pairing, protocol::{Command, ControlPhase, Mode, Operation, Request, Response, ResultValue, SessionStatus, VideoPacket}};
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
                document_revision: source.document_revision, viewport_revision: source.viewport_revision };
        }
    };
    let observed = connection.status().await.unwrap();
    assert!(matches!(connection.input(Input::Click { x:30., y:30. }, displayed.clone(), observed.control.epoch).await,
        Err(Failure::Host { .. })), "Observer cannot input");
    let human = connection.takeover(observed.control.epoch).await.unwrap();
    assert!(matches!(human.control.phase, ControlPhase::Human { .. }));
    let mut stale = displayed.clone(); stale.document_revision += 1;
    assert!(matches!(connection.input(Input::Click { x:30., y:30. }, stale, human.control.epoch).await,
        Err(Failure::Host { .. })), "Displayed revisions must not be replaced with latest status");
    connection.input(Input::Click { x:30., y:30. }, displayed.clone(), human.control.epoch).await.unwrap();
    connection.release(human.control.epoch).await.unwrap();
    let latest = state(local_call(&mut local, &mut id, Command::Status {}, None).await);
    let effect = local_call(&mut local, &mut id, Command::Evaluate { expression: "window.clicked".into() }, Some(identity(&latest))).await;
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
    Operation { session_id: state.session_id.clone(), attachment_id: state.attachment_id.unwrap(), sequence: state.next_sequence,
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
