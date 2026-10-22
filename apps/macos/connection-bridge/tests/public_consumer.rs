//! Public consumer of the AgentBrowserMacBridge process boundary.
//!
//! This drives the real bridge binary over its line-delimited request /
//! frame-delimited response protocol, exactly as the Swift host does, rather
//! than calling internal functions. It proves the typed command surface the
//! native client depends on: closed command fields, typed refusals, and the
//! "no session" guard that keeps a typed editing/gesture command from being
//! coerced into a fake success.
//!
//! Host-dependent behaviour (real hit test, EditorContext, editing receipt,
//! Host refusal codes, and OUTCOME_UNKNOWN) requires a live Host endpoint and
//! pairing directory. Those paths are exercised in the runtime window with
//! `AGENTBROWSER_MAC_PAIRING` set; see the crate's run notes. This consumer
//! never mocks a Host success.

use std::{
    io::{Read, Write},
    os::unix::net::UnixListener,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

struct Bridge {
    child: std::process::Child,
    next_id: u64,
    home: PathBuf,
    cleanup_home: bool,
}

impl Bridge {
    fn start() -> Self {
        // Every test owns a private HOME so the developer's real persistent
        // pairing store never influences a test result.
        let home = unique_path("home");
        std::fs::create_dir_all(&home).expect("create isolated home");
        Self::start_with_home(&home, |command| {
            // A real authority path is never inherited by accident; the
            // availability tests below set it explicitly.
            command.env_remove("AGENTBROWSER_MAC_ADMIN_SOCKET");
        })
    }

    fn start_with(configure: impl FnOnce(&mut Command)) -> Self {
        let home = unique_path("home");
        std::fs::create_dir_all(&home).expect("create isolated home");
        Self::start_with_home(&home, configure)
    }

    /// Owns `home` on drop. A test that must survive the bridge process keeps
    /// the directory with `keep_home` so its own assertions can read it.
    fn start_with_home(home: &Path, configure: impl FnOnce(&mut Command)) -> Self {
        let mut command = Command::new(env!("CARGO_BIN_EXE_agentbrowser-macos-bridge"));
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env("HOME", home)
            .env_remove("AGENTBROWSER_MAC_PAIRING");
        configure(&mut command);
        let child = command.spawn().expect("spawn bridge");
        Self {
            child,
            next_id: 0,
            home: home.to_path_buf(),
            cleanup_home: true,
        }
    }

    fn keep_home(&mut self) {
        self.cleanup_home = false;
    }

    fn request(&mut self, command: &str) -> String {
        self.next_id += 1;
        let id = self.next_id;
        let envelope = serde_json::json!({"id": id, "command": command});
        let stdin = self.child.stdin.as_mut().expect("stdin");
        writeln!(stdin, "{envelope}").expect("write request");
        stdin.flush().expect("flush request");
        let stdout = self.child.stdout.as_mut().expect("stdout");
        let mut kind = [0u8; 1];
        stdout.read_exact(&mut kind).expect("response kind");
        assert_eq!(kind[0], 0, "expected a text response frame");
        let header_length = read_u32(stdout) as usize;
        let mut header = vec![0u8; header_length];
        stdout.read_exact(&mut header).expect("response header");
        let body_length = read_u32(stdout) as usize;
        let mut body = vec![0u8; body_length];
        stdout.read_exact(&mut body).expect("response body");
        let header: serde_json::Value =
            serde_json::from_slice(&header).expect("header json");
        assert_eq!(header["type"], "response");
        assert_eq!(header["id"].as_u64(), Some(id));
        header["value"].as_str().expect("value string").to_owned()
    }
}

impl Drop for Bridge {
    fn drop(&mut self) {
        drop(self.child.stdin.take());
        let _ = self.child.wait();
        if self.cleanup_home {
            let _ = std::fs::remove_dir_all(&self.home);
        }
    }
}

fn read_u32(stream: &mut impl Read) -> u32 {
    let mut bytes = [0u8; 4];
    stream.read_exact(&mut bytes).expect("u32");
    u32::from_be_bytes(bytes)
}

fn rejection(value: &str) -> Option<String> {
    serde_json::from_str::<serde_json::Value>(value)
        .ok()?
        .get("rejection")?
        .as_str()
        .map(str::to_owned)
}

#[test]
fn typed_editing_command_without_session_is_explicit() {
    let mut bridge = Bridge::start();
    let command = serde_json::json!({
        "op": "editing",
        "epoch": 1,
        "operation_id": "pointer-1",
        "event_sequence": 0,
        "binding": {"type": "gesture", "gesture_session_id": "pointer-1"},
        "event": {"type": "pointer_down", "pointer_id": 1, "x": 5.0, "y": 6.0, "button": "left", "buttons": 1, "modifiers": 0}
    });
    let value = bridge.request(&command.to_string());
    assert_eq!(rejection(&value), Some("NETWORK_NOT_CONNECTED".to_owned()));
}

#[test]
fn typed_select_without_session_is_explicit() {
    let mut bridge = Bridge::start();
    let command = serde_json::json!({
        "op": "select",
        "epoch": 1,
        "operation_id": "edit-1",
        "event_sequence": 0,
        "edit_session_id": "session-edit",
        "target_id": "node-1",
        "focus_revision": 3,
        "editor_revision": 4,
        "start": 0,
        "end": 2,
        "direction": "forward"
    });
    let value = bridge.request(&command.to_string());
    assert_eq!(rejection(&value), Some("NETWORK_NOT_CONNECTED".to_owned()));
}

#[test]
fn unknown_editing_field_is_rejected() {
    let mut bridge = Bridge::start();
    let command = serde_json::json!({
        "op": "editing",
        "epoch": 1,
        "operation_id": "pointer-1",
        "event_sequence": 0,
        "binding": {"type": "gesture", "gesture_session_id": "pointer-1"},
        "event": {"type": "pointer_down", "pointer_id": 1, "x": 5.0, "y": 6.0, "button": "left", "buttons": 1, "modifiers": 0},
        "extra": true
    });
    let value = bridge.request(&command.to_string());
    assert_eq!(rejection(&value), Some("UNKNOWN_COMMAND_FIELD".to_owned()));
}

#[test]
fn invalid_editing_binding_is_typed() {
    let mut bridge = Bridge::start();
    let command = serde_json::json!({
        "op": "editing",
        "epoch": 1,
        "operation_id": "pointer-1",
        "event_sequence": 0,
        "binding": {"type": "gesture"},
        "event": {"type": "pointer_down", "pointer_id": 1, "x": 5.0, "y": 6.0, "button": "left", "buttons": 1, "modifiers": 0}
    });
    let value = bridge.request(&command.to_string());
    assert_eq!(rejection(&value), Some("INVALID_EDITING_BINDING".to_owned()));
}

#[test]
fn invalid_selection_direction_is_typed() {
    let mut bridge = Bridge::start();
    let command = serde_json::json!({
        "op": "select",
        "epoch": 1,
        "operation_id": "edit-1",
        "event_sequence": 0,
        "edit_session_id": "session-edit",
        "target_id": "node-1",
        "focus_revision": 3,
        "editor_revision": 4,
        "start": 0,
        "end": 2,
        "direction": "sideways"
    });
    let value = bridge.request(&command.to_string());
    assert_eq!(rejection(&value), Some("INVALID_SELECTION_DIRECTION".to_owned()));
}

#[test]
fn status_without_session_reports_observe() {
    let mut bridge = Bridge::start();
    let value = bridge.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_eq!(snapshot["controlMode"], "observe");
    assert_eq!(snapshot["inputReady"], false);
    assert!(snapshot.get("editorContext").is_none());
    assert!(snapshot.get("inputLease").is_none());
}

fn unique_path(tag: &str) -> PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!(
        "agentbrowser-macos-bridge-{tag}-{}-{:?}",
        std::process::id(),
        std::thread::current().id()
    ));
    path
}

