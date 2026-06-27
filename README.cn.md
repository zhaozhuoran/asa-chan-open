# Asa Chan

一个基于 QWeather 推送天气预报的 Slack 机器人。

## 截图

![截图](assests/screenshot.jpg)

## 功能

- 每日自动天气报告推送
- 实时天气查询
- 每个用户可自定义城市和通知时间

## 安装与设置

### 1. 前置准备

- 获取 [QWeather API Key](https://dev.qweather.com/)
- 创建一个 [Slack App](https://api.slack.com/apps)

### 2. Slack App 配置

1.  **Socket Mode**：启用 Socket Mode。
2.  **App-Level Tokens**：生成一个具有 `connections:write` 权限的 App-Level Token（以 `xapp-` 开头）。
3.  **Slash Commands**：注册以下命令（确保 Socket Mode 已启用，以便机器人能够接收）：
    - `/asachan-start`：订阅每日天气报告
    - `/asachan-unsub`：取消订阅
    - `/asachan-now`：获取实时天气
    - `/asachan-status`：查看当前订阅状态
    - `/asachan-setcity`：设置城市（例如 `/asachan-setcity 北京`）
    - `/asachan-settime`：设置通知时间（例如 `/asachan-settime 08:30`）
    - `/asachan-ping`：测试命令，返回 "Pong！"
    - `/asachan-up`：检查机器人状态
    - `/asachan-dailynow`：获取今日摘要（调试/手动拉取）
4.  **Bot Token Scopes**：在 `OAuth & Permissions` 中添加以下权限范围：
    - `chat:write`
    - `commands`
5.  **安装 App**：将 App 安装到你的工作区并获取 Bot User OAuth Token（以 `xoxb-` 开头）。

### 3. 本地部署

1.  克隆仓库。
2.  创建一个 `.env` 文件，内容如下：
    ```env
    QWEATHER_KEY=你的和风天气API密钥
    SLACK_BOT_TOKEN=xoxb-你的机器人令牌
    SLACK_APP_TOKEN=xapp-你的应用令牌
    ```
3.  运行启动脚本：
    ```bash
    ./start.sh
    ```

## 命令列表

| 命令                              | 描述                           |
| :-------------------------------- | :----------------------------- |
| `/asachan-start`                  | 订阅每日天气推送               |
| `/asachan-unsub`                  | 取消订阅                       |
| `/asachan-now`                    | 获取当前实时天气               |
| `/asachan-status`                 | 查看当前设置（城市、时间）     |
| `/asachan-setcity [城市]`         | 设置目标城市                   |
| `/asachan-settime [HH:MM]`        | 设置每日推送时间               |
| `/asachan-ping`                   | 测试命令，返回 Pong！          |
| `/asachan-up`                     | 检查机器人是否在线             |
| `/asachan-settimezone [Timezone]` | 设置时区（例如 Asia/Shanghai） |

## 许可证

本项目基于 GNU Affero General Public License v3.0 许可。详见 [LICENSE](LICENSE) 文件。
