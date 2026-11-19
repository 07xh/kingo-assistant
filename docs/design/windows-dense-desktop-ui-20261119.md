# KingoAssistant Windows 密集桌面外壳（2026-11-19）

## 参考基准与边界

视觉参考是 Windows 上常见的 **分流抢票（Bypass）** 类桌面客户端：Win32 /
WinForms 风格的密集布局。本仓库只借鉴它的**设计语言**，不复用其代码、资源、
图标或二进制；没有引入任何第三方 UI 库，仍然是 `tkinter` / `ttk`。

之所以换掉 V1/V2 的「深色侧边栏 + 浅色卡片」方案，是因为实机截图显示：

- 侧边栏吃掉 98–112px 宽度，三页内容实际可用宽度不足，中文标签频繁截断；
- 卡片化留白过大，一屏放不下首页 5 个步骤，主操作按钮被推到折叠线以下；
- 深色侧栏与浅色内容区两种色系并存，视觉噪音高于信息本身。

密集桌面风格正好相反：**用小字号、1px 分组框、扁平命令条把一屏塞满**，
让用户在一屏内看到全部操作路径。

## 提取到的设计特征

从参考程序的截图与 UIA 控件树（205 个控件）里提取出的、可迁移的规则：

| 特征 | 参考程序 | 本仓库落地 |
| --- | --- | --- |
| 顶部命令条 | 扁平工具条，无边框、高约 34px | `TOOLBAR_HEIGHT = 34`，按钮 `relief="flat"` |
| 导航 | 真实标签页（凸起），不是侧边栏 | `ttk.Notebook`，`① 选课首页 / ② 手动官网备用 / ③ 工具与日志` |
| 分组 | 1px 实线 GroupBox + 左上标题 | `_group()`：`LabelFrame(borderwidth=1, relief="solid")` |
| 字号 | 9pt 全篇，无大标题 | `FONT_UI/FONT_BOLD/FONT_SMALL/FONT_TITLE`，最大 11pt |
| 数据表 | 白底、灰表头、无斑马纹、单行紧凑 | `Treeview` `rowheight=20`，表头 `grid_header` |
| 状态栏 | 底部凹陷一条，左侧状态、右侧计数 | `relief="sunken"`，`status_gate` 常驻 |
| 主操作 | 右侧唯一大按钮 | 首页第 ⑤ 组固定贴在页底 |

## 代码分层

改动集中在三个文件，职责不重叠：

1. `clients/windows/source/windows_visual_shell.py` —— **外壳与主题**
   - 新增 `PALETTE` 令牌表、五个字体常量、`TABS` 表、`TOOLBAR_HEIGHT`；
   - `make_visual_shell()` 只产出「命令条 + Notebook + 状态栏」三段，
     **删除** `NAV_SECTIONS` / `connect_sidebar` / `sync_sidebar` 与整个侧边栏；
   - `update_responsive_shell()` 改为：窄窗时折叠命令条次级按钮
     （`toolbar_extras`）、状态栏按宽度换行、标签 `wraplength` 钳制；
   - 显式 `style.layout("Kingo.Content.TNotebook.Tab", ...)` 已移除，
     标签条重新可见。
2. `clients/windows/source/windows_page_sections.py` —— **三页布局**
   - 新增四个无状态辅助函数 `_group()` / `_row()` / `_button()` / `_status()`；
   - 首页改为**固定密集布局**，不套滚动容器：第 ⑤ 组先
     `pack(side="bottom")`，第 ④ 组课程网格 `fill="both", expand=True`
     吸收剩余高度；
   - 「学校接口只读核验」从纵向堆叠改为 `grid` 两列（按钮列 + 状态列）；
   - 官网页重做为三组：手动操作 / 华水官方入口清单（`OFFICIAL_ENTRIES`
     填充 `Treeview`）/ 到点提醒。
3. `clients/windows/source/windows_gui.py` —— **装配**
   - `APP_TITLE` 明示只读边界；
   - `geometry("1180x790")`、`minsize(960, 640)`；
   - `_build()` 顺序：`apply_light_theme` → `make_visual_shell` →
     `_build_command_strip` → 三个 `TABS` 标签页 → 只有工具页包滚动容器。

