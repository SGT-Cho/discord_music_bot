#!/usr/bin/env python3
"""
Opus library load test script
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("Opus Library Load Test")
print("=" * 60)

# discord.py import
try:
    import discord
    print("✅ discord.py imported successfully")
except ImportError as e:
    print(f"❌ Failed to import discord.py: {e}")
    sys.exit(1)

# Check initial Opus status
print(f"\nInitial Opus status: {discord.opus.is_loaded()}")

# Try to load Opus using our improved function
from src.audio.ffmpeg_optimizer import FFmpegOptimizer

print("\nRunning FFmpegOptimizer.validate_opus_loaded()...")
result = FFmpegOptimizer.validate_opus_loaded()

if result:
    print(f"✅ Opus library loaded successfully!")
    print(f"Final Opus status: {discord.opus.is_loaded()}")
else:
    print(f"❌ Failed to load Opus library")
    print("Trying paths manually...")
    
    # Try manual paths
    test_paths = [
        '/opt/homebrew/lib/libopus.0.dylib',
        '/opt/homebrew/lib/libopus.dylib',
        '/opt/homebrew/Cellar/opus/1.5.2/lib/libopus.0.dylib',
        '/opt/homebrew/Cellar/opus/1.5.2/lib/libopus.dylib'
    ]
    
    for path in test_paths:
        if os.path.exists(path):
            print(f"  File exists: {path}")
            try:
                discord.opus.load_opus(path)
                print(f"  ✅ Loaded successfully: {path}")
                break
            except Exception as e:
                print(f"  ❌ Load failed: {e}")
        else:
            print(f"  File not found: {path}")

print("\n" + "=" * 60)
print(f"Final Opus load status: {discord.opus.is_loaded()}")
print("=" * 60)