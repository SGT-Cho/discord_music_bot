#!/usr/bin/env python3
"""Bot startup test script"""

import sys
import os
import asyncio
import discord
from discord.ext import commands

# Add project path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that required modules import correctly"""
    print("Testing imports...")
    try:
        import yt_dlp
        print("✅ yt-dlp imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import yt-dlp: {e}")
        return False
    
    try:
        from dotenv import load_dotenv
        print("✅ python-dotenv imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import python-dotenv: {e}")
        return False
    
    try:
        from src.utils import ErrorHandler
        print("✅ ErrorHandler imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import ErrorHandler: {e}")
        return False
    
    return True

def test_config():
    """Test config file and environment variables"""
    print("\nTesting configuration...")
    from dotenv import load_dotenv
    load_dotenv()
    
    token = os.getenv("DISCORD_TOKEN") or os.getenv("discord_token")
    if not token:
        print("❌ Discord token not found in .env file")
        return False
    
    print("✅ Discord token found")
    
    # Opus library test
    if not discord.opus.is_loaded():
        opus_paths = [
            '/opt/homebrew/lib/libopus.dylib',
            '/usr/local/lib/libopus.dylib',
            'libopus.0.dylib',
            'libopus.dylib',
            'opus'
        ]
        loaded = False
        for path in opus_paths:
            try:
                discord.opus.load_opus(path)
                print(f"✅ Opus loaded from: {path}")
                loaded = True
                break
            except:
                continue
        if not loaded:
            print("⚠️ Opus library could not be loaded")
    else:
        print("✅ Opus already loaded")
    
    return True

async def test_bot_connection():
    """Bot connection test"""
    print("\nTesting bot connection...")
    from dotenv import load_dotenv
    load_dotenv()
    
    token = os.getenv("DISCORD_TOKEN") or os.getenv("discord_token")
    if not token:
        print("❌ Cannot test connection without token")
        return False
    
    intents = discord.Intents.default()
    intents.message_content = True
    
    bot = commands.Bot(command_prefix="/", intents=intents)
    
    @bot.event
    async def on_ready():
        print(f"✅ Bot connected as {bot.user}")
        await bot.close()
    
    try:
        await bot.start(token)
        return True
    except discord.LoginFailure:
        print("❌ Invalid token")
        return False
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Discord Music Bot Startup Test\n")
    
    # 1. Import test
    if not test_imports():
        print("\n❌ Import test failed. Please install required packages:")
        print("   pip install -r requirements.txt")
        sys.exit(1)
    
    # 2. Config test
    if not test_config():
        print("\n❌ Configuration test failed. Please check your .env file")
        sys.exit(1)
    
    # 3. Connection test (optional)
    response = input("\nDo you want to test bot connection? (y/n): ")
    if response.lower() == 'y':
        asyncio.run(test_bot_connection())
    
    print("\n✅ All basic tests passed! You can now run:")
    print("   python music_bot.py")

if __name__ == "__main__":
    main()
