#!/bin/zsh
classroom_plugin_dir=${0:A:h}
exec python3 "$classroom_plugin_dir/bin/setup-key.py"
