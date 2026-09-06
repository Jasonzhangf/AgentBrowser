use std::{collections::HashMap, env, fs, path::PathBuf};

use agentbrowser_connection::relay::{
    DeviceIdentity, RelayNetwork, RelayPeerBinding, RelayTlsServerIdentity,
};
use agentbrowser_relay_host::{run, RelayHostSettings};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async_main())
}

async fn async_main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args(env::args().skip(1))?;
    let seed = parse_fixed_hex::<32>(&required(&args, "device-seed")?, "device-seed")?;
    let peer_auth_public_key = parse_fixed_hex::<32>(
        &required(&args, "peer-auth-public-key")?,
        "peer-auth-public-key",
    )?;
    let peer_certificate_sha256 =
        parse_fixed_hex::<32>(&required(&args, "peer-cert-sha256")?, "peer-cert-sha256")?;
    let network = parse_network(&required(&args, "network")?)?;
    let read = |name: &str| -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        Ok(fs::read(PathBuf::from(required(&args, name)?))?)
    };
    run(RelayHostSettings {
        relay_origin: required(&args, "relay")?,
        relay_ca_der: read("relay-ca")?,
        relay_username: required(&args, "username")?,
        relay_password: required(&args, "password")?,
        device_name: required(&args, "device-name")?,
        endpoint_url: required(&args, "endpoint")?,
        endpoint_network: network,
        endpoint_ca_der: read("endpoint-ca")?,
        endpoint_client_cert_der: read("endpoint-client-cert")?,
        endpoint_client_key_pkcs8_der: read("endpoint-client-key")?,
        inner_server: RelayTlsServerIdentity::new(
            read("inner-server-cert")?,
            read("inner-server-key")?,
            read("inner-client-ca")?,
        ),
        peer: RelayPeerBinding::new(
            required(&args, "peer-device-id")?,
            peer_auth_public_key,
            peer_certificate_sha256,
        ),
        device_identity: DeviceIdentity::from_seed(seed),
    })
    .await?;
    Ok(())
}

fn parse_args<I>(values: I) -> Result<HashMap<String, String>, Box<dyn std::error::Error>>
where
    I: IntoIterator<Item = String>,
{
    let mut args = HashMap::new();
    let mut values = values.into_iter();
    while let Some(value) = values.next() {
        if value == "--help" {
            print_usage();
            std::process::exit(0);
        }
        let name = value
            .strip_prefix("--")
            .ok_or_else(|| format!("expected --option, got {value}"))?;
        if name.is_empty() || args.contains_key(name) {
            return Err(format!("duplicate or empty option: {value}").into());
        }
        let value = values
            .next()
            .ok_or_else(|| format!("missing value for --{name}"))?;
        if value.starts_with("--") {
            return Err(format!("missing value for --{name}").into());
        }
        args.insert(name.to_owned(), value);
    }
    Ok(args)
}

fn required(
    args: &HashMap<String, String>,
    name: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    args.get(name)
        .cloned()
        .ok_or_else(|| format!("missing required --{name}").into())
}

fn parse_fixed_hex<const N: usize>(
    value: &str,
    name: &str,
) -> Result<[u8; N], Box<dyn std::error::Error>> {
    if value.len() != N * 2 {
        return Err(format!("{name} must contain exactly {} hex characters", N * 2).into());
    }
    let mut output = [0u8; N];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let high = hex_digit(pair[0]).ok_or_else(|| format!("invalid {name}"))?;
        let low = hex_digit(pair[1]).ok_or_else(|| format!("invalid {name}"))?;
        output[index] = (high << 4) | low;
    }
    Ok(output)
}

fn hex_digit(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn parse_network(value: &str) -> Result<RelayNetwork, Box<dyn std::error::Error>> {
    match value {
        "lan" => Ok(RelayNetwork::Lan),
        "public" => Ok(RelayNetwork::Public),
        "tailscale" => Ok(RelayNetwork::Tailscale),
        _ => Err(format!("invalid --network: {value}").into()),
    }
}

fn print_usage() {
    eprintln!(
        "agentbrowser-relay-host --relay ORIGIN --relay-ca DER --username USER --password PASS \
--device-name NAME --device-seed HEX64 --endpoint WSS_ORIGIN --network lan|public|tailscale \
--endpoint-ca DER --endpoint-client-cert DER --endpoint-client-key PKCS8_DER \
--inner-server-cert DER --inner-server-key PKCS8_DER --inner-client-ca DER \
--peer-device-id ID --peer-auth-public-key HEX64 --peer-cert-sha256 HEX64"
    );
}
