# VoiceMeeter Banana Troubleshooting Guide

## Problem: TTS audio plays to VoiceMeeter but VRChat doesn't hear it

### Confirmed Working
✅ Application successfully outputs TTS audio to "Voicemeeter Input (VB-Audio Voicemeeter VAIO)" (device 14)
✅ Audio playback completes without errors
✅ Audio data is valid (24kHz, ~6 seconds for test phrase)

### Issue
❌ VRChat does not receive the audio from VoiceMeeter

---

## Solution Steps

### Step 1: Check VoiceMeeter Virtual Input Routing

1. **Open VoiceMeeter Banana**
2. **Locate "VOICEMEETER VAIO" section** (right side, Virtual Inputs)
3. **Check the A1 button** under VOICEMEETER VAIO:
   - It should be **GREEN/LIT** (enabled)
   - If it's **GRAY/OFF**, click it to enable
4. **Check the audio meter** for VOICEMEETER VAIO:
   - Run the test script: `python test_text_input_tts.py`
   - You should see the **green audio level meter moving** when TTS plays
   - If the meter doesn't move, the audio isn't reaching VoiceMeeter

### Step 2: Verify A1 Output Device

1. In VoiceMeeter, look at the **top-right corner**
2. Find **"A1"** hardware output section
3. Click the device name to open the dropdown
4. Select your **VRChat microphone device** (the one VRChat listens to)
   - Common choices:
     - "VoiceMeeter Output" (VB-Audio VoiceMeeter VAIO)
     - "VoiceMeeter Aux Output" (VB-Audio VoiceMeeter AUX VAIO)
     - Your physical microphone (if you want to mix real voice + TTS)

### Step 3: Configure VRChat Microphone

1. **In VRChat**, go to Settings → Audio
2. Set **Microphone** to the same device as VoiceMeeter A1 output:
   - If A1 = "VoiceMeeter Output" → VRChat mic = "VoiceMeeter Output"
   - If A1 = "VoiceMeeter Aux Output" → VRChat mic = "VoiceMeeter Aux Output"

### Step 4: Test the Audio Path

1. Run the test script:
   ```powershell
   python test_text_input_tts.py
   ```

2. **Watch VoiceMeeter**:
   - VOICEMEETER VAIO meter should move (green bars)
   - Master Section meter should move (shows A1 output)

3. **In VRChat**:
   - Check your microphone indicator (should light up when TTS plays)
   - Ask another player if they can hear you

---

## Common Issues

### Issue 1: VOICEMEETER VAIO meter doesn't move
**Cause**: Application isn't sending audio to the correct device
**Solution**:
- Check `config.json` → `tts.output_device` should be `14`
- Restart the application
- Run test script to verify

### Issue 2: VOICEMEETER VAIO meter moves but Master doesn't
**Cause**: A1 button is not enabled for VOICEMEETER VAIO
**Solution**: Click the A1 button under VOICEMEETER VAIO to enable routing

### Issue 3: VoiceMeeter works but VRChat doesn't hear
**Cause**: VRChat is using a different microphone
**Solution**:
- Check VRChat Settings → Audio → Microphone
- Must match VoiceMeeter A1 output device

### Issue 4: Want to use real microphone + TTS simultaneously
**Solution**:
1. In VoiceMeeter, add your physical microphone to **Hardware Input 1**
2. Enable **A1** for both:
   - Hardware Input 1 (your real mic)
   - VOICEMEETER VAIO (TTS audio)
3. Set VRChat microphone to VoiceMeeter A1 output device
4. Now VRChat will hear both your real voice and TTS

---

## Recommended VoiceMeeter Configuration

### For TTS Only (No Real Microphone)
```
VOICEMEETER VAIO (Virtual Input):
  A1: ✅ ENABLED (green)

A1 Output Device:
  → VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)

VRChat Microphone:
  → VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)
```

### For Real Microphone + TTS
```
Hardware Input 1:
  Device: Your Physical Microphone
  A1: ✅ ENABLED (green)

VOICEMEETER VAIO (Virtual Input):
  A1: ✅ ENABLED (green)

A1 Output Device:
  → VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)

VRChat Microphone:
  → VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)
```

---

## Testing Checklist

- [ ] VoiceMeeter Banana is running
- [ ] VOICEMEETER VAIO → A1 button is GREEN
- [ ] A1 output device is selected (not "---")
- [ ] VRChat microphone matches A1 output device
- [ ] Run `python test_text_input_tts.py`
- [ ] VOICEMEETER VAIO meter moves during playback
- [ ] Master Section meter moves during playback
- [ ] VRChat microphone indicator lights up
- [ ] Other players can hear the TTS audio

---

## Still Not Working?

### Check Windows Sound Settings
1. Right-click speaker icon → **Sound settings**
2. Go to **Advanced sound options** → **App volume and device preferences**
3. Find **Python** or **MioTranslator**
4. Verify output device is set to **VoiceMeeter Input**

### Restart Everything
1. Close VRChat
2. Close MioTranslator
3. Restart VoiceMeeter Banana (Menu → Restart Audio Engine)
4. Start VoiceMeeter → Start MioTranslator → Start VRChat

### Enable VoiceMeeter Logging
1. In VoiceMeeter, Menu → System Settings
2. Enable "Log to file"
3. Check logs for errors

---

## Contact Support

If you've followed all steps and it still doesn't work:
1. Take a screenshot of your VoiceMeeter configuration
2. Share your `config.json` (tts section)
3. Share the output of `python test_text_input_tts.py`
4. Report the issue with these details