/// `docs/m1/connection-minimum.md` §7.6 freezes the client projection to exactly
/// `state`, `error`, `label`, with `state` in {`configured`, `unconfigured`}.
/// The client-domain consumer rejects any other key and any other state, so an
/// extra field or a legacy state here breaks app boot with
/// `INVALID_NATIVE_RESPONSE`. This asserts the producer against that contract in
/// both pairing states, so the projection cannot drift from the consumer again.
#[test]
fn connection_setup_projection_matches_the_frozen_client_contract() {
    fn assert_frozen(snapshot: &serde_json::Value, expected_state: &str) {
        let setup = snapshot["connectionSetup"]
            .as_object()
            .expect("snapshot carries a connectionSetup object");
        let mut keys: Vec<&str> = setup.keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec!["error", "label", "state"],
            "connectionSetup must carry exactly the frozen client keys"
        );
        assert_eq!(
            setup["state"], expected_state,
            "connectionSetup reported the wrong frozen state"
        );
        assert!(
            setup["error"].is_null() || setup["error"].is_string(),
            "connectionSetup error must be null or a string"
        );
    }

    let mut unpaired = Bridge::start();
    let value = unpaired.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_frozen(&snapshot, "unconfigured");

    let pairing = unique_path("connection-setup");
    std::fs::create_dir_all(&pairing).expect("create pairing dir");
    std::fs::write(pairing.join("endpoint.txt"), "127.0.0.1:50123").expect("write endpoint");
    std::fs::write(pairing.join("token"), "fixture-token").expect("write token");
    let mut paired = Bridge::start_with(|command| {
        command.env("AGENTBROWSER_MAC_PAIRING", &pairing);
    });
    let value = paired.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_frozen(&snapshot, "configured");
    let _ = std::fs::remove_dir_all(&pairing);
}

