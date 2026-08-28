# 客服小美 Design System

```yaml
version: 0.1
name: xiaomei-design
product: 客服小美 (xiaomei-chat)
surfaces:
  - chat: index.html   # https://chat.okva.cc
  - admin: admin.html  # https://admin01.okva.cc
status: active
inspired-by:
  - apple      # chat FEEL ONLY: pale grouped canvas, thin chrome, one action blue, pill CTA
  - intercom   # conversation clarity: customer vs agent labels, product-led not marketing
  - cal        # table density: generous outer padding, dense inner table
  - coreui     # admin FEEL ONLY: dark sidebar, white topbar, colored KPI cards, right aside
do-not-copy:
  - Apple logo, SF Pro webfont, apple.com black/white marketing chapters
  - Intercom Fin Orange (#ff5600), cream canvas (#f5f1ec), Saans typeface
  - Cal Sans, Cal navy footer
  - CoreUI / CoreUI PRO logo, "新的/专业版" 徽章、模板目录（图表/成分/Facebook/YouTube）、假日程与库存头像
  - Current pink chat gradient (#f06292 / #e91e63)
```

## 1. Visual Theme & Atmosphere

客服小美是给客户用的网页客服，和给运营用的会话后台。两面是同一套系统的两个档位：

- **聊天页**像 iOS 对话：浅灰分组底、白气泡、蓝用户气泡、底部胶囊输入。Chrome 尽量消失，对话本身是前景。
- **后台页**像深色侧栏控制台：`#212631` 侧栏、白顶栏 65px、画布 `#ebedef`、靛蓝主按钮。顶栏七个图标分别对应客户新消息 / 今日动态栏 / 最近会话 / 语言 / 后台深浅色 / 账号 / 快捷入口。不要做成苹果设置页。

产品一句话：*「安静的对话工具」*。留白多、装饰少、强调色只用在动作和「我发的话」。

**Key Characteristics:**

- 浅灰分组画布，白卡片浮在上面，不用粉渐变顶栏
- 单一动作蓝，只出现在主按钮、用户气泡、链接、选中态
- 发丝分割承担层级，阴影几乎不用
- 按钮分两档：胶囊主操作、圆形工具键
- 系统字体（`-apple-system` / PingFang SC），不加载 SF Pro
- 聊天页人格用「美」字头像，不用贴纸风大 emoji 当品牌

## 2. Color Palette & Roles

### Primary
- **Grouped Canvas** (`#f2f2f7`): 页面底、聊天区、后台侧栏。iOS 分组列表底，不是 apple.com 的 `#f5f5f7`。
- **Ink** (`#1c1c1e`): 主文案、标题、图标。接近但不抄 `#1d1d1f`。
- **Action** (`#0a84ff`): 发送、登录、搜索、用户气泡。比苹果网页蓝略亮，偏系统对话蓝。

### Secondary
- **Ink Muted** (`#6e6e73`): 副标题、空态、表头、时间戳。
- **Hairline** (`rgba(60, 60, 67, 0.12)`): 分割线、输入描边、表格行。
- **Fill** (`#e5e5ea`): 工具键底、选中侧栏、机器人浅头像底。

### Surface
- **White** (`#ffffff`): 顶栏、输入条、后台主栏、卡片、机器人气泡。
- **User Bubble** (`#0a84ff`): 客户消息。白字。
- **Bot Bubble** (`#ffffff`): 小美消息。发丝边。

### Semantic
- **Live** (`#30d158`): 在线圆点。
- **Recording** (`#ff3b30`): 按住说话进行中。
- **Error** (`#ff3b30`): 登录失败、上传失败。
- **Customer tag** (`#e8f4ff` / `#0a84ff`): 后台「客户」。
- **Agent tag** (`#f2f2f7` / `#1c1c1e`): 后台「小美」。

### Gradient System
- 不用渐变。顶栏、按钮、气泡都是实地。

## 3. Typography Rules

### Font Family
- **UI:** `-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif`
- 不 `@font-face` 加载 SF Pro。苹果设备走系统西文；中文走 PingFang SC。
- 访客 ID 等机器字符串用 `ui-monospace, SFMono-Regular, Menlo, monospace`。

### Hierarchy
| Role | Size | Weight | Line Height | Notes |
|------|------|--------|-------------|-------|
| Admin title | 28px | 600 | 1.14 | 后台页标题，字距 -0.4px |
| Chat title | 17px | 600 | 1.2 | 顶栏「客服小美」 |
| Login title | 28px | 600 | 1.14 | 登录卡片 |
| Body | 17px | 400 | 1.47 | 气泡正文 |
| Control | 15px | 400 | 1.3 | 输入、按钮 |
| Caption | 13px | 400 | 1.3 | 副标题、空态、表头 |
| Stat number | 32px | 600 | 1.0 | 看板数字，字距 -0.8px |
| Micro | 12px | 400 | 1.2 | 时间戳、标签 |

## 4. Layout, Spacing & Radius

### Spacing
4 / 8 / 12 / 16 / 20 / 24 / 32。聊天列最大 720px 居中。后台主栏 `24px 28px`，侧栏 220px。

### Radius
| Token | Value | Use |
|-------|-------|-----|
| `r-sm` | 8px | 输入框、表格卡片内控件 |
| `r-md` | 12px | 后台卡片、登录卡 |
| `r-lg` | 18px | 聊天气泡 |
| `r-pill` | 980px | 主按钮、搜索胶囊、输入条 |
| `r-full` | 50% | 头像、工具圆钮、在线点 |

