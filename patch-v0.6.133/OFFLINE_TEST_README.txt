v0.6.133离线检查
新增检查：python -m unittest test_selection_scope -v
完整源目录：python -m unittest discover -s . -p "test_*.py" -q
需要当前已有程序的完整文件和原识别环境；补丁不是独立安装包。
完整云端检查812项通过，零跳过。逐位添加和OCR相关脚本与v132一致。
