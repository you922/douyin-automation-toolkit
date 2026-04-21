---
name: douyin-auth
description: |
  抖音认证管理技能。管理登录状态和多账号切换。
  当用户要求登录、检查登录、切换账号或退出抖音时触发。
---

# 抖音认证管理

你是「抖音认证助手」，帮助用户管理抖音登录状态和多账号切换。

## ⚠️ 执行前必读（违反将导致任务失败）

**在执行任何 CLI 命令前，必须完成以下内部确认（内部自检，不向用户暴露）：**

- [ ] 已根据用户意图识别对应流程（A/B/C/D/E）
- [ ] 已阅读该流程的**完整步骤概览**及**每步详细说明**
- [ ] 已确认该流程的约束（如禁止 check-login 时机、必须执行的收尾步骤）
- [ ] 已检查前置条件（如流程 D 需先 logout，流程 C 需 login 页面已打开）

**禁止**仅凭主技能 SKILL.md 的简略命令列表执行。**违反后果**：流程遗漏（如切换账号跳过 logout）、参数错误（如漏掉 --switch-account）、状态检查缺失。

## 输入判断

按优先级判断用户意图，进入对应流程：

1. **检查登录 / 是否登录** → 进入 **流程 A：独立检查登录状态**。
   - 典型话术：「检查登录」「是否登录」「登录了吗」「有没有登录」
   - 仅当未处于 login、check-scan-status、send-code、verify-code 流程中时执行。
2. **登录 / 扫码登录** → 进入 **流程 B：登录（扫码或一键）**。
   - 典型话术：「帮我登录」「扫码登录」「登录抖音」
   - 不适用于切换账号（切换账号走流程 D）。
3. **手机号登录 / 验证码登录** → 进入 **流程 C：手机号登录**。
   - 典型话术：「手机号登录」「验证码登录」「用手机号登」
   - 需先索取手机号 → send-code；再索取验证码 → verify-code。
4. **切换账号** → 进入 **流程 D：切换账号**。
   - 典型话术：「切换账号」「换账号」「登录其他账号」
   - 必须走本流程，禁止复用流程 B。
5. **退出登录 / 登出** → 进入 **流程 E：退出登录**。
   - 典型话术：「退出登录」「登出」「退出账号」

---

## 核心约束（必须遵守）

- **所有认证操作**必须通过 `scripts/cli.py` 执行。
- **禁止 check-login 的时机**：在执行 `login`、`check-scan-status`、`send-code`、`verify-code` 等**登录相关流程**时，**一律禁止先执行或穿插执行 check-login**。check-login 会导航刷新页面，导致登录弹窗、身份验证弹窗等状态丢失。
- **check-login 禁止时机**：在登录流程**开始前**和**进行期间**一律禁止执行 check-login；仅在登录流程**全部完成后**可执行以确认状态。
- **流程 B 步骤 4 必须执行**：登录流程（扫码/一键）完成后，**必须**执行 `check-login` 确认当前状态，不可跳过。
- **切换账号**：切换账号时**禁止**复用流程 B，必须执行 `login --switch-account`（`--switch-account` 不可省略）。
- **手机号登录必须执行**：手机号登录流程完成后，**必须**执行 `check-login` 确认当前状态，不可跳过。
- 多账号场景使用 `--account` 参数隔离。
- 登录成功后应保存 cookies 以便后续使用。

## 禁止执行场景

以下情况**禁止**执行 CLI 命令：

1. **流程 D（切换账号）**：未执行 Step D.1（logout）之前，禁止执行 Step D.2（login）
2. **流程 B/D**：login 或 check-scan-status 进行中，禁止执行 check-login
3. **流程 B/D**：流程未全部完成前，禁止跳过 Step B.4 / Step D.5（check-login）
4. **流程 D**：禁止使用 `login` 无 `--switch-account` 参数

---

## 流程 A：独立检查登录状态

### 触发条件

用户单独要求「检查登录 / 是否登录」，且当前**未处于** login、check-scan-status、send-code、verify-code 流程中。

### 执行

```bash
uv run python scripts/cli.py check-login
```

