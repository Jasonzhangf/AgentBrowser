#![cfg(unix)]

use std::{
    net::{IpAddr, Ipv4Addr},
    os::unix::fs::{DirBuilderExt, PermissionsExt},
    path::PathBuf,
    process::Stdio,
    time::Duration,
};

use agentbrowser_connection::{
    protocol::{
        Command, ControlPhase, Mode, Operation, Request, Response, ResultValue, SessionStatus,
        VideoPacket,
    },
    Connector, DisplayedFrame, Failure, Input, Pairing, Video, WebRtcConfig,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command as ProcessCommand,
};

#[tokio::test]
async fn real_host_webrtc_observe_takeover_input_and_reconnect() {
    tokio::time::timeout(Duration::from_secs(120), exercise())
        .await
        .unwrap();
}

async fn exercise() {
    let bin = PathBuf::from(
        std::env::var("OBSCURA_BIN_DIR")
            .expect("Set OBSCURA_BIN_DIR to validated WebRTC Host/media/endpoint binaries"),
    );
    let root = PathBuf::from(format!("/tmp/ac-webrtc-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new()
        .mode(0o700)
        .create(&root)
        .unwrap();

    let ca_key = rcgen::KeyPair::generate().unwrap();
    let mut ca_params = rcgen::CertificateParams::new(Vec::new()).unwrap();
    ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    ca_params
        .distinguished_name
        .push(rcgen::DnType::CommonName, "WebRTC connection test CA");
    let ca = ca_params.self_signed(&ca_key).unwrap();
    let server_key = rcgen::KeyPair::generate().unwrap();
    let server = rcgen::CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()])
        .unwrap()
        .signed_by(&server_key, &ca, &ca_key)
        .unwrap();
    let client_key = rcgen::KeyPair::generate().unwrap();
    let mut client_params = rcgen::CertificateParams::new(Vec::new()).unwrap();
    client_params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    let client = client_params.signed_by(&client_key, &ca, &ca_key).unwrap();
    std::fs::write(root.join("server.der"), server.der()).unwrap();
    std::fs::write(root.join("server.key"), server_key.serialize_der()).unwrap();
    std::fs::set_permissions(
        root.join("server.key"),
        std::fs::Permissions::from_mode(0o600),
    )
    .unwrap();
    std::fs::write(root.join("ca.der"), ca.der()).unwrap();

    let mut host = ProcessCommand::new(bin.join("obscura-host"))
        .arg("--socket-dir")
        .arg(root.join("host"))
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(
        tokio::net::UnixStream::connect(root.join("host/host.sock"))
            .await
            .unwrap(),
    );
    let mut ready = String::new();
    local.read_line(&mut ready).await.unwrap();
    let mut local_id = 0;
    let attached = state(
        local_call(
            &mut local,
            &mut local_id,
            Command::Attach {
                mode: Mode::Agent,
                viewport: None,
            },
            None,
        )
        .await,
    );
    let initial_page = "data:text/html,<style>body{margin:0}input{position:absolute;left:10px;top:10px;width:250px;height:40px}button{position:absolute;left:10px;top:70px;width:250px;height:40px}</style><input id='field'><button id='button' onclick='window.clicked=(window.clicked||0)+1'>click</button>";
    let navigated = state(
        local_call(
            &mut local,
            &mut local_id,
            Command::Navigate {
                url: initial_page.into(),
            },
            Some(identity(&attached)),
        )
        .await,
    );

    let reservation = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = reservation.local_addr().unwrap();
    drop(reservation);
    let mut endpoint = ProcessCommand::new(bin.join("obscura-endpoint"))
        .arg("--listen")
        .arg(address.to_string())
        .arg("--host-dir")
        .arg(root.join("host"))
        .arg("--socket-dir")
        .arg(root.join("endpoint"))
        .arg("--server-cert")
        .arg(root.join("server.der"))
        .arg("--server-key")
        .arg(root.join("server.key"))
        .arg("--client-ca")
        .arg(root.join("ca.der"))
        .arg("--media-bin")
        .arg(bin.join("obscura-media"))
        .arg("--enable-webrtc")
        .arg("--webrtc-bind-ip")
        .arg("127.0.0.1")
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    wait(root.join("endpoint/encoded.sock")).await;

    let pairing = || Pairing {
        endpoint: format!("wss://{address}"),
        server_ca_der: ca.der().to_vec(),
        client_cert_der: client.der().to_vec(),
        client_key_pkcs8_der: client_key.serialize_der(),
    };
    let config = WebRtcConfig {
        bind_ip: IpAddr::V4(Ipv4Addr::LOCALHOST),
    };
    let mut connector = Connector::default();
    let mut bad_pairing = pairing();
    bad_pairing.server_ca_der = rcgen::generate_simple_self_signed(vec!["localhost".into()])
        .unwrap()
        .cert
        .der()
        .to_vec();
    assert!(
        connector
            .connect_webrtc_with_config(bad_pairing, None, config)
            .await
            .is_err(),
        "wrong CA must fail before WebRTC selection"
    );

    let mut connection = connector
        .connect_webrtc_with_config(pairing(), None, config)
        .await
        .unwrap();
    assert_eq!(connection.initial_status.session_id, navigated.session_id);
    let first = wait_frame(&mut connection, |video| {
        matches!(video.packet, VideoPacket::AccessUnit { .. })
    })
    .await;
    decode_h264(&first).await;
    let first_displayed = displayed(&connection, &first);

    let observed = connection.status().await.unwrap();
    assert!(matches!(observed.mode, Some(Mode::Observe)));
    assert!(matches!(
        connection
            .input(
                Input::Click { x: 30., y: 30. },
                first_displayed.clone(),
                observed.control.epoch
            )
            .await,
        Err(Failure::Host { .. })
    ));
    let human = connection.takeover(observed.control.epoch).await.unwrap();
    assert!(matches!(human.control.phase, ControlPhase::Human { .. }));

    let changed = connection
        .navigate("data:text/html,<title>webrtc</title><style>body{margin:0}input{position:absolute;left:10px;top:10px;width:250px;height:40px}button{position:absolute;left:10px;top:70px;width:250px;height:40px}</style><input id='field'><button id='button' onclick='window.clicked=(window.clicked||0)+1'>click</button>".into(), human.control.epoch)
        .await
        .unwrap();
    assert!(changed.document_revision > human.document_revision);
    let changed_video = wait_frame(&mut connection, |video| {
        matches!(&video.packet, VideoPacket::AccessUnit { source, .. } if source.document_revision == changed.document_revision)
    })
    .await;
    decode_h264(&changed_video).await;
    let changed_displayed = displayed(&connection, &changed_video);
    assert!(matches!(
        connection
            .input(
                Input::Click { x: 30., y: 30. },
                first_displayed.clone(),
                human.control.epoch,
            )
            .await,
        Err(Failure::Host { .. })
    ));
    connection
        .input(
            Input::Click { x: 30., y: 30. },
            changed_displayed.clone(),
            human.control.epoch,
        )
        .await
        .unwrap();
    connection
        .input(
            Input::Text("中文输入🙂".into()),
            changed_displayed.clone(),
            human.control.epoch,
        )
        .await
        .unwrap();
    connection
        .input(
            Input::Click { x: 30., y: 90. },
            changed_displayed.clone(),
            human.control.epoch,
        )
        .await
        .unwrap();
    connection
        .input(
            Input::Scroll {
                x: 100.,
                y: 100.,
                delta_x: 0.,
                delta_y: 400.,
            },
            changed_displayed,
            human.control.epoch,
        )
        .await
        .unwrap();
    connection.release(human.control.epoch).await.unwrap();

    let after = state(local_call(&mut local, &mut local_id, Command::Status {}, None).await);
    let effect = local_call(
        &mut local,
        &mut local_id,
        Command::Evaluate {
            expression: "[document.getElementById('field').value,window.clicked]".into(),
        },
        Some(identity(&after)),
    )
    .await;
    match effect {
        ResultValue::Evaluation { result } => {
            let value = result.value.unwrap();
            assert_eq!(value[0], "中文输入🙂");
            assert_eq!(value[1].as_f64(), Some(1.0));
        }
        other => panic!("Expected DOM evidence, got {other:?}"),
    }

    // A new generation fences the old DC and leaves a human-held Host paused.
    let human_again = connection.takeover(after.control.epoch).await.unwrap();
    assert!(matches!(
        human_again.control.phase,
        ControlPhase::Human { .. }
    ));
    let replacement = connector
        .connect_webrtc_with_config(pairing(), None, config)
        .await
        .unwrap();
    connection
        .failure
        .wait_for(|failure| failure.is_some())
        .await
        .unwrap();
    assert!(connection.status().await.is_err());
    let replacement_status = replacement.status().await.unwrap();
    assert_eq!(replacement_status.session_id, first_displayed.session_id);
    assert!(matches!(
        replacement_status.control.phase,
        ControlPhase::Paused
    ));
    assert!(matches!(
        replacement
            .input(
                Input::Click { x: 30., y: 30. },
                first_displayed,
                replacement_status.control.epoch
            )
            .await,
        Err(Failure::Protocol(_))
    ));

    drop(replacement);
    endpoint.kill().await.unwrap();
    endpoint.wait().await.unwrap();
    host.kill().await.unwrap();
    host.wait().await.unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

async fn wait_frame<F>(connection: &mut agentbrowser_connection::Connection, predicate: F) -> Video
where
    F: Fn(&Video) -> bool,
{
    loop {
        connection.media.changed().await.unwrap();
        let video = connection.media.borrow_and_update().clone().unwrap();
        if predicate(&video) {
            return (*video).clone();
        }
    }
}

fn displayed(connection: &agentbrowser_connection::Connection, video: &Video) -> DisplayedFrame {
    let VideoPacket::AccessUnit { source, .. } = &video.packet else {
        panic!("Expected H.264 access unit");
    };
    DisplayedFrame {
        generation: connection.generation,
        session_id: source.session_id.clone(),
        document_revision: source.document_revision,
        viewport_revision: source.viewport_revision,
    }
}

async fn decode_h264(video: &Video) {
    let VideoPacket::AccessUnit {
        coded_width,
        coded_height,
        ..
    } = video.packet
    else {
        panic!("Expected H.264 access unit");
    };
    let expected = coded_width as usize * coded_height as usize * 4;
    let mut ffmpeg = ProcessCommand::new("ffmpeg")
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut stdin = ffmpeg.stdin.take().unwrap();
    stdin.write_all(&video.bytes).await.unwrap();
    drop(stdin);
    let output = tokio::time::timeout(Duration::from_secs(5), ffmpeg.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        output.status.success(),
        "H.264 decode failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.stdout.len(), expected);
    assert!(output.stdout.iter().any(|byte| *byte != 0));
}

fn state(value: ResultValue) -> SessionStatus {
    match value {
        ResultValue::Status(status) => status,
        other => panic!("Expected status, got {other:?}"),
    }
}

fn identity(state: &SessionStatus) -> Operation {
    Operation {
        session_id: state.session_id.clone(),
        attachment_id: state.attachment_id.unwrap(),
        sequence: state.next_sequence,
        control_epoch: state.control.epoch,
        viewport_revision: state.viewport_revision,
        document_revision: state.document_revision,
    }
}

async fn local_call(
    local: &mut BufReader<tokio::net::UnixStream>,
    id: &mut u64,
    command: Command,
    operation: Option<Operation>,
) -> ResultValue {
    *id += 1;
    let mut bytes = serde_json::to_vec(&Request {
        id: *id,
        command,
        operation,
    })
    .unwrap();
    bytes.push(b'\n');
    local.get_mut().write_all(&bytes).await.unwrap();
    let mut line = String::new();
    local.read_line(&mut line).await.unwrap();
    match serde_json::from_str::<Response>(&line).unwrap() {
        Response::Result {
            id: reply_id,
            value,
        } if reply_id == *id => value,
        other => panic!("Host error: {other:?}"),
    }
}

async fn wait(path: PathBuf) {
    tokio::time::timeout(Duration::from_secs(10), async {
        while !path.exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    })
    .await
    .unwrap();
}
