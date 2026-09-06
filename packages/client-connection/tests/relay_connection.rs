#![cfg(unix)]

use std::{
    os::unix::fs::DirBuilderExt,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use agentbrowser_connection::{
    protocol::VideoPacket,
    relay::{DeviceIdentity, RelayClient, RelayConfig, RelayPeerBinding, RelayTlsClientIdentity},
    Connector, DisplayedFrame, Failure, Input,
};
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

struct ReplayTempDir {
    path: PathBuf,
}

impl ReplayTempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "agentbrowser-relay-connection-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .expect("create replay temp directory");
        Self { path }
    }

    fn write(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.path.join(name);
        std::fs::write(&path, bytes).expect("write replay input");
        path
    }
}

impl Drop for ReplayTempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires explicit Relay, relay-host, and Obscura fixtures"]
async fn real_relay_connection_uses_shared_pump_and_reconnects() {
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

    let client_device_identity = DeviceIdentity::from_seed([0x24; 32]);
    let client_public_key = client_device_identity.public_key_bytes();
    let client_device = alice
        .register_device("m1-relay-connection-client", client_device_identity)
        .await
        .expect("register Relay client");
    let inner = inner_tls();
    let replay_temp = ReplayTempDir::new();
    let host_seed = [0x42; 32];
    let host_public_key = DeviceIdentity::from_seed(host_seed).public_key_bytes();
    let mut adapter = start_host_adapter(
        &root,
        &relay,
        &endpoint,
        &inner,
        &replay_temp,
        host_seed,
        &client_device,
        client_public_key,
    );
    let host = wait_for_host(&alice, &endpoint.info.session, &mut adapter).await;
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

    let mut connector = Connector::default();
    let wrong_session = connector
        .connect_relay(
            alice.clone(),
            client_device.clone(),
            &host.host_id,
            "not-published",
            host_binding.clone(),
            client_tls.clone(),
            None,
        )
        .await;
    assert!(
        matches!(wrong_session, Err(Failure::Transport(_))),
        "Relay must reject a session not in the published Host snapshot"
    );

    let mut connection = connector
        .connect_relay(
            alice.clone(),
            client_device.clone(),
            &host.host_id,
            &endpoint.info.session,
            host_binding.clone(),
            client_tls.clone(),
            None,
        )
        .await
        .expect("public Relay connection");
    assert_eq!(connection.initial_status.session_id, endpoint.info.session);
    assert!(connection.initial_status.attachment_id.is_some());
    let mut displayed = next_displayed(&mut connection).await;

    let observed = connection.status().await.expect("Observe status");
    assert!(matches!(
        connection
            .input(
                Input::Click { x: 30.0, y: 30.0 },
                displayed.clone(),
                observed.control.epoch
            )
            .await,
        Err(Failure::Host { .. })
    ));
    let human = connection
        .takeover(observed.control.epoch)
        .await
        .expect("takeover");
    assert!(matches!(
        human.control.phase,
        agentbrowser_connection::protocol::ControlPhase::Human { .. }
    ));

    connection
        .input(
            Input::Click { x: 30.0, y: 120.0 },
            displayed.clone(),
            human.control.epoch,
        )
        .await
        .expect("focus input");
    displayed = next_displayed(&mut connection).await;
    connection
        .input(
            Input::Text("你好，Relay".into()),
            displayed.clone(),
            human.control.epoch,
        )
        .await
        .expect("Chinese input");
    displayed = next_displayed(&mut connection).await;
    connection
        .input(
            Input::Scroll {
                x: 100.0,
                y: 300.0,
                delta_x: 0.0,
                delta_y: 500.0,
            },
            displayed.clone(),
            human.control.epoch,
        )
        .await
        .expect("scroll");
    connection
        .release(human.control.epoch)
        .await
        .expect("release control");

    let replacement = connector
        .connect_relay(
            alice.clone(),
            client_device.clone(),
            &host.host_id,
            &endpoint.info.session,
            host_binding.clone(),
            client_tls.clone(),
            None,
        )
        .await
        .expect("Relay reconnect");
    let mut old_failure = connection.failure.clone();
    tokio::time::timeout(
        Duration::from_secs(5),
        old_failure.wait_for(|value| value.is_some()),
    )
    .await
    .expect("old generation close timeout")
    .expect("old generation close");
    assert!(connection.status().await.is_err());
    assert_eq!(replacement.initial_status.session_id, endpoint.info.session);
    assert_ne!(
        replacement.initial_status.attachment_id,
        connection.initial_status.attachment_id
    );

    let mut wrong_peer = host_binding.clone();
    wrong_peer.auth_public_key = [0; 32];
    assert!(
        matches!(
            connector
                .connect_relay(
                    alice,
                    client_device,
                    &host.host_id,
                    &endpoint.info.session,
                    wrong_peer,
                    client_tls,
                    None,
                )
                .await,
            Err(Failure::Protocol(_))
        ),
        "Relay client must reject an unpinned Host auth key"
    );

    drop(replacement);
    stop_child(adapter).await;
    endpoint.shutdown().await;
    relay.shutdown().await;
}