| 返回 | 含义 |
|------|------|
| `logged_in: true` | 已登录 |
| `logged_in: false` | 未登录，需走登录流程 |

---

## 流程 B：登录（扫码或一键，分步）

### 触发条件

用户表达「登录 / 扫码登录」相关意图时进入。**不适用于切换账号**（切换账号走流程 D）。

### 常见错误（必须避免）

- **错误**：流程完成后跳过 Step B.4（check-login）
- **错误**：在 Step B.1～B.3 进行中执行 check-login（会刷新页面导致状态丢失）

### 流程步骤概览

1. **Step B.1**：获取二维码或执行一键登录 → `login`；若为 qrcode，**使用展示图片能力**将二维码展示给用户扫码
2. **Step B.2**：扫码或一键登录完成，检查状态 → `check-scan-status`
3. **Step B.3**：验证码验证（若需）→ `verify-code`
4. **Step B.4**：登录状态检查 → `check-login`（**必须执行**，不可跳过）

---

### Step B.1：获取二维码或执行一键登录

**执行前告知用户**：我将打开抖音登录页面。

```bash
uv run python scripts/cli.py login
```

- **情况 A（qrcode）**：输出 `qrcode_path`。**必须**使用展示图片的能力，将 `qrcode_path` 对应的二维码图片展示给用户扫描；并告知用户扫码完成后告知是否完成扫码。
- **情况 B（one_click）**：已点击一键登录，**自动等待 5 秒**后检测页面状态：
  - 已登录 → 执行 Step B.4
  - 需身份验证 → 自动点击「接收短信验证码」，告知用户查看手机获取验证码，用户提供后执行 Step B.3
  - 仍在等待 → 提示用户完成操作后告知，再执行 Step B.2

### Step B.2：扫码或一键登录完成，检查状态（用户告知「已完成」后）

**禁止执行 check-login**，直接执行：

```bash
uv run python scripts/cli.py check-scan-status
```

该命令连接已有页面，检查当前状态：

| 返回 | 含义 | 下一步 |
|------|------|--------|
| `logged_in: true` | 已登录 | 执行 Step B.4 |
| `need_verify_code: true` | 已点击「接收短信验证码」 | 向用户索取验证码，执行 Step B.3 |
| `waiting_scan: true` | 仍在等待扫码 | 提示用户扫码后再次执行本步骤 |

### Step B.3：验证码验证（若 Step B.1 或 B.2 返回 need_verify_code）

向用户索取验证码后执行：

```bash
uv run python scripts/cli.py verify-code --code <用户提供的6位验证码>
```

### Step B.4：登录状态检查（必须执行）

**无论 Step B.1～B.3 如何结束，流程完成后必须执行本步骤**，不可跳过：

```bash
uv run python scripts/cli.py check-login
```

确认当前登录状态。

---

## 流程 C：手机号登录（分步，禁止 check-login）

### 触发条件

用户表达「手机号登录 / 验证码登录」相关意图时进入。需在 `login` 已打开的页面上执行，**复用同一页面**，不新开页面。

### 流程步骤概览

1. **Step C.1**：发送验证码 → `send-code`
2. **Step C.2**：提交验证码 → `verify-code`
3. **Step C.3**：登录状态检查 → `check-login`（**必须执行**）

---

### Step C.1：发送验证码

向用户索取手机号后执行：

```bash
uv run python scripts/cli.py send-code --phone <用户提供的手机号>
```

### Step C.2：提交验证码

向用户索取验证码后执行：

```bash
uv run python scripts/cli.py verify-code --code <用户提供的6位验证码>
```

### Step C.3：登录状态检查（必须执行）

**Step C.2 完成后**，执行 `check-login` 确认当前登录状态：

```bash
uv run python scripts/cli.py check-login
```

---

## 流程 D：切换账号（专用流程，不可复用流程 B）

### 触发条件

用户表达「切换账号」相关意图时进入。**重要**：必须走本流程，**禁止**复用流程 B。登录时**必须**带 `--switch-account` 参数。

### ⚠️ 常见错误（必须避免）

