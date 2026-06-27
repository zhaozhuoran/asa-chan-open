# Asa Chan

A Slack bot that pushes weather forecasts from QWeather.

## Screenshots

![Screenshot](assests/screenshot.jpg)

## Features

- Daily automatic weather report push
- Real-time weather query
- Customizable city and notification time per user

## Installation and Setup

### 1. Prerequisites

- Get a [QWeather API Key](https://dev.qweather.com/)
- Create a [Slack App](https://api.slack.com/apps)

### 2. Slack App Configuration

1.  **Socket Mode**: Enable Socket Mode.
2.  **App-Level Tokens**: Generate an App-Level Token with `connections:write` scope (starts with `xapp-`).
3.  **Slash Commands**: Register the following commands (ensure Socket Mode is enabled so the bot can receive them):
    - `/asachan-start`: Subscribe to daily weather reports
    - `/asachan-unsub`: Unsubscribe from reports
    - `/asachan-now`: Get real-time weather
    - `/asachan-status`: Check current subscription status
    - `/asachan-setcity`: Set your city (e.g., `/asachan-setcity London`)
    - `/asachan-settime`: Set notification time (e.g., `/asachan-settime 08:30`)
    - `/asachan-ping`: Test command, returns "Pong!"
    - `/asachan-up`: Check bot status
    - `/asachan-dailynow`: Get today's summary (debug/manual fetch)
4.  **Bot Token Scopes**: Add the following scopes in `OAuth & Permissions`:
    - `chat:write`
    - `commands`
5.  **Install App**: Install the app to your Workspace and get the Bot User OAuth Token (starts with `xoxb-`).

### 3. Local Deployment

1.  Clone the repository.
2.  Create a `.env` file with the following content:
    ```env
    QWEATHER_KEY=your_qweather_api_key
    SLACK_BOT_TOKEN=xoxb-your-bot-token
    SLACK_APP_TOKEN=xapp-your-app-token
    ```
3.  Run the startup script:
    ```bash
    ./start.sh
    ```

## Command List

| Command                           | Description                             |
| :-------------------------------- | :-------------------------------------- |
| `/asachan-start`                  | Subscribe to daily weather push         |
| `/asachan-unsub`                  | Unsubscribe                             |
| `/asachan-now`                    | Get current real-time weather           |
| `/asachan-status`                 | View current settings (city, time)      |
| `/asachan-setcity [City]`         | Set your target city                    |
| `/asachan-settime [HH:MM]`        | Set daily push time (Asia/Shanghai)     |
| `/asachan-ping`                   | Test command, returns Pong!             |
| `/asachan-up`                     | Check if the bot is online              |
| `/asachan-settimezone [Timezone]` | Set your timezone (e.g., Asia/Shanghai) |

## License

This project is licensed under the GNU Affero General Public License v3.0. See the [LICENSE](LICENSE) file for details.
