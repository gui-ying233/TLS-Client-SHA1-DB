'use strict';
// One native Node/OpenSSL connection with OpenSSL DEFAULT cipher selection.
const fs = require('node:fs');
const os = require('node:os');
const tls = require('node:tls');
const { URL } = require('node:url');
const [expected, endpoint] = process.argv.slice(2);
if (expected !== 'stdlib') throw Error('Expected stdlib');
const url = new URL(endpoint);
if (url.protocol !== 'https:') throw Error('HTTPS required');
const ca = process.env.FPDB_LOCAL_CA;
if (!ca) throw Error('FPDB_LOCAL_CA required');
const socket = tls.connect({
  host: url.hostname, port: Number(url.port || 443), servername: url.hostname,
  ALPNProtocols: ['http/1.1'], ciphers: 'DEFAULT', ca: fs.readFileSync(ca),
  rejectUnauthorized: true,
});
let wire = Buffer.alloc(0);
socket.setTimeout(25000, () => socket.destroy(Error('timeout')));
socket.once('secureConnect', () => {
  socket.write(`GET ${url.pathname || '/'} HTTP/1.1\r\nHost: ${url.host}\r\nAccept: application/json\r\nConnection: close\r\n\r\n`);
});
socket.on('data', buf => { wire = Buffer.concat([wire, buf]); });
socket.once('end', () => {
  const sep = wire.indexOf('\r\n\r\n');
  if (sep < 0 || !/^HTTP\/1\.1 200\b/.test(wire.toString('ascii', 0, sep))) throw Error('Invalid HTTP response');
  const echo = JSON.parse(wire.subarray(sep + 4).toString('utf8'));
  console.log(JSON.stringify({runtime_version: process.versions.node, client_version: expected,
    environment: {os: os.platform(), arch: os.arch(),
      tls_backend: {name: 'OpenSSL', version: process.versions.openssl}}, echo}));
});
socket.once('error', e => { console.error(e); process.exitCode = 1; });
