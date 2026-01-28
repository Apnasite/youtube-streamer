// server.js
// Usage: node server.js
// Ensure you have certs at ./certs/key.pem and ./certs/cert.pem (use mkcert for localhost)

const fs = require('fs');
const path = require('path');
const https = require('https');

const express = require('express');
const { Server } = require('socket.io');
const WebSocket = require('ws');
const { spawn } = require('child_process');

const app = express();
const KEY = path.join(__dirname, 'server.key');
const CERT = path.join(__dirname, 'server.cert');

if (!fs.existsSync(KEY) || !fs.existsSync(CERT)) {
  console.error('Missing certs. Create certs/server.key and certs/server.cert (see README).');
  process.exit(1);
}

const server = https.createServer({
  key: fs.readFileSync(KEY),
  cert: fs.readFileSync(CERT)
}, app);

// --- Socket.io signaling for WebRTC receiver ---
// Track active RTMP keys
const activeKeys = new Set();

const io = new Server(server);

io.on('connection', (socket) => {
  socket.on('offer', (offer) => socket.broadcast.emit('offer', offer));
  socket.on('answer', (answer) => socket.broadcast.emit('answer', answer));
  socket.on('ice-candidate', (candidate) => socket.broadcast.emit('ice-candidate', candidate));
});

const wss = new WebSocket.Server({ server, path: '/youtube-stream' });

app.use(express.static(path.join(__dirname, 'public')));

wss.on('connection', (ws, req) => {
  console.log('[WS] Publisher connected from', req.socket.remoteAddress);

  let ffmpeg = null;
  let started = false;
  let lastChunkAt = Date.now();
  let ffmpegError = false;
  let initialBuffer = [];
  let headerReceived = false;
  let rtmpKey = null;

  // timeout: if no data for 20s, stop ffmpeg
  const IDLE_TIMEOUT_MS = 20_000;
  const idleInterval = setInterval(() => {
    if (ffmpeg && Date.now() - lastChunkAt > IDLE_TIMEOUT_MS) {
      console.log('[WS] Idle timeout - stopping ffmpeg');
      try { ffmpeg.stdin.end(); } catch(e){}
      try { ffmpeg.kill('SIGINT'); } catch(e){}
      ffmpeg = null;
    }
  }, 5000);

  ws.on('message', (msg, isBinary) => {
    // first message should be JSON with rtmpUrl
    if (!started) {
      let init;
      try {
        init = JSON.parse(isBinary ? msg.toString() : msg);
      } catch (e) {
        ws.send(JSON.stringify({ error: 'Invalid init JSON' }));
        ws.close();
        return;
      }
      const rtmpUrl = init && init.rtmpUrl ? init.rtmpUrl : null;
      if (!rtmpUrl) {
        ws.send(JSON.stringify({ error: 'Missing rtmpUrl in init message' }));
        ws.close();
        return;
      }
      // Extract key from rtmpUrl (YouTube keys are after last /)
      const keyMatch = rtmpUrl.match(/live2\/(.+)$/);
      rtmpKey = keyMatch ? keyMatch[1] : null;
      if (!rtmpKey) {
        ws.send(JSON.stringify({ error: 'Invalid RTMP key in URL' }));
        ws.close();
        return;
      }
      // Check if key is already in use
      if (activeKeys.has(rtmpKey)) {
        ws.send(JSON.stringify({ error: 'RTMP key already in use' }));
        ws.close();
        return;
      }
      // Mark key as active
      activeKeys.add(rtmpKey);
      console.log(`[WS] RTMP key ${rtmpKey} marked as active.`);
      console.log('[WS] Waiting for WebM header before starting ffmpeg ->', rtmpUrl);
      ws.rtmpUrl = rtmpUrl;
      started = true;
      ws.send(JSON.stringify({ ok: true, info: 'waiting for WebM header' }));
      return;
    }

    // Buffer initial chunks until WebM header is detected
    if (!headerReceived) {
      if (!isBinary) return;
      initialBuffer.push(msg);
      // Check for EBML header in buffered data
      const concat = Buffer.concat(initialBuffer.map(b => Buffer.isBuffer(b) ? b : Buffer.from(b)));
      // EBML header starts with 0x1A 0x45 0xDF 0xA3
      if (concat.length > 4 && concat[0] === 0x1A && concat[1] === 0x45 && concat[2] === 0xDF && concat[3] === 0xA3) {
        headerReceived = true;
        // Start ffmpeg now
        const ffArgs = [
          '-fflags', '+nobuffer',
          '-fflags', '+genpts',
          '-hide_banner',
          '-loglevel', 'info',
          '-f', 'webm',
          '-i', 'pipe:0',
          '-c:v', 'libx264',
          '-preset', 'fast',
          '-tune', 'zerolatency',
          '-r', '30',
          '-g', '60',
          '-b:v', '2000k',
          '-maxrate', '2200k',
          '-bufsize', '3000k',
          '-c:a', 'aac',
          '-ar', '44100',
          '-b:a', '128k',
          '-f', 'flv',
          '-flvflags', 'no_duration_filesize',
          ws.rtmpUrl
        ];
        ffmpeg = spawn('ffmpeg', ffArgs, { stdio: ['pipe', 'inherit', 'inherit'] });
        ffmpegError = false;
        ffmpeg.on('close', (code, signal) => {
          console.log('[FFmpeg] exited', code, signal);
          ffmpegError = true;
          ffmpeg = null;
          try { ws.send(JSON.stringify({ error: 'FFmpeg exited', code, signal })); } catch(e){}
          ws.close();
        });
        ffmpeg.stdin.on('error', (e) => {
          console.log('[FFmpeg stdin error]', e && e.message);
          ffmpegError = true;
          try { ws.send(JSON.stringify({ error: 'FFmpeg stdin error', message: e && e.message })); } catch(e){}
          ws.close();
        });
        // Write buffered data to ffmpeg
        try {
          ffmpeg.stdin.write(concat);
        } catch(e){
          console.error('[WS] Error writing initial buffer to ffmpeg', e && e.message);
        }
        initialBuffer = [];
        ws.send(JSON.stringify({ ok: true, info: 'ffmpeg started' }));
      }
      return;
    }

    // subsequent messages are binary WebM chunks (ArrayBuffer/Buffer)
    if (ffmpegError) {
      return;
    }
    if (!isBinary) {
      return;
    }
    lastChunkAt = Date.now();
    if (ffmpeg && ffmpeg.stdin.writable) {
      try {
        ffmpeg.stdin.write(msg);
      } catch (e) {
        console.error('[WS] Error writing to ffmpeg stdin', e && e.message);
        ffmpegError = true;
        try { ws.send(JSON.stringify({ error: 'FFmpeg stdin write error', message: e && e.message })); } catch(e){}
        ws.close();
      }
    }
  });

  ws.on('close', () => {
    console.log('[WS] Publisher disconnected');
    if (ffmpeg) {
      try { ffmpeg.stdin.end(); } catch(e){}
      try { ffmpeg.kill('SIGINT'); } catch(e){}
      ffmpeg = null;
    }
    // Release RTMP key for future use
    if (rtmpKey && activeKeys.has(rtmpKey)) {
      activeKeys.delete(rtmpKey);
      console.log(`[WS] RTMP key ${rtmpKey} released.`);
    }
    clearInterval(idleInterval);
  });

  ws.on('error', (err) => {
    console.error('[WS] socket error', err && err.message);
  });
});

const PORT = process.env.PORT || 8443;
server.listen(PORT, () => console.log(`HTTPS+WSS server listening at https://localhost:${PORT}`));