#[test]
fn provisioning_unavailable_without_admin_socket() {
    let mut bridge = Bridge::start();
    let value = bridge.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_eq!(
        snapshot["provisioningAvailable"],
        serde_json::json!(false),
        "an absent admin socket must not report local provisioning available"
    );
    // Remote pairing truth is a separate concern and keeps its own value.
    assert_eq!(snapshot["networkConfigured"], serde_json::json!(false));
}

#[test]
fn provisioning_unavailable_for_stale_socket_path() {
    // A plain file at the declared path is not a live authority. Existence
    // alone must never be treated as an available admin socket.
    let path = unique_path("stale");
    std::fs::write(&path, b"not a socket").expect("write stale path");
    let mut bridge = Bridge::start_with(|command| {
        command.env("AGENTBROWSER_MAC_ADMIN_SOCKET", &path);
    });
    let value = bridge.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_eq!(snapshot["provisioningAvailable"], serde_json::json!(false));
    let _ = std::fs::remove_file(&path);
}

#[test]
fn provisioning_available_requires_connectable_authority() {
    // A real listening local control socket at the declared path is the one
    // condition that reports the capability available. This proves only the
    // transport probe; it is not a host-admin authority, a provisioning
    // success, or an exported bundle.
    let path = unique_path("live");
    let listener = UnixListener::bind(&path).expect("bind admin socket");
    let mut bridge = Bridge::start_with(|command| {
        command.env("AGENTBROWSER_MAC_ADMIN_SOCKET", &path);
    });
    let value = bridge.request("{\"op\":\"status\"}");
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("snapshot json");
    assert_eq!(snapshot["provisioningAvailable"], serde_json::json!(true));
    drop(listener);
    let _ = std::fs::remove_file(&path);
}

fn home_store(home: &Path) -> PathBuf {
    home.join("Library")
        .join("Application Support")
        .join("AgentBrowser")
        .join("mac-pairing")
}

fn write_bundle(home: &Path, name: &str, value: &serde_json::Value) -> PathBuf {
    let path = home.join(name);
    std::fs::write(&path, serde_json::to_vec(value).expect("bundle json")).expect("write bundle");
    path
}

fn status_snapshot(bridge: &mut Bridge) -> serde_json::Value {
    let value = bridge.request("{\"op\":\"status\"}");
    serde_json::from_str(&value).expect("snapshot json")
}

