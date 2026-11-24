# Agent Router 多账号自动签到

推荐搭配使用[Auo](https://github.com/millylee/auo)，支持任意 Claude Code Token 切换的工具。

**维护开源不易，如果本项目帮助到了你，请帮忙点个 Star，谢谢!**

用于 Claude Code 中转站 Agent Router 多账号每日签到。

## 功能特性

- ✅ 单个/多账号自动签到
- ✅ 多种机器人通知（可选）
- ✅ 绕过 WAF 限制

## 使用方法

### 1. Fork 本仓库

点击右上角的 "Fork" 按钮，将本仓库 fork 到你的账户。

### 2. 获取账号信息

对于每个需要签到的账号，你需要获取：
1. **Cookies**: 用于身份验证
2. **API User**: 用于请求头的 new-api-user 参数

#### 获取 Cookies：
1. 打开浏览器，访问 https://agentrouter.org/
2. 登录你的账户
3. 打开开发者工具 (F12)
4. 切换到 "Application" 或 "存储" 选项卡
5. 找到 "Cookies" 选项
6. 复制所有 cookies

#### 获取 API User：
通常在网站的用户设置或 API 设置中可以找到，每个账号都有唯一的标识。

### 3. 设置 GitHub Environment Secret

1. 在你 fork 的仓库中，点击 "Settings" 选项卡
2. 在左侧菜单中找到 "Environments" -> "New environment"
3. 新建一个名为 `production` 的环境
4. 点击新建的 `production` 环境进入环境配置页
5. 点击 "Add environment secret" 创建 secret：
   - Name: `AGENTROUTER_ACCOUNTS`
   - Value: 你的多账号配置数据

### 4. 多账号配置格式

支持单个与多个账号配置，可选 `name` 字段用于自定义账号显示名称：

```json
[
  {
    "name": "我的主账号",
    "cookies": {
      "session": "account1_session_value"
    },
    "api_user": "account1_api_user_id"
  },
  {
    "name": "备用账号",
    "cookies": {
      "session": "account2_session_value"
    },
    "api_user": "account2_api_user_id"
  }
]
```

**字段说明**：
- `cookies` (必需)：用于身份验证的 cookies 数据
- `api_user` (必需)：用于请求头的 new-api-user 参数
- `name` (可选)：自定义账号显示名称，用于通知和日志中标识账号

如果未提供 `name` 字段，会使用 `Account 1`、`Account 2` 等默认名称。

接下来获取 cookies 与 api_user 的值。

通过 F12 工具，切到 Application 面板，拿到 session 的值，最好重新登录下，该值 1 个月有效期，但有可能提前失效，失效后报 401 错误，到时请再重新获取。

![获取 cookies](./assets/request-session.png)

通过 F12 工具，切到 Network 面板，可以过滤下，只要 Fetch/XHR，找到带 `New-Api-User`，这个值正常是 5 位数，如果是负数或者个位数，正常是未登录。

![获取 api_user](./assets/request-api-user.png)

### 5. 启用 GitHub Actions

1. 在你的仓库中，点击 "Actions" 选项卡
2. 如果提示启用 Actions，请点击启用
3. 找到 "AgentRouter 自动签到" workflow
4. 点击 "Enable workflow"

### 6. 测试运行

你可以手动触发一次签到来测试：

1. 在 "Actions" 选项卡中，点击 "AgentRouter 自动签到"
2. 点击 "Run workflow" 按钮
3. 确认运行

![运行结果](./assets/check-in.png)

## 执行时间

- 脚本每6小时执行一次（1. action 无法准确触发，基本延时 1~1.5h；2. 目前观测到签到是每 24h 而不是零点就可签到）
- 你也可以随时手动触发签到

## 注意事项

- 请确保每个账号的 cookies 和 API User 都是正确的
- 可以在 Actions 页面查看详细的运行日志
- 支持部分账号失败，只要有账号成功签到，整个任务就不会失败
- 报 401 错误，请重新获取 cookies，理论 1 个月失效

## 支持的站点

脚本通过 `checkin.py` 的 `SITES` 字典按站点类型扩展，每个站点对应一个独立的 `<SITE>_ACCOUNTS` Secret：

| 站点 key | 站点类型 | accounts_env | 账号字段 | 认证方式 |
| --- | --- | --- | --- | --- |
| `pin` | Xboard(V2Board) 面板 | `PIN_ACCOUNTS` | `token` | `Authorization: <JWT 裸值>`（不可加 `Bearer`） |
| `trae` | Trae CN 云 IDE | `TRAE_ACCOUNTS` | `token`、`device_id`（可选 `machine_id` / `app_version` / `os_version`） | `Authorization: Cloud-IDE-JWT <token>` + `x-device-id` 设备上下文 |

Xboard 站点账号配置示例：

```json
[
  {
    "name": "pin-account",
    "token": "从浏览器 localStorage[\"token\"] 复制的 JWT"
  }
]
```

说明：
- `token` 为面板会话 JWT，登录态长期有效，失效后重新取 `localStorage["token"]` 即可
- 重复签到返回 400「今天已经签到过了」，脚本按幂等成功处理
- 该站点服务端证书为 IP 直连的 ZeroSSL 证书，脚本对该站点关闭 TLS 校验（`verify_tls: False`）

Trae CN 站点账号配置示例：

```json
[
  {
    "name": "trae-account",
    "token": "<Trae 客户端登录态中的 accessToken 占位符>",
    "device_id": "<客户端设备号占位符>",
    "machine_id": "<可选：设备指纹占位符>",
    "app_version": "1.107.1",
    "os_version": "<可选：不填则取本机 platform.version()>"
  }
]
```

说明：
- `token` 为 Trae CN 客户端的 `accessToken`；`device_id` 为用户目录下 Trae CN 登录态中的设备号，**必填**，缺失时脚本直接判失败
- 请求头必须为 `Authorization: Cloud-IDE-JWT <token>`（加 `Bearer` 会返回 `code=1001` 认证失败），并携带 `X-User-Region: CN`、`x-device-id`、`x-device-type: Windows`、`User-Agent: Trae/1.107.1`
- 签到流程：先 `POST /ug/checkin_credits/status`（body 固定 `{}`），返回 `checked_in` / `did_checked_in` 为 `true` 即视为已签到；否则 `POST /ug/checkin_credits/claim`，`code == 0` 为成功，`message` 含「已签/已经签/already」按幂等成功处理
- 常见错误码：`9074` 设备号未注册（`x-device-id` 不被认可）、`1001` 令牌失效或缺少设备上下文、`20405` 缺少设备证明，脚本会把上述错误码翻译成可读原因写入日志
- 令牌策略：Actions 只持有 `accessToken`，不使用 `refreshToken` 刷新，因此该站点**不参与** Secret 回写；`accessToken` 过期需在本机重新读取后更新 `TRAE_ACCOUNTS` Secret

## 配置示例

假设你有两个账号需要签到：

```json
[
  {
    "cookies": {
      "session": "abc123session"
    },
    "api_user": "user123"
  },
  {
    "cookies": {
      "session": "xyz789session"
    },
    "api_user": "user456"
  }
]
```

## 开启通知

脚本支持多种通知方式，可以通过配置以下环境变量开启，如果 `webhook` 有要求安全设置，例如钉钉，可以在新建机器人时选择自定义关键词，填写 `AgentRouter`。

### 邮箱通知
- `EMAIL_USER`: 发件人邮箱地址
- `EMAIL_PASS`: 发件人邮箱密码/授权码
- `EMAIL_TO`: 收件人邮箱地址

### 钉钉机器人
- `DINGDING_WEBHOOK`: 钉钉机器人的 Webhook 地址

### 飞书机器人
- `FEISHU_WEBHOOK`: 飞书机器人的 Webhook 地址

### 企业微信机器人
- `WEIXIN_WEBHOOK`: 企业微信机器人的 Webhook 地址

### PushPlus 推送
- `PUSHPLUS_TOKEN`: PushPlus 的 Token

### Server酱
- `SERVERPUSHKEY`: Server酱的 SendKey

配置步骤：
1. 在仓库的 Settings -> Environments -> production -> Environment secrets 中添加上述环境变量
2. 每个通知方式都是独立的，可以只配置你需要的推送方式
3. 如果某个通知方式配置不正确或未配置，脚本会自动跳过该通知方式

## 故障排除

如果签到失败，请检查：

1. 账号配置格式是否正确
2. cookies 是否过期
3. API User 是否正确
4. 网站是否更改了签到接口
5. 查看 Actions 运行日志获取详细错误信息

## 本地开发环境设置

如果你需要在本地测试或开发，请按照以下步骤设置：

```bash
# 安装所有依赖
uv sync --dev

# 安装 Playwright 浏览器
playwright install chromium

# 按 .env.example 创建 .env
uv run checkin.py
```

## 测试

```bash
uv sync --dev

# 安装 Playwright 浏览器
playwright install chromium

# 运行测试
uv run pytest tests/
```

## 免责声明

本脚本仅用于学习和研究目的，使用前请确保遵守相关网站的使用条款.

