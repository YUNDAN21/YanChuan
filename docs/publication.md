# 公开源码范围与上传前检查

项目中文名为“言川”，英文名为 **YanChuan**。公开源码包含核心 API、共享包、WinUI/后台/HTTP 测试应用、有效测试、依赖锁文件、构建脚本、图标、第三方声明和公开文档。

## 本地保留

以下内容由 `.gitignore` 排除，继续保留在电脑上：

- `.venv`、`bin/obj`、IDE/测试缓存及生成的 DLL/EXE。
- `data/`：API 配置、录音、会议数据库和验证报告。
- `models/`：模型权重与缓存；全局模型后缀也已排除。
- `work/`、`dist/`：构建工具、上游源码、临时实验、便携版与安装包。
- `plan/`、`archive/`：个人开发记录和退役实现，不作为公开源码。
- `.env`、`.env.*`、本地配置、日志、私钥、证书、录音和数据库。

`.env.example` 是可公开的配置示例，只包含回环地址和相对路径，没有 API 密钥。桌面总结、问答及翻译服务通过应用配置；真实配置不放进仓库。

原生 DLL 不进入 Git。源码开发需先通过原生构建工具或已发布运行库准备 `runtime/native/win-x64/`，原生构建会使用本机 Visual Studio 和 Vulkan SDK。仓库保留版本说明与许可证，安装包和便携版应单独作为发布附件。

## 检查当前 Git 候选文件

在仓库根目录运行：

```powershell
uv run --project apps/desktop-backend --no-sync python scripts/check_publication.py
```

可保存仅含路径、行号与问题类别的本地报告：

```powershell
uv run --project apps/desktop-backend --no-sync python scripts/check_publication.py --report data/verification/publication/audit.json
```

检查范围包含 Git 已跟踪文件与未被忽略的新文件；即使曾用强制添加把私密文件纳入 Git，也会报告。检查常见密钥格式、已知本地配置密钥、私钥、带凭据的 URL、私人服务 URL、个人绝对路径、链接、超大文件和本地产物。输出不包含匹配到的敏感原文，不计算文件哈希，不执行暂存、提交或网络上传。

新增文件或完成暂存后应重新运行检查，并人工查看待提交文件列表。该工具执行固定规则扫描，不解析任意混淆或编码内容。

## 当前发布准备状态

源码清理与扫描完成后，仍需确定项目自身采用的许可证；现有第三方许可证保留。首次提交前自行选择许可证并确认作者署名。GitHub 仓库创建、提交和上传另行执行。
