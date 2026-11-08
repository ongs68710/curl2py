// afterPack：用 rcedit 写入 exe 图标与版本信息。
// 之所以不用 electron-builder 内置的 signAndEditExecutable：那一步依赖
// winCodeSign 压缩包，而该包内含 macOS 符号链接，在无管理员/未开开发者模式的
// Windows 上解压会失败（ERROR: Cannot create symbolic link）。
const path = require('node:path');
const fs = require('node:fs');

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return;

  const { rcedit } = require('rcedit');
  const appInfo = context.packager.appInfo;
  const exePath = path.join(context.appOutDir, `${appInfo.productFilename}.exe`);
  const iconPath = path.join(__dirname, 'icon.ico');

  if (!fs.existsSync(exePath)) {
    throw new Error(`afterPack 找不到可执行文件：${exePath}`);
  }

  const version = appInfo.version;
  const numeric = version.split('.').map((n) => parseInt(n, 10) || 0);
  while (numeric.length < 4) numeric.push(0);

  await rcedit(exePath, {
    icon: iconPath,
    'file-version': numeric.join('.'),
    'product-version': numeric.join('.'),
    'version-string': {
      CompanyName: 'szs3',
      FileDescription: appInfo.productName,
      ProductName: appInfo.productName,
      InternalName: appInfo.productName,
      OriginalFilename: `${appInfo.productFilename}.exe`,
      LegalCopyright: appInfo.copyright || '',
      Comments: 'curl to Python/Feapder toolbox with bundled Python runtime',
    },
  });

  console.log(`  • rcedit 已写入图标与版本信息  file=${path.basename(exePath)} version=${numeric.join('.')}`);
};
