use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use agentbrowser_connection::{
    decode_video,
    protocol::{
        Command, ControlPhase, Mode, Operation, Request, Response, ResultValue, SessionStatus,
        VideoPacket,
    },
    relay::{DeviceIdentity, RelayClient, RelayConfig, RelayPeerBinding, RelayTlsClientIdentity},
    Video,
};
use agentbrowser_relay_host::{run, RelayHostSettings};
use rcgen::{BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, KeyPair};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command as ProcessCommand},
};

#[derive(Deserialize)]
struct RelayFixtureInfo {
    origin: String,
    #[serde(rename = "caPath")]
    ca_path: PathBuf,
    alice: Credentials,
}

#[derive(Deserialize)]
struct Credentials {
    username: String,
    password: String,
}

struct RelayFixture {
    child: Child,
    stdin: ChildStdin,
    info: RelayFixtureInfo,
}

#[derive(Deserialize)]
struct EndpointFixtureInfo {
    fixture: PathBuf,
    endpoint: String,
    session: String,
}

struct EndpointFixture {
    child: Child,
    stdin: ChildStdin,
    info: EndpointFixtureInfo,
}

struct InnerTls {
    ca_der: Vec<u8>,
    server_cert_der: Vec<u8>,
    server_key_der: Vec<u8>,
    client_cert_der: Vec<u8>,
    client_key_der: Vec<u8>,
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires explicit OBSCURA_BIN_DIR and real Host/endpoint binaries"]
async fn relay_host_real_endpoint_replay() {
    let root = std::fs::canonicalize(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
        .expect("repository root");
    let relay = RelayFixture::start(&root).await;
    let endpoint = EndpointFixture::start(&root).await;
    let relay_ca_der = read_pem_certificate(&relay.info.ca_path);
    let relay_config =
        RelayConfig::new(&relay.info.origin, relay_ca_der.clone()).expect("Relay TLS config");
    let alice = RelayClient::login(
        relay_config,
        &relay.info.alice.username,
        &relay.info.alice.password,
    )
    .await
    .expect("Relay login");

    let client_identity = DeviceIdentity::from_seed([0x24; 32]);
    let client_public_key = client_identity.public_key_bytes();
    let client_device = alice
        .register_device("m1-replay-client", client_identity)
        .await
        .expect("register replay client");
    let inner = inner_tls();
    let host_seed = [0x42; 32];
    let host_public_key = DeviceIdentity::from_seed(host_seed).public_key_bytes();
    let mut host_task = tokio::spawn(run(RelayHostSettings {
        relay_origin: relay.info.origin.clone(),
        relay_ca_der,
        relay_username: relay.info.alice.username.clone(),
        relay_password: relay.info.alice.password.clone(),
        device_name: "m1-replay-host".into(),
        endpoint_url: endpoint.info.endpoint.clone(),
        endpoint_network: agentbrowser_connection::relay::RelayNetwork::Lan,
        endpoint_ca_der: std::fs::read(endpoint.info.fixture.join("ca.der")).expect("endpoint CA"),
        endpoint_client_cert_der: std::fs::read(endpoint.info.fixture.join("client.der"))
            .expect("endpoint client certificate"),
        endpoint_client_key_pkcs8_der: std::fs::read(endpoint.info.fixture.join("key.der"))
            .expect("endpoint client key"),
        inner_server: agentbrowser_connection::relay::RelayTlsServerIdentity::new(
            inner.server_cert_der.clone(),
            inner.server_key_der.clone(),
            inner.ca_der.clone(),
        ),
        peer: RelayPeerBinding::new(
            client_device.id(),
            client_public_key,
            fingerprint(&inner.client_cert_der),
        ),
        device_identity: DeviceIdentity::from_seed(host_seed),
    }));

    let host = tokio::select! {
        result = &mut host_task => panic!("relay-host exited before snapshot publication: {result:?}"),
        host = wait_for_host(&alice, &endpoint.info.session) => host,
    };
    let client_connection = alice
        .connector()
        .connect(&client_device)
        .await
        .expect("client Relay connection");
    let host_binding = RelayPeerBinding::new(
        host.device_id.clone(),
        host_public_key,
        fingerprint(&inner.server_cert_der),
    );
    let client_tls = RelayTlsClientIdentity::new(
        "localhost",
        inner.ca_der.clone(),
        inner.client_cert_der.clone(),
        inner.client_key_der.clone(),
    );
    let tunnel = client_connection
        .open_secure_tunnel(
            &host.host_id,
            &endpoint.info.session,
            host_binding,
            client_tls,
        )
        .await
        .expect("secure Relay tunnel");

    let ready = recv_control(&tunnel).await;
    match ready {
        Response::Ready {
            version: 4,
            session_id,
        } => assert_eq!(session_id, endpoint.info.session),
        other => panic!("expected endpoint Ready, got {other:?}"),
    }
    let attached = status(
        request(
            &tunnel,
            1,
            Command::Attach {
                mode: Mode::Observe,
                viewport: None,
            },
            None,
        )
        .await,
    );
    assert_eq!(attached.session_id, endpoint.info.session);
    assert!(attached.attachment_id.is_some());
    assert_eq!(attached.mode, Some(Mode::Observe));
    let takeover = status(
        request(
            &tunnel,
            2,
            Command::RequestTakeover {
                epoch: attached.control.epoch,
            },
            None,
        )
        .await,
    );
    assert!(matches!(takeover.control.phase, ControlPhase::Human { .. }));

    let first_frame = next_access_unit(&tunnel).await;
    let (session_id, sequence, document_revision, viewport_revision) =
        access_identity(&first_frame);
    let attachment_id = attached.attachment_id.expect("attachment remains active");
    let before_click = status(request(&tunnel, 3, Command::Status {}, None).await);
    let click = request(
        &tunnel,
        4,
        Command::Click { x: 30.0, y: 30.0 },
        Some(Operation {
            session_id: session_id.clone(),
            attachment_id,
            sequence: before_click.next_sequence,
            control_epoch: takeover.control.epoch,
            viewport_revision,
            document_revision,
        }),
    )
    .await;
    assert!(matches!(
        click,
        Response::Result {
            value: ResultValue::Input { .. },
            ..
        }
    ));
    let changed_frame = next_changed_access_unit(&tunnel, sequence).await;
    assert!(!changed_frame.bytes.is_empty());
    assert!(access_identity(&changed_frame).1 > sequence);

    drop(tunnel);
    drop(client_connection);
    host_task.abort();
    let _ = host_task.await;
    endpoint.shutdown().await;
    relay.shutdown().await;
}

async fn wait_for_host(
    client: &RelayClient,
    session_id: &str,
) -> agentbrowser_connection::relay::DirectoryHost {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if let Some(host) = client
                .list_directory()
                .await
                .expect("list Relay directory")
                .into_iter()
                .find(|host| {
                    host.device_name == "m1-replay-host"
                        && host
                            .snapshot
                            .sessions
                            .iter()
                            .any(|session| session.id == session_id)
                })
            {
                return host;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("Host snapshot publication timeout")
}

async fn request(
    tunnel: &agentbrowser_connection::relay::SecureRelayTunnel,
    id: u64,
    command: Command,
    operation: Option<Operation>,
) -> Response {
    let bytes = serde_json::to_vec(&Request {
        id,
        command,
        operation,
    })
    .expect("serialize endpoint request");
    tunnel
        .control()
        .send(&bytes)
        .await
        .expect("send endpoint request");
    recv_control(tunnel).await
}

async fn recv_control(tunnel: &agentbrowser_connection::relay::SecureRelayTunnel) -> Response {
    let bytes = tokio::time::timeout(Duration::from_secs(20), tunnel.control().recv())
        .await
        .expect("control response timeout")
        .expect("control response");
    serde_json::from_slice(&bytes).expect("decode endpoint response")
}

fn status(response: Response) -> SessionStatus {
    match response {
        Response::Result {
            value: ResultValue::Status(status),
            ..
        } => status,
        other => panic!("expected status response, got {other:?}"),
    }
}

async fn next_access_unit(tunnel: &agentbrowser_connection::relay::SecureRelayTunnel) -> Video {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let bytes = tunnel.media().recv().await.expect("media frame");
            let video = decode_video(&bytes).expect("decode media frame");
            if matches!(video.packet, VideoPacket::AccessUnit { .. }) {
                return video;
            }
        }
    })
    .await
    .expect("first access unit timeout")
}

