//! Owned real Host endpoint for Android acceptance. Credentials are ephemeral,
//! output goes to the private fixture directory, and no system trust changes.
//! The fixture also writes the frozen `agentbrowser.connection@1` bundle so the
//! installed app imports the fixture pairing through its real setup owner.
use std::{path::PathBuf, process::Stdio, time::Duration, os::unix::fs::{DirBuilderExt, PermissionsExt}};
use agentbrowser_connection::protocol::{Command, ControlPhase, Mode, Operation, Request, Response, ResultValue, SessionStatus};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

/// One runtime authorization revision for both binaries: the Host reads the
/// registry, the endpoint reads the derived projection.
const AUTHORIZATION_REVISION: u64 = 1;
const DEVICE_LABEL: &str = "Android acceptance device";
const CLIENT_ALIAS: &str = "agentbrowser-client";
const ENDPOINT_ALIAS: &str = "agentbrowser-endpoint";
const CA_ALIAS: &str = "agentbrowser-host-ca";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bin = PathBuf::from(std::env::var("OBSCURA_BIN_DIR")?);
    let bind: std::net::IpAddr = std::env::var("OBSCURA_ENDPOINT_BIND_IP")?.parse()?;
    let enable_webrtc = std::env::var("OBSCURA_ENABLE_WEBRTC").as_deref() == Ok("1");
    // The device identity is a real UUID minted per run. The registry, the
    // endpoint projection and the exported bundle all carry this one value.
    let device_id = uuid::Uuid::new_v4().to_string();
    let root = PathBuf::from(format!("/tmp/an-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&root)?;
    let ca_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![])?;
    params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    params.distinguished_name.push(rcgen::DnType::CommonName, "Android network acceptance CA");
    let ca = params.self_signed(&ca_key)?;
    let server_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![bind.to_string()])?;
    // rcgen emits no EKU by default; the frozen bundle contract requires the
    // explicit serverAuth usage on the endpoint certificate.
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ServerAuth];
    params.distinguished_name.push(rcgen::DnType::CommonName, "Android network acceptance endpoint");
    let server = params.signed_by(&server_key, &ca, &ca_key)?;
    let client_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![])?;
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    params.distinguished_name.push(rcgen::DnType::CommonName, "Android network acceptance client");
    // rcgen writes SubjectKeyIdentifier only for CA certificates, so the client
    // leaf carries it as an explicit extension. RFC 7093 method 1.
    let key_id = Sha256::digest(client_key.public_key_der())[..20].to_vec();
    params.custom_extensions.push(rcgen::CustomExtension::from_oid_content(
        &[2, 5, 29, 14], der(0x04, &key_id)));
    let client = params.signed_by(&client_key, &ca, &ca_key)?;
    std::fs::write(root.join("server.der"), server.der())?;
    std::fs::write(root.join("server-key.der"), server_key.serialize_der())?;
    std::fs::set_permissions(root.join("server-key.der"), std::fs::Permissions::from_mode(0o600))?;
    std::fs::write(root.join("ca.der"), ca.der())?;
    std::fs::write(root.join("client.der"), client.der())?;
    std::fs::write(root.join("key.der"), client_key.serialize_der())?;
    std::fs::set_permissions(root.join("key.der"), std::fs::Permissions::from_mode(0o600))?;
    // The projection pins the exact registry bytes by SHA-256; neither file
    // carries a private key.
    let fingerprint = hex(&Sha256::digest(client.der()));
    let scopes = vec!["observe_human_control", "profile_read", "profile_create", "profile_select"];
    let registry = serde_json::json!({
        "schema_version": 2,
        "authorization_revision": AUTHORIZATION_REVISION,
        "host_id": "android-acceptance-host",
        "devices": [{
            "device_id": &device_id,
            "device_label": DEVICE_LABEL,
            "client_certificate_sha256": &fingerprint,
            "scopes": &scopes,
            "initial_profile_grant": {"selection": "default_profile", "authorization_revision": AUTHORIZATION_REVISION},
            "scope_revision": 1,
            "status": "active",
            "not_before_unix": 0,
            "not_after_unix": 4102444800i64,
            "created_at_unix": 0,
            "revoked_at_unix": null
        }]
    });
    let registry_bytes = serde_json::to_vec(&registry)?;
    std::fs::write(root.join("registry.json"), &registry_bytes)?;
    let projection = serde_json::json!({
        "schema_version": 2,
        "authorization_revision": AUTHORIZATION_REVISION,
        "registry_sha256": hex(&Sha256::digest(&registry_bytes)),
        "clients": [{
            "device_id": &device_id,
            "client_certificate_sha256": &fingerprint,
            "scopes": &scopes,
            "initial_profile_grant": {"selection": "default_profile", "authorization_revision": AUTHORIZATION_REVISION}
        }]
    });
    std::fs::write(root.join("authorized-clients.json"), serde_json::to_vec(&projection)?)?;
    // The registry names the device, but the Host profile catalog owns profile
    // membership. Grant the same device the default profile so remote admission
    // resolves the default-profile grant instead of refusing it.
    let profile_root = root.join("profiles");
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&profile_root)?;
    let catalog = serde_json::json!({
        "catalog_revision": 1,
        "profiles": [{
            "profile_id": "default",
            "display_name": "Default",
            "default": true,
            "access": {"owner_device_id": "local", "members": ["local", &device_id]}
        }],
        "default_profile_id": "default",
        "next_id": 1
    });
    std::fs::write(profile_root.join("catalog.json"), serde_json::to_vec(&catalog)?)?;
    let mut host = tokio::process::Command::new(bin.join("obscura-host")).arg("--socket-dir").arg(root.join("host"))
        .arg("--profile-root").arg(&profile_root)
        .arg("--authorization-registry").arg(root.join("registry.json"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn()?;
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(tokio::net::UnixStream::connect(root.join("host/host.sock")).await?);
    let mut line = String::new(); local.read_line(&mut line).await?;
    let mut id = 0;
    let attached = status(call(&mut local, &mut id, Command::Attach { mode: Mode::Agent, viewport: None }, None).await);
    let initial_url: String =
        "data:text/html,<body style='margin:0;background:white'><button id='target' style='width:180px;height:100px;background:red' onclick=\"window.clicked=(window.clicked||0)+1;this.style.background='lime'\">touch</button><input id='field' style='display:block;width:200px;height:50px' placeholder='type here'><p>AgentBrowser live Host</p><div style='height:3000px;background:blue'></div><script>window.maxScroll=0;window.__obscuraProbe=[];['compositionstart','compositionupdate','compositionend','beforeinput','input'].forEach(t=>document.addEventListener(t,e=>window.__obscuraProbe.push({type:t,value:document.getElementById('field').value,inputType:e.inputType||null,data:e.data||null})));window.addEventListener('scroll',()=>{window.maxScroll=Math.max(window.maxScroll,window.scrollY)});</script>".into()
    ;
    let navigated = status(call(&mut local, &mut id, Command::Navigate { url: initial_url.clone() }, Some(identity(&attached))).await);
    let resized = status(call(&mut local, &mut id, Command::Resize { width:391,height:845 }, Some(identity(&navigated))).await);
    let reservation = std::net::TcpListener::bind((bind,0))?;
    let address = reservation.local_addr()?; drop(reservation);
    std::fs::write(root.join("endpoint.txt"), format!("wss://{address}"))?;
    let mut endpoint_command = tokio::process::Command::new(bin.join("obscura-endpoint"));
    endpoint_command
        .arg("--listen").arg(address.to_string()).arg("--host-dir").arg(root.join("host"))
        .arg("--socket-dir").arg(root.join("endpoint"))
        .arg("--server-cert").arg(root.join("server.der")).arg("--server-key").arg(root.join("server-key.der"))
        .arg("--client-ca").arg(root.join("ca.der")).arg("--authorized-clients").arg(root.join("authorized-clients.json"))
        .arg("--media-bin").arg(bin.join("obscura-media"));
    if enable_webrtc {
        endpoint_command.arg("--enable-webrtc").arg("--webrtc-bind-ip").arg(bind.to_string());
    }
    let mut endpoint = endpoint_command
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn()?;
    // The endpoint binds its per-session encoded socket lazily on first
    // admission, so readiness is the socket directory it creates after the TCP
    // listener is bound.
    wait(root.join("endpoint")).await;
    // The frozen agentbrowser.connection@1 bundle. Its endpoint URL keeps the
    // empty path of the frozen contract, and the PKCS#12 opens with the empty
    // password of the no-code import.
    let p12 = pkcs12(ca.der(), server.der(), client.der(), &client_key.serialize_der(), &key_id)?;
    let bundle_endpoint = format!("wss://{address}/");
    let bundle = serde_json::json!({
        "format": "agentbrowser.connection",
        "version": 1,
        "transport": "wss",
        "endpoint": bundle_endpoint,
        "device_id": &device_id,
        "device_label": DEVICE_LABEL,
        "created_at_unix": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_secs(),
        "p12_base64": base64(&p12),
        "p12_sha256": hex(&Sha256::digest(&p12)),
    });
    let bundle_path = root.join("pairing.abconnection");
    std::fs::write(&bundle_path, serde_json::to_vec(&bundle)?)?;
    std::fs::set_permissions(&bundle_path, std::fs::Permissions::from_mode(0o600))?;
    println!("{}",serde_json::json!({"fixture":root,"endpoint":format!("wss://{address}"),"bundle":bundle_path,"deviceId":device_id,"session":resized.session_id,"initialUrl":initial_url}));
    let mut input = BufReader::new(tokio::io::stdin()).lines();
    while let Some(command) = input.next_line().await? {
        if command == "quit" { break; }
        if command == "status" {
            let current = status(call(&mut local,&mut id,Command::Status {},None).await);
            println!("{}",serde_json::to_string(&current)?);
        } else if command == "inspect" {
            let resumed = resume_agent(&mut local,&mut id).await;
            eprintln!("inspect resume: {resumed}");
            let current = status(call(&mut local,&mut id,Command::Status {},None).await);
            let result = probe(&mut local,&mut id,Command::Evaluate { expression:"JSON.stringify({clicked:window.clicked||0,text:document.getElementById('field').value,scrollY:window.scrollY,maxScroll:window.maxScroll,editorEvents:window.__obscuraProbe||[]})".into() },Some(identity(&current))).await;
            println!("{}",evidence_line(&result));
        } else { eprintln!("Expected status, inspect or quit"); }
    }
    let resumed = resume_agent(&mut local, &mut id).await;
    eprintln!("teardown resume: {resumed}");
    let observed = status(call(&mut local, &mut id, Command::Status {}, None).await);
    // Name the owner of the first failing gate. The Host status carries the live
    // control phase and input lease, and the Engine reports the element that
    // actually received the last pointer press plus its own focus target.
    eprintln!("teardown status: {observed:?}");
    let engine = probe(&mut local, &mut id, Command::Evaluate { expression:
        "JSON.stringify({active:document.activeElement?(document.activeElement.id||document.activeElement.tagName):null,\
focused:globalThis.__obscura_focused?(globalThis.__obscura_focused.id||globalThis.__obscura_focused.tagName):null,\
clickTarget:globalThis.__obscura_click_target?(globalThis.__obscura_click_target.id||globalThis.__obscura_click_target.tagName):null,\
hit100x125:(document.elementFromPoint(100,125)||{}).id||null,hit30x30:(document.elementFromPoint(30,30)||{}).id||null,\
field:(document.getElementById('field')||{}).value||null,clicked:window.clicked||0,scrollY:window.scrollY,\
viewport:[window.innerWidth,window.innerHeight]})".into() }, Some(identity(&observed))).await;
    eprintln!("teardown engine: {engine}");
    // The diagnostic Evaluate consumes the next sequence, so the close reads the
    // Host's current identity instead of reusing the observed one.
    let current = status(call(&mut local, &mut id, Command::Status {}, None).await);
    let close = probe(&mut local, &mut id, Command::CloseSession {}, Some(identity(&current))).await;
    eprintln!("teardown close: {close}");
    // The endpoint exits only on SIGINT/SIGTERM. Teardown signals its exact pid
    // and waits, so its owner finalizes session media before the Host is reaped.
    let endpoint_pid = endpoint.id().expect("endpoint pid");
    std::process::Command::new("kill").arg("-TERM").arg(endpoint_pid.to_string()).status()?;
    let endpoint_status = tokio::time::timeout(Duration::from_secs(10), endpoint.wait()).await??;
    if !endpoint_status.success() {
        return Err(format!("Endpoint exited unsuccessfully after SIGTERM: {endpoint_status}").into());
    }
    host.kill().await?; host.wait().await?;
    std::fs::remove_dir_all(root)?;
    Ok(())
}

// --- Frozen agentbrowser.connection@1 PKCS#12 owner ---

/// Build the no-code PKCS#12 of the frozen bundle: plain `keyBag` plus
/// `certBag` entries and an empty-password SHA-256 MAC. It is the exact
/// container shape the Android setup owner opens with `new char[0]`.
fn pkcs12(ca_der: &[u8], endpoint_der: &[u8], client_der: &[u8], client_key_pkcs8: &[u8], key_id: &[u8]) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let attributes = |friendly: &str| set_of(&[
        seq(&[oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x09,0x15]), set_of(&[octet(key_id)])]),
        seq(&[oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x09,0x14]), set_of(&[bmp(friendly)])]),
    ]);
    let cert_bag = |certificate: &[u8], friendly: &str| seq(&[
        oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x0c,0x0a,0x01,0x03]),
        explicit(0, &seq(&[
            oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x09,0x16,0x01]),
            explicit(0, &octet(certificate)),
        ])),
        attributes(friendly),
    ]);
    let key_bag = seq(&[
        oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x0c,0x0a,0x01,0x01]),
        explicit(0, client_key_pkcs8),
        attributes(CLIENT_ALIAS),
    ]);
    let safe_contents = seq(&[
        cert_bag(client_der, CLIENT_ALIAS),
        cert_bag(endpoint_der, ENDPOINT_ALIAS),
        key_bag,
        cert_bag(ca_der, CA_ALIAS),
    ]);
    let authenticated_safe = seq(&[data_content_info(&safe_contents)]);
    let salt = uuid::Uuid::new_v4().as_bytes()[..8].to_vec();
    // The empty password of the no-code import is one empty BMPString unit.
    let mac = hmac_sha256(&pkcs12_kdf(&[0, 0], &salt, 3, 2048, 32), &authenticated_safe);
    let digest_info = seq(&[
        seq(&[oid(&[0x60,0x86,0x48,0x01,0x65,0x03,0x04,0x02,0x01]), null()]),
        octet(&mac),
    ]);
    let mac_data = seq(&[digest_info, octet(&salt), integer(2048)]);
    Ok(seq(&[integer(3), data_content_info(&authenticated_safe), mac_data]))
}

