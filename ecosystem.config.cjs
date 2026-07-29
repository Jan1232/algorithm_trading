module.exports = {
  apps: [
    {
      name: "bybit-demo-bot",
      cwd: __dirname,
      // Node wrapper hides the Windows console of python.exe
      script: "scripts/run_demo_hidden.js",
      interpreter: "node",
      windowsHide: true,
      autorestart: true,
      max_restarts: 50,
      min_uptime: "10s",
      restart_delay: 5000,
      watch: false,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
        BOT_MODE: "demo",
      },
      error_file: "logs/pm2-demo-error.log",
      out_file: "logs/pm2-demo-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
  ],
};