async fn next_changed_access_unit(
    tunnel: &agentbrowser_connection::relay::SecureRelayTunnel,
    previous_sequence: u64,
) -> Video {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let video = next_access_unit(tunnel).await;
            if access_identity(&video).1 > previous_sequence {
                return video;
            }
        }
    })
    .await
    .expect("changed access unit timeout")
}

fn access_identity(video: &Video) -> (String, u64, u64, u64) {
    match &video.packet {
        VideoPacket::AccessUnit { source, .. } => (
            source.session_id.clone(),
            source.sequence,
            source.document_revision,
            source.viewport_revision,
        ),
        other => panic!("expected access unit, got {other:?}"),
    }
}

fn inner_tls() -> InnerTls {
    let ca_key = KeyPair::generate().expect("inner CA key");
    let mut ca_params = CertificateParams::new(vec![]).expect("inner CA params");
    ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    let ca = ca_params.self_signed(&ca_key).expect("inner CA");
    let server_key = KeyPair::generate().expect("inner server key");
    let server = CertificateParams::new(vec!["localhost".into()])
        .expect("inner server params")
        .signed_by(&server_key, &ca, &ca_key)
        .expect("inner server");
    let client_key = KeyPair::generate().expect("inner client key");
    let mut client_params = CertificateParams::new(vec![]).expect("inner client params");
    client_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ClientAuth];
    let client = client_params
        .signed_by(&client_key, &ca, &ca_key)
        .expect("inner client");
    InnerTls {
        ca_der: ca.der().to_vec(),
        server_cert_der: server.der().to_vec(),
        server_key_der: server_key.serialize_der(),
        client_cert_der: client.der().to_vec(),
        client_key_der: client_key.serialize_der(),
    }
}

