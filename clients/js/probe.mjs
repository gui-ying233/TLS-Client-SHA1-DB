// One process performs one HTTP request (and therefore a fresh TLS handshake).
const deno = typeof globalThis.Deno !== 'undefined';
const bun = !deno && typeof globalThis.Bun !== 'undefined';
const runtime = deno ? 'deno' : bun ? 'bun' : 'node';
const args = deno ? Deno.args : process.argv.slice(2);
const [client, expected, endpoint] = args;
if (!client || !expected || !endpoint?.startsWith('https://')) {
  throw new Error('Usage: probe.[mjs|ts] CLIENT VERSION HTTPS_URL');
}

const runtimeVersion = deno ? Deno.version.deno : bun ? Bun.version : process.versions.node;
const dependencies = {};
let actual = 'stdlib';
if (client.startsWith('tsx-') || client.startsWith('oxc-')) {
  if (runtime !== 'node') throw new Error('tsx and Oxc require Node.js');
  const pkg = client.startsWith('tsx-') ? 'tsx' : '@oxc-node/cli';
  const { readFileSync } = await import('node:fs');
  actual = JSON.parse(readFileSync(new URL(`./node_modules/${pkg}/package.json`, import.meta.url), 'utf8')).version;
  dependencies[pkg] = actual;
}
if (actual !== expected) throw new Error(`Client version mismatch ${actual} != ${expected}`);

const backend = deno ? { name: 'rustls', version: 'bundled' }
  : bun ? { name: 'BoringSSL', version: 'bundled' }
  : { name: 'OpenSSL', version: process.versions.openssl };
const os = deno ? Deno.build.os : process.platform;
const arch = deno ? Deno.build.arch : process.arch;
const response = await fetch(endpoint, {
  headers: { Accept: 'application/json' },
  redirect: 'manual',
  signal: AbortSignal.timeout(25000),
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const echo = await response.json();
console.log(JSON.stringify({
  runtime_version: runtimeVersion, client_version: actual,
  environment: { os, arch, tls_backend: backend, dependencies }, echo,
}));