/// ContentInfo for pkcs7-data: the one carrier of every plain PKCS#12 payload.
fn data_content_info(content: &[u8]) -> Vec<u8> {
    seq(&[oid(&[0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x07,0x01]), explicit(0, &octet(content))])
}

/// RFC 7292 appendix B key derivation. SHA-256 fixes u = 32 and v = 64.
fn pkcs12_kdf(password: &[u8], salt: &[u8], id: u8, iterations: u32, length: usize) -> Vec<u8> {
    const U: usize = 32;
    const V: usize = 64;
    let expand = |source: &[u8], target: usize| -> Vec<u8> {
        if source.is_empty() || target == 0 { return Vec::new(); }
        source.repeat(target / source.len() + 1)[..target].to_vec()
    };
    let mut blocks = expand(salt, salt.len().div_ceil(V) * V);
    blocks.extend_from_slice(&expand(password, password.len().div_ceil(V) * V));
    let diversifier = vec![id; V];
    let mut out: Vec<u8> = Vec::with_capacity(length);
    while out.len() < length {
        let mut digest = Sha256::digest([diversifier.as_slice(), blocks.as_slice()].concat()).to_vec();
        for _ in 1..iterations { digest = Sha256::digest(&digest).to_vec(); }
        out.extend_from_slice(&digest[..std::cmp::min(U, length - out.len())]);
        if out.len() >= length { break; }
        let mut carry_block = expand(&digest, V);
        for index in (0..V).rev() {
            carry_block[index] = carry_block[index].wrapping_add(1);
            if carry_block[index] != 0 { break; }
        }
        for block in blocks.chunks_mut(V) {
            let mut carry = 0u16;
            for index in (0..V).rev() {
                let sum = block[index] as u16 + carry_block[index] as u16 + carry;
                block[index] = (sum & 0xff) as u8;
                carry = sum >> 8;
            }
        }
    }
    out
}

fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    let mut normalized = if key.len() > 64 { Sha256::digest(key).to_vec() } else { key.to_vec() };
    normalized.resize(64, 0);
    let mut inner: Vec<u8> = normalized.iter().map(|byte| byte ^ 0x36).collect();
    inner.extend_from_slice(data);
    let inner_digest = Sha256::digest(&inner);
    let mut outer: Vec<u8> = normalized.iter().map(|byte| byte ^ 0x5c).collect();
    outer.extend_from_slice(&inner_digest);
    Sha256::digest(&outer).to_vec()
}

// --- Minimal DER writer ---

fn der(tag: u8, content: &[u8]) -> Vec<u8> {
    let mut out = vec![tag];
    let length = content.len();
    if length < 0x80 {
        out.push(length as u8);
    } else {
        let mut digits = Vec::new();
        let mut value = length;
        while value > 0 { digits.push((value & 0xff) as u8); value >>= 8; }
        digits.reverse();
        out.push(0x80 | digits.len() as u8);
        out.extend_from_slice(&digits);
    }
    out.extend_from_slice(content);
    out
}

fn concat(parts: &[Vec<u8>]) -> Vec<u8> { let mut out = Vec::new(); for part in parts { out.extend_from_slice(part); } out }
fn seq(parts: &[Vec<u8>]) -> Vec<u8> { der(0x30, &concat(parts)) }
fn set_of(parts: &[Vec<u8>]) -> Vec<u8> { der(0x31, &concat(parts)) }
fn octet(bytes: &[u8]) -> Vec<u8> { der(0x04, bytes) }
fn oid(content: &[u8]) -> Vec<u8> { der(0x06, content) }
fn explicit(tag: u8, inner: &[u8]) -> Vec<u8> { der(0xa0 | tag, inner) }
fn null() -> Vec<u8> { der(0x05, &[]) }
fn bmp(text: &str) -> Vec<u8> { der(0x1e, &text.encode_utf16().flat_map(u16::to_be_bytes).collect::<Vec<u8>>()) }
fn integer(value: u32) -> Vec<u8> {
    let mut digits = Vec::new();
    let mut rest = value;
    loop { digits.push((rest & 0xff) as u8); rest >>= 8; if rest == 0 { break; } }
    digits.reverse();
    if digits[0] & 0x80 != 0 { digits.insert(0, 0); }
    der(0x02, &digits)
}

fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let triple = ((chunk[0] as u32) << 16)
            | ((chunk.get(1).copied().unwrap_or(0) as u32) << 8)
            | (chunk.get(2).copied().unwrap_or(0) as u32);
        out.push(ALPHABET[((triple >> 18) & 0x3f) as usize] as char);
        out.push(ALPHABET[((triple >> 12) & 0x3f) as usize] as char);
        out.push(if chunk.len() > 1 { ALPHABET[((triple >> 6) & 0x3f) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { ALPHABET[(triple & 0x3f) as usize] as char } else { '=' });
    }
    out
}

fn status(value:ResultValue)->SessionStatus { match value {ResultValue::Status(status)=>status,_=>panic!("Expected status")} }
fn identity(state:&SessionStatus)->Operation {
    Operation {operation_id:String::new(),session_id:state.session_id.clone(),attachment_id:state.attachment_id.unwrap(),sequence:state.next_sequence,
        control_epoch:state.control.epoch,viewport_revision:state.viewport_revision,document_revision:state.document_revision}
}
async fn call(local:&mut BufReader<tokio::net::UnixStream>,id:&mut u64,command:Command,operation:Option<Operation>)->ResultValue {
    *id+=1; let mut bytes=serde_json::to_vec(&Request{id:*id,command,operation}).unwrap(); bytes.push(b'\n');
    local.get_mut().write_all(&bytes).await.unwrap();let mut line=String::new();local.read_line(&mut line).await.unwrap();
    match serde_json::from_str::<Response>(&line).unwrap(){Response::Result{id:reply,value} if reply==*id=>value,other=>panic!("{other:?}")}
}
/// Diagnostic call that reports the Host response instead of panicking, so the
/// teardown keeps its evidence when a live lease or a suspended control phase
/// still fences the session.
async fn probe(local:&mut BufReader<tokio::net::UnixStream>,id:&mut u64,command:Command,operation:Option<Operation>)->String {
    *id+=1; let mut bytes=serde_json::to_vec(&Request{id:*id,command,operation}).unwrap(); bytes.push(b'\n');
    if local.get_mut().write_all(&bytes).await.is_err() { return "write failed".into(); }
    let mut line=String::new();
    if local.read_line(&mut line).await.is_err() { return "read failed".into(); }
    line.trim().to_string()
}
/// An acceptance run can end in observe, which suspends Agent control. The Host
/// exposes the explicit Agent resume for exactly that state, so the fixture
/// resumes before it reads the page instead of failing the read.
async fn resume_agent(local:&mut BufReader<tokio::net::UnixStream>,id:&mut u64)->String {
    let current = status(call(local,id,Command::Status {},None).await);
    if !matches!(current.control.phase,ControlPhase::Paused) {
        return format!("not paused: {:?}",current.control.phase);
    }
    probe(local,id,Command::ResumeAgent { epoch: current.control.epoch },None).await
}
/// The replay reads the untagged `ResultValue` from a successful response. An
/// explicit rejection becomes an error object so the end state stays observable
/// instead of panicking the fixture.
fn evidence_line(raw:&str)->String {
    match serde_json::from_str::<serde_json::Value>(raw) {
        Ok(value) if value.get("type").and_then(|kind| kind.as_str())==Some("result") =>
            value.get("value").cloned().unwrap_or(serde_json::Value::Null).to_string(),
        _ => serde_json::json!({"error":raw}).to_string(),
    }
}
async fn wait(path:PathBuf){tokio::time::timeout(Duration::from_secs(10),async{while !path.exists(){tokio::time::sleep(Duration::from_millis(20)).await;}tokio::time::sleep(Duration::from_millis(100)).await;}).await.unwrap();}
fn hex(bytes:&[u8])->String{const DIGITS:&[u8;16]=b"0123456789abcdef";let mut out=String::with_capacity(bytes.len()*2);for byte in bytes{out.push(DIGITS[(byte>>4)as usize]as char);out.push(DIGITS[(byte&0x0f)as usize]as char);}out}
