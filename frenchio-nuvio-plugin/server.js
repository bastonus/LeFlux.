// server.js — Local dev server for testing the Nuvio plugin
// Usage: npm start
// Then in Nuvio: Settings > Developer > Plugin Tester → http://<your-ip>:3000/manifest.json

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PORT = 3000;

function getLocalIP() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return '127.0.0.1';
}

const MIME_TYPES = {
  '.json': 'application/json',
  '.js': 'application/javascript',
  '.html': 'text/html',
  '.css': 'text/css',
};

const server = http.createServer((req, res) => {
  // CORS headers — required for Nuvio
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  let filePath = req.url === '/' ? '/manifest.json' : req.url;
  // Strip query string
  filePath = filePath.split('?')[0];

  const fullPath = path.join(__dirname, filePath);
  const ext = path.extname(fullPath);
  const contentType = MIME_TYPES[ext] || 'text/plain';

  fs.readFile(fullPath, (err, data) => {
    if (err) {
      console.log(`404: ${filePath}`);
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    console.log(`200: ${filePath}`);
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  const ip = getLocalIP();
  console.log('\n🚀 Frenchio P2P Dev Server running!');
  console.log(`\n📍 Local:   http://localhost:${PORT}/manifest.json`);
  console.log(`📍 Network: http://${ip}:${PORT}/manifest.json`);
  console.log('\n📱 In Nuvio: Settings → Developer → Plugin Tester');
  console.log(`   Paste: http://${ip}:${PORT}/manifest.json\n`);
});
