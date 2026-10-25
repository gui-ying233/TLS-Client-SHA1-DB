'use strict';
const fs = require('fs');
const os = require('os');
const https = require('https');
const { client, version, endpoint } = {client: process.argv[2], version: process.argv[3], endpoint: process.argv[4]};
if (!client || !version || !endpoint) process.exit(2);
async function read() {
  if (client === 'https') return await new Promise((resolve,reject) => {
    const req=https.get(endpoint, {headers:{Accept:'application/json',Connection:'close'},agent:false}, res => {
      let data=''; res.setEncoding('utf8');res.on('data',v=>data+=v);
      res.on('end',()=>res.statusCode>=200&&res.statusCode<300?resolve(JSON.parse(data)):reject(new Error('HTTP '+res.statusCode)));
    });req.on('error',reject);req.setTimeout(25000,()=>req.destroy(new Error('timeout')));
  });
  if (client === 'fetch') {
    const response=await fetch(endpoint,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(25000)});
    if(!response.ok) throw Error('HTTP '+response.status);
    return response.json();
  }
  if (client === 'undici') {
    const { Agent, request } = require('undici');
    const dispatcher = new Agent({connections:1,pipelining:0});
    try { const response=await request(endpoint,{dispatcher,headers:{accept:'application/json'},headersTimeout:25000,bodyTimeout:25000});
      if(response.statusCode<200||response.statusCode>=300) throw Error('HTTP '+response.statusCode);
      return await response.body.json();
    } finally {await dispatcher.close();}
  }
  if (client === 'axios') {
    const axios=require('axios');
    const response=await axios.get(endpoint,{proxy:false,httpsAgent:new https.Agent({keepAlive:false}),headers:{Accept:'application/json',Connection:'close'},timeout:25000});
    return response.data;
  }
  throw Error('Unknown client '+client);
}
(async()=>{
  const dependencies={};let actual='stdlib';
  if(client==='undici'||client==='axios'){
    actual=require(client+'/package.json').version;dependencies[client]=actual;
  }
  if(actual!==version) throw Error('Version mismatch '+actual+' vs '+version);
  console.log(JSON.stringify({runtime_version:process.versions.node,client_version:actual,
    environment:{os:os.platform(),arch:os.arch(),tls_backend:{name:'OpenSSL',version:process.versions.openssl},dependencies},echo:await read()}));
})().catch(e=>{console.error(e.stack||e);process.exit(1)});
