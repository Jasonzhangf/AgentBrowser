use std::{path::PathBuf, process::Stdio, sync::Arc, time::Duration};

use agentbrowser_connection::relay::{
    DeviceIdentity, HostSnapshot, RelayChannelKind, RelayClient, RelayConfig, RelayEndpoint,
    RelayFailure, RelayNetwork, RelayPeerBinding, RelaySession, RelayTlsClientIdentity,
    RelayTlsServerIdentity,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
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
        first
            .open_tunnel(&fixture.info.host_id, "fixture-session")
            .await,
        Err(RelayFailure::Superseded)
    ));

    let tunnel = second
        .open_tunnel(&fixture.info.host_id, "fixture-session")
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
    let cross_account = match bob_connection
        .open_tunnel(&fixture.info.host_id, "fixture-session")
        .await
    {
        Ok(_) => panic!("cross-account tunnel unexpectedly opened"),
        Err(error) => error,
    };
    assert!(matches!(
        cross_account,
        RelayFailure::Remote { ref code, .. } if code == "HOST_UNAVAILABLE"
    ));

    alice.revoke().await.expect("Alice token revocation");
    assert!(matches!(
        second
            .open_tunnel(&fixture.info.host_id, "fixture-session")
            .await,
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

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn host_role_registers_publishes_and_consumes_side_one_offer() {
    let fixture = Fixture::start().await;
    let ca_pem = std::fs::read(&fixture.info.ca_path).expect("fixture CA");
    let mut pem = ca_pem.as_slice();
    let ca_der = rustls_pemfile::certs(&mut pem)
        .next()
        .expect("fixture certificate")
        .expect("parse fixture certificate")
        .to_vec();
    let config = RelayConfig::new(&fixture.info.origin, ca_der).expect("TLS config");
    let alice = RelayClient::login(
        config,
        &fixture.info.alice.username,
        &fixture.info.alice.password,
    )
    .await
    .expect("Alice login");

    let host_device = alice
        .register_device("relay-host", DeviceIdentity::generate())
        .await
        .expect("Host device registration");
    let host = alice
        .register_host(&host_device)
        .await
        .expect("Host registration");
    let host_connection = Arc::new(
        alice
            .connector()
            .connect_host(&host)
            .await
            .expect("Host control connection"),
    );
    host_connection
        .publish(HostSnapshot {
            incarnation: "host-incarnation".into(),
            revision: 1,
            endpoints: vec![RelayEndpoint {
                network: RelayNetwork::Tailscale,
                url: "wss://100.64.0.10:9443".into(),
            }],
            sessions: vec![RelaySession {
                id: "host-session".into(),
            }],
        })
        .await
        .expect("publish Host snapshot");
    let waiting_host = Arc::clone(&host_connection);
    let waiter = tokio::spawn(async move { waiting_host.next_offer().await });
    tokio::time::sleep(Duration::from_millis(20)).await;
    tokio::time::timeout(
        Duration::from_secs(1),
        host_connection.publish(HostSnapshot {
            incarnation: "host-incarnation".into(),
            revision: 2,
            endpoints: vec![],
            sessions: vec![RelaySession {
                id: "host-session".into(),
            }],
        }),
    )
    .await
    .expect("Host publish must not wait for control reads")
    .expect("republish Host snapshot");
    waiter.abort();

    let client_device = alice
        .register_device("relay-client", DeviceIdentity::generate())
        .await
        .expect("Client device registration");
    let client_connection = alice
        .connector()
        .connect(&client_device)
        .await
        .expect("Client control connection");
    let directory = alice.list_directory().await.expect("directory");
    let published = directory
        .iter()
        .find(|entry| entry.host_id == host.id())
        .expect("published Host in directory");
    assert_eq!(published.device_id, host.device_id());
    assert_eq!(published.snapshot.sessions[0].id, "host-session");

    let client_task = tokio::spawn({
        let client_connection = client_connection;
        let host_id = host.id().to_owned();
        async move {
            let tunnel = client_connection
                .open_tunnel(&host_id, "host-session")
                .await;
            (client_connection, tunnel)
        }
    });
    let offer = host_connection
        .next_offer()
        .await
        .expect("Host receives side=1 offer");
    assert_eq!(offer.peer_device_id(), client_device.id());
    let host_tunnel = host_connection
        .accept_offer(offer)
        .await
        .expect("Host accepts side=1 offer");
    let (client_connection, client_tunnel) = client_task.await.expect("client tunnel task");
    let client_tunnel = client_tunnel.expect("client tunnel");
    assert_eq!(host_tunnel.peer_device_id(), client_device.id());
    assert_eq!(client_tunnel.peer_device_id(), host_device.id());

    host_tunnel
        .control()
        .send(b"host-control")
        .await
        .expect("Host control send");
    assert_eq!(
        client_tunnel
            .control()
            .recv()
            .await
            .expect("client control receive"),
        b"host-control"
    );
    client_tunnel
        .media()
        .send(b"client-media")
        .await
        .expect("client media send");
    assert_eq!(
        host_tunnel
            .media()
            .recv()
            .await
            .expect("Host media receive"),
        b"client-media"
    );

    drop(client_tunnel);
    drop(client_connection);
    drop(host_tunnel);
    drop(host_connection);
    fixture.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn secure_relay_tunnel_binds_inner_mtls_and_tunnel_hello() {
    let fixture = Fixture::start().await;
    let ca_pem = std::fs::read(&fixture.info.ca_path).expect("fixture CA");
    let mut pem = ca_pem.as_slice();
    let relay_ca_der = rustls_pemfile::certs(&mut pem)
        .next()
        .expect("fixture certificate")
        .expect("parse fixture certificate")
        .to_vec();
    let relay_config = RelayConfig::new(&fixture.info.origin, relay_ca_der).expect("TLS config");
    let alice = RelayClient::login(
        relay_config,
        &fixture.info.alice.username,
        &fixture.info.alice.password,
    )
    .await
    .expect("Alice login");

    let ca_key = rcgen::KeyPair::generate().expect("inner CA key");
    let mut ca_params = rcgen::CertificateParams::new(vec![]).expect("inner CA params");
    ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    let ca = ca_params
        .self_signed(&ca_key)
        .expect("inner CA certificate");
    let server_key = rcgen::KeyPair::generate().expect("inner server key");
    let server = rcgen::CertificateParams::new(vec!["localhost".into()])
        .expect("inner server params")
        .signed_by(&server_key, &ca, &ca_key)
        .expect("inner server certificate");
    let client_key = rcgen::KeyPair::generate().expect("inner client key");
    let mut client_params = rcgen::CertificateParams::new(vec![]).expect("inner client params");
    client_params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    let client = client_params
        .signed_by(&client_key, &ca, &ca_key)
        .expect("inner client certificate");
    let fingerprint = |bytes: &[u8]| {
        let digest = Sha256::digest(bytes);
        let mut output = [0u8; 32];
        output.copy_from_slice(&digest);
        output
    };
    let server_der = server.der().to_vec();
    let client_der = client.der().to_vec();

    let host_identity = DeviceIdentity::generate();
    let host_auth_public_key = host_identity.public_key_bytes();
    let host_device = alice
        .register_device("secure-relay-host", host_identity)
        .await
        .expect("Host device registration");
    let host = alice
        .register_host(&host_device)
        .await
        .expect("Host registration");
    let host_connection = alice
        .connector()
        .connect_host(&host)
        .await
        .expect("Host control connection");
    host_connection
        .publish(HostSnapshot {
            incarnation: "secure-host-incarnation".into(),
            revision: 1,
            endpoints: vec![RelayEndpoint {
                network: RelayNetwork::Tailscale,
                url: "wss://100.64.0.10:9443".into(),
            }],
            sessions: vec![RelaySession {
                id: "secure-host-session".into(),
            }],
        })
        .await
        .expect("publish Host snapshot");

    let client_identity = DeviceIdentity::generate();
    let client_auth_public_key = client_identity.public_key_bytes();
    let client_device = alice
        .register_device("secure-relay-client", client_identity)
        .await
        .expect("Client device registration");
    let client_connection = alice
        .connector()
        .connect(&client_device)
        .await
        .expect("Client control connection");
    let host_binding = RelayPeerBinding::new(
        host_device.id(),
        host_auth_public_key,
        fingerprint(&server_der),
    );
    let client_binding = RelayPeerBinding::new(
        client_device.id(),
        client_auth_public_key,
        fingerprint(&client_der),
    );
    let client_tls = RelayTlsClientIdentity::new(
        "localhost",
        ca.der().to_vec(),
        client_der.clone(),
        client_key.serialize_der(),
    );
    let server_tls = RelayTlsServerIdentity::new(
        server_der.clone(),
        server_key.serialize_der(),
        ca.der().to_vec(),
    );

    let host_id = host.id().to_owned();
    let client_task = tokio::spawn({
        let client_tls = client_tls.clone();
        async move {
            let tunnel = client_connection
                .open_secure_tunnel(&host_id, "secure-host-session", host_binding, client_tls)
                .await;
            (client_connection, tunnel)
        }
    });
    let offer = host_connection
        .next_offer()
        .await
        .expect("Host receives secure offer");
    let host_tunnel = host_connection
        .accept_secure_offer(offer, client_binding, server_tls.clone())
        .await
        .expect("Host accepts secure offer");
    let (client_connection, client_result) = client_task.await.expect("client task");
    let client_tunnel = client_result.expect("client secure tunnel");
    assert_eq!(client_tunnel.host_id(), host.id());
    assert_eq!(client_tunnel.session_id(), "secure-host-session");

    host_tunnel
        .control()
        .send(b"secure-control")
        .await
        .expect("secure control send");
    assert_eq!(
        client_tunnel
            .control()
            .recv()
            .await
            .expect("secure control receive"),
        b"secure-control"
    );
    client_tunnel
        .media()
        .send(b"secure-media")
        .await
        .expect("secure media send");
    assert_eq!(
        host_tunnel
            .media()
            .recv()
            .await
            .expect("secure media receive"),
        b"secure-media"
    );

    drop(client_tunnel);
    drop(host_tunnel);

    let wrong_host_key =
        RelayPeerBinding::new(host_device.id(), [0u8; 32], fingerprint(&server_der));
    let host_id = host.id().to_owned();
    let mut wrong_key_task = tokio::spawn({
        let client_tls = client_tls.clone();
        async move {
            let result = client_connection
                .open_secure_tunnel(&host_id, "secure-host-session", wrong_host_key, client_tls)
                .await;
            (client_connection, result)
        }
    });
    let wrong_key_offer = host_connection
        .next_offer()
        .await
        .expect("Host receives wrong-key offer");
    let mut wrong_key_host_handshake = Box::pin(host_connection.accept_secure_offer(
        wrong_key_offer,
        RelayPeerBinding::new(
            client_device.id(),
            client_auth_public_key,
            fingerprint(&client_der),
        ),
        server_tls.clone(),
    ));
    let (client_connection, wrong_key_result) = tokio::time::timeout(
        Duration::from_secs(5),
        async {
            tokio::select! {
                result = &mut wrong_key_task => result.expect("wrong-key task"),
                _ = &mut wrong_key_host_handshake => (&mut wrong_key_task).await.expect("wrong-key task after host"),
            }
        },
    )
    .await
    .expect("wrong-key handshake timeout");
    drop(wrong_key_host_handshake);
    assert!(matches!(
        wrong_key_result,
        Err(RelayFailure::IdentityMismatch)
    ));

    let alternate_server_key = rcgen::KeyPair::generate().expect("alternate server key");
    let alternate_server = rcgen::CertificateParams::new(vec!["localhost".into()])
        .expect("alternate server params")
        .signed_by(&alternate_server_key, &ca, &ca_key)
        .expect("alternate server certificate");
    let alternate_server_tls = RelayTlsServerIdentity::new(
        alternate_server.der().to_vec(),
        alternate_server_key.serialize_der(),
        ca.der().to_vec(),
    );
    let host_binding = RelayPeerBinding::new(
        host_device.id(),
        host_auth_public_key,
        fingerprint(&server_der),
    );
    let host_id = host.id().to_owned();
    let mut unpinned_task = tokio::spawn({
        let client_tls = client_tls.clone();
        async move {
            let result = client_connection
                .open_secure_tunnel(&host_id, "secure-host-session", host_binding, client_tls)
                .await;
            (client_connection, result)
        }
    });
    let unpinned_offer = host_connection
        .next_offer()
        .await
        .expect("Host receives unpinned-certificate offer");
    let mut unpinned_host_handshake = Box::pin(host_connection.accept_secure_offer(
        unpinned_offer,
        RelayPeerBinding::new(
            client_device.id(),
            client_auth_public_key,
            fingerprint(&client_der),
        ),
        alternate_server_tls,
    ));
    let (client_connection, unpinned_result) = tokio::time::timeout(
        Duration::from_secs(5),
        async {
            tokio::select! {
                result = &mut unpinned_task => result.expect("unpinned-certificate task"),
                _ = &mut unpinned_host_handshake => (&mut unpinned_task).await.expect("unpinned-certificate task after host"),
            }
        },
    )
    .await
    .expect("unpinned-certificate handshake timeout");
    drop(unpinned_host_handshake);
    assert!(matches!(
        unpinned_result,
        Err(RelayFailure::IdentityMismatch)
    ));

    drop(client_connection);
    drop(host_connection);
    fixture.shutdown().await;
}
