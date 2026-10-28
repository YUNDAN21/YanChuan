# 言川 · Windows 安装包

英文品牌名 **YanChuan**。安装窗口、开始菜单、桌面快捷方式和 Windows 应用列表显示“言川”，程序为 `YanChuan.exe`，安装包命名为 `YanChuan-0.1.0-Setup-x64.exe`。

全新安装默认位于 `%LOCALAPPDATA%\Programs\YanChuan`，可自行选择目录。程序、Python、WinUI 与原生库随包提供；模型另行下载，API 密钥、本地配置和会议不进入安装包。

新用户的数据保存在 `%LOCALAPPDATA%\YanChuan`。已有 `%LOCALAPPDATA%\LiveCaptions` 时应用继续使用原目录，会议、模型、自定义路径与 API 配置保持原值。内部注册键保留旧标识，用于识别原安装位置并原位更新；升级时检查新旧 EXE 的文件占用，要求先退出应用，安装新程序后仅删除明确列出的旧入口文件与快捷方式。

卸载使用明确程序文件清单，不递归删除安装根目录，不删除用户数据或额外保存的模型文件。

## 构建

工具使用项目内已准备的 NSIS 3.13，许可证与帮助位于 `work/installer-tools/`。工具本身不进入发布包。

```powershell
uv run --project apps/desktop-backend --no-sync python scripts/package_desktop.py --output dist/YanChuan-0.1.0-win-x64
uv run --project apps/desktop-backend --no-sync python scripts/package_installer.py --package dist/YanChuan-0.1.0-win-x64 --compiler work/installer-tools/nsis/nsis-3.13/Bin/makensis.exe
```

脚本检查 DLL 白名单、文件路径和大小、排除模型及本地配置，并扫描真实本地凭据。发布清单只记录路径和大小，不计算或比较文件哈希。构建日志位于 `work/installer-build/`，安装包旁附 JSON 体积和构建记录。当前未配置代码签名。

## 验证

`tests/release/test_installer.py` 覆盖清单、凭据扫描、安装路径转义、精确卸载、中文品牌与旧安装识别。

`scripts/verify_installer.py` 使用项目验证目录中的临时安装位置，检查文件存在及大小、快捷方式、卸载注册、重复安装、占用拒绝、后台握手、DLL 加载及卸载后数据保留。检测到现有正式安装或同名快捷方式时停止，不操作用户安装。该脚本不计算文件哈希，不打开设备或播放声音。

```powershell
uv run --project apps/desktop-backend --no-sync python scripts/verify_installer.py --installer dist/YanChuan-0.1.0-Setup-x64.exe --package dist/YanChuan-0.1.0-win-x64 --output data/verification/yanchuan-installer
```
