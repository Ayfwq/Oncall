# Playwright / Chromium 异常处理手册

## 1. 概述

本文档描述 Python Playwright 驱动 Chromium 时常见异常（launch 失败、依赖缺失、沙箱问题、超时、浏览器崩溃）的标准处理流程。适用于自动化巡检、爬虫、截图任务。

## 2. 常见异常与错误信息

| 异常类型 | 典型报错文本 | 根因方向 |
| --- | --- | --- |
| Launch 失败 | `Executable doesn't exist at .../chromium-XXXX/chrome-win/chrome.exe` | 浏览器未安装或版本不匹配 |
| 依赖缺失 | `error while loading shared libraries: libnss3.so` | 系统依赖库缺失（Linux） |
| 沙箱错误 | `Running as root without --no-sandbox is not supported` | root 用户运行 Chromium 沙箱 |
| 连接中断 | `Target page, context or browser has been closed` | 浏览器进程被回收或崩溃 |
| 超时 | `Timeout 30000ms exceeded`、`waiting for selector` | 页面加载慢或选择器不存在 |
| WebSocket | `WebSocket closed`、`browser.newContext: websocket: bad handshake` | CDP 通道异常或代理拦截 |

## 3. 快速诊断步骤

1. 复现命令：`python -m playwright install --dry-run chromium` 检查浏览器安装状态。
2. 执行 `python -m playwright install chromium` 安装匹配版本的 Chromium。
3. Linux 环境下执行 `python -m playwright install-deps chromium` 安装系统依赖（libnss3、libatk、libx11 等）。
4. 检查任务运行环境：是否 root 用户、是否容器（docker）、是否有外网代理。
5. 用最小脚本复现：`browser = p.chromium.launch(headless=True); page = browser.new_page(); page.goto("https://example.com")`。

## 4. 常见原因与处理

### 4.1 浏览器可执行文件缺失

- 现象：`Executable doesn't exist` 或 `BrowserType.launch: Executable doesn't exist`。
- 处理：在项目根目录执行 `playwright install chromium`；CI 中在镜像构建阶段预装浏览器。
- 注意：Playwright 版本升级（如 1.40 → 1.45）后浏览器 revision 变化，必须重新 install。

### 4.2 Linux 系统依赖缺失

- 现象：`libnss3.so: cannot open shared object file`、`libatk-bridge-2.0.so.0` 缺失。
- 处理：Debian/Ubuntu 执行 `playwright install-deps chromium`；CentOS/RHEL 参考 Playwright 官方依赖清单安装 `nss`、`atk`、`cups-libs`、`libX11`、`libXcomposite`、`libXdamage`、`libXrandr`、`mesa-libgbm` 等包。

### 4.3 root 用户沙箱限制

- 现象：`Running as root without --no-sandbox is not supported`。
- 处理：容器内以 root 运行时，launch 参数加 `chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])`；更优做法是以非 root 用户运行任务。

### 4.4 页面加载 / 选择器超时

- 现象：`Timeout 30000ms exceeded`。
- 处理：调大 `page.goto(url, timeout=60000)`；改用 `page.wait_for_selector(sel, state="attached")`；检查网络代理与目标站点的访问策略；对懒加载内容使用 `scroll_into_view` 后再断言。

### 4.5 浏览器进程崩溃 / WebSocket 断开

- 现象：`browser has been closed`、`WebSocket closed`。
- 处理：检查容器内存限制（`--disable-dev-shm-usage` 可缓解 /dev/shm 不足）；单任务单浏览器实例，避免并发复用同一 browser；捕获异常后重建 browser context 并重试（指数退避）。

### 4.6 headless 模式差异

- 现象：headless 下页面渲染结果与有头模式不一致。
- 处理：确认 headless 参数（new headless 为 `headless=True` 时默认使用新模式）；对依赖 GPU 的页面可尝试 `--disable-gpu` 或 `--use-gl=swiftshader`。

## 5. 恢复后验证

- 运行最小 launch + goto + screenshot 脚本，确认无异常输出。
- 检查浏览器版本与 Playwright 包版本匹配：`python -m playwright --version`。
- 连续运行 10 次任务确认无偶发崩溃。

## 6. 预防措施

- 在 CI/容器镜像中固定 Playwright 版本并预装浏览器。
- 任务级异常捕获 + 重试（建议最多 3 次，指数退避）。
- 监控任务失败率，告警阈值设 5%。
