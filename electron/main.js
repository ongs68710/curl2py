// curl2py 桌面版主进程
// 架构对齐 ms-rewards-auto：Electron 主进程负责窗口/单实例/托盘，
// 业务后端以 sidecar 子进程（Python embed + waitress）方式拉起，只监听 127.0.0.1。

const { app, BrowserWindow, dialog, shell, ipcMain, Menu } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

// 每次会话随机生成，注入页面后由前端带到 X-API-Token；不落盘、不打印
const API_TOKEN = crypto.randomBytes(24).toString('base64url');

const isDev = !app.isPackaged;
const RESOURCES = isDev ? __dirname : process.resourcesPath;

// 绿色单文件版：electron-builder 的 portable 目标会注入 PORTABLE_EXECUTABLE_DIR。
// 这里把 userData 一并搬到 exe 同级，做到真正「拷走即用」——配置/日志/收藏夹都不落
// AppData；同时也避免与已安装版抢同一个单实例锁（否则已装版在跑时绿色版会直接秒退）。
// 目录不可写（光盘、写保护 U 盘）时静默回退到默认行为。
let PORTABLE_ROOT = null;
const portableBase = process.env.PORTABLE_EXECUTABLE_DIR;
if (portableBase) {
  const candidate = path.join(portableBase, 'curl2py-data');
  try {
    fs.mkdirSync(candidate, { recursive: true });
    fs.accessSync(candidate, fs.constants.W_OK);
    PORTABLE_ROOT = candidate;
  } catch (_) {
    /* 保持默认 userData */
  }
}

// 开发态用独立 userData：避免和已安装版本抢同一个单实例锁（否则开发窗口会秒退）
if (PORTABLE_ROOT) {
  app.setPath('userData', path.join(PORTABLE_ROOT, 'profile'));
} else if (isDev) {
  app.setPath('userData', path.join(app.getPath('appData'), 'curl2py-dev'));
}

// 小工具窗口长期前台常驻，关掉 Windows 的遮挡计算可减少 GPU 进程被周期性唤醒
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');

const LAUNCHER = isDev
  ? path.join(__dirname, '..', 'desktop_launcher.py')
  : path.join(RESOURCES, 'desktop_launcher.py');

const PYTHON_BIN = isDev
  ? path.join(__dirname, '..', 'runtime', 'python.exe')
  : path.join(RESOURCES, 'runtime', 'python.exe');

const STARTUP_TIMEOUT_MS = 60000;

let mainWindow = null;
let backend = null;
let backendPort = 0;
let quitting = false;

function logFile() {
  return path.join(app.getPath('userData'), 'backend.log');
}

function appendLog(chunk) {
  try {
    fs.appendFileSync(logFile(), `[${new Date().toISOString()}] ${chunk}\n`);
  } catch (_) {
    /* 日志写失败不影响主流程 */
  }
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const userData = app.getPath('userData');
    // 绿色单文件版：数据落在 exe 同目录的 curl2py-data 里，实现真正的「拷走即用」；
    // 若目录只读（光盘/U 盘写保护），PORTABLE_ROOT 为 null，回退到用户目录。
    const dataDir = PORTABLE_ROOT
      ? path.join(PORTABLE_ROOT, 'data')
      : path.join(userData, 'data');
    const runtimeDir = path.join(userData, 'vault-runtime');
    fs.mkdirSync(dataDir, { recursive: true });
    fs.mkdirSync(runtimeDir, { recursive: true });
    try {
      fs.writeFileSync(logFile(), `=== curl2py desktop start ${new Date().toISOString()} ===\n`);
    } catch (_) {}

    if (!fs.existsSync(PYTHON_BIN)) {
      reject(new Error(`找不到内置 Python 运行时：${PYTHON_BIN}`));
      return;
    }
    if (!fs.existsSync(LAUNCHER)) {
      reject(new Error(`找不到启动器：${LAUNCHER}`));
      return;
    }

    backend = spawn(PYTHON_BIN, [LAUNCHER], {
      cwd: path.dirname(LAUNCHER),
      windowsHide: true,
      env: {
        ...process.env,
        CURL2PY_DATA_DIR: dataDir,
        CURL2PY_RUNTIME_DIR: runtimeDir,
        CURL2PY_PARENT_PID: String(process.pid),
        CURL2PY_API_TOKEN: API_TOKEN,
        ENABLE_SCRIPT_RUNNER: 'true',
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(`后端在 ${STARTUP_TIMEOUT_MS / 1000}s 内未就绪，详见 ${logFile()}`));
    }, STARTUP_TIMEOUT_MS);

    let buffered = '';
    backend.stdout.setEncoding('utf8');
    backend.stdout.on('data', (chunk) => {
      appendLog(chunk.trimEnd());
      buffered += chunk;
      const match = buffered.match(/CURL2PY_READY port=(\d+)/);
      if (match && !settled) {
        settled = true;
        clearTimeout(timer);
        backendPort = Number(match[1]);
        resolve(backendPort);
      }
    });

    backend.stderr.setEncoding('utf8');
    backend.stderr.on('data', (chunk) => appendLog(`[stderr] ${chunk.trimEnd()}`));

    backend.on('error', (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });

    backend.on('exit', (code, signal) => {
      appendLog(`[exit] code=${code} signal=${signal}`);
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`后端进程启动失败（code=${code}），详见 ${logFile()}`));
        return;
      }
      if (!quitting) {
        dialog.showErrorBox('curl2py 后端已退出', `后端异常退出（code=${code}）。\n日志：${logFile()}`);
        app.quit();
      }
    });
  });
}

