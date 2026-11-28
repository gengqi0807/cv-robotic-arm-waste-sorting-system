"""Start PC video services explicitly; importing this module starts nothing."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    path = ROOT / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def locate(tool, override):
    if override:
        path = Path(override)
        if not path.is_file():
            raise ValueError(f'{tool} 指定路径不存在')
        return path
    folder = Path('D:/tool') / ('ffmpeg' if tool == 'ffmpeg' else 'mediamtx')
    matches = sorted(folder.rglob(tool + '.exe'))
    if not matches:
        raise ValueError(f'未在 {folder} 找到 {tool}.exe，请配置对应路径')
    if len(matches) != 1:
        raise ValueError(f'{folder} 中存在多个 {tool}.exe，请用环境变量明确选择')
    return matches[0]


def configuration(pc_ip, stream):
    # Separate generated configuration; do not alter D:/tool's original YAML.
    return '\n'.join([
        'logLevel: info', 'api: false', 'metrics: false', 'pprof: false',
        'playback: false', 'rtmp: false', 'hls: false', 'srt: false',
        'rtsp: true', 'rtspAddress: 127.0.0.1:8554', 'rtspTransports: [tcp]',
        'webrtc: true', 'webrtcAddress: :8889',
        'webrtcLocalUDPAddress: :8189', 'webrtcLocalTCPAddress: :8189',
        'webrtcIPsFromInterfaces: false',
        'webrtcAdditionalHosts: ' + json.dumps([pc_ip, '127.0.0.1']),
        'webrtcAllowOrigins: ' + json.dumps([f'http://{pc_ip}:5000', 'http://127.0.0.1:5000', 'http://localhost:5000']),
        'authInternalUsers:', '  - user: any', '    ips: [127.0.0.1, "::1"]',
        '    permissions:', '      - action: publish', f'        path: {stream}',
        '  - user: any', '    ips: []', '    permissions:', '      - action: read', f'        path: {stream}',
        'paths:', f'  {stream}:', '    source: publisher', '',
    ])


def ffmpeg_command(binary, robot_ip, token, fps, stream):
    if '\r' in token or '\n' in token:
        raise ValueError('令牌不能包含换行')
    return [str(binary), '-hide_banner', '-loglevel', 'warning', '-nostdin',
            '-rw_timeout', '5000000', '-fflags', 'nobuffer', '-flags', 'low_delay',
            '-analyzeduration', '0', '-probesize', '32768', '-use_wallclock_as_timestamps', '1',
            '-headers', 'Authorization: Bearer ' + token + '\r\n',
            '-f', 'mpjpeg', '-i', f'http://{robot_ip}:5001/stream',
            '-an', '-vf', f'scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p',
            '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-tune', 'zerolatency',
            '-profile:v', 'baseline', '-bf', '0', '-g', str(fps), '-keyint_min', str(fps),
            '-sc_threshold', '0', '-b:v', '1200k', '-maxrate', '1500k', '-bufsize', '600k',
            '-flush_packets', '1', '-f', 'rtsp', '-rtsp_transport', 'tcp',
            f'rtsp://127.0.0.1:8554/{stream}']


def validate_address(value):
    import ipaddress
    # Current deployment is an IPv4 LAN, not a URL/command string.
    return str(ipaddress.IPv4Address(value))


def check_ports():
    for port, kind in [(8554, socket.SOCK_STREAM), (8889, socket.SOCK_STREAM),
                       (8189, socket.SOCK_STREAM), (8189, socket.SOCK_DGRAM)]:
        with socket.socket(socket.AF_INET, kind) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('0.0.0.0', port))
            except OSError as error:
                raise RuntimeError(f'端口 {port} 已占用，请先停止另一个视频服务') from error


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description='PC端 MediaMTX + FFmpeg；Ctrl+C停止两个子进程')
    parser.add_argument('--pc-ip', default='192.168.137.101')
    parser.add_argument('--robot-ip', default='192.168.137.100')
    parser.add_argument('--fps', type=int, default=15, choices=range(1, 31))
    parser.add_argument('--check-only', action='store_true', help='只检查工具文件与配置，不启动任何服务')
    args = parser.parse_args()
    pc_ip, robot_ip = validate_address(args.pc_ip), validate_address(args.robot_ip)
    load_env()
    media = locate('mediamtx', os.environ.get('MEDIAMTX_EXE'))
    ffmpeg = locate('ffmpeg', os.environ.get('FFMPEG_EXE'))
    token = os.environ.get('ROBOT_BRIDGE_TOKEN', '')
    if not token:
        raise ValueError('请先配置项目 .env 的 ROBOT_BRIDGE_TOKEN；与设备一致')
    stream = os.environ.get('VIDEO_STREAM_PATH', 'robot-camera')
    if not stream or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in stream):
        raise ValueError('视频流名称无效')
    config = configuration(pc_ip, stream)
    command = ffmpeg_command(ffmpeg, robot_ip, token, args.fps, stream)
    print(f'MediaMTX: {media}\nFFmpeg: {ffmpeg}\n设备: {robot_ip}:5001\n网页视频: http://{pc_ip}:8889/{stream}/whep')
    if args.check_only:
        print('静态检查完成；未启动服务、访问设备或测量延迟。')
        return
    check_ports()
    runtime = ROOT / 'runtime' / 'video'
    runtime.mkdir(parents=True, exist_ok=True)
    config_path = runtime / 'mediamtx.generated.yml'
    config_path.write_text(config, encoding='utf8')
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    media_process = encoder = None
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        with (runtime / 'mediamtx.log').open('ab') as media_log, (runtime / 'ffmpeg.log').open('ab') as encoder_log:
            media_process = subprocess.Popen([str(media), str(config_path)], stdout=media_log,
                stderr=subprocess.STDOUT, creationflags=creation_flags)
            for _ in range(50):
                if stopped.is_set():
                    return
                if media_process.poll() is not None:
                    raise RuntimeError('MediaMTX启动失败，请查看 runtime/video/mediamtx.log')
                try:
                    with socket.create_connection(('127.0.0.1', 8554), timeout=.2):
                        break
                except OSError:
                    stopped.wait(.2)
            else:
                raise RuntimeError('MediaMTX监听超时')
            while not stopped.is_set():
                if media_process.poll() is not None:
                    raise RuntimeError('MediaMTX已退出，请查看日志')
                if encoder is None or encoder.poll() is not None:
                    if encoder is not None:
                        print('设备源未就绪或编码退出，3秒后重连；详见 runtime/video/ffmpeg.log')
                        if stopped.wait(3):
                            break
                    encoder = subprocess.Popen(command, stdout=encoder_log, stderr=subprocess.STDOUT,
                                               creationflags=creation_flags)
                    print('FFmpeg拉流已启动（不显示令牌），请在网页切换“接口联调”。')
                stopped.wait(1)
    finally:
        stop_process(encoder)
        stop_process(media_process)
        print('视频服务已停止。')


if __name__ == '__main__':
    main()
