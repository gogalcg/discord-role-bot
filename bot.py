import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_TO_REQUEST = int(os.getenv("ROLE_TO_REQUEST"))
ROLE_CAN_APPROVE = int(os.getenv("ROLE_CAN_APPROVE"))
APPLICATION_CHANNEL_NAME = os.getenv("APPLICATION_CHANNEL_NAME")

# Database file for tracking role expirations
DB_FILE = "role_expirations.json"

def load_expirations():
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_expirations(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)

# Bot setup
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

class ApplicationModal(discord.ui.Modal, title='Подать заявку'):
    screenshot = discord.ui.TextInput(
        label='Скриншот из криминальной фракции',
        style=discord.TextStyle.long,
        placeholder='Вставьте ссылку на скриншот или опишите...',
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        
        # Find the application channel
        app_channel = None
        for channel in guild.text_channels:
            if channel.name.lower() == APPLICATION_CHANNEL_NAME.lower():
                app_channel = channel
                break
        
        if not app_channel:
            await interaction.response.send_message("Канал для заявок не найден!", ephemeral=True)
            return
        
        # Create a private thread
        thread = await app_channel.create_thread(
            name=f"Заявка от {user.display_name}",
            type=discord.ChannelType.private_thread,
            reason=f"Новая заявка от {user.id}"
        )
        
        # Add the applicant to the thread
        await thread.add_user(user)
        
        # Add users with approval role to the thread
        approval_role = guild.get_role(ROLE_CAN_APPROVE)
        if approval_role:
            for member in approval_role.members:
                await thread.add_user(member)
        
        # Send the application message in the thread
        embed = discord.Embed(
            title=f"Заявка от {user.display_name}",
            description=f"**Пользователь:** {user.mention}\n**ID:** {user.id}\n\n**Скриншот/Описание:**\n{self.screenshot.value}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
        
        view = ApplicationView(user.id, thread.id)
        await thread.send(embed=embed, view=view)
        
        await interaction.response.send_message(f"Ваша заявка создана! Проверьте ветку: {thread.mention}", ephemeral=True)

class ApplyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ApplicationModal()
        await interaction.response.send_modal(modal)

class ApplicationView(discord.ui.View):
    def __init__(self, applicant_id: int, thread_id: int):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.thread_id = thread_id

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, custom_id="approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user has approval role
        if not any(role.id == ROLE_CAN_APPROVE for role in interaction.user.roles):
            await interaction.response.send_message("У вас нет прав для одобрения заявок!", ephemeral=True)
            return
        
        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id)
        role_to_give = guild.get_role(ROLE_TO_REQUEST)
        
        if not applicant:
            await interaction.response.send_message("Пользователь не найден на сервере!", ephemeral=True)
            return
        
        if not role_to_give:
            await interaction.response.send_message("Роль не найдена!", ephemeral=True)
            return
        
        # Give the role
        await applicant.add_roles(role_to_give, reason="Заявка одобрена")
        
        # Calculate expiration time (7 days from now)
        expiration_time = datetime.now() + timedelta(days=7)
        
        # Save to database
        expirations = load_expirations()
        expirations[str(self.applicant_id)] = expiration_time.isoformat()
        save_expirations(expirations)
        
        # Update the message
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Статус", value=f"✅ Одобрено {interaction.user.mention}", inline=False)
        embed.add_field(name="Роль выдана", value=f"Роль будет снята {expiration_time.strftime('%d.%m.%Y %H:%M')}", inline=False)
        
        # Disable buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Send notification to applicant
        try:
            await applicant.send(f"Ваша заявка одобрена! Вам выдана роль. Она будет автоматически снята через 7 дней ({expiration_time.strftime('%d.%m.%Y %H:%M')}).")
        except:
            pass

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, custom_id="reject")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user has approval role
        if not any(role.id == ROLE_CAN_APPROVE for role in interaction.user.roles):
            await interaction.response.send_message("У вас нет прав для отклонения заявок!", ephemeral=True)
            return
        
        guild = interaction.guild
        thread = guild.get_thread(self.thread_id)
        
        # Update the message
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Статус", value=f"❌ Отклонено {interaction.user.mention}", inline=False)
        
        # Disable buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Send notification to applicant
        applicant = guild.get_member(self.applicant_id)
        if applicant:
            try:
                await applicant.send("Ваша заявка была отклонена.")
            except:
                pass
        
        # Delete the thread after a short delay
        await asyncio.sleep(5)
        if thread:
            await thread.delete()

