// 渲染层桥接：页面本身是本地 HTTP 服务提供的普通 Web 页面，
// 只额外暴露少量桌面能力（版本信息、数据目录、外部链接）。
const { contextBridge, ipcRenderer, shell } = require('electron');

contextBridge.exposeInMainWorld('desktopShell', {
  isDesktop: true,
  info: () => ipcRenderer.invoke('app:info'),
  openExternal: (url) => {
    if (/^https?:\/\//i.test(String(url))) shell.openExternal(String(url));
  },
});