async fn next_displayed(connection: &mut agentbrowser_connection::Connection) -> DisplayedFrame {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            connection.media.changed().await.expect("media channel");
            let video = connection
                .media
                .borrow_and_update()
                .clone()
                .expect("video item");
            let VideoPacket::AccessUnit { source, .. } = &video.packet else {
                continue;
            };
            return DisplayedFrame {
                generation: connection.generation,
                session_id: source.session_id.clone(),
                document_revision: source.document_revision,
                viewport_revision: source.viewport_revision,
            };
        }
    })
    .await
    .expect("media frame timeout")
}

async fn wait_for_host(
    client: &RelayClient,
    session_id: &str,
    adapter: &mut Child,
) -> agentbrowser_connection::relay::DirectoryHost {
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if let Some(status) = adapter.try_wait().expect("check relay-host status") {
                panic!("relay-host exited before publishing snapshot: {status}");
            }
            if let Some(host) = client
                .list_directory()
                .await
                .expect("list Relay directory")
                .into_iter()
                .find(|host| {
                    host.device_name == "m1-relay-connection-host"
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

fn start_host_adapter(
    root: &Path,
    relay: &RelayFixture,
    endpoint: &EndpointFixture,
    inner: &InnerTls,
    replay_temp: &ReplayTempDir,
    host_seed: [u8; 32],
    client_device: &agentbrowser_connection::relay::RegisteredDevice,
    client_public_key: [u8; 32],
) -> Child {
    let bin = std::env::var_os("AGENTBROWSER_RELAY_HOST_BIN")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("target/debug/agentbrowser-relay-host"));
    assert!(bin.is_file(), "build agentbrowser-relay-host before replay");
    let relay_ca_der = read_pem_certificate(&relay.info.ca_path);
    let relay_ca_path = replay_temp.write("relay-ca.der", &relay_ca_der);
    let host = ProcessCommand::new(bin)
        .args(["--relay", relay.info.origin.as_str(), "--relay-ca"])
        .arg(relay_ca_path)
        .args([
            "--username",
            relay.info.alice.username.as_str(),
            "--password",
            relay.info.alice.password.as_str(),
            "--device-name",
            "m1-relay-connection-host",
            "--device-seed",
        ])
        .arg(hex(&host_seed))
        .args([
            "--endpoint",
            endpoint.info.endpoint.as_str(),
            "--network",
            "lan",
        ])
        .args(["--endpoint-ca"])
        .arg(endpoint.info.fixture.join("ca.der"))
        .args(["--endpoint-client-cert"])
        .arg(endpoint.info.fixture.join("client.der"))
        .args(["--endpoint-client-key"])
        .arg(endpoint.info.fixture.join("key.der"))
        .args(["--inner-server-cert"])
        .arg(write_inner_file(
            &inner.server_cert_der,
            replay_temp,
            "server.der",
        ))
        .args(["--inner-server-key"])
        .arg(write_inner_file(
            &inner.server_key_der,
            replay_temp,
            "server-key.der",
        ))
        .args(["--inner-client-ca"])
        .arg(write_inner_file(&inner.ca_der, replay_temp, "inner-ca.der"))
        .args([
            "--peer-device-id",
            client_device.id(),
            "--peer-auth-public-key",
        ])
        .arg(hex(&client_public_key))
        .args(["--peer-cert-sha256"])
        .arg(hex(&fingerprint(&inner.client_cert_der)))
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .expect("start relay-host adapter");
    host
}

fn write_inner_file(bytes: &[u8], replay_temp: &ReplayTempDir, name: &str) -> PathBuf {
    replay_temp.write(&format!("relay-connection-{name}"), bytes)
}

async fn stop_child(mut child: Child) {
    let _ = child.kill().await;
    let _ = child.wait().await;
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

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn read_pem_certificate(path: &Path) -> Vec<u8> {
    let bytes = std::fs::read(path).expect("read Relay CA");
    let mut input = bytes.as_slice();
    let certificate = rustls_pemfile::certs(&mut input)
        .next()
        .expect("Relay CA certificate")
        .expect("parse Relay CA certificate")
        .to_vec();
    certificate
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
            .expect("start Obscura endpoint fixture");
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

impl Drop for RelayFixture {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

impl Drop for EndpointFixture {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}