## 主题令牌

```python
PALETTE = {
    "background": "#F0F0F0", "surface": "#FFFFFF", "panel": "#F0F0F0",
    "panel_alt": "#E9E9E9", "header": "#E4ECF7", "ink": "#101010",
    "muted": "#5B5B5B", "stroke": "#A0A0A0", "stroke_soft": "#C8C8C8",
    "accent": "#0F6CBD", "accent_hover": "#0C5896", "accent_soft": "#DCEBFA",
    "selected": "#CCE4FF", "ok": "#0B6A0B", "warn": "#8A5300",
    "danger": "#A4262C", "grid_header": "#E8EEF7",
}
```

`clam` 主题是唯一被强制使用的内置主题；`TNotebook.Tab` 需要显式给
`lightcolor` / `darkcolor` / `bordercolor`，否则 clam 不画凸起，三个标签会
变成三块白色方块 —— 这是本次改造踩到的第一个坑。

## 布局约束（改代码前必读）

- **`pack` 与 `grid` 不能在同一父容器混用**。工具页的 `contract_row` 因此
  统一改成 `grid`；主页则全部用 `pack`。
- **首页不滚动**。`_home_content = self.tab_home`，不再是 Canvas。主操作按钮
  必须在一屏内可见，这是本次改造的核心目标，不要退回到滚动容器。
- 只有 `tab_tools` 包 `make_scrollable_content`，因为日志和高级配置天生很长。
- 按钮宽度用容器控制：`ttk.Button` 没有 `-width` 选项，需要
  `ttk.Frame(width=236)` + `pack_propagate(False)`。

## DPI 与字号

字体一律用**点字号**（`9`），不用像素（`-12`）。参考程序是 96dpi 下的 9pt
Win32 应用，Tk 会自己乘缩放因子；写死像素会在高 DPI 屏上变得极挤。

截图脚本 `.audit/shot_kingo.py` 先
`SetThreadDpiAwarenessContext(-4)`（Per-Monitor V2），再抓图并
`resize(LANCZOS)` 下采样，否则抓出来是 1.0 逻辑缩放叠加 2.0 物理缩放的巨字。

## 真实截图

`tests/export_visual_preview.py` 仍导出三张真实 Tk 截图（首页 / 官网页 /
工具页），1180×790，`scale=2.00`。首页那张不再需要「滚动至课程列表」的
第二步，因为整页已经在一屏内。

## 回归门禁

| 测试 | 锁定的内容 |
| --- | --- |
| `test_windows_visual_shell.py` | 命令条 + 状态栏 + 三标签、`TABS` 表、无侧边栏、主题令牌 |
| `test_windows_responsive_scroll.py` | `toolbar_extras` 折叠、状态栏换行、`wraplength` 钳制、仅工具页有 Canvas |
| `test_windows_home_page.py` | 5 个固定分组标题、网格列宽 `[62, 240, 110, 110, 320]`、13 个按钮回调 |
| `test_windows_page_sections.py` | 三页 22 个按钮全部映射到 `Application` 回调 |
| `tests/smoke_gui.py` | 真实 Tk：外壳、三步离线演示、配置恢复、原子保存、关闭 |
| `run_packaged_gui_smoke()` | `minsize (960, 640)`、`toolbar`/`status_bar`/`status_gate` 挂载、无 `sidebar` |

`run_packaged_gui_smoke` 里新增了 `mounted()` 辅助函数：工具页的
`school_page_button` 现在由 `grid` 管理，原先只认 `winfo_manager() == "pack"`
的断言会误报未挂载 —— `("pack", "grid")` 都算已挂载。

## 明确没有改的东西

网络请求、会话与 Cookie 生命周期、配置读写、课程状态机、日志格式、
按钮文案的业务含义，全部保持原样。默认程序标题本身就在提醒边界：
`KingoAssistant · 华水选课助手（只读监测｜自动提交未接入）`。
