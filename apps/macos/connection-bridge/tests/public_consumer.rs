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
    path::PathBuf,
    process::{Command, Stdio},
};

struct Bridge {
    child: std::process::Child,
    next_id: u64,
}

impl Bridge {
    fn start() -> Self {
        Self::start_with(|command| {
            // A real authority path is never inherited by accident; the
            // availability tests below set it explicitly.
            command.env_remove("AGENTBROWSER_MAC_ADMIN_SOCKET");
        })
    }

    fn start_with(configure: impl FnOnce(&mut Command)) -> Self {
        let mut command = Command::new(env!("CARGO_BIN_EXE_agentbrowser-macos-bridge"));
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        configure(&mut command);
        let child = command.spawn().expect("spawn bridge");
        Self { child, next_id: 0 }
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
