//! JNI owns native handle lifetime and displayed-frame acknowledgement only.
//! TLS, browser ABI and control arbitration stay in their existing owners.
use std::{collections::{HashMap,VecDeque}, net::IpAddr, sync::{Arc, Mutex, OnceLock}, time::Duration};
use agentbrowser_connection::{Connection, Connector, DisplayedFrame, Failure, Input, Pairing, WebRtcConfig, protocol::{VideoPacket, ViewportDeclaration, Device, Orientation}};
use jni::{JNIEnv, objects::{JByteArray, JClass, JObject, JString, JThrowable, JValue}, sys::{jdouble, jint, jlong, jobject, jstring}};

mod account;

struct Session {
    // Retain the connector: dropping it fences its Connection.
    _connector: Connector,
    connection: Connection,
    pending: Option<(u64, DisplayedFrame)>,
    displayed: VecDeque<(u64,DisplayedFrame)>,
}

#[derive(Default)]
struct Registry { next: u64, sessions: HashMap<u64, Arc<Mutex<Session>>> }
static SESSIONS: OnceLock<Mutex<Registry>> = OnceLock::new();
static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
type Result<T> = std::result::Result<T, String>;

pub(crate) fn runtime() -> Result<&'static tokio::runtime::Runtime> {
    if let Some(runtime) = RUNTIME.get() { return Ok(runtime); }
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().map_err(error)?;
    // Concurrent first callers may construct an unused runtime; no sessions have
    // been spawned on it, and only the installed runtime owns network tasks.
    let _ = RUNTIME.set(runtime);
    Ok(RUNTIME.get().unwrap())
}
pub(crate) fn error(value: impl std::fmt::Display) -> String { value.to_string() }
fn registry() -> &'static Mutex<Registry> { SESSIONS.get_or_init(Default::default) }
fn session(handle: jlong) -> Result<Arc<Mutex<Session>>> {
    if handle <= 0 { return Err("Invalid native connection handle".into()); }
    registry().lock().map_err(error)?.sessions.get(&(handle as u64)).cloned().ok_or("Closed native connection handle".into())
}
pub(crate) fn fail(env: &mut JNIEnv, message: String) {
    // Preserve a pending JVM exception (for example allocation failure).
    if !env.exception_check().unwrap_or(true) { let _ = env.throw_new("java/lang/IllegalStateException", message); }
}

enum CommandFailure { Local(String), Connection(Failure) }
impl From<String> for CommandFailure { fn from(value: String) -> Self { Self::Local(value) } }
impl From<&str> for CommandFailure { fn from(value: &str) -> Self { Self::Local(value.into()) } }
impl From<Failure> for CommandFailure { fn from(value: Failure) -> Self { Self::Connection(value) } }

fn fail_command(env: &mut JNIEnv, failure: CommandFailure) {
    match failure {
        CommandFailure::Connection(Failure::Host { code, message }) => {
            if env.exception_check().unwrap_or(true) { return; }
            let raised = (|| -> jni::errors::Result<()> {
                let code = JObject::from(env.new_string(code)?);
                let message = JObject::from(env.new_string(message)?);
                let exception = env.new_object("com/agentbrowser/probe/HostCommandException",
                    "(Ljava/lang/String;Ljava/lang/String;)V", &[JValue::Object(&code), JValue::Object(&message)])?;
                env.throw(JThrowable::from(exception))
            })();
            if let Err(error) = raised { fail(env, error.to_string()); }
        }
        CommandFailure::Connection(failure) => fail(env, error(failure)),
        CommandFailure::Local(message) => fail(env, message),
    }
}

enum NativeTransport { Wss, WebRtc(IpAddr) }

fn parse_webrtc_bind_ip(value: &str) -> Result<IpAddr> {
    let ip = value.parse::<IpAddr>().map_err(|_| "Invalid WebRTC bind IP".to_string())?;
    if ip.is_unspecified() || ip.is_multicast() {
        return Err("Invalid WebRTC bind IP: unspecified or multicast".into());
    }
    Ok(ip)
}

fn pairing(env: &mut JNIEnv, endpoint: JString, ca: JByteArray, cert: JByteArray, key: JByteArray) -> Result<Pairing> {
    Ok(Pairing {
        endpoint: env.get_string(&endpoint).map_err(error)?.into(),
        server_ca_der: env.convert_byte_array(ca).map_err(error)?,
        client_cert_der: env.convert_byte_array(cert).map_err(error)?,
        client_key_pkcs8_der: env.convert_byte_array(key).map_err(error)?,
    })
}

