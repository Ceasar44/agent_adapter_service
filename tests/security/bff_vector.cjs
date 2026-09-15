// Regenerate the shared, non-secret fixture with Node's standard crypto implementation.
const { createHash, createHmac } = require('node:crypto');
const { writeFileSync } = require('node:fs');
const key = Buffer.from(Array.from({length: 32}, (_, i) => i));
const claims = {iss:'storefront', aud:'adapter', iat:1800000000, exp:1800000060,
  jti:'0123456789abcdef', scope:{tenant_id:'tenant',store_id:'store'},
  identity:{visitor_id:'visitor',status:'anonymous'}};
const body = '{"message":"你好\\n世界","number":1}';
const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
const path = '/api/agent';
const input = ['v1','active','POST',path,createHash('sha256').update(body).digest('hex'),payload].join('\n');
const signature = createHmac('sha256',key).update(input).digest('base64url');
writeFileSync(__dirname + '/bff_vector.json', JSON.stringify({key:key.toString('base64'),
  claims, body, path, payload, signature, authorization:`v1.active.${payload}.${signature}`},null,2)+'\n');