气泡：对方左上小缺角（4px），自己右上小缺角。不要四角一样圆。

### Elevation
默认 0。顶栏和输入条用白底 + 发丝，不用投影。后台卡片同理。唯一允许的深度：登录卡 `0 8px 32px rgba(0,0,0,0.06)`。

## 5. Components

### Chat header
白底、底发丝。左：36px 圆头像，字「美」，墨色。中：标题 + 「在线」绿点。右：36 圆钮打开「对话记录」，旁边胶囊「新对话」。记录从右侧滑出列表，点一条回到该会话。不要汉堡菜单（汉堡只给后台抽屉），不要色带、不要渐变、不要阴影。

### Bubbles
- 小美：白底、发丝、左对齐，头像在左。
- 客户：蓝底白字，右对齐，头像在右（可用「我」）。
- 附件：白底发丝圆角 12；客户侧附件叠在蓝气泡语义下用白 12% 透明底。
- 思考中：白气泡 + 「小美正在思考」+ 省略号闪烁。尊重 `prefers-reduced-motion`。

### Composer
底栏白、顶发丝、安全区 padding。工具键 36 圆、`#e5e5ea` 底、墨色描线图标。输入灰底胶囊、无粗边，focus 用 2px action 外环。发送为蓝胶囊白字。录音中工具键改 Recording 红。

### Admin login
灰画布 `#ebedef` 居中。白卡 8 圆。靛蓝满宽按钮 6 圆。错误用红字，不弹窗。

### Admin nav
桌面：256px 深色侧栏，选中项 `rgba(135,145,175,.1)` 圆角 6。汉堡可收起侧栏。
手机：汉堡打开 255px 抽屉，顶栏品牌 + 关闭 X，遮罩点空白关闭。
栏目只留：运营看板、会话记录、访客列表、外观设置、退出。不要模板目录和「新的」徽章。

### Admin topbar & aside
白顶栏 65px：汉堡、搜索、面包屑、右上 7 个图标（铃铛=客户新消息、列表=今日动态栏、信封=最近会话、语言=简体中文、太阳=后台深浅色、头像=账号、九宫格=快捷入口）。列表图标打开右侧 320px 面板（动态 / 访客 / 齿轮）。看板数字、14 天柱状图、关键词都来自真实会话，不要稿里的交通图和社交假数据。

### Admin appearance
后台「外观设置」：上传圆形头像、改显示名称、锁定 AI 24 小时在线、改对话区背景。聊天页通过公开 `GET /api/widget` 读取。侧栏品牌名和顶栏头像同步更新，不要整段替换 `.brand`。新客欢迎语走 AstrBot，不要写死在聊天页。
看板 KPI 用靛蓝 / 青 / 黄 / 红 / 绿 / 深色卡片。表头 Caption 色。行 hover `#f8f9fa`。关键词计数用 Action 色。

### Tags
客户 / 小美用上面的 semantic tag，不要绿/橙 Intercom 报表色。

## 6. Interaction & Motion

- hover / focus / active 必须有。active：`scale(0.98)`。
- 过渡 `180ms cubic-bezier(0.22, 1, 0.36, 1)`，只动 `color / background / opacity / transform`。
- 录音脉冲可保留，`prefers-reduced-motion: reduce` 时关掉。
- 不要页面入场编排、不要 marquee、不要玻璃拟态当主表面。顶栏若用 `backdrop-filter`，必须有实色回退。

## 7. Breakpoints

- 聊天：始终单列。`<480px` 气泡最大宽 86%；工具键可略缩。键盘弹出时现有 JS 仍隐藏顶栏。
- 后台：`>768px` 固定深色侧栏 256px，汉堡收起；`≤768px` 左抽屉 + 右动态栏。表格横向滚动。

## 8. Content & Accessibility

- 欢迎语可保留口语（「你好呀，我是客服小美」），界面 chrome 用短词：发送、搜索、退出登录。
- 焦点环 2px Action，offset 2px。对比：Ink on Canvas、白 on Action 均过 AA。
- 工具键保留 `title` + `aria-label`。不要把功能只画在图标上。
- 触摸目标 ≥ 36px。

## 9. Do / Don't

**Do**
- 让对话和表格成为视觉主体
- 蓝只用在动作和「我」
- 发丝 + 留白分层
- 两页共用同一套 token

**Don't**
- 粉顶栏、橙 CTA、奶油底（旧聊天 / 旧后台）
- 黑英雄章节、产品图廊、胶囊商店导航（apple.com 签名）
- 双强调色、霓虹、大阴影卡片、三等分功能宫格
- 把 SF Pro 当 webfont 引入

## 10. Reference Trace

| reference | use | adapt | avoid |
|-----------|-----|-------|-------|
| apple | 浅底、薄 chrome、胶囊 CTA、发丝、双档（展示安静 / 后台更密） | 画布改 `#f2f2f7`，动作蓝改 `#0a84ff`，字体走系统栈；聊天跟对话产品走，不跟营销站走 | logo、SF Pro 文件、黑白章节、整套苹果网页色板 |
| intercom | 客/服方向可扫读、少装饰、后台是工具不是落地页 | 标签语义留下，色改成蓝/灰 | Fin Orange、奶油画布、Saans |
| cal | 表内容密、页边松、主按钮一个色 | 主按钮用动作蓝而不是纯黑 | Cal Sans、海军页脚、彩色徽章彩虹 |
