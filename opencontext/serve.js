const http = require('http');
const { execSync } = require('child_process');

// Patch native module loading
const M = require('module');
const origResolve = M._resolveFilename;
M._resolveFilename = function(r) {
  if (r.includes('core-native') || r.includes('core-native-linux')) {
    return require.resolve('./node_modules/@aicontextlab/cli/../src/core/native.js');
  }
  return origResolve.apply(this, arguments);
};

function oc(args) {
  try {
    return execSync(`node /app/opencontext/bin/oc.js ${args}`, {
      cwd: '/app/opencontext',
      encoding: 'utf8',
      env: { ...process.env, OPENCONTEXT_DATA_DIR: '/data' }
    });
  } catch (e) {
    return e.stdout || e.message || '';
  }
}

// Initialize on first request
let initialized = false;

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', initialized }));
    return;
  }

  if (req.url === '/init') {
    try {
      if (!initialized) {
        oc('init --data-dir /data --non-interactive');
        initialized = true;
      }
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('initialized');
    } catch (e) {
      res.writeHead(500);
      res.end(e.message);
    }
    return;
  }

  res.writeHead(404);
  res.end('not found');
});

server.listen(3284, '0.0.0.0', () => {
  console.log('OpenContext HTTP wrapper on :3284');
  // Auto-init
  try {
    oc('init --data-dir /data --non-interactive');
    initialized = true;
    console.log('OpenContext initialized');
  } catch (e) {
    console.log('OpenContext init:', e.message);
  }
});
