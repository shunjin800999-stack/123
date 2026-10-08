v0.6.135离线检查
新增检查：python -m unittest test_username_number_read -q
用户名及原队列检查：python -m unittest test_username_number_read test_username_contact test_unregistered_contact test_contact_submit test_contact_queue test_selection_scope test_controls_probe -q
PowerShell数字读取检查：powershell -NoProfile -File .\test_profile_number_probe.ps1
PowerShell页面过渡检查：powershell -NoProfile -File .\test_contact_result_transition.ps1
完整源目录：python -m unittest discover -s . -p "test_*.py" -q
需要原助手完整源文件和识别环境；本补丁不是独立安装包。
最终云端833项Python检查、37种PowerShell生产函数模拟场景通过；未执行真实Windows Telegram输入。