#[test]
fn import_persists_pairing_for_a_later_launch_without_arguments() {
    let home = unique_path("persist-home");
    std::fs::create_dir_all(&home).expect("create home");
    let bundle = write_bundle(
        &home,
        "pairing.abconnection",
        &serde_json::json!({"endpoint": "127.0.0.1:50999", "token": "persisted-token"}),
    );
    {
        let mut bridge = Bridge::start_with_home(&home, |_| {});
        assert_eq!(
            status_snapshot(&mut bridge)["connectionSetup"]["state"],
            "unconfigured"
        );
        let command = serde_json::json!({"op": "import_connection", "path": bundle.to_string_lossy()});
        let value = bridge.request(&command.to_string());
        assert_eq!(rejection(&value), None, "import must succeed: {value}");
        assert!(
            !value.contains("persisted-token"),
            "the import response must never carry the token"
        );
        let snapshot = status_snapshot(&mut bridge);
        assert_eq!(snapshot["connectionSetup"]["state"], "configured");
        assert_eq!(snapshot["networkConfigured"], serde_json::json!(true));
        // The store is asserted after this bridge exits, so keep its HOME.
        bridge.keep_home();
    }

    let store = home_store(&home);
    assert_eq!(
        std::fs::read_to_string(store.join("endpoint.txt")).expect("endpoint"),
        "127.0.0.1:50999"
    );
    let mode = |path: &Path| {
        std::os::unix::fs::PermissionsExt::mode(&std::fs::metadata(path).expect("metadata").permissions())
            & 0o777
    };
    assert_eq!(mode(&store), 0o700, "store directory must be private");
    assert_eq!(mode(&store.join("endpoint.txt")), 0o600);
    assert_eq!(
        mode(&store.join("token")),
        0o600,
        "the token file must be private"
    );

    // A second process with the same HOME and no pairing argument is the
    // ordinary "user double-clicks the app later" case.
    let mut relaunched = Bridge::start_with_home(&home, |_| {});
    let snapshot = status_snapshot(&mut relaunched);
    assert_eq!(
        snapshot["connectionSetup"]["state"], "configured",
        "a later launch must find the persisted pairing without any argument"
    );
    let value = relaunched.request("{\"op\":\"connect\"}");
    assert!(
        !value.contains("PAIRING_"),
        "a persisted pairing must reach the network boundary instead of a pairing error: {value}"
    );
    let snapshot: serde_json::Value = serde_json::from_str(&value).expect("connect snapshot");
    assert_eq!(snapshot["connectionState"], "connecting");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn import_rejections_are_typed_and_keep_the_store() {
    let home = unique_path("reject-home");
    std::fs::create_dir_all(&home).expect("create home");
    let good = write_bundle(
        &home,
        "good.abconnection",
        &serde_json::json!({"endpoint": "127.0.0.1:50999", "token": "kept-token"}),
    );
    let mut bridge = Bridge::start_with_home(&home, |_| {});
    let command = serde_json::json!({"op": "import_connection", "path": good.to_string_lossy()});
    assert_eq!(rejection(&bridge.request(&command.to_string())), None);

    let missing = write_bundle(
        &home,
        "missing.abconnection",
        &serde_json::json!({"endpoint": "127.0.0.1:50999"}),
    );
    let command = serde_json::json!({"op": "import_connection", "path": missing.to_string_lossy()});
    assert_eq!(
        rejection(&bridge.request(&command.to_string())),
        Some("BUNDLE_INVALID".to_owned())
    );

    let absent = home.join("absent.abconnection");
    let command = serde_json::json!({"op": "import_connection", "path": absent.to_string_lossy()});
    assert_eq!(
        rejection(&bridge.request(&command.to_string())),
        Some("BUNDLE_READ_FAILED".to_owned())
    );

    let relative = serde_json::json!({"op": "import_connection", "path": "pairing.abconnection"});
    assert_eq!(
        rejection(&bridge.request(&relative.to_string())),
        Some("BUNDLE_READ_FAILED".to_owned())
    );

    let unknown = serde_json::json!({"op": "import_connection", "path": good.to_string_lossy(), "extra": 1});
    assert_eq!(
        rejection(&bridge.request(&unknown.to_string())),
        Some("UNKNOWN_COMMAND_FIELD".to_owned())
    );

    assert_eq!(
        std::fs::read_to_string(home_store(&home).join("token")).expect("token"),
        "kept-token",
        "a rejected import must not replace the working store"
    );
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn explicit_pairing_override_wins_over_the_home_store() {
    let home = unique_path("override-home");
    std::fs::create_dir_all(&home).expect("create home");
    let override_dir = unique_path("override-dir");
    std::fs::create_dir_all(&override_dir).expect("create override");
    std::fs::write(override_dir.join("endpoint.txt"), "127.0.0.1:1234").expect("endpoint");
    std::fs::write(override_dir.join("token"), "override-token").expect("token");
    let mut bridge = Bridge::start_with_home(&home, |command| {
        command.env("AGENTBROWSER_MAC_PAIRING", &override_dir);
    });
    assert_eq!(status_snapshot(&mut bridge)["connectionSetup"]["state"], "configured");
    assert!(
        !home_store(&home).exists(),
        "the override must not touch the home store"
    );
    let _ = std::fs::remove_dir_all(&home);
    let _ = std::fs::remove_dir_all(&override_dir);
}
