//! Android account-directory JNI owner.
//!
//! Relay credentials and the generated device identity stay in this native
//! process. JNI exposes only a bounded, secret-free account projection.

use std::{
    collections::{BTreeMap, HashMap, HashSet},
    sync::{Arc, Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

use agentbrowser_connection::relay::{
    DeviceIdentity, DirectoryHost, RegisteredDevice, RelayClient, RelayConfig, RelayEndpoint,
    RelayNetwork, RelaySession,
};
use jni::{
    objects::{JByteArray, JClass, JString},
    sys::{jlong, jstring},
    JNIEnv,
};
use serde_json::{json, Value};
use zeroize::Zeroize;

const MAX_USERNAME: usize = 64;
const MAX_PASSWORD: usize = 1024;
const MAX_DEVICE_NAME: usize = 64;
const MAX_HANDLE_COUNT: usize = 4;
const DIRECTORY_STALE_MS: u64 = 30_000;

type Result<T> = std::result::Result<T, String>;

struct KnownHost {
    host: DirectoryHost,
    last_seen_ms: u64,
    online: bool,
}

struct AccountSession {
    relay: RelayClient,
    device: Option<RegisteredDevice>,
    hosts: BTreeMap<String, KnownHost>,
    last_refresh_ms: Option<u64>,
}

#[derive(Default)]
struct AccountRegistry {
    next: u64,
    sessions: HashMap<u64, Arc<Mutex<AccountSession>>>,
}

static ACCOUNTS: OnceLock<Mutex<AccountRegistry>> = OnceLock::new();

fn registry() -> &'static Mutex<AccountRegistry> {
    ACCOUNTS.get_or_init(Default::default)
}

fn account(handle: jlong) -> Result<Arc<Mutex<AccountSession>>> {
    if handle <= 0 {
        return Err("Invalid native account handle".into());
    }
    registry()
        .lock()
        .map_err(super::error)?
        .sessions
        .get(&(handle as u64))
        .cloned()
        .ok_or_else(|| "Closed native account handle".into())
}

fn text(env: &mut JNIEnv, value: JString, name: &str, max: usize) -> Result<String> {
    let value: String = env.get_string(&value).map_err(super::error)?.into();
    if value.is_empty() || value.len() > max {
        return Err(format!("Invalid {name}"));
    }
    Ok(value)
}

fn json_string(env: &mut JNIEnv, value: Value) -> Result<jstring> {
    let value = serde_json::to_string(&value).map_err(super::error)?;
    Ok(env.new_string(value).map_err(super::error)?.into_raw())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before Unix epoch")
        .as_millis()
        .try_into()
        .expect("system time overflow")
}

fn network(value: RelayNetwork) -> &'static str {
    match value {
        RelayNetwork::Lan => "lan",
        RelayNetwork::Public => "public",
        RelayNetwork::Tailscale => "tailscale",
    }
}

fn endpoint(value: &RelayEndpoint) -> Value {
    json!({"network": network(value.network), "url": value.url})
}

fn session(value: &RelaySession) -> Value {
    json!({"id": value.id})
}

fn host(value: &KnownHost, now: u64) -> Value {
    let status = if value.online && now.saturating_sub(value.last_seen_ms) < DIRECTORY_STALE_MS {
        "online"
    } else if now.saturating_sub(value.last_seen_ms) >= DIRECTORY_STALE_MS {
        "expired"
    } else {
        "offline"
    };
    json!({
        "hostId": value.host.host_id,
        "deviceId": value.host.device_id,
        "deviceName": value.host.device_name,
        "status": status,
        "lastSeenAtMs": value.last_seen_ms,
        "snapshot": {
            "incarnation": value.host.snapshot.incarnation,
            "revision": value.host.snapshot.revision,
            "endpoints": value.host.snapshot.endpoints.iter().map(endpoint).collect::<Vec<_>>(),
            "sessions": value.host.snapshot.sessions.iter().map(session).collect::<Vec<_>>(),
        },
    })
}

fn snapshot(value: &AccountSession) -> Value {
    let now = now_ms();
    let expired = now >= value.relay.expires_at_ms();
    let directory_state = match value.last_refresh_ms {
        None => "empty",
        Some(refreshed) if now.saturating_sub(refreshed) >= DIRECTORY_STALE_MS => "expired",
        Some(_) if value.hosts.is_empty() => "empty",
        Some(_) => "fresh",
    };
    json!({
        "accountState": if expired { "expired" } else { "authenticated" },
        "expiresAtMs": value.relay.expires_at_ms(),
        "deviceId": value.device.as_ref().map(RegisteredDevice::id),
        "directoryState": directory_state,
        "hosts": value.hosts.values().map(|item| host(item, now)).collect::<Vec<_>>(),
    })
}

