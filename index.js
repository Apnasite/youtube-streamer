const fs = require("fs");
const path = require("path");
const https = require("https");
const express = require("express");
const { Server } = require("socket.io");
const WebSocket = require("ws");
const { spawn } = require("child_process");

const app = express();

// Use your own certs or mkcert self-signed for localhost
const server = https.createServer(
  {
    key: fs.readFileSync(path.join(__dirname, "server.key")),
    cert: fs.readFileSync(path.join(__dirname, "server.cert")),
  },
  app
);

const io = new Server(server);
app.use(express.static(path.join(__dirname, "public")));

// WebRTC signaling for receivers (if you want viewer.html)
io.on("connection", (socket) => {
  socket.on("offer", (offer) => socket.broadcast.emit("offer", offer));
  socket.on("answer", (answer) => socket.broadcast.emit("answer", answer));
  socket.on("ice-candidate", (candidate) =>
    socket.broadcast.emit("ice-candidate", candidate)
  );
});

// WebSocket for publisher → ffmpeg → YouTube
const wss = new WebSocket.Server({ server, path: "/youtube-stream" });

wss.on("connection", (ws) => {
  console.log("Publisher connected to YouTube stream");

  let ffmpeg;
  let started = false;

  // Default RTMP values
  const defaultUrl = "rtmp://a.rtmp.youtube.com/live2";
  const defaultKey = "your-default-stream-key"; // <-- Replace with your default key

  ws.on("message", (msg) => {
    if (!started) {
      // First message is JSON { rtmpUrl }
      let rtmpUrl;
      try {
        const data = JSON.parse(msg.toString());
        rtmpUrl = (data && typeof data.rtmpUrl === 'string' && data.rtmpUrl.trim())
          ? data.rtmpUrl.trim()
          : `${defaultUrl}/${defaultKey}`;
      } catch {
        rtmpUrl = `${defaultUrl}/${defaultKey}`;
      }
      // Validate RTMP URL
      if (!/^rtmp:\/\/.+\/.+/.test(rtmpUrl)) {
        console.log(`[WS /youtube-stream] Invalid or empty RTMP URL, using default.`);
        rtmpUrl = `${defaultUrl}/${defaultKey}`;
      }
      console.log(`[WS /youtube-stream] Using RTMP: ${rtmpUrl}`);

      ffmpeg = spawn("ffmpeg", [
        "-re",
        "-f", "webm",
        "-c:v", "vp8",
        "-c:a", "opus",
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-r", "30",
        "-g", "60",
        "-b:v", "2500k",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "flv",
        "-flvflags", "no_duration_filesize",
        rtmpUrl,
      ]);

      ffmpeg.stderr.on("data", (d) => console.log("ffmpeg:", d.toString()));
      ffmpeg.on("exit", (c, s) => console.log("ffmpeg exited", c, s));
      ffmpeg.on('error', (err) => {
        console.log(`[FFmpeg process error]: ${err}`);
      });

      started = true;
    } else {
      if (ffmpeg && ffmpeg.stdin.writable) {
        ffmpeg.stdin.write(msg);
      }
    }
  });

  ws.on("close", () => {
    console.log("YouTube stream closed");
    if (ffmpeg) {
      try {
        ffmpeg.stdin.end();
        ffmpeg.kill("SIGINT");
      } catch (e) {}
    }
  });
});

const PORT = 5000;
server.listen(PORT, () => {
  console.log(`HTTPS server running on https://localhost:${PORT}`);
});
