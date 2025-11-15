# NotiDeep Discord Bot

A Discord bot that creates customizable notification embeds with buttons. Users can click buttons to trigger various notification actions like DMing users, pinging roles, or sending channel alerts.

## Features

✅ **Slash Command** - Create embeds with buttons  
✅ **Multiple Notification Types**:
   - DM a specific user
   - Ping a user in a channel
   - Ping a role in a channel
   - Both DM + channel alert
✅ **Fully Configurable** - Each command run is independently configured
✅ **Easy Setup** - Get running in minutes

## Installation

### 1. Prerequisites

- Node.js (v16.9.0 or higher)
- A Discord Bot Token
- Bot Client ID

### 2. Setup Steps

1. **Install dependencies:**
   ```bash
   npm install
   ```

2. **Configure the bot:**
   - Open `config.json`
   - Replace `YOUR_BOT_TOKEN` with your Discord bot token
   - Replace `YOUR_CLIENT_ID` with your bot's client ID

3. **Get your Discord Bot Token:**
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application or select an existing one
   - Go to the "Bot" section
   - Copy the token
   - Enable "Message Content Intent" under Privileged Gateway Intents

4. **Get your Client ID:**
   - In the same Developer Portal, go to "General Information"
   - Copy the Application ID (this is your Client ID)

5. **Invite your bot:**
   - Go to OAuth2 > URL Generator
   - Select scopes: `bot` and `applications.commands`
   - Select bot permissions: `Send Messages`, `Embed Links`, `Read Message History`
   - Copy the generated URL and open it in your browser to invite the bot

6. **Run the bot:**
   ```bash
   npm start
   ```

## Usage

### Command Syntax

```
/notifysetup 
  title:"Your Title"
  description:"Your Description"
  target_type:[DM a User | Ping User in Channel | Ping Role in Channel | DM + Channel]
  target_id:USER_ID_OR_ROLE_ID
  post_channel:#channel
  notify_channel:#channel (optional, required for channel notifications)
```

### Examples

**Example 1: DM a User**
```
/notifysetup 
  title:"Need Help"
  description:"Click to alert the owner"
  target_type:DM a User
  target_id:123456789012345678
  post_channel:#general
```

**Example 2: Ping Role in Channel**
```
/notifysetup 
  title:"Help Needed"
  description:"Click to notify staff"
  target_type:Ping Role in Channel
  target_id:987654321098765432
  post_channel:#helpdesk
  notify_channel:#staff-alerts
```

**Example 3: Both DM + Channel Alert**
```
/notifysetup 
  title:"Dual Alert"
  description:"Click below to notify"
  target_type:DM + Channel
  target_id:123456789012345678
  post_channel:#announcements
  notify_channel:#alerts
```

## How to Get User/Role IDs

1. **Enable Developer Mode** in Discord:
   - User Settings > Advanced > Developer Mode

2. **Get User ID:**
   - Right-click on a user > Copy User ID

3. **Get Role ID:**
   - Right-click on a role > Copy Role ID

## Notes

- The bot stores button configurations in memory. If the bot restarts, existing buttons will stop working (new buttons will work fine).
- Make sure the bot has proper permissions in all channels it needs to access.
- For DM notifications, the bot must share a server with the target user.

## Troubleshooting

**Bot doesn't respond to commands:**
- Make sure the bot is online
- Check that commands were deployed (you should see "Successfully reloaded application (/) commands" in console)
- Try re-inviting the bot with `applications.commands` scope

**Button doesn't work:**
- Make sure the bot has permission to send messages in the target channel
- Check that the target ID is correct
- Verify the bot has permission to DM users (if using DM notifications)

**Permission errors:**
- Ensure the bot has "Send Messages" and "Embed Links" permissions
- For DM notifications, the bot must be able to send DMs

## License

ISC