@bot.event
async def on_ready():
    print(f'Бот запущен как {bot.user}')
    print(f'ID: {bot.user.id}')
    
    # Add persistent views
    bot.add_view(ApplyButtonView())
    
    try:
        synced = await bot.tree.sync()
        print(f'Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'Ошибка синхронизации команд: {e}')
    
    # Start the role expiration check task
    check_role_expirations.start()

@bot.event
async def on_guild_channel_create(channel):
    # Check if this is the application channel
    if channel.name.lower() == APPLICATION_CHANNEL_NAME.lower():
        # Send the application button message
        view = ApplyButtonView()
        
        embed = discord.Embed(
            title="Подача заявки",
            description="Нажмите кнопку ниже, чтобы подать заявку на получение роли.",
            color=discord.Color.blue()
        )
        
        await channel.send(embed=embed, view=view)

@bot.tree.command(name="setup", description="Настроить канал для заявок")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    await interaction.response.defer()
    
    guild = interaction.guild
    
    # Find or create the application channel
    app_channel = None
    for channel in guild.text_channels:
        if channel.name.lower() == APPLICATION_CHANNEL_NAME.lower():
            app_channel = channel
            break
    
    if not app_channel:
        # Create the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=False,
                create_public_threads=False,
                create_private_threads=False
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_threads=True,
                create_public_threads=True,
                create_private_threads=True
            )
        }
        app_channel = await guild.create_text_channel(
            APPLICATION_CHANNEL_NAME,
            overwrites=overwrites,
            reason="Канал для подачи заявок"
        )
    
    # Clear existing messages in the channel
    async for message in app_channel.history(limit=None):
        try:
            await message.delete()
        except:
            pass
    
    # Send the application button message
    view = ApplyButtonView()
    
    embed = discord.Embed(
        title="Подача заявки",
        description="Нажмите кнопку ниже, чтобы подать заявку на получение роли.",
        color=discord.Color.blue()
    )
    
    await app_channel.send(embed=embed, view=view)
    
    try:
        await interaction.followup.send(f"Канал для заявок настроен: {app_channel.mention}")
    except:
        try:
            await interaction.edit_original_response(content=f"Канал для заявок настроен: {app_channel.mention}")
        except:
            pass

@tasks.loop(minutes=5)
async def check_role_expirations():
    """Check for expired roles and remove them"""
    expirations = load_expirations()
    now = datetime.now()
    to_remove = []
    
    for user_id_str, expiration_str in expirations.items():
        expiration_time = datetime.fromisoformat(expiration_str)
        
        if now >= expiration_time:
            user_id = int(user_id_str)
            to_remove.append(user_id)
            
            # Remove role from user in all guilds
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    role = guild.get_role(ROLE_TO_REQUEST)
                    if role and role in member.roles:
                        await member.remove_roles(role, reason="Срок действия роли истек")
                        try:
                            await member.send("Срок действия вашей роли истек. Она была автоматически снята.")
                        except:
                            pass
    
    # Remove expired entries from database
    for user_id in to_remove:
        del expirations[str(user_id)]
    
    if to_remove:
        save_expirations(expirations)

@check_role_expirations.before_loop
async def before_check_role_expirations():
    await bot.wait_until_ready()

if __name__ == "__main__":
    bot.run(TOKEN)
