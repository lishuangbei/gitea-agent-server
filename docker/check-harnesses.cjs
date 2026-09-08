// An Alpine install can contain a glibc node-pty binary even if dsh --version works.
// Exercise the real pseudo-terminal without credentials or model API calls.
const fs = require('node:fs');
const path = require('node:path');
function findPty(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!item.isDirectory() || item.name.startsWith('.')) continue;
    const p = path.join(dir, item.name);
    if (item.name === 'node-pty') return p;
    if (item.name.startsWith('@')) {
      const found = findPty(p);
      if (found) return found;
    } else if (fs.existsSync(path.join(p, 'node_modules'))) {
      const found = findPty(path.join(p, 'node_modules'));
      if (found) return found;
    }
  }
}
const ptyPath = findPty(path.join(__dirname, 'node_modules'));
if (!ptyPath) throw new Error('DSH node-pty dependency was not found');
const pty = require(ptyPath).spawn('/bin/bash', ['-c', 'printf harness-pty-ok'], {
  cwd: '/tmp', env: process.env, name: 'xterm-256color', cols: 80, rows: 24,
});
let output = '';
pty.onData(data => { output += data; });
const timer = setTimeout(() => { pty.kill(); process.exit(1); }, 10000);
pty.onExit(({ exitCode }) => {
  clearTimeout(timer);
  if (exitCode !== 0 || !output.includes('harness-pty-ok')) process.exit(1);
  console.log('DSH pseudo-terminal check passed');
});
