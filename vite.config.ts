import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import http from 'http';
import { spawn } from 'child_process';
import { defineConfig, Plugin } from 'vite';

function pythonBackendPlugin(): Plugin {
  return {
    name: 'python-backend-runner',
    configureServer() {
      let spawned = false;
      function startPython() {
        if (spawned) return;
        spawned = true;
        console.log('[backend] Starting Python Flask backend daemon (python3 run.py)...');
        const py = spawn('python3', ['run.py'], {
          cwd: process.cwd(),
          stdio: 'inherit',
        });
        py.on('exit', (code) => {
          console.log(`[backend] Python backend exited with code ${code}`);
          spawned = false;
        });
        const cleanExit = () => {
          try {
            py.kill('SIGTERM');
          } catch {}
        };
        process.on('exit', cleanExit);
        process.on('SIGINT', cleanExit);
        process.on('SIGTERM', cleanExit);
      }

      const req = http.get('http://127.0.0.1:5000/api/health', (res) => {
        if (res.statusCode === 200) {
          console.log('[backend] Python Flask backend active on port 5000');
        } else {
          startPython();
        }
      });
      req.on('error', () => {
        startPython();
      });
      req.setTimeout(800, () => {
        req.destroy();
        startPython();
      });
    },
  };
}

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss(), pythonBackendPlugin()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      port: 3000,
      host: '0.0.0.0',
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
        },
      },
    },
  };
});
