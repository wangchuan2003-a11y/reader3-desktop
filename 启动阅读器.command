#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "尚未配置运行环境，请查看 README.md。"
  read -r -p "按回车退出"
  exit 1
fi
.venv/bin/python launch_reader.py
if [ "$?" -ne 0 ]; then
  read -r -p "按回车退出"
fi
