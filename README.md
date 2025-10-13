A python program the mimics the ZeDMD hardware to put a virtual DMD on your PC. Uses Python and Tkinter.  Easily swap whatever UI (gtk, kde, sdl) you want.

<img width="826" height="305" alt="Screenshot From 2025-10-13 15-32-12" src="https://github.com/user-attachments/assets/31d2a761-57f2-4df8-bf3a-51847cb2bcd6" />

<img width="826" height="305" alt="Screenshot From 2025-10-13 15-32-22" src="https://github.com/user-attachments/assets/b55dd8bf-f84a-4f78-8f86-1da144540fb0" />

## Execute

Run as root because vpx hits the port 80 first for settings.  To run on 80 you need root.
```
sudo python3 zedmd_udp_renderer.py
```

Then in your `VPinball.ini`:  Activate the DMDUtil plugin and set your IP address.
```
[Plugin.DMDUtil]
Enable = 1
LumTintR =
LumTintG =
LumTintB =
ZeDMD =
ZeDMDDevice =
ZeDMDDebug =
ZeDMDBrightness =
ZeDMDWiFi = 1
ZeDMDWiFiAddr = {{{ YOUR HOSTS IP}}}
Pixelcade =
PixelcadeDevice =
DumpDMDTxt =
DumpDMDRaw =
FindDisplays =
DMDServer =
DMDServerAddr =
DMDServerPort =
```
