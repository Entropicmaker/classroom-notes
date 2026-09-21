#!/usr/bin/env python3
"""Run manually in Terminal. Never paste a credential into an assistant chat."""
import getpass
import subprocess
import sys
from pathlib import Path

if not sys.stdin.isatty():
    raise SystemExit('请由用户本人在本机终端运行此脚本；不要经聊天传入密钥。')
print('请粘贴北京地域百炼 API Key。输入不会显示，也不会进入命令历史。')
key = getpass.getpass('API Key: ').strip()
if not key:
    raise SystemExit('未输入；没有更改配置。')
result = subprocess.run(['node', str(Path(__file__).with_name('set-key.mjs'))], input=key.encode())
raise SystemExit(result.returncode)
