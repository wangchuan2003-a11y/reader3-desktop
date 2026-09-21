"""Double-click launcher: reuse this local server or start it, then open the shelf."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from local_state import library_directory

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8123'
WORKSPACE = hashlib.sha256(str(library_directory(ROOT)).encode()).hexdigest()[:16]


def server_state():
    # Do not route the local connection through a configured network proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + '/api/health', timeout=1) as response:
            data = json.loads(response.read(4096))
        return 'ours' if data == {'app': 'reader3', 'workspace': WORKSPACE} else 'other'
    except urllib.error.HTTPError:
        return 'other'
    except (ValueError, json.JSONDecodeError):
        return 'other'
    except (urllib.error.URLError, TimeoutError, OSError):
        return 'offline'


def main():
    state = server_state()
    if state == 'other':
        print('8123 端口已有其他服务。请先关闭旧的阅读器服务，再重新双击启动。')
        return 1
    if state == 'offline':
        python = ROOT / '.venv' / 'bin' / 'python'
        if not python.exists():
            print('项目运行环境不存在，请先按使用说明配置。')
            return 1
        logs = ROOT / '.reader3'
        try:
            logs.mkdir(exist_ok=True)
            with (logs / 'server.log').open('a') as log:
                process = subprocess.Popen([str(python), '-u', str(ROOT / 'server.py')], cwd=ROOT,
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        except OSError:
            print('阅读器未能启动，请检查项目文件夹的权限和可用空间。')
            return 1
        for _ in range(40):
            if server_state() == 'ours':
                break
            if process.poll() is not None:
                print('阅读器启动未成功，请查看项目中 .reader3/server.log。')
                return 1
            time.sleep(.25)
        else:
            print('阅读器尚未就绪，请稍后重试。')
            return 1
    print('书架已就绪：' + URL)
    if not webbrowser.open(URL):
        print('没有自动打开浏览器，请复制上方地址到浏览器中。')
    print('可以关闭此终端窗口；书库位置：' + str(library_directory(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
