// server.js
// Usage: node server.js
// Ensure you have certs at ./certs/key.pem and ./certs/cert.pem (use mkcert for localhost)

const fs = require('fs');
const path = require('path');
const https = require('https');
const express = require('express');
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

const wss = new WebSocket.Server({ server, path: '/youtube-stream' });

app.use(express.static(path.join(__dirname, 'public')));

wss.on('connection', (ws, req) => {
  console.log('[WS] Publisher connected from', req.socket.remoteAddress);

  let ffmpeg = null;
  let started = false;
  let lastChunkAt = Date.now();

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

      console.log('[WS] Starting ffmpeg ->', rtmpUrl);

      // spawn ffmpeg to read webm from stdin and push to rtmp
      // tuned for general performance; adjust b:v, preset, and threads for your host
      const ffArgs = [
        '-fflags', '+nobuffer',
        '-hide_banner',
        '-loglevel', 'info',

        // Input: webm from pipe
        '-f', 'webm',
        '-i', 'pipe:0',

        // Video encoding
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-tune', 'zerolatency',
        '-r', '30',
        '-g', '60',
        '-b:v', '2000k', // set to 2000kbps
        '-maxrate', '2200k',
        '-bufsize', '3000k',

        // Audio encoding
        '-c:a', 'aac',
        '-ar', '44100',
        '-b:a', '128k',

        // Output
        '-f', 'flv',
        '-flvflags', 'no_duration_filesize',
        rtmpUrl
      ];

      // If you have a supported NVIDIA GPU, you can use hardware encoding for lower CPU usage:
      // const ffArgs = [
      //   '-f','webm','-i','pipe:0',
      //   '-c:v','h264_nvenc','-preset','p1','-b:v','4000k','-maxrate','5000k','-bufsize','8000k',
      //   '-c:a','aac','-ar','44100','-b:a','192k',
      //   '-f','flv','-flvflags','no_duration_filesize', rtmpUrl
      // ];

      ffmpeg = spawn('ffmpeg', ffArgs, { stdio: ['pipe', 'inherit', 'inherit'] });

      ffmpeg.on('close', (code, signal) => {
        console.log('[FFmpeg] exited', code, signal);
        ffmpeg = null;
      });

      ffmpeg.stdin.on('error', (e) => {
        console.log('[FFmpeg stdin error]', e && e.message);
      });

      started = true;
      ws.send(JSON.stringify({ ok: true, info: 'ffmpeg started' }));
      return;
    }

    // subsequent messages are binary WebM chunks (ArrayBuffer/Buffer)
    if (started) {
      // ensure binary
      if (!isBinary) {
        // ignore non-binary during streaming
        return;
      }
      lastChunkAt = Date.now();
      if (ffmpeg && ffmpeg.stdin.writable) {
        try {
          ffmpeg.stdin.write(msg);
        } catch (e) {
          console.error('[WS] Error writing to ffmpeg stdin', e && e.message);
        }
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
    clearInterval(idleInterval);
  });

  ws.on('error', (err) => {
    console.error('[WS] socket error', err && err.message);
  });
});

const PORT = process.env.PORT || 8443;
server.listen(PORT, () => console.log(`HTTPS+WSS server listening at https://localhost:${PORT}`));
