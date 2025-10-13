#!/usr/bin/env python3
"""
ZeDMD UDP renderer 
"""

import argparse
import socket
import struct
import zlib
import threading
import os
import tkinter as tk
from PIL import Image, ImageTk
import numpy as np

try:
    RESAMPLE_NEAREST = Image.Resampling.NEAREST
except Exception:
    try:
        RESAMPLE_NEAREST = Image.NEAREST
    except Exception:
        RESAMPLE_NEAREST = 0

# -------------------- Global Defaults --------------------
DEFAULT_LUMINOSITY = 100  # percent (100 = normal, <100 darker, >100 brighter)

# Display constants
TOTAL_WIDTH = 128
TOTAL_HEIGHT = 32
NUM_ZONES_X = 16
NUM_ZONES_Y = 8
ZONE_WIDTH = TOTAL_WIDTH // NUM_ZONES_X
ZONE_HEIGHT = TOTAL_HEIGHT // NUM_ZONES_Y
ZONE_PIXELS = ZONE_WIDTH * ZONE_HEIGHT

CTRL1 = b'ZeDMD'
CTRL2 = b'FRAMEZeDMD'

CMD_ZONES = 0x05
CMD_RENDER = 0x06
CMD_CLEAR = 0x0A

SCALE = 3

VPX_HANDSHAKE_RESPONSE = b"128|32|5.1.7|1|UDP|3333|5|1216|15|0|0|0|16|8|30|0|vidmd|0|58"