fn open_native(pairing: Pairing, transport: NativeTransport) -> Result<jlong> {
    let mut connector = Connector::default();
    let connection = match transport {
        NativeTransport::Wss => runtime()?.block_on(connector.connect(pairing, None)).map_err(error)?,
        NativeTransport::WebRtc(bind_ip) => runtime()?.block_on(connector.connect_webrtc_with_config(
            pairing,
            None,
            WebRtcConfig { bind_ip },
        )).map_err(error)?,
    };
    let mut registry = registry().lock().map_err(error)?;
    if registry.sessions.len() >= 4 { return Err("Native connection capacity reached".into()); }
    registry.next = registry.next.checked_add(1).filter(|id| *id <= i64::MAX as u64).ok_or("Native handle exhausted")?;
    let id = registry.next;
    registry.sessions.insert(id, Arc::new(Mutex::new(Session { _connector: connector, connection, pending: None, displayed: VecDeque::new() })));
    Ok(id as jlong)
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_open(
    mut env: JNIEnv, _: JClass, endpoint: JString, ca: JByteArray, cert: JByteArray, key: JByteArray,
) -> jlong {
    let result = (|| -> Result<jlong> {
        open_native(pairing(&mut env, endpoint, ca, cert, key)?, NativeTransport::Wss)
    })();
    match result { Ok(id) => id, Err(message) => { fail(&mut env, message); 0 } }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_openWebRtc(
    mut env: JNIEnv, _: JClass, endpoint: JString, ca: JByteArray, cert: JByteArray, key: JByteArray, bind_ip: JString,
) -> jlong {
    let result = (|| -> Result<jlong> {
        let bind_ip: String = env.get_string(&bind_ip).map_err(error)?.into();
        let bind_ip = parse_webrtc_bind_ip(&bind_ip)?;
        open_native(pairing(&mut env, endpoint, ca, cert, key)?, NativeTransport::WebRtc(bind_ip))
    })();
    match result { Ok(id) => id, Err(message) => { fail(&mut env, message); 0 } }
}

#[cfg(test)]
mod tests {
    use super::parse_webrtc_bind_ip;
    use std::net::{IpAddr, Ipv4Addr};

    #[test]
    fn bind_ip_accepts_literal_unicast_address() {
        assert_eq!(parse_webrtc_bind_ip("100.66.1.82").unwrap(), IpAddr::V4(Ipv4Addr::new(100, 66, 1, 82)));
    }

    #[test]
    fn bind_ip_rejects_invalid_unspecified_and_multicast_values() {
        for value in ["not-an-ip", "0.0.0.0", "224.0.0.1", "::"] {
            assert!(parse_webrtc_bind_ip(value).is_err(), "accepted {value}");
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_frame(mut env: JNIEnv, _: JClass, handle: jlong) -> jobject {
    let result = (|| -> Result<jobject> {
        let session = session(handle)?; let mut session = session.lock().map_err(error)?;
        if let Some(failure) = session.connection.failure.borrow().clone() { return Err(error(failure)); }
        if session.pending.is_some() { return Err("Frame acknowledgement pending".into()); }
        let changed = runtime()?.block_on(async { tokio::time::timeout(Duration::from_millis(100), session.connection.media.changed()).await });
        match changed { Err(_) => return Ok(std::ptr::null_mut()), Ok(Err(failure)) => return Err(error(failure)), Ok(Ok(())) => {} }
        let video = session.connection.media.borrow_and_update().clone().ok_or("Missing media event")?;
        match &video.packet {
            VideoPacket::Waiting { .. } => Ok(std::ptr::null_mut()),
            VideoPacket::Unavailable { message, .. } => Err(format!("Host media unavailable: {message}")),
            VideoPacket::Closed { .. } => Err("Host media closed".into()),
            VideoPacket::AccessUnit { source, pts_us, coded_width, coded_height, .. } => {
                if video.bytes.len() > 1024 * 1024 { return Err("Access unit exceeds Android decoder 1MiB limit".into()); }
                if [source.sequence, *pts_us, source.document_revision, source.viewport_revision].iter().any(|value| *value > i64::MAX as u64) { return Err("Native frame identity overflow".into()); }
                let bytes = env.byte_array_from_slice(&video.bytes).map_err(error)?;
                let object = env.new_object("com/agentbrowser/probe/NetworkFrame", "([BIIIIJJJJ)V", &[
                    JValue::Object(&JObject::from(bytes)), JValue::Int(*coded_width as i32), JValue::Int(*coded_height as i32),
                    JValue::Int(source.width as i32), JValue::Int(source.height as i32), JValue::Long(*pts_us as i64), JValue::Long(source.sequence as i64),
                    JValue::Long(source.document_revision as i64), JValue::Long(source.viewport_revision as i64),
                ]).map_err(error)?;
                session.pending = Some((source.sequence, DisplayedFrame { generation: session.connection.generation,
                    session_id: source.session_id.clone(), document_revision: source.document_revision, viewport_revision: source.viewport_revision }));
                Ok(object.into_raw())
            }
        }
    })();
    match result { Ok(object) => object, Err(message) => { fail(&mut env, message); std::ptr::null_mut() } }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_acknowledge(mut env: JNIEnv, _: JClass, handle: jlong, ticket: jlong) {
    let result = (|| -> Result<()> {
        let session = session(handle)?; let mut session = session.lock().map_err(error)?;
        acknowledge(&mut session, ticket)
    })();
    if let Err(message) = result { fail(&mut env, message); }
}

fn acknowledge(session: &mut Session, ticket: jlong) -> Result<()> {
    if let Some(failure) = session.connection.failure.borrow().clone() { return Err(error(failure)); }
    if ticket <= 0 || session.pending.as_ref().map(|(id, _)| *id) != Some(ticket as u64) { return Err("Stale displayed frame acknowledgement".into()); }
    session.displayed.push_back(session.pending.take().unwrap());
    if session.displayed.len()>8 { session.displayed.pop_front(); }
    Ok(())
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_command(
    mut env: JNIEnv, _: JClass, handle: jlong, op: jint, epoch: jlong, ticket: jlong,
    x: jdouble, y: jdouble, dx: jdouble, dy: jdouble, text: JString,
) -> jstring {
    let result = (|| -> std::result::Result<jstring, CommandFailure> {
        if epoch < 0 { return Err("Invalid control epoch".into()); }
        let text: String = env.get_string(&text).map_err(error)?.into();
        let session = session(handle)?; let session = session.lock().map_err(error)?;
        let connection = &session.connection;
        let response: std::result::Result<String, CommandFailure> = runtime()?.block_on(async {
            match op {
                0 => Ok(serde_json::to_string(&connection.status().await?).map_err(error)?),
                6 => {
                    if x <= 0.0 || y <= 0.0 || x > 4096.0 || y > 4096.0 || x * y > 4_194_304.0 {
                        return Err("INVALID_VIEWPORT".into());
                    }
                    let viewport = ViewportDeclaration {
                        device: Device::Phone,
                        css_width: x as u32,
                        css_height: y as u32,
                        orientation: if dx != 0.0 { Orientation::Landscape } else { Orientation::Portrait },
                    };
                    Ok(serde_json::to_string(&connection.declare_viewport(viewport).await?).map_err(error)?)
                }
                1 => Ok(serde_json::to_string(&connection.takeover(epoch as u64).await?).map_err(error)?),
                2 => Ok(serde_json::to_string(&connection.release(epoch as u64).await?).map_err(error)?),
                7 => Ok(serde_json::to_string(&connection.navigate(text, epoch as u64).await?).map_err(error)?),
                3..=5 => {
                    if ![x,y,dx,dy].iter().all(|number| number.is_finite()) { return Err("Nonfinite input coordinates".into()); }
                    let frame = session.displayed.iter().find(|(id,_)|ticket>0&&*id==ticket as u64)
                        .map(|(_,frame)|frame.clone()).ok_or("No retained acknowledged displayed frame")?;
                    let input = match op { 3 => Input::Click { x,y }, 4 => Input::Text(text), _ => Input::Scroll { x,y,delta_x:dx,delta_y:dy } };
                    connection.input(input, frame, epoch as u64).await?;
                    // The Host receipt is represented by the successful typed API;
                    // subsequent status remains a separate authoritative read.
                    Ok(serde_json::to_string(&agentbrowser_connection::protocol::ResultValue::Input {
                        input: agentbrowser_connection::protocol::InputReceipt { state: agentbrowser_connection::protocol::InputState::Succeeded }
                    }).map_err(error)?)
                }
                _ => Err("Unknown native command".into()),
            }
        });
        Ok(env.new_string(response?).map_err(error)?.into_raw())
    })();
    match result { Ok(value) => value, Err(failure) => { fail_command(&mut env, failure); std::ptr::null_mut() } }
}

#[no_mangle]
pub extern "system" fn Java_com_agentbrowser_probe_NativeConnection_close(mut env: JNIEnv, _: JClass, handle: jlong) {
    let result = (|| -> Result<()> {
        let session = registry().lock().map_err(error)?.sessions.remove(&(handle as u64)).ok_or("Closed native connection handle")?;
        // Java serializes operations; refusing outstanding shared access avoids
        // declaring release before an in-flight JNI operation has returned.
        let session = Arc::try_unwrap(session).map_err(|_| "Native operation still in progress")?.into_inner().map_err(error)?;
        session.connection.close();
        Ok(())
    })();
    if let Err(message) = result { fail(&mut env, message); }
}
