v0.6.134离线检查
Python提交与队列检查：python -m unittest test_contact_submit test_contact_queue test_contact_profile_close test_phone_username_fallback test_unregistered_contact test_selection_scope -q
PowerShell页面转换检查：powershell -NoProfile -File .\test_contact_result_transition.ps1
完整源目录：python -m unittest discover -s . -p "test_*.py" -q
需要当前已有程序的完整源文件和识别环境；本补丁不是独立安装包。
云端815项Python检查、29种PowerShell生产函数模拟场景通过；不包含真实Windows Telegram输入测试。
