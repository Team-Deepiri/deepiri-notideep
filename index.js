const {
  Client,
  GatewayIntentBits,
  SlashCommandBuilder,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  EmbedBuilder,
  Routes,
  REST,
  Events
} = require('discord.js');

const { token, clientId } = require('./config.json');

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages
  ]
});

// Slash Command Definition
const commands = [
  new SlashCommandBuilder()
    .setName("notifysetup")
    .setDescription("Create a notification embed with button.")
    .addStringOption(option =>
      option.setName("title")
        .setDescription("Embed title")
        .setRequired(true))
    .addStringOption(option =>
      option.setName("description")
        .setDescription("Embed description")
        .setRequired(true))
    .addStringOption(option =>
      option.setName("target_type")
        .setDescription("Where the button sends the notification")
        .addChoices(
          { name: "DM a User", value: "dm" },
          { name: "Ping User in Channel", value: "user_channel" },
          { name: "Ping Role in Channel", value: "role_channel" },
          { name: "DM + Channel", value: "both" }
        )
        .setRequired(true))
    .addStringOption(option =>
      option.setName("target_id")
        .setDescription("User ID or Role ID depending on configuration")
        .setRequired(true))
    .addChannelOption(option =>
      option.setName("post_channel")
        .setDescription("Channel to send the embed with the button")
        .setRequired(true))
    .addChannelOption(option =>
      option.setName("notify_channel")
        .setDescription("Channel to send notification into (if applicable)")
        .setRequired(false))
].map(cmd => cmd.toJSON());

// Deploy Commands
async function deployCommands() {
  try {
    console.log('Started refreshing application (/) commands.');
    const rest = new REST({ version: '10' }).setToken(token);
    await rest.put(Routes.applicationCommands(clientId), { body: commands });
    console.log('Successfully reloaded application (/) commands.');
  } catch (error) {
    console.error('Error deploying commands:', error);
  }
}

// Initialize notification configs storage
client.notificationConfigs = {};

// Bot Ready
client.once(Events.ClientReady, async () => {
  console.log(`Logged in as ${client.user.tag}`);
  await deployCommands();
});

// Handle Interactions
client.on(Events.InteractionCreate, async interaction => {
  // Slash command
  if (interaction.isChatInputCommand()) {
    if (interaction.commandName === "notifysetup") {
      try {
        const title = interaction.options.getString("title");
        const description = interaction.options.getString("description");
        const targetType = interaction.options.getString("target_type");
        const targetId = interaction.options.getString("target_id");
        const postChannel = interaction.options.getChannel("post_channel");
        const notifyChannel = interaction.options.getChannel("notify_channel");

        // Validate target ID format
        if (!targetId.match(/^\d+$/)) {
          return interaction.reply({
            content: "❌ Invalid target ID. Must be a numeric User ID or Role ID.",
            ephemeral: true
          });
        }

        // Create embed
        const embed = new EmbedBuilder()
          .setTitle(title)
          .setDescription(description)
          .setColor(0x00A2FF)
          .setTimestamp();

        // Create button
        const button = new ButtonBuilder()
          .setCustomId(`notify_${interaction.id}`)
          .setLabel("Notify")
          .setStyle(ButtonStyle.Primary);

        const row = new ActionRowBuilder().addComponents(button);

        // Store configuration in memory
        client.notificationConfigs[`notify_${interaction.id}`] = {
          targetType,
          targetId,
          notifyChannelId: notifyChannel ? notifyChannel.id : null,
          createdAt: Date.now()
        };

        // Send embed to target channel
        await postChannel.send({ embeds: [embed], components: [row] });

        return interaction.reply({
          content: "✅ Notification button created successfully!",
          ephemeral: true
        });
      } catch (error) {
        console.error('Error creating notification setup:', error);
        return interaction.reply({
          content: "❌ An error occurred while creating the notification button.",
          ephemeral: true
        });
      }
    }
  }

  // Button click
  if (interaction.isButton()) {
    const config = client.notificationConfigs?.[interaction.customId];

    if (!config) {
      return interaction.reply({ 
        content: "❌ Configuration missing. This button may have expired.", 
        ephemeral: true 
      });
    }

    const { targetType, targetId, notifyChannelId } = config;

    try {
      // Notify logic
      if (targetType === "dm") {
        const user = await client.users.fetch(targetId);
        await user.send(`🔔 Someone clicked the button and notified you!`);
        return interaction.reply({ 
          content: "✅ User has been notified via DM.", 
          ephemeral: true 
        });
      }

      if (targetType === "user_channel") {
        if (!notifyChannelId) {
          return interaction.reply({ 
            content: "❌ Notification channel is required for this target type.", 
            ephemeral: true 
          });
        }
        const channel = client.channels.cache.get(notifyChannelId);
        if (!channel) {
          return interaction.reply({ 
            content: "❌ Notification channel not found.", 
            ephemeral: true 
          });
        }
        await channel.send(`<@${targetId}> 🔔 Someone pressed the button!`);
        return interaction.reply({ 
          content: "✅ User pinged in channel.", 
          ephemeral: true 
        });
      }

      if (targetType === "role_channel") {
        if (!notifyChannelId) {
          return interaction.reply({ 
            content: "❌ Notification channel is required for this target type.", 
            ephemeral: true 
          });
        }
        const channel = client.channels.cache.get(notifyChannelId);
        if (!channel) {
          return interaction.reply({ 
            content: "❌ Notification channel not found.", 
            ephemeral: true 
          });
        }
        await channel.send(`<@&${targetId}> 🔔 Someone pressed the button!`);
        return interaction.reply({ 
          content: "✅ Role pinged in channel.", 
          ephemeral: true 
        });
      }

      if (targetType === "both") {
        if (!notifyChannelId) {
          return interaction.reply({ 
            content: "❌ Notification channel is required for this target type.", 
            ephemeral: true 
          });
        }
        const channel = client.channels.cache.get(notifyChannelId);
        if (!channel) {
          return interaction.reply({ 
            content: "❌ Notification channel not found.", 
            ephemeral: true 
          });
        }
        const user = await client.users.fetch(targetId);
        
        await channel.send(`<@${targetId}> 🔔 A button was clicked!`);
        await user.send(`🔔 You were notified both in the channel AND here in DM.`);
        
        return interaction.reply({ 
          content: "✅ Both notifications sent.", 
          ephemeral: true 
        });
      }
    } catch (error) {
      console.error('Error handling button click:', error);
      return interaction.reply({ 
        content: "❌ An error occurred while sending the notification. Please check the target ID and permissions.", 
        ephemeral: true 
      });
    }
  }
});

// Error handling
client.on(Events.Error, error => {
  console.error('Discord client error:', error);
});

process.on('unhandledRejection', error => {
  console.error('Unhandled promise rejection:', error);
});

// Login
client.login(token).catch(error => {
  console.error('Failed to login:', error);
  process.exit(1);
});

