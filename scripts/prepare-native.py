"""Build a requested native client version in its own case directory."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
GO_MODULES = {
    "resty": "github.com/go-resty/resty/v2",
    "colly": "github.com/gocolly/colly/v2",
    "fasthttp": "github.com/valyala/fasthttp",
    "req": "github.com/imroc/req/v3",
    "retryablehttp": "github.com/hashicorp/go-retryablehttp",
    "gorequest": "github.com/parnurzeal/gorequest",
    "requests": "github.com/carlmjohnson/requests",
}


def run(argv: list[str], directory: Path, env: dict[str, str] | None = None) -> None:
    process = subprocess.run(argv, cwd=directory, env=env, capture_output=True, text=True, check=False)
    if process.returncode:
        raise RuntimeError("Native build failed: " + " ".join(argv) + "\n" +
                           (process.stdout + process.stderr)[-6000:])


def prepare_go(client: str, version: str, runtime: str, directory: Path) -> None:
    module = GO_MODULES[client]
    shutil.copyfile(ROOT / "clients/go" / client / "main.go", directory / "main.go")
    # The module's Go directive also controls compatibility defaults at runtime.
    # Retain the original directive; changing it is separate from selecting a compiler.
    manifest = (ROOT / "clients/go" / client / "go.mod").read_text(encoding="utf-8")
    manifest, replacements = re.subn(r"(?m)^(require\s+" + re.escape(module) + r"\s+)v\S+",
                                     r"\g<1>v" + version, manifest)
    if replacements != 1:
        raise ValueError("Expected one direct client requirement in go.mod")
    (directory / "go.mod").write_text(manifest, encoding="utf-8")
    env = {**os.environ, "GOTOOLCHAIN": "local"}
    run(["go", "mod", "tidy"], directory, env)
    run(["go", "build", "-o", str(directory / "fpdb-probe"), "."], directory, env)


def prepare_java(version: str, directory: Path) -> None:
    artifact = "okhttp-jvm" if int(version.split(".")[0]) >= 5 else "okhttp"
    (directory / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        '<modelVersion>4.0.0</modelVersion><groupId>fpdb</groupId>'
        '<artifactId>okhttp-probe</artifactId><version>1</version>'
        '<dependencies><dependency><groupId>com.squareup.okhttp3</groupId>'
        f'<artifactId>{artifact}</artifactId><version>{escape(version)}</version>'
        '</dependency></dependencies></project>\n', encoding="utf-8")
    run(["mvn", "-B", "-q", "-f", str(directory / "pom.xml"),
         "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:build-classpath",
         "-Dmdep.outputFile=" + str(directory / "classpath.txt")], directory)
    classpath = (directory / "classpath.txt").read_text(encoding="utf-8").strip()
    run(["javac", "-cp", classpath, "-d", str(directory),
         str(ROOT / "clients/java/okhttp/ProbeOkHttp.java")], directory)


def prepare_php(version: str, directory: Path) -> None:
    manifest = {
        "require": {"php": "^8.1", "guzzlehttp/guzzle": version},
        "config": {"allow-plugins": False, "policy": {"advisories": {"block": False}}},
    }
    (directory / "composer.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    run(["composer", "install", "--working-dir=" + str(directory), "--no-interaction",
         "--no-progress", "--prefer-dist", "--no-scripts", "--no-plugins"], directory)


def prepare_dotnet(version: str, runtime: str, directory: Path) -> None:
    major = int(version.split(".")[0])
    defines = "<DefineConstants>RESTSHARP_LEGACY</DefineConstants>" if major < 107 else ""
    (directory / "FpdbRestSharp.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        f'<TargetFramework>net{runtime}</TargetFramework>'
        '<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable>'
        f'{defines}</PropertyGroup><ItemGroup>'
        f'<PackageReference Include="RestSharp" Version="[{escape(version)}]" />'
        '</ItemGroup></Project>\n', encoding="utf-8")
    shutil.copyfile(ROOT / "clients/dotnet-restsharp/Program.cs", directory / "Program.cs")
    run(["dotnet", "build", "FpdbRestSharp.csproj", "-c", "Release", "--nologo",
         "-o", str(directory / "bin")], directory)
    assets = json.loads((directory / "obj/project.assets.json").read_text(encoding="utf-8"))
    actual = [key.split("/", 1)[1] for key in assets["libraries"] if key.lower().startswith("restsharp/")]
    if actual != [version]:
        raise ValueError(f"Resolved RestSharp versions {actual} != {version}")
    (directory / "bin/client-version.txt").write_text(actual[0], encoding="utf-8")


def prepare_rust(client: str, version: str, runtime: str, directory: Path) -> None:
    run(["rustup", "toolchain", "install", runtime, "--profile", "minimal"], directory)
    reqwest_feature = "rustls" if tuple(map(int, version.split(".")[:2])) >= (0, 13) else "rustls-tls"
    (directory / "Cargo.toml").write_text(
        '[package]\nname="fpdb-probe"\nversion="0.1.0"\nedition="2021"\n'
        f'[features]\nrustls=["reqwest/{reqwest_feature}"]\nnative=["reqwest/native-tls"]\n'
        '[dependencies]\n'
        f'reqwest={{version="={version}",default-features=false,features=["blocking"]}}\n'
        'serde_json="=1.0.145"\n', encoding="utf-8")
    (directory / "src").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "clients/rust/src/main.rs", directory / "src/main.rs")
    compiler = subprocess.check_output(["rustup", "run", runtime, "rustc", "--version"], text=True).split()[1]
    env = {**os.environ, "FPDB_RUSTC_VERSION": compiler,
           "CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS": "fallback"}
    feature = "native" if client == "reqwest-native" else "rustls"
    run(["cargo", "+" + runtime, "build", "--release", "--no-default-features",
         "--features", feature], directory, env)
    target = Path(env.get("CARGO_TARGET_DIR", str(directory / "target")))
    shutil.copyfile(target / "release/fpdb-probe", directory / "fpdb-probe")
    (directory / "fpdb-probe").chmod(0o755)


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: prepare-native.py TARGET CLIENT_VERSION RUNTIME_VERSION CASE_ID")
    target, version, runtime, case_id = sys.argv[1:]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version):
        raise ValueError("Invalid client version")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}|stable", runtime) or not re.fullmatch(r"[a-f0-9]{24}", case_id):
        raise ValueError("Invalid runtime or case ID")
    directory = ROOT / ".fpdb-local/build" / case_id
    directory.mkdir(parents=True, exist_ok=True)
    if target.startswith("go-"):
        prepare_go(target.removeprefix("go-"), version, runtime, directory)
    elif target == "java-okhttp":
        prepare_java(version, directory)
    elif target == "php-guzzle":
        prepare_php(version, directory)
    elif target == "dotnet-restsharp":
        prepare_dotnet(version, runtime, directory)
    elif target in {"rust-reqwest-native", "rust-reqwest-rustls"}:
        prepare_rust(target.removeprefix("rust-"), version, runtime, directory)
    else:
        raise ValueError("Unsupported native target: " + target)


if __name__ == "__main__":
    main()
