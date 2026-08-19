#!/usr/bin/env python3
"""Audio playback test script"""

import sys
import os
import time
import asyncio

# Add project path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.audio import FFmpegOptimizer
from src.utils import performance_monitor

def test_ffmpeg_optimizer():
    """Test the FFmpeg Optimizer"""
    print("🔧 Testing FFmpeg Optimizer...")
    
    # 1. Opus load test
    opus_loaded = FFmpegOptimizer.validate_opus_loaded()
    print(f"{'✅' if opus_loaded else '❌'} Opus library loaded: {opus_loaded}")
    
    # 2. Option generation test
    for source_type in ['youtube', 'spotify', 'local']:
        options = FFmpegOptimizer.get_optimized_options(source_type)
        print(f"✅ {source_type} options generated:")
        print(f"   Before: {options['before_options']}")
        print(f"   Options: {options['options']}")
    
    return True

async def test_latency_measurement():
    """Latency measurement test"""
    print("\n⏱️ Testing Latency Measurement...")
    
    # Measure latency with mock operations
    for i in range(3):
        performance_monitor.start_timer("test_operation")
        await asyncio.sleep(0.1 * (i + 1))  # 0.1s, 0.2s, 0.3s
        elapsed = performance_monitor.end_timer("test_operation")
        print(f"✅ Test operation {i+1} took {elapsed:.3f}s")
    
    # Check average latency
    avg_latency = performance_monitor.get_average_latency("test_operation")
    print(f"✅ Average latency: {avg_latency:.3f}s")
    
    # Check metrics
    metrics = performance_monitor.get_metrics()
    print(f"✅ Metrics collected: {list(metrics.keys())}")
    
    return True

def test_audio_source_creation():
    """Audio source creation test (without a real URL)"""
    print("\n🎵 Testing Audio Source Creation...")

    # Creating a real source needs a reachable URL, so this only covers the
    # options every source type is built with.
    for source_type in ['youtube', 'spotify', 'local']:
        options = FFmpegOptimizer.get_optimized_options(source_type)
        assert 'options' in options, f"{source_type} produced no output options"
        assert 'before_options' in options, f"{source_type} produced no before_options"
        print(f"✅ {source_type} audio source options ready")

async def main():
    """Main test function"""
    print("🚀 Audio Playback Test Suite\n")
    
    # 1. FFmpeg Optimizer test
    test_ffmpeg_optimizer()
    
    # 2. Latency measurement test
    await test_latency_measurement()
    
    # 3. Audio source creation test
    test_audio_source_creation()
    
    print("\n✅ All audio playback tests completed!")
    print("\nNote: For full integration testing, run the bot and use:")
    print("  /play <YouTube URL>")
    print("  /performance  (to see performance metrics)")

if __name__ == "__main__":
    asyncio.run(main())