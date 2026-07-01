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
import datetime
import tkinter as tk
from tkinter import messagebox
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

ZEDMD_VERSION = "5.1.8"
VPX_HANDSHAKE_RESPONSE = b"128|32|5.1.8|1|UDP|3333|5|1216|15|0|0|0|16|8|30|0|vidmd|0|0|58|0|0"


class ZeDMDRenderer(tk.Tk):
    def __init__(self, udp_port=3333, debug=False, frameless=False, record_dir=None,
                 video_dir='recordings', video_fps=30, settings_port=80, bind_host='0.0.0.0'):
        super().__init__()
        self.title(f'ZeDMD UDP Renderer (listening {udp_port})')
        self.frame = np.zeros((TOTAL_HEIGHT, TOTAL_WIDTH, 3), dtype=np.uint8)

        self.canvas = tk.Canvas(self, width=TOTAL_WIDTH*SCALE, height=TOTAL_HEIGHT*SCALE, highlightthickness=0)
        self.canvas.pack()

        if not frameless:
            btn_frame = tk.Frame(self)
            btn_frame.pack(fill=tk.X)

            tk.Button(btn_frame, text='Save', command=self.save_frame).pack(side=tk.LEFT)
            self.start_record_button = tk.Button(btn_frame, text='Start MP4', command=self.start_video_recording)
            self.start_record_button.pack(side=tk.LEFT)
            self.stop_record_button = tk.Button(btn_frame, text='Stop MP4', command=self.stop_video_recording, state=tk.DISABLED)
            self.stop_record_button.pack(side=tk.LEFT)
            tk.Button(btn_frame, text='Clear', command=self.clear_screen).pack(side=tk.LEFT)
            tk.Button(btn_frame, text='Quit', command=self.on_close).pack(side=tk.RIGHT)

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
        self.video_dir = video_dir
        self.video_fps = video_fps
        self.settings_port = settings_port
        self.bind_host = bind_host
        self.video_writer = None
        self.video_path = None
        self.video_frame_count = 0
        self._pending_packets = []
        self._frame_counter = 0
        self._udp_packet_count = 0
        self._udp_byte_count = 0
        self._tcp_connection_count = 0
        self._http_request_count = 0
        self._partial_buffer = b''  # Accumulate any partial packets

        if self.record_dir:
            os.makedirs(self.record_dir, exist_ok=True)
            self.record_log_path = os.path.join(self.record_dir, 'frames.txt')
        else:
            self.record_log_path = None

        # UDP socket
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind((bind_host, udp_port))
        self.udp_sock.setblocking(False)
        self.log(f'Listening for ZeDMD frames on UDP {bind_host}:{udp_port}')

        # TCP handshake socket
        self.tcp_sock = None
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            tcp_sock.bind((bind_host, settings_port))
            self.tcp_sock = tcp_sock
            self.tcp_sock.listen(5)
            threading.Thread(target=self.tcp_listener, daemon=True).start()
            self.log(f'Listening for ZeDMD version/settings handshake on TCP {bind_host}:{settings_port}')
            self.log(f'Version endpoint ready: http://<this-host>/get_version -> {ZEDMD_VERSION}')
        except Exception as e:
            tcp_sock.close()
            self.log(
                f'WARNING: Could not listen on TCP {bind_host}:{settings_port} for the ZeDMD '
                f'version/settings handshake: {e}'
            )
            self.log(
                'VPX may log "ZeDMD version could not be detected". '
                'Run this program with sudo, or allow Python to bind low ports.'
            )

        self.after(10, self.poll_udp)

        img = self.get_display_image()
        self.tkimg = ImageTk.PhotoImage(img)
        self.image_id = self.canvas.create_image(0,0,anchor=tk.NW,image=self.tkimg)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if frameless:
            try:
                self.overrideredirect(True)
                self.bind('<Escape>', lambda e: self.on_close())
                self.bind('<Button-3>', lambda e: self.on_close())
            except Exception:
                pass

    def log(self, message):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        print(f'{timestamp} {message}', flush=True)

    # -------------------- UDP Polling --------------------
    def poll_udp(self):
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(65536)
            except BlockingIOError:
                break
            except Exception as e:
                self.log(f'UDP recv error: {e}')
                break

            self.log_udp_packet(addr, data)

            if self.record_dir:
                printable = ''.join((chr(b) if 32<=b<=126 else '.') for b in data)
                self._pending_packets.append((printable, data.hex()))

            # Append incoming data to the partial buffer
            self._partial_buffer += data
            self.process_buffer()
        self.after(10, self.poll_udp)

    def log_udp_packet(self, addr, data):
        self._udp_packet_count += 1
        self._udp_byte_count += len(data)

        if self.debug or self._udp_packet_count <= 10 or self._udp_packet_count % 300 == 0:
            prefix = data[:16].hex()
            ascii_prefix = ''.join((chr(b) if 32 <= b <= 126 else '.') for b in data[:16])
            self.log(
                f'UDP packet #{self._udp_packet_count} from {addr[0]}:{addr[1]} '
                f'bytes={len(data)} total_bytes={self._udp_byte_count} '
                f'prefix_hex={prefix} prefix_ascii={ascii_prefix}'
            )

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
                    self.log(f"Failed to decompress CMD{cmd}: {e}")
                    self.clear_screen()
                    self.update_display()
                    pos = body_end
                    continue

            if cmd == CMD_ZONES:
                if self.debug:
                    self.log(f"CMD_ZONES: payload_bytes={len(body)} compressed={compressed}")
                self.apply_zones(body)
            elif cmd == CMD_RENDER:
                if self.debug:
                    self.log("CMD_RENDER: updating display")
                self.update_display()
            elif cmd == CMD_CLEAR:
                if self.debug:
                    self.log("CMD_CLEAR: clearing display")
                self.clear_screen()
                self.update_display()
            else:
                if self.debug:
                    self.log(f"Unknown CMD {cmd}")

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
    def get_display_image(self):
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
        return Image.fromarray(adjusted, 'RGB').resize((img_w, img_h), RESAMPLE_NEAREST)

    def update_display(self):
        img = self.get_display_image()

        self.canvas.config(width=img.width, height=img.height)
        self.tkimg = ImageTk.PhotoImage(img)
        try:
            self.canvas.itemconfig(self.image_id, image=self.tkimg)
        except Exception:
            self.image_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tkimg)

        self.record_video_frame(img)

    # -------------------- TCP --------------------
    def tcp_listener(self):
        while True:
            if self.tcp_sock is None:
                return
            try:
                conn, addr = self.tcp_sock.accept()
            except OSError:
                return
            self._tcp_connection_count += 1
            self.log(f'TCP connection #{self._tcp_connection_count} from {addr[0]}:{addr[1]}')
            threading.Thread(target=self.handle_tcp_client, args=(conn,addr), daemon=True).start()

    def handle_tcp_client(self, conn, addr):
        try:
            conn.settimeout(1.0)
            data = b''
            try:
                data = conn.recv(4096)
            except socket.timeout:
                self.log(f'TCP {addr[0]}:{addr[1]} sent no data before timeout; sending raw handshake')

            if data.startswith(b'GET '):
                request_line = data.split(b'\r\n', 1)[0].decode('ascii', errors='replace')
                self.log(f'TCP {addr[0]}:{addr[1]} HTTP request: {request_line}')
                self.handle_http_request(conn, data)
                return

            if data:
                prefix = data[:32].hex()
                ascii_prefix = ''.join((chr(b) if 32 <= b <= 126 else '.') for b in data[:32])
                self.log(
                    f'TCP {addr[0]}:{addr[1]} non-HTTP first_bytes={len(data)} '
                    f'prefix_hex={prefix} prefix_ascii={ascii_prefix}; sending raw handshake'
                )
            conn.send(VPX_HANDSHAKE_RESPONSE+b"\n")
            self.log(f'TCP {addr[0]}:{addr[1]} sent raw handshake: {VPX_HANDSHAKE_RESPONSE.decode("ascii")}')
        finally:
            conn.close()
            self.log(f'TCP connection from {addr[0]}:{addr[1]} closed')

    def handle_http_request(self, conn, data):
        try:
            request_line = data.split(b'\r\n', 1)[0].decode('ascii', errors='replace')
            parts = request_line.split()
            path = parts[1] if len(parts) >= 2 else '/'
            self._http_request_count = self.__dict__.get('_http_request_count', 0) + 1

            payload = self.get_http_payload(path)
            if payload is not None:
                self.log(f'HTTP request #{self._http_request_count}: GET {path} -> 200 {payload}')
                self.send_http_response(conn, 200, 'OK', payload, 'text/plain')
            else:
                self.log(f'HTTP request #{self._http_request_count}: GET {path} -> 404')
                self.send_http_response(conn, 404, 'Not Found', 'Not Found', 'text/plain')
        except Exception as e:
            self.log(f'HTTP request handling failed: {e}')
            self.send_http_response(conn, 500, 'Internal Server Error', str(e), 'text/plain')

    def send_http_response(self, conn, status_code, status_text, body, content_type):
        body_bytes = body.encode('utf-8')
        headers = (
            f'HTTP/1.1 {status_code} {status_text}\r\n'
            'connection: close\r\n'
            'accept-ranges: none\r\n'
            f'content-length: {len(body_bytes)}\r\n'
            f'content-type: {content_type}\r\n'
            '\r\n'
        ).encode('ascii')
        conn.sendall(headers + body_bytes)

    def get_http_payload(self, path):
        if path == '/handshake':
            return VPX_HANDSHAKE_RESPONSE.decode('ascii')
        if path == '/get_version':
            return ZEDMD_VERSION
        if path == '/get_width':
            return str(TOTAL_WIDTH)
        if path == '/get_height':
            return str(TOTAL_HEIGHT)
        if path == '/get_s3':
            return '1'
        if path == '/get_protocol':
            return 'UDP'
        if path == '/get_port':
            return str(self.udp_sock.getsockname()[1])
        if path == '/get_udp_delay':
            return '5'
        return None

    # -------------------- Buttons --------------------
    def save_frame(self):
        Image.fromarray(self.frame, 'RGB').save('zedmd_capture_renderer.png')
        self.log('Saved zedmd_capture_renderer.png')

    def start_video_recording(self):
        if self.video_writer is not None:
            self.log(f'MP4 recording is already running: {self.video_path}')
            return

        try:
            import imageio.v2 as imageio
        except ImportError:
            self.log('MP4 recording unavailable: imageio or imageio-ffmpeg is not installed')
            messagebox.showerror(
                'MP4 recording unavailable',
                'Install imageio and imageio-ffmpeg to enable MP4 recording:\n\n'
                'python3 -m pip install imageio imageio-ffmpeg'
            )
            return

        os.makedirs(self.video_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.video_path = os.path.join(self.video_dir, f'zedmd_recording_{timestamp}.mp4')
        self.video_frame_count = 0

        try:
            self.video_writer = imageio.get_writer(
                self.video_path,
                fps=self.video_fps,
                codec='libx264',
                quality=8,
                macro_block_size=1,
            )
        except Exception as e:
            self.video_writer = None
            self.video_path = None
            self.log(f'Could not start MP4 recording: {e}')
            messagebox.showerror('MP4 recording unavailable', f'Could not start MP4 recording:\n\n{e}')
            return

        self.set_recording_buttons(recording=True)
        self.record_video_frame(self.get_display_image())
        self.log(f'Started MP4 recording: {self.video_path}')

    def stop_video_recording(self):
        if self.video_writer is None:
            return

        video_path = self.video_path
        try:
            self.video_writer.close()
            self.log(f'Saved MP4 recording: {video_path} ({self.video_frame_count} frames)')
        finally:
            self.video_writer = None
            self.video_path = None
            self.video_frame_count = 0
            self.set_recording_buttons(recording=False)

    def record_video_frame(self, img):
        if self.video_writer is None:
            return

        try:
            frame = np.asarray(img.convert('RGB'))
            pad_h = frame.shape[0] % 2
            pad_w = frame.shape[1] % 2
            if pad_h or pad_w:
                frame = np.pad(frame, ((0, pad_h), (0, pad_w), (0, 0)), mode='constant')
            self.video_writer.append_data(frame)
            self.video_frame_count += 1
            if self.video_frame_count == 1 or self.video_frame_count % 300 == 0:
                self.log(f'Wrote MP4 frame #{self.video_frame_count} to {self.video_path}')
        except Exception as e:
            self.stop_video_recording()
            messagebox.showerror('MP4 recording stopped', f'Could not write MP4 frame:\n\n{e}')

    def set_recording_buttons(self, recording):
        if hasattr(self, 'start_record_button'):
            self.start_record_button.config(state=tk.DISABLED if recording else tk.NORMAL)
        if hasattr(self, 'stop_record_button'):
            self.stop_record_button.config(state=tk.NORMAL if recording else tk.DISABLED)

    def clear_screen(self):
        self.frame.fill(0)
        self.update_display()

    def on_close(self):
        self.stop_video_recording()
        if self.tcp_sock is not None:
            self.tcp_sock.close()
        self.udp_sock.close()
        self.destroy()


# -------------------- Main --------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=3333)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--frameless', action='store_true')
    parser.add_argument('--record-dir', type=str, default=None)
    parser.add_argument('--video-dir', type=str, default='recordings')
    parser.add_argument('--video-fps', type=int, default=30)
    parser.add_argument('--settings-port', type=int, default=80)
    parser.add_argument('--bind-host', type=str, default='0.0.0.0')
    args = parser.parse_args()

    app = ZeDMDRenderer(
        udp_port=args.port,
        debug=args.debug,
        frameless=args.frameless,
        record_dir=args.record_dir,
        video_dir=args.video_dir,
        video_fps=args.video_fps,
        settings_port=args.settings_port,
        bind_host=args.bind_host
    )
    app.mainloop()
