/**
 * Hidden launcher for Windows: PM2 runs this Node process,
 * which spawns python.exe with windowsHide so no console pops up.
 */
const { spawn } = require("child_process");
const path = require("path");

const root = path.join(__dirname, "..");
// pythonw.exe = Windows GUI subsystem, no console window at all
const python = path.join(root, ".venv", "Scripts", "pythonw.exe");

const child = spawn(python, ["-m", "bot", "--mode", "paper"], {
  cwd: root,
  windowsHide: true,
  stdio: ["ignore", "pipe", "pipe"],
  env: {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    BOT_MODE: "paper",
  },
});

if (child.stdout) {
  child.stdout.pipe(process.stdout);
}
if (child.stderr) {
  child.stderr.pipe(process.stderr);
}

function shutdown(signal) {
  if (!child.killed) {
    child.kill(signal);
  }
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

child.on("error", (err) => {
  console.error("failed to start python:", err);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 0);
});
