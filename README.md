A python program the mimics the ZeDMD hardware to put a virtual DMD on your PC. Uses Python and Tkinter.  Easily swap whatever UI (gtk, kde, sdl) you want.

<img width="826" height="305" alt="Screenshot From 2025-10-13 15-32-12" src="https://github.com/user-attachments/assets/31d2a761-57f2-4df8-bf3a-51847cb2bcd6" />

<img width="826" height="305" alt="Screenshot From 2025-10-13 15-32-22" src="https://github.com/user-attachments/assets/b55dd8bf-f84a-4f78-8f86-1da144540fb0" />

## Execute

For MP4 recording support, install the optional video writer dependencies:
```
python3 -m pip install imageio imageio-ffmpeg
```

Run as root because vpx hits the port 80 first for settings.  To run on 80 you need root.
```
sudo python3 zedmd_udp_renderer.py
```

If it is run without access to TCP port 80, VPX can still send UDP frame data to port 3333, but it may log:
```
ZeDMD version could not be detected
```
The renderer serves `GET /handshake`, `GET /get_version`, and the fallback width/height/protocol endpoints on TCP port 80 for the ZeDMD WiFi probe.
By default it binds UDP and TCP to all IPv4 interfaces with `--bind-host 0.0.0.0`.
The terminal logs TCP connections, HTTP version requests, and the first UDP packets so you can confirm whether VPX is reaching the renderer.

The window includes a `Start MP4` / `Stop MP4` button. Recordings are saved to `recordings/` by default.

You can change the output directory or frame rate:
```
sudo python3 zedmd_udp_renderer.py --video-dir captures --video-fps 30
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
