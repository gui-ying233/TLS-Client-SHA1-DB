use std::env;
use serde_json::{json,Value};
fn locked_version(name: &str) -> String {
 let mut current=false;
 for line in include_str!("../Cargo.lock").lines() {
  if line=="[[package]]" {current=false;}
  if line.trim()==format!("name = \"{}\"",name) {current=true;}
  else if current && line.trim().starts_with("version = ") {
   return line.split('"').nth(1).unwrap_or("unknown").to_string();
  }
 }
 String::from("unknown")
}
fn main() -> Result<(),Box<dyn std::error::Error>> {
 let args:Vec<String>=env::args().collect();
 if args.len()!=5 {return Err("usage client version endpoint toolchain".into());}
 let actual_client=locked_version("reqwest");
 if args[2]!=actual_client {return Err("reqwest version mismatch".into());}
 let native=args[1]=="reqwest-native";
 let builder=reqwest::blocking::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(25));
 #[cfg(feature="rustls")]
 let builder=builder.use_rustls_tls();
 #[cfg(feature="native")]
 let builder=builder.use_native_tls();
 // Bundled rustls roots do not include the per-run test certificate.
 let builder=if let Ok(path)=env::var("FPDB_LOCAL_CA") {
  builder.add_root_certificate(reqwest::Certificate::from_pem(&std::fs::read(path)?)?)
 }else{builder};
 let c=builder.build()?;
 let body=c.get(&args[3]).header("Accept","application/json").header("Connection","close").send()?.error_for_status()?.text()?;
 let echo:Value=serde_json::from_str(&body)?;
 let version=env!("FPDB_RUSTC_VERSION");
 let tlsversion=if native {
  let out=std::process::Command::new("openssl").arg("version").output()?;
  String::from_utf8_lossy(&out.stdout).trim().to_string()
 }else{
  locked_version("rustls")
 };
 let backend=if native{"OpenSSL"}else{"rustls"};
 println!("{}",json!({"runtime_version":version,"client_version":actual_client,
 "environment":{"os":env::consts::OS,"arch":env::consts::ARCH,
 "tls_backend":{"name":backend,"version":tlsversion},"dependencies":{"reqwest":args[2]}},"echo":echo}));
 Ok(())
}