fn allocate(session: AccountSession) -> Result<jlong> {
    let mut registry = registry().lock().map_err(super::error)?;
    if registry.sessions.len() >= MAX_HANDLE_COUNT {
        return Err("Native account capacity reached".into());
    }
    registry.next = registry
        .next
        .checked_add(1)
        .filter(|value| *value <= i64::MAX as u64)
        .ok_or_else(|| "Native account handle exhausted".to_string())?;
    let handle = registry.next;
    registry
        .sessions
        .insert(handle, Arc::new(Mutex::new(session)));
    Ok(handle as jlong)
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_login(
    mut env: JNIEnv,
    _: JClass,
    origin: JString,
    ca: JByteArray,
    username: JString,
    password: JString,
) -> jlong {
    let result = (|| -> Result<jlong> {
        let origin = text(&mut env, origin, "Relay origin", 256)?;
        let mut username = text(&mut env, username, "username", MAX_USERNAME)?;
        let mut password = text(&mut env, password, "password", MAX_PASSWORD)?;
        let ca = env.convert_byte_array(ca).map_err(super::error)?;
        let config = RelayConfig::new(&origin, ca);
        let login = match config {
            Ok(config) => super::runtime()?
                .block_on(RelayClient::login(config, &username, &password))
                .map_err(super::error),
            Err(error) => Err(super::error(error)),
        };
        password.zeroize();
        username.zeroize();
        let relay = login?;
        allocate(AccountSession {
            relay,
            device: None,
            hosts: BTreeMap::new(),
            last_refresh_ms: None,
        })
    })();
    match result {
        Ok(handle) => handle,
        Err(message) => {
            super::fail(&mut env, message);
            0
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_status(
    mut env: JNIEnv,
    _: JClass,
    handle: jlong,
) -> jstring {
    let result = (|| -> Result<jstring> {
        let session = account(handle)?;
        let session = session.lock().map_err(super::error)?;
        json_string(&mut env, snapshot(&session))
    })();
    match result {
        Ok(value) => value,
        Err(message) => {
            super::fail(&mut env, message);
            std::ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_registerDevice(
    mut env: JNIEnv,
    _: JClass,
    handle: jlong,
    name: JString,
) -> jstring {
    let result = (|| -> Result<jstring> {
        let name = text(&mut env, name, "device name", MAX_DEVICE_NAME)?;
        let session = account(handle)?;
        let mut session = session.lock().map_err(super::error)?;
        if session.device.is_some() {
            return Err("DEVICE_ALREADY_REGISTERED".into());
        }
        let device = super::runtime()?
            .block_on(
                session
                    .relay
                    .register_device(&name, DeviceIdentity::generate()),
            )
            .map_err(super::error)?;
        session.device = Some(device);
        json_string(&mut env, snapshot(&session))
    })();
    match result {
        Ok(value) => value,
        Err(message) => {
            super::fail(&mut env, message);
            std::ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_refresh(
    mut env: JNIEnv,
    _: JClass,
    handle: jlong,
) -> jstring {
    let result = (|| -> Result<jstring> {
        let session = account(handle)?;
        let mut session = session.lock().map_err(super::error)?;
        let hosts = super::runtime()?
            .block_on(session.relay.list_directory())
            .map_err(super::error)?;
        let refreshed = now_ms();
        let current = hosts
            .iter()
            .map(|item| item.host_id.clone())
            .collect::<HashSet<_>>();
        for item in hosts {
            session.hosts.insert(
                item.host_id.clone(),
                KnownHost {
                    host: item,
                    last_seen_ms: refreshed,
                    online: true,
                },
            );
        }
        for (id, item) in &mut session.hosts {
            if !current.contains(id) {
                item.online = false;
            }
        }
        session.last_refresh_ms = Some(refreshed);
        json_string(&mut env, snapshot(&session))
    })();
    match result {
        Ok(value) => value,
        Err(message) => {
            super::fail(&mut env, message);
            std::ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_revoke(
    mut env: JNIEnv,
    _: JClass,
    handle: jlong,
) {
    let result = (|| -> Result<()> {
        let session = account(handle)?;
        let session = session.lock().map_err(super::error)?;
        super::runtime()?
            .block_on(session.relay.revoke())
            .map_err(super::error)?;
        drop(session);
        registry()
            .lock()
            .map_err(super::error)?
            .sessions
            .remove(&(handle as u64));
        Ok(())
    })();
    if let Err(message) = result {
        super::fail(&mut env, message);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeAccount_close(
    mut env: JNIEnv,
    _: JClass,
    handle: jlong,
) {
    let result = (|| -> Result<()> {
        if handle <= 0 {
            return Err("Invalid native account handle".into());
        }
        registry()
            .lock()
            .map_err(super::error)?
            .sessions
            .remove(&(handle as u64))
            .ok_or_else(|| "Closed native account handle".to_string())?;
        Ok(())
    })();
    if let Err(message) = result {
        super::fail(&mut env, message);
    }
}