fn fingerprint(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

fn read_pem_certificate(path: &Path) -> Vec<u8> {
    let bytes = std::fs::read(path).expect("read PEM certificate");
    let mut input = bytes.as_slice();
    let certificate = rustls_pemfile::certs(&mut input)
        .next()
        .expect("PEM certificate")
        .expect("parse PEM certificate");
    certificate.to_vec()
}

impl RelayFixture {
    async fn start(root: &Path) -> Self {
        let loader = root.join("services/relay/node_modules/tsx/dist/loader.mjs");
        assert!(
            loader.is_file(),
            "Relay fixture requires services/relay/node_modules"
        );
        let script = root.join("packages/client-connection/tests/support/relay-fixture.mjs");
        let mut child = ProcessCommand::new("node")
            .arg("--import")
            .arg(loader)
            .arg(script)
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("start Relay fixture");
        let stdout = child.stdout.take().expect("Relay fixture stdout");
        let mut lines = BufReader::new(stdout).lines();
        let line = tokio::time::timeout(Duration::from_secs(20), lines.next_line())
            .await
            .expect("Relay fixture startup timeout")
            .expect("read Relay fixture startup")
            .expect("Relay fixture exited");
        let info = serde_json::from_str(&line).expect("Relay fixture JSON");
        Self {
            stdin: child.stdin.take().expect("Relay fixture stdin"),
            child,
            info,
        }
    }

    async fn shutdown(mut self) {
        self.stdin
            .write_all(b"{\"command\":\"shutdown\"}\n")
            .await
            .expect("stop Relay fixture");
        self.stdin.flush().await.expect("flush Relay fixture stop");
        let status = tokio::time::timeout(Duration::from_secs(10), self.child.wait())
            .await
            .expect("Relay fixture shutdown timeout")
            .expect("wait Relay fixture");
        assert!(status.success(), "Relay fixture status: {status}");
    }
}

impl Drop for RelayFixture {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

impl EndpointFixture {
    async fn start(root: &Path) -> Self {
        let bin_dir = std::env::var_os("OBSCURA_BIN_DIR")
            .expect("OBSCURA_BIN_DIR must point to validated Obscura binaries");
        let binary = root.join("target/debug/examples/device_fixture");
        assert!(
            binary.is_file(),
            "build packages/android-bridge example device_fixture before replay"
        );
        let mut child = ProcessCommand::new(binary)
            .env("OBSCURA_BIN_DIR", bin_dir)
            .env("OBSCURA_ENDPOINT_BIND_IP", "127.0.0.1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("start real Obscura Host/endpoint fixture");
        let stdout = child.stdout.take().expect("endpoint fixture stdout");
        let mut lines = BufReader::new(stdout).lines();
        let line = tokio::time::timeout(Duration::from_secs(30), lines.next_line())
            .await
            .expect("endpoint fixture startup timeout")
            .expect("read endpoint fixture startup")
            .expect("endpoint fixture exited");
        let info = serde_json::from_str(&line).expect("endpoint fixture JSON");
        Self {
            stdin: child.stdin.take().expect("endpoint fixture stdin"),
            child,
            info,
        }
    }

    async fn shutdown(mut self) {
        self.stdin
            .write_all(b"quit\n")
            .await
            .expect("stop endpoint fixture");
        self.stdin
            .flush()
            .await
            .expect("flush endpoint fixture stop");
        let status = tokio::time::timeout(Duration::from_secs(10), self.child.wait())
            .await
            .expect("endpoint fixture shutdown timeout")
            .expect("wait endpoint fixture");
        assert!(status.success(), "endpoint fixture status: {status}");
    }
}

impl Drop for EndpointFixture {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}