class ZeDMDRenderer(tk.Tk):
    def __init__(self, udp_port=3333, debug=False, frameless=False, record_dir=None):
        super().__init__()
        self.title(f'ZeDMD UDP Renderer (listening {udp_port})')
        self.frame = np.zeros((TOTAL_HEIGHT, TOTAL_WIDTH, 3), dtype=np.uint8)

        self.canvas = tk.Canvas(self, width=TOTAL_WIDTH*SCALE, height=TOTAL_HEIGHT*SCALE, highlightthickness=0)
        self.canvas.pack()

        if not frameless:
            btn_frame = tk.Frame(self)
            btn_frame.pack(fill=tk.X)

            tk.Button(btn_frame, text='Save', command=self.save_frame).pack(side=tk.LEFT)
            tk.Button(btn_frame, text='Clear', command=self.clear_screen).pack(side=tk.LEFT)

            # Luminosity slider
            self.luminosity = tk.IntVar(value=DEFAULT_LUMINOSITY)
            tk.Label(btn_frame, text="Luminosity").pack(side=tk.LEFT, padx=5)
            tk.Scale(btn_frame, from_=10, to=200, orient=tk.HORIZONTAL,
                     variable=self.luminosity, command=lambda v: self.update_display(),
                     length=200).pack(side=tk.LEFT)

        else:
            self.luminosity = tk.IntVar(value=DEFAULT_LUMINOSITY)

        self.debug = debug
        self.frameless = frameless
        self.record_dir = record_dir
        self._pending_packets = []
        self._frame_counter = 0
        self._partial_buffer = b''  # Accumulate any partial packets

        if self.record_dir:
            os.makedirs(self.record_dir, exist_ok=True)
            self.record_log_path = os.path.join(self.record_dir, 'frames.txt')
        else:
            self.record_log_path = None

        # UDP socket
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind(('0.0.0.0', udp_port))
        self.udp_sock.setblocking(False)

        # TCP handshake socket
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.tcp_sock.bind(("0.0.0.0", 80))
            self.tcp_sock.listen(5)
            threading.Thread(target=self.tcp_listener, daemon=True).start()
        except Exception:
            pass

        self.after(10, self.poll_udp)

        img = Image.fromarray(self.frame, 'RGB').resize((TOTAL_WIDTH*SCALE, TOTAL_HEIGHT*SCALE), RESAMPLE_NEAREST)
        self.tkimg = ImageTk.PhotoImage(img)
        self.image_id = self.canvas.create_image(0,0,anchor=tk.NW,image=self.tkimg)

        if frameless:
            try:
                self.overrideredirect(True)
                self.bind('<Escape>', lambda e: self.destroy())
                self.bind('<Button-3>', lambda e: self.destroy())
            except Exception:
                pass

    # -------------------- UDP Polling --------------------
    def poll_udp(self):
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(65536)
            except BlockingIOError:
                break
            except Exception as e:
                print('UDP recv error:', e)
                break

            if self.record_dir:
                printable = ''.join((chr(b) if 32<=b<=126 else '.') for b in data)
                self._pending_packets.append((printable, data.hex()))

            # Append incoming data to the partial buffer
            self._partial_buffer += data
            self.process_buffer()
        self.after(10, self.poll_udp)

    # -------------------- Packet Processing --------------------
    def process_buffer(self):
        buf = self._partial_buffer
        pos = 0
        while pos < len(buf):
            # Find next control prefix
            idx1 = buf.find(CTRL1, pos)
            idx2 = buf.find(CTRL2, pos)
            if idx1 != -1 and (idx2 == -1 or idx1 <= idx2):
                idx = idx1
                prefix_len = len(CTRL1)
            elif idx2 != -1:
                idx = idx2
                prefix_len = len(CTRL2)
            else:
                break

            hdr_off = idx + prefix_len
            if hdr_off + 4 > len(buf):
                break
            try:
                cmd, size_be, compressed = struct.unpack('>BHB', buf[hdr_off:hdr_off+4])
            except struct.error:
                break
            size = size_be
            body_off = hdr_off + 4
            body_end = body_off + size
            if body_end > len(buf):
                break
            body = buf[body_off:body_end]

            if compressed:
                try:
                    body = zlib.decompress(body)
                except Exception as e:
                    print(f"Failed to decompress CMD{cmd}: {e}")
                    self.clear_screen()
                    self.update_display()
                    pos = body_end
                    continue

            if cmd == CMD_ZONES:
                self.apply_zones(body)
            elif cmd == CMD_RENDER:
                self.update_display()
            elif cmd == CMD_CLEAR:
                if self.debug:
                    print("CMD_CLEAR: clearing display")
                self.clear_screen()
                self.update_display()
            else:
                if self.debug:
                    print(f"Unknown CMD {cmd}")

            pos = body_end

        self._partial_buffer = buf[pos:]

    # -------------------- Zone Handling --------------------
    def apply_zones(self, payload: bytes):
        idx = 0
        while idx < len(payload):
            b = payload[idx]
            idx += 1
            if b >= 128:
                zone_index = b - 128
                if 0 <= zone_index < NUM_ZONES_X*NUM_ZONES_Y:
                    self.clear_zone(zone_index)
                continue
            zone_index = b
            expected = ZONE_PIXELS*2
            if idx + expected > len(payload):
                break
            zone_data = payload[idx:idx+expected]
            idx += expected
            self.write_zone(zone_index, zone_data)

    def clear_zone(self, zone_index):
        zx = zone_index % NUM_ZONES_X
        zy = zone_index // NUM_ZONES_X
        x0, y0 = zx*ZONE_WIDTH, zy*ZONE_HEIGHT
        x1, y1 = x0+ZONE_WIDTH, y0+ZONE_HEIGHT
        self.frame[y0:y1, x0:x1] = 0

    def write_zone(self, zone_index, zone_data):
        zx = zone_index % NUM_ZONES_X
        zy = zone_index // NUM_ZONES_X
        x0, y0 = zx*ZONE_WIDTH, zy*ZONE_HEIGHT
        x1, y1 = x0+ZONE_WIDTH, y0+ZONE_HEIGHT

        arr = np.frombuffer(zone_data, dtype='<u2')
        if arr.size < ZONE_PIXELS:
            arr = np.pad(arr, (0, ZONE_PIXELS-arr.size), constant_values=0)
        elif arr.size > ZONE_PIXELS:
            arr = arr[:ZONE_PIXELS]

        arr = arr.reshape((ZONE_HEIGHT, ZONE_WIDTH))
        r5 = (arr >> 11) & 0x1F
        g6 = (arr >> 5) & 0x3F
        b5 = arr & 0x1F
        r = ((r5<<3)|(r5>>2)).astype(np.uint8)
        g = ((g6<<2)|(g6>>4)).astype(np.uint8)
        b = ((b5<<3)|(b5>>2)).astype(np.uint8)
        rgb = np.stack([r,g,b], axis=2)
        self.frame[y0:y1, x0:x1] = rgb
    
    

    # -------------------- Display --------------------
    def update_display(self):
        h, w, c = self.frame.shape
        expanded_h = h + (h-1)
        expanded_w = w + (w-1)
        expanded = np.zeros((expanded_h, expanded_w, c), dtype=np.uint8)
        expanded[::2, ::2] = self.frame

        # Apply luminosity scaling
        factor = self.luminosity.get() / 100.0
        adjusted = np.clip(expanded.astype(np.float32) * factor, 0, 255).astype(np.uint8)

        img_w = expanded_w * SCALE
        img_h = expanded_h * SCALE
        img = Image.fromarray(adjusted, 'RGB').resize((img_w, img_h), RESAMPLE_NEAREST)

        self.canvas.config(width=img_w, height=img_h)
        self.tkimg = ImageTk.PhotoImage(img)
        try:
            self.canvas.itemconfig(self.image_id, image=self.tkimg)
        except Exception:
            self.image_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tkimg)

    # -------------------- TCP --------------------
    def tcp_listener(self):
        while True:
            conn, addr = self.tcp_sock.accept()
            threading.Thread(target=self.handle_tcp_client, args=(conn,addr), daemon=True).start()

    def handle_tcp_client(self, conn, addr):
        try:
            conn.send(VPX_HANDSHAKE_RESPONSE+b"\n")
            while True:
                data = conn.recv(4096)
                if not data:
                    break
        finally:
            conn.close()

    # -------------------- Buttons --------------------
    def save_frame(self):
        Image.fromarray(self.frame, 'RGB').save('zedmd_capture_renderer.png')
        print('Saved zedmd_capture_renderer.png')

    def clear_screen(self):
        self.frame.fill(0)
        self.update_display()


# -------------------- Main --------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=3333)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--frameless', action='store_true')
    parser.add_argument('--record-dir', type=str, default=None)
    args = parser.parse_args()

    app = ZeDMDRenderer(
        udp_port=args.port,
        debug=args.debug,
        frameless=args.frameless,
        record_dir=args.record_dir
    )
    app.mainloop()
