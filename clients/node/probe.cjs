'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const https = require('https');
const { client, version, endpoint } = {client: process.argv[2], version: process.argv[3], endpoint: process.argv[4]};
if (!client || !version || !endpoint) process.exit(2);
const diagnosticRetry = process.env.FPDB_DIAGNOSTIC_RETRY === '1';
const verify = process.env.FPDB_TLS_VERIFY !== 'false';
function describeResponse(data) {
  if (data === null) return {type:'null'};
  if (Array.isArray(data)) return {type:'array',length:data.length};
  if (typeof data !== 'object') return {type:typeof data,length:typeof data === 'string' ? data.length : undefined};
  const http = data.http;
  const tls = http && typeof http === 'object' ? http.tls : null;
  return {type:'object',keys:Object.keys(data).slice(0,24),
    httpKeys:http && typeof http === 'object' ? Object.keys(http).slice(0,24) : [],
    tlsKeys:tls && typeof tls === 'object' ? Object.keys(tls).slice(0,24) : []};
}
function reportAxiosResponse(response) {
  if (!diagnosticRetry || !response) return;
  const headers = response.headers || {};
  console.error('FPDB retry HTTP diagnostics '+JSON.stringify({
    status:response.status,
    contentType:headers['content-type'],
    contentEncoding:headers['content-encoding'],
    server:headers.server,
    data:describeResponse(response.data)
  }));
}
async function read() {
  if (client === 'https') return await new Promise((resolve,reject) => {
    const req=https.get(endpoint, {headers:{Accept:'application/json',Connection:'close'},agent:false,
      ...(!verify ? {rejectUnauthorized:false} : {})}, res => {
      let data=''; res.setEncoding('utf8');res.on('data',v=>data+=v);
      res.on('end',()=>res.statusCode>=200&&res.statusCode<300?resolve(JSON.parse(data)):reject(new Error('HTTP '+res.statusCode)));
    });req.on('error',reject);req.setTimeout(25000,()=>req.destroy(new Error('timeout')));
  });
  if (client === 'fetch') {
    if (!verify) process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
    const response=await fetch(endpoint,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(25000)});
    if(!response.ok) throw Error('HTTP '+response.status);
    return response.json();
  }
  if (client === 'undici') {
    const { Agent, request } = require('undici');
    const dispatcher = new Agent({connections:1,pipelining:0,...(!verify ? {connect:{rejectUnauthorized:false}} : {})});
    try { const response=await request(endpoint,{dispatcher,headers:{accept:'application/json'},headersTimeout:25000,bodyTimeout:25000});
      if(response.statusCode<200||response.statusCode>=300) throw Error('HTTP '+response.statusCode);
      return await response.body.json();
    } finally {await dispatcher.close();}
  }
  if (client === 'axios') {
    // Axios 1.1.0 exports its CommonJS client under `default`.
    const axiosModule=require('axios');
    const axios=typeof axiosModule.get === 'function' ? axiosModule : axiosModule.default;
    if (typeof axios?.get !== 'function') throw Error('Unsupported axios module export');
    try {
      // Response compression is not part of the TLS ClientHello fingerprint.
      // Avoid old axios versions' Brotli decompression failures.
      const response=await axios.get(endpoint,{proxy:false,httpsAgent:new https.Agent({keepAlive:false,...(!verify ? {rejectUnauthorized:false} : {})}),
        headers:{Accept:'application/json','Accept-Encoding':'identity',Connection:'close'},timeout:25000});
      reportAxiosResponse(response);
      return response.data;
    } catch (error) {
      if (diagnosticRetry) {
        reportAxiosResponse(error.response);
        console.error('FPDB retry axios error '+JSON.stringify({name:error.name,code:error.code,
          message:String(error.message).slice(0,240)}));
      }
      throw error;
    }
  }
  throw Error('Unknown client '+client);
}
(async()=>{
  const dependencies={};let actual='stdlib';
  if(client==='undici'||client==='axios'){
    // Resolve installed package metadata directly; some axios versions block package.json exports.
    actual=JSON.parse(fs.readFileSync(path.join(__dirname,'node_modules',client,'package.json'),'utf8')).version;
    dependencies[client]=actual;
  }
  if(actual!==version) throw Error('Version mismatch '+actual+' vs '+version);
  console.log(JSON.stringify({runtime_version:process.versions.node,client_version:actual,verify,
    environment:{os:os.platform(),arch:os.arch(),tls_backend:{name:'OpenSSL',version:process.versions.openssl},dependencies},echo:await read()}));
})().catch(e=>{console.error(e.stack||e);process.exit(1)});
