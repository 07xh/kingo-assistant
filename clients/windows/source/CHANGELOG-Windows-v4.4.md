# Windows V4.4 | 2026-11-11

定位：仍为**只读余量监测与提醒**。EXE 和 Android APK 可以分别独立运行，本发行版只包含 Windows 桌面源码。**尚不具备自动选课提交功能，也不包含验证码破解、绕过限流或高频并发。**

本版变更：

1. 通过学校官网公开通知核验电脑端教务网址 `https://jwmis.ncwu.edu.cn`；加入“浏览器打开官方教务”按钮，使用 Windows 默认浏览器手动登录。该网址是**用户入口而非经验证的程序接口**。
2. 新增 `--self-test` 纯离线自检，检查安装包内 YAML 模板、只读协议门禁、原子保存以及敏感 Cookie 不落盘。该自检不启动 Tk、不发网络请求。
3. `build-windows.bat` 增加 Python 启动器回退、构建前清理旧 EXE、单元测试、自检和新 EXE 自检；任一步失败则删除不可信的构建产物。
4. GitHub Actions 在 Windows 2022 运行器上打包后执行 `KingoAssistant-Windows.exe --self-test`，非零返回码阻止上传构建产物。
5. 新增 `debug-windows.bat` 用于 Windows 源码启动错误诊断；扩展无网络测试。

验收：

- Linux Python 3.13：58 项离线测试通过；`python windows_gui.py --self-test` 返回 0。
- Xvfb/Tk：主窗口、官网浏览器打开调用、配置编辑、原子保存、数据刷新与退出流程通过。
- **尚未执行真实 Windows EXE 构建和运行测试**：需要将源码上传至独立仓库并执行包含的 GitHub Actions 或在 Windows 实机运行 `build-windows.bat`。
- **尚未确认华水需要登录后的课表/选课列表读取接口和本人会话**：不允许假设可用；真实查询默认关闭。

安全说明：官方页面打开动作完全由用户触发，程序不读取浏览器密码，不修改官方会话，不将账号、Cookie 或 HAR 打包。复制/分享前仍应检查自建配置文件及抓包记录中的个人数据。