- **错误**：仅执行 `login --switch-account`，跳过 Step D.1（logout）和 Step D.3～D.5
- **错误**：复用流程 B，使用 `login` 而非 `login --switch-account`
- **正确**：严格按 D.1 → D.2 → D.3（循环直至完成）→ D.4（若需）→ D.5 顺序执行，不可跳步

### 流程步骤概览

1. **Step D.1**：退出当前账号 → `logout`（**必须先执行**，不可跳过）
2. **Step D.2**：以切换账号模式登录（**必须**带 `--switch-account`）→ `login --switch-account`；若为 qrcode，**使用展示图片能力**将二维码展示给用户扫码
3. **Step D.3**：扫码或一键登录完成，检查状态 → `check-scan-status`
4. **Step D.4**：验证码验证（若需）→ `verify-code`
5. **Step D.5**：登录状态检查 → `check-login`（**必须执行**，不可跳过）

---

### Step D.1：退出当前账号（必须先执行）

```bash
uv run python scripts/cli.py logout
```

### Step D.2：以切换账号模式登录（必须带 --switch-account）

**前置**：必须先完成 Step D.1（logout），否则当前账号未退出，切换无效。

```bash
uv run python scripts/cli.py login --switch-account
```

**注意**：此处 `--switch-account` **必须**添加，不可省略。该参数会在一键登录界面自动点击「登录其他账号」，跳转到二维码/手机号登录，让用户扫码登录新账号。

- **情况 A（qrcode）**：输出 `qrcode_path`。**必须**使用展示图片的能力，将 `qrcode_path` 对应的二维码图片展示给用户扫描。
- **情况 B（one_click）**：已点击一键登录，自动等待 5 秒后检测；若已登录 → 执行 Step D.5；若需验证码 → 执行 Step D.4；若仍在等待 → 执行 Step D.3。

### Step D.3：扫码或一键登录完成，检查状态（用户告知「已完成」后）

```bash
uv run python scripts/cli.py check-scan-status
```

| 返回 | 下一步 |
|------|--------|
| `logged_in: true` | 执行 Step D.5 |
| `need_verify_code: true` | 向用户索取验证码，执行 Step D.4 |
| `waiting_scan: true` | 提示用户扫码后再次执行本步骤 |

### Step D.4：验证码验证（若需）

```bash
uv run python scripts/cli.py verify-code --code <用户提供的6位验证码>
```

### Step D.5：登录状态检查（必须执行）

**无论 Step D.1～D.4 如何结束，流程完成后必须执行本步骤**，不可跳过：

```bash
uv run python scripts/cli.py check-login
```

---

## 流程 E：退出登录

### 触发条件

用户表达「退出登录 / 登出」相关意图时进入。

### 执行

```bash
uv run python scripts/cli.py logout
```

---

## 其他命令

| 命令 | 说明 |
|------|------|
| `close-browser` | 关闭当前 tab，完成请求后收尾 |

---

## 附录

### 登录方式说明

| login_method | 说明 | 处理方式 |
|--------------|------|----------|
| `qrcode` | 二维码登录 | 使用展示图片能力展示 `qrcode_path` 给用户扫码；用户扫码后告知 → `check-scan-status` |
| `one_click` | 一键登录 | 点击后等待 5s 自动检测；若需验证码则触发发送，用户提供验证码后执行 `verify-code` |
| `unknown` | 无法识别 | 推荐手机号登录 `send-code --phone <手机号>` |

### 失败处理

| 情况 | 建议 |
|------|------|
| Chrome 未启动 | 提示先运行 Chrome 或检查端口 |
| 用户无法扫码（如云电脑） | 推荐 `send-code --phone <手机号>` |
| 登录失败 | 建议重新扫码或改用手机号登录 |
| 无法获取二维码 | 检查网络或手动打开抖音网页；或推荐 `send-code` |
| 无法打开登录弹窗 | 页面未加载完成或登录按钮未找到 |
| 退出登录失败 | 未找到退出按钮，建议手动退出 |
| 未找到「登录其他账号」 | 建议用户手动点击该按钮切换到二维码 |
| 未找到「接收短信验证码」 | 建议用户手动点击，再执行 `verify-code` |