function stopBackend() {
  if (!backend || backend.killed) return;
  try {
    backend.kill();
  } catch (_) {}
  backend = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 880,
    minWidth: 960,
    minHeight: 620,
    show: false,
    backgroundColor: '#0f172a',
    title: 'curl2py',
    icon: isDev
      ? path.join(__dirname, '..', 'build', 'icon.ico')
      : path.join(RESOURCES, 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
      webSecurity: true,
    },
  });

  Menu.setApplicationMenu(null);

  // 把本会话令牌注入页面（前端在请求头里按 window.CURL2PY_API_TOKEN 取用）。
  // 用主世界注入而不是 preload，是因为页面读的是 window 上的全局变量；
  // 每次加载/刷新都重注一次，避免刷新后丢令牌导致「运行 Python」403。
  const injectToken = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const script =
      `window.CURL2PY_API_TOKEN=${JSON.stringify(API_TOKEN)};` + 'window.CURL2PY_DESKTOP=true;';
    mainWindow.webContents.executeJavaScript(script, true).catch(() => {});
  };
  mainWindow.webContents.on('dom-ready', injectToken);
  mainWindow.webContents.on('did-finish-load', injectToken);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // 页面 <title> 不覆盖窗口标题，保持产品名一致
  mainWindow.on('page-title-updated', (event) => {
    event.preventDefault();
    mainWindow.setTitle('curl2py');
  });

  // 站外链接一律交给系统浏览器，窗口内不放行
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      const target = new URL(url);
      if (!(target.hostname === '127.0.0.1' || target.hostname === 'localhost')) {
        shell.openExternal(url);
        return { action: 'deny' };
      }
    }
    return { action: 'allow' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`http://127.0.0.1:${backendPort}`)) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });

  mainWindow.webContents.session.on('will-download', (_event, item) => {
    item.setSavePath(path.join(app.getPath('downloads'), item.getFilename()));
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
}

async function boot() {
  try {
    await startBackend();
    createWindow();
  } catch (err) {
    const message = err && err.stack ? err.stack : String(err);
    console.error('[boot] failed:', message);
    appendLog(`[boot] failed: ${message}`);
    dialog.showErrorBox('curl2py 启动失败', String(err && err.message ? err.message : err));
    stopBackend();
    app.quit();
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(boot);

  app.on('window-all-closed', () => {
    app.quit();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow();
  });

  app.on('before-quit', () => {
    quitting = true;
    stopBackend();
  });

  process.on('exit', stopBackend);
  process.on('SIGINT', () => {
    quitting = true;
    stopBackend();
    process.exit(0);
  });
}

ipcMain.handle('app:info', () => ({
  version: app.getVersion(),
  port: backendPort,
  userData: app.getPath('userData'),
}));
