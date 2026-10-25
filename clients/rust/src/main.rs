use std::env;
use serde_json::{json,Value};
fn main() -> Result<(),Box<dyn std::error::Error>> {
 let args:Vec<String>=env::args().collect();
 if args.len()!=5 {return Err("usage client version endpoint toolchain".into());}
 if args[2]!="0.12.28" {return Err("unsupported reqwest version".into());}
 let native=args[1]=="reqwest-native";
 let builder=reqwest::blocking::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(25));
 #[cfg(feature="rustls")]
 let builder=builder.use_rustls_tls();
 #[cfg(feature="native")]
 let builder=builder.use_native_tls();
 let c=builder.build()?;
 let body=c.get(&args[3]).header("Accept","application/json").header("Connection","close").send()?.error_for_status()?.text()?;
 let echo:Value=serde_json::from_str(&body)?;
 let actual=std::process::Command::new("rustup").args(["run",&args[4],"rustc","--version"]).output()?;
 let compiler=String::from_utf8_lossy(&actual.stdout);
 let version=compiler.split_whitespace().nth(1).ok_or("unknown rust version")?;
 let tlsversion=if native {
  let out=std::process::Command::new("openssl").arg("version").output()?;
  String::from_utf8_lossy(&out.stdout).trim().to_string()
 }else{
  // Cargo.lock is generated during the build; record the actual rustls version.
  let lock=std::fs::read_to_string("clients/rust/Cargo.lock").unwrap_or_default();
  let mut current=false;let mut found=String::from("unknown");
  for line in lock.lines() {
   if line=="[[package]]" {current=false;}
   if line.trim()=="name = \"rustls\"" {current=true;}
   else if current && line.trim().starts_with("version = ") {
    found=line.split('\"').nth(1).unwrap_or("unknown").to_string();break;
   }
  }
  found
 };
 let backend=if native{"OpenSSL"}else{"rustls"};
 println!("{}",json!({"runtime_version":version,"client_version":args[2],
 "environment":{"os":env::consts::OS,"arch":env::consts::ARCH,
 "tls_backend":{"name":backend,"version":tlsversion},"dependencies":{"reqwest":args[2]}},"echo":echo}));
 Ok(())
}
