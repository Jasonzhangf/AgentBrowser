//! Owned real Host endpoint for Android acceptance. Credentials are ephemeral,
//! output goes to the private fixture directory, and no system trust changes.
use std::{path::PathBuf, process::Stdio, time::Duration, os::unix::fs::{DirBuilderExt, PermissionsExt}};
use agentbrowser_connection::protocol::{Command, Mode, Operation, Request, Response, ResultValue, SessionStatus};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bin = PathBuf::from(std::env::var("OBSCURA_BIN_DIR")?);
    let bind: std::net::IpAddr = std::env::var("OBSCURA_ENDPOINT_BIND_IP")?.parse()?;
    let root = PathBuf::from(format!("/tmp/an-{}", uuid::Uuid::new_v4().simple()));
    std::fs::DirBuilder::new().mode(0o700).create(&root)?;
    let ca_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![])?;
    params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    params.distinguished_name.push(rcgen::DnType::CommonName, "Android network acceptance CA");
    let ca = params.self_signed(&ca_key)?;
    let server_key = rcgen::KeyPair::generate()?;
    let server = rcgen::CertificateParams::new(vec![bind.to_string()])?.signed_by(&server_key, &ca, &ca_key)?;
    let client_key = rcgen::KeyPair::generate()?;
    let mut params = rcgen::CertificateParams::new(vec![])?;
    params.extended_key_usages = vec![rcgen::ExtendedKeyUsagePurpose::ClientAuth];
    let client = params.signed_by(&client_key, &ca, &ca_key)?;
    std::fs::write(root.join("server.der"), server.der())?;
    std::fs::write(root.join("server-key.der"), server_key.serialize_der())?;
    std::fs::set_permissions(root.join("server-key.der"), std::fs::Permissions::from_mode(0o600))?;
    std::fs::write(root.join("ca.der"), ca.der())?;
    std::fs::write(root.join("client.der"), client.der())?;
    std::fs::write(root.join("key.der"), client_key.serialize_der())?;
    std::fs::set_permissions(root.join("key.der"), std::fs::Permissions::from_mode(0o600))?;
    let mut host = tokio::process::Command::new(bin.join("obscura-host")).arg("--socket-dir").arg(root.join("host"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn()?;
    wait(root.join("host/host.sock")).await;
    let mut local = BufReader::new(tokio::net::UnixStream::connect(root.join("host/host.sock")).await?);
    let mut line = String::new(); local.read_line(&mut line).await?;
    let mut id = 0;
    let attached = status(call(&mut local, &mut id, Command::Attach { mode: Mode::Agent, viewport: None }, None).await);
    let navigated = status(call(&mut local, &mut id, Command::Navigate { url:
        "data:text/html,<body style='margin:0;background:white'><button id='target' style='width:180px;height:100px;background:red' onclick=\"window.clicked=(window.clicked||0)+1;this.style.background='lime'\">touch</button><input id='field' style='display:block;width:200px;height:50px' placeholder='type here'><p>AgentBrowser live Host</p>".into()
    }, Some(identity(&attached))).await);
    let resized = status(call(&mut local, &mut id, Command::Resize { width:391,height:845 }, Some(identity(&navigated))).await);
    let reservation = std::net::TcpListener::bind((bind,0))?;
    let address = reservation.local_addr()?; drop(reservation);
    std::fs::write(root.join("endpoint.txt"), format!("wss://{address}"))?;
    let mut endpoint = tokio::process::Command::new(bin.join("obscura-endpoint"))
        .arg("--listen").arg(address.to_string()).arg("--host-dir").arg(root.join("host"))
        .arg("--socket-dir").arg(root.join("endpoint"))
        .arg("--server-cert").arg(root.join("server.der")).arg("--server-key").arg(root.join("server-key.der"))
        .arg("--client-ca").arg(root.join("ca.der")).arg("--media-bin").arg(bin.join("obscura-media"))
        .stdout(Stdio::null()).stderr(Stdio::inherit()).kill_on_drop(true).spawn()?;
    wait(root.join("endpoint/encoded.sock")).await;
    println!("{}",serde_json::json!({"fixture":root,"endpoint":format!("wss://{address}"),"session":resized.session_id}));
    let mut input = BufReader::new(tokio::io::stdin()).lines();
    while let Some(command) = input.next_line().await? {
        if command == "quit" { break; }
        if command == "inspect" {
            let current = status(call(&mut local,&mut id,Command::Status {},None).await);
            let result = call(&mut local,&mut id,Command::Evaluate { expression:"JSON.stringify({clicked:window.clicked||0,text:document.getElementById('field').value})".into() },Some(identity(&current))).await;
            println!("{}",serde_json::to_string(&result)?);
        } else { eprintln!("Expected inspect or quit"); }
    }
    endpoint.kill().await?; endpoint.wait().await?;
    host.kill().await?; host.wait().await?;
    std::fs::remove_dir_all(root)?;
    Ok(())
}
fn status(value:ResultValue)->SessionStatus { match value {ResultValue::Status(status)=>status,_=>panic!("Expected status")} }
fn identity(state:&SessionStatus)->Operation {
    Operation {session_id:state.session_id.clone(),attachment_id:state.attachment_id.unwrap(),sequence:state.next_sequence,
        control_epoch:state.control.epoch,viewport_revision:state.viewport_revision,document_revision:state.document_revision}
}
async fn call(local:&mut BufReader<tokio::net::UnixStream>,id:&mut u64,command:Command,operation:Option<Operation>)->ResultValue {
    *id+=1; let mut bytes=serde_json::to_vec(&Request{id:*id,command,operation}).unwrap(); bytes.push(b'\n');
    local.get_mut().write_all(&bytes).await.unwrap();let mut line=String::new();local.read_line(&mut line).await.unwrap();
    match serde_json::from_str::<Response>(&line).unwrap(){Response::Result{id:reply,value} if reply==*id=>value,other=>panic!("{other:?}")}
}
async fn wait(path:PathBuf){tokio::time::timeout(Duration::from_secs(10),async{while !path.exists(){tokio::time::sleep(Duration::from_millis(20)).await;}tokio::time::sleep(Duration::from_millis(100)).await;}).await.unwrap();}
