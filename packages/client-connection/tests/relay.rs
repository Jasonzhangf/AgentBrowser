use std::{path::PathBuf, process::Stdio, time::Duration};

use agentbrowser_connection::relay::{
    DeviceIdentity, RelayChannelKind, RelayClient, RelayConfig, RelayFailure,
};
use serde::Deserialize;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
};

#[derive(Deserialize)]
struct FixtureInfo {
    origin: String,
    #[serde(rename = "caPath")]
    ca_path: PathBuf,
    #[serde(rename = "databasePath")]
    database_path: PathBuf,
    #[serde(rename = "hostId")]
    host_id: String,
    #[serde(rename = "hostDeviceId")]
    host_device_id: String,
    alice: Credentials,
    bob: Credentials,
}

#[derive(Deserialize)]
struct Credentials {
    username: String,
    password: String,
}

struct Fixture {
    child: Child,
    stdin: ChildStdin,
    info: FixtureInfo,
}

impl Fixture {
    async fn start() -> Self {
        let root = std::fs::canonicalize(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
            .expect("repository root");
        let loader = root.join("services/relay/node_modules/tsx/dist/loader.mjs");
        assert!(
            loader.is_file(),
            "Relay fixture needs services/relay/node_modules; run npm --prefix services/relay ci"
        );
        let script = root.join("packages/client-connection/tests/support/relay-fixture.mjs");
        let mut child = Command::new("node")
            .arg("--import")
            .arg(loader)
            .arg(script)
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .stdin(Stdio::piped())
            .current_dir(root)
            .spawn()
            .expect("start real Relay fixture");
        let stdout = child.stdout.take().expect("fixture stdout");
        let mut lines = BufReader::new(stdout).lines();
        let line = tokio::time::timeout(Duration::from_secs(20), lines.next_line())
            .await
            .expect("Relay fixture startup timeout")
            .expect("read Relay fixture startup")
            .expect("Relay fixture exited before startup");
        let info: FixtureInfo = serde_json::from_str(&line).expect("Relay fixture JSON");
        assert_eq!(line.as_bytes().first(), Some(&b'{'));
        assert!(info.ca_path.is_file(), "fixture CA path");
        assert!(
            info.database_path
                .to_string_lossy()
                .contains("agentbrowser-relay-client-"),
            "fixture database must be temporary"
        );
        let stdin = child.stdin.take().expect("fixture stdin");
        Self { child, stdin, info }
    }

    async fn shutdown(mut self) {
        self.stdin
            .write_all(b"{\"command\":\"shutdown\"}\n")
            .await
            .expect("stop Relay fixture");
        self.stdin.flush().await.expect("flush Relay fixture stop");
        let status = tokio::time::timeout(Duration::from_secs(5), self.child.wait())
            .await
            .expect("Relay fixture shutdown timeout")
            .expect("wait Relay fixture");
        assert!(status.success(), "Relay fixture status: {status}");
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn authenticated_relay_directory_tunnel_isolated_and_generation_fenced() {
    let fixture = Fixture::start().await;
    let ca_pem = std::fs::read(&fixture.info.ca_path).expect("fixture CA");
    let mut pem = ca_pem.as_slice();
    let ca_der = rustls_pemfile::certs(&mut pem)
        .next()
        .expect("fixture certificate")
        .expect("parse fixture certificate")
        .to_vec();
    let config = RelayConfig::new(&fixture.info.origin, ca_der).expect("TLS config");

    let wrong_cert =
        rcgen::generate_simple_self_signed(vec!["localhost".into()]).expect("wrong test CA");
    let wrong_config = RelayConfig::new(&fixture.info.origin, wrong_cert.cert.der().to_vec())
        .expect("wrong TLS config");
    assert!(
        RelayClient::login(
            wrong_config,
            &fixture.info.alice.username,
            &fixture.info.alice.password,
        )
        .await
        .is_err(),
        "client must not bypass Relay certificate validation"
    );

    let alice = RelayClient::login(
        config.clone(),
        &fixture.info.alice.username,
        &fixture.info.alice.password,
    )
    .await
    .expect("Alice login");
    let alice_device = alice
        .register_device("alice-phone", DeviceIdentity::generate())
        .await
        .expect("Alice device registration");
    let directory = alice.list_directory().await.expect("Alice directory");
    assert_eq!(directory.len(), 1);
    assert_eq!(directory[0].host_id, fixture.info.host_id);
    assert_eq!(directory[0].device_id, fixture.info.host_device_id);
    assert_eq!(directory[0].snapshot.sessions[0].id, "fixture-session");

    let connector = alice.connector();
    let first = connector
        .connect(&alice_device)
        .await
        .expect("first control");
    assert_eq!(first.generation(), 1);
    let second = connector
        .connect(&alice_device)
        .await
        .expect("second control");
    assert_eq!(second.generation(), 2);
    assert!(matches!(
        first.open_tunnel(&fixture.info.host_id).await,
        Err(RelayFailure::Superseded)
    ));

    let tunnel = second
        .open_tunnel(&fixture.info.host_id)
        .await
        .expect("authorized tunnel");
    assert_eq!(tunnel.peer_device_id(), fixture.info.host_device_id);
    assert_eq!(tunnel.control().kind(), RelayChannelKind::Control);
    assert_eq!(tunnel.media().kind(), RelayChannelKind::Media);
    let control_payload = b"control-plane-payload";
    tunnel
        .control()
        .send(control_payload)
        .await
        .expect("control send");
    assert_eq!(
        tunnel.control().recv().await.expect("control receive"),
        control_payload
    );
    let media_payload = b"media-plane-payload";
    tunnel
        .media()
        .send(media_payload)
        .await
        .expect("media send");
    assert_eq!(
        tunnel.media().recv().await.expect("media receive"),
        media_payload
    );

    let bob = RelayClient::login(
        config,
        &fixture.info.bob.username,
        &fixture.info.bob.password,
    )
    .await
    .expect("Bob login");
    assert!(bob
        .list_directory()
        .await
        .expect("Bob directory")
        .is_empty());
    let bob_device = bob
        .register_device("bob-phone", DeviceIdentity::generate())
        .await
        .expect("Bob device registration");
    let bob_connection = bob
        .connector()
        .connect(&bob_device)
        .await
        .expect("Bob control");
    let cross_account = match bob_connection.open_tunnel(&fixture.info.host_id).await {
        Ok(_) => panic!("cross-account tunnel unexpectedly opened"),
        Err(error) => error,
    };
    assert!(matches!(
        cross_account,
        RelayFailure::Remote { ref code, .. } if code == "HOST_UNAVAILABLE"
    ));

    alice.revoke().await.expect("Alice token revocation");
    assert!(matches!(
        second.open_tunnel(&fixture.info.host_id).await,
        Err(RelayFailure::Revoked)
    ));
    assert!(matches!(
        tunnel.control().send(b"after-revoke").await,
        Err(RelayFailure::Revoked)
    ));

    drop(bob_connection);
    drop(tunnel);
    drop(second);
    drop(first);
    fixture.shutdown().await;
}
