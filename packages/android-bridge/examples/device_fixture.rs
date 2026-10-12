//! Owned real Host endpoint for Android acceptance. The connection path carries
//! exactly one credential: the access token the Host compares. Credentials are
//! ephemeral, output goes to the private fixture directory, and no system trust
//! changes. The fixture also writes the frozen `agentbrowser.connection@1`
//! bundle so the installed app imports the fixture pairing through its real
//! setup owner.
use std::{path::PathBuf, process::Stdio, time::Duration, os::unix::fs::{DirBuilderExt, PermissionsExt}};
use agentbrowser_connection::protocol::{Command, ControlPhase, Mode, Operation, Request, Response, ResultValue, SessionStatus};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bin = PathBuf::from(std::env::var("OBSCURA_BIN_DIR")?);
    let bind: std::net::IpAddr = std::env::var("OBSCURA_ENDPOINT_BIND_IP")?.parse()?;
    let enable_webrtc = std::env::var("OBSCURA_ENABLE_WEBRTC").as_deref() == Ok("1");
    let root = PathBuf::from(format!("/tmp/an-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&root)?;
    // The one credential on the connection path. The Host compares this value
    // against the `Authorization: Bearer` the endpoint forwards; nothing else
    // authorizes a remote session.
    let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
    // The listener's own TLS identity. It is generated here, stays inside the
    // private fixture root, and is never part of the user-facing bundle.
    let server_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![bind.to_string()])?;
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ServerAuth];
    params.distinguished_name.push(rcgen::DnType::CommonName, "Android network acceptance endpoint");
    let server = params.self_signed(&server_key)?;
    std::fs::write(root.join("server.der"), server.der())?;
    std::fs::write(root.join("server-key.der"), server_key.serialize_der())?;
    std::fs::set_permissions(root.join("server-key.der"), std::fs::Permissions::from_mode(0o600))?;
    // The Host owns the profile catalog: an absent `catalog.json` is created as
    // the single default profile, so the fixture grants nothing by hand.
    let profile_root = root.join("profiles");
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&profile_root)?;
    let mut host = tokio::process::Command::new(bin.join("obscura-host")).arg("--socket-dir").arg(root.join("host"))
        .arg("--profile-root").arg(&profile_root)
        .arg("--access-token").arg(&token)
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn()?;
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(tokio::net::UnixStream::connect(root.join("host/host.sock")).await?);
    let mut line = String::new(); local.read_line(&mut line).await?;
    let mut id = 0;
    let attached = status(call(&mut local, &mut id, Command::Attach { mode: Mode::Agent, viewport: None }, None).await);
    let initial_url: String =
        "data:text/html,<body style='margin:0;background:white'><button id='target' style='width:180px;height:100px;background:red' onclick=\"window.clicked=(window.clicked||0)+1;this.style.background='lime'\">touch</button><input id='field' style='display:block;width:200px;height:50px' placeholder='type here'><p>AgentBrowser live Host</p><div style='height:3000px;background:blue'></div><script>window.maxScroll=0;window.__scrollTrace=[0];window.__obscuraProbe=[];['compositionstart','compositionupdate','compositionend','beforeinput','input'].forEach(t=>document.addEventListener(t,e=>window.__obscuraProbe.push({type:t,value:document.getElementById('field').value,inputType:e.inputType||null,data:e.data||null})));window.addEventListener('scroll',()=>{window.maxScroll=Math.max(window.maxScroll,window.scrollY);window.__scrollTrace.push(window.scrollY)});</script>".into()
    ;
    let navigated = status(call(&mut local, &mut id, Command::Navigate { url: initial_url.clone() }, Some(identity(&attached))).await);
    let resized = status(call(&mut local, &mut id, Command::Resize { width:391,height:845 }, Some(identity(&navigated))).await);
    let reservation = std::net::TcpListener::bind((bind,0))?;
    let address = reservation.local_addr()?; drop(reservation);
    // The frozen pairing directory: the endpoint as `<host>:<port>` and the
    // token, each in its own private file.
    std::fs::write(root.join("endpoint.txt"), address.to_string())?;
    std::fs::write(root.join("token"), &token)?;
    std::fs::set_permissions(root.join("token"), std::fs::Permissions::from_mode(0o600))?;
    let mut endpoint_command = tokio::process::Command::new(bin.join("obscura-endpoint"));
    endpoint_command
        .arg("--listen").arg(address.to_string()).arg("--host-dir").arg(root.join("host"))
        .arg("--socket-dir").arg(root.join("endpoint"))
        .arg("--server-cert").arg(root.join("server.der")).arg("--server-key").arg(root.join("server-key.der"))
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
    // The frozen agentbrowser.connection@1 bundle. Four fields are the whole
    // contract: the format, its version, the endpoint and the access token.
    let bundle = serde_json::json!({
        "format": "agentbrowser.connection",
        "version": 1,
        "endpoint": address.to_string(),
        "token": token,
    });
    let bundle_path = root.join("pairing.abconnection");
    std::fs::write(&bundle_path, serde_json::to_vec(&bundle)?)?;
    std::fs::set_permissions(&bundle_path, std::fs::Permissions::from_mode(0o600))?;
    println!("{}",serde_json::json!({"fixture":root,"endpoint":address.to_string(),"bundle":bundle_path,"session":resized.session_id,"initialUrl":initial_url}));
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
            let result = probe(&mut local,&mut id,Command::Evaluate { expression:"JSON.stringify({clicked:window.clicked||0,text:document.getElementById('field').value,scrollY:window.scrollY,maxScroll:window.maxScroll,scrollTrace:window.__scrollTrace||[],editorEvents:window.__obscuraProbe||[]})".into() },Some(identity(&current))).await;
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
