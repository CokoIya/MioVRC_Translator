"""Test script for TTS playback functionality."""
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import sounddevice as sd
import numpy as np

print("=" * 60)
print("Testing TTS Playback Functionality")
print("=" * 60)

# Test 1: Probe supported sample rates
print("\n[Test 1] Probing supported sample rates...")
try:
    common_rates = [8000, 16000, 22050, 24000, 32000, 44100, 48000, 96000]
    supported = []

    for rate in common_rates:
        try:
            sd.check_output_settings(device=None, samplerate=rate, channels=1)
            supported.append(rate)
        except sd.PortAudioError:
            continue

    if supported:
        print(f"[PASS] Found {len(supported)} supported sample rates: {supported}")
    else:
        print("[FAIL] No supported sample rates found")
except Exception as e:
    print(f"[FAIL] Error probing sample rates: {e}")

# Test 2: Choose best sample rate
print("\n[Test 2] Testing sample rate selection...")
try:
    def choose_best_sample_rate(source_rate, supported):
        if source_rate in supported:
            return source_rate
        higher = [r for r in supported if r > source_rate]
        if higher:
            return min(higher)
        return max(supported)

    test_cases = [
        (24000, [16000, 44100, 48000], 44100),  # Edge TTS -> Voicemeeter
        (16000, [16000, 44100, 48000], 16000),  # Exact match
        (96000, [44100, 48000], 48000),         # Higher than all
    ]

    all_passed = True
    for source, supported_list, expected in test_cases:
        result = choose_best_sample_rate(source, supported_list)
        if result == expected:
            print(f"[PASS] {source}Hz -> {result}Hz (expected {expected}Hz)")
        else:
            print(f"[FAIL] {source}Hz -> {result}Hz (expected {expected}Hz)")
            all_passed = False

    if all_passed:
        print("[PASS] All sample rate selection tests passed")
except Exception as e:
    print(f"[FAIL] Error testing sample rate selection: {e}")

# Test 3: Audio resampling
print("\n[Test 3] Testing audio resampling...")
try:
    from scipy import signal

    # Create test audio (1 second at 24kHz)
    source_rate = 24000
    target_rate = 48000
    duration = 1.0

    t = np.linspace(0, duration, int(source_rate * duration))
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)  # 440Hz sine wave

    # Resample
    num_samples = int(len(audio) * target_rate / source_rate)
    resampled = signal.resample(audio, num_samples)

    expected_length = int(source_rate * duration * target_rate / source_rate)
    actual_length = len(resampled)

    if abs(actual_length - expected_length) < 10:  # Allow small rounding error
        print(f"[PASS] Resampled {len(audio)} samples ({source_rate}Hz) to {actual_length} samples ({target_rate}Hz)")
    else:
        print(f"[FAIL] Expected ~{expected_length} samples, got {actual_length}")
except Exception as e:
    print(f"[FAIL] Error testing resampling: {e}")

# Test 4: Device name resolution
print("\n[Test 4] Testing device name resolution...")
try:
    devices = sd.query_devices()
    output_devices = [d for d in devices if d['max_output_channels'] > 0]

    if output_devices:
        print(f"[PASS] Found {len(output_devices)} output devices:")
        for i, dev in enumerate(output_devices[:5]):  # Show first 5
            print(f"  - {dev['name']} (index {dev['index']})")
    else:
        print("[FAIL] No output devices found")
except Exception as e:
    print(f"[FAIL] Error querying devices: {e}")

# Test 5: PortAudio error classification
print("\n[Test 5] Testing PortAudio error handling...")
try:
    # Try to use an invalid device
    try:
        sd.check_output_settings(device=9999, samplerate=44100, channels=1)
        print("[FAIL] Should have raised PortAudioError for invalid device")
    except sd.PortAudioError as e:
        error_msg = str(e).lower()
        if "invalid" in error_msg or "device" in error_msg:
            print(f"[PASS] Correctly caught invalid device error: {e}")
        else:
            print(f"[WARN] Caught PortAudioError but message unclear: {e}")
except Exception as e:
    print(f"[FAIL] Unexpected error: {e}")

print("\n" + "=" * 60)
print("TTS Playback Tests Complete")
print("=" * 60)
