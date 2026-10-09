v0.6.136离线检查
新增功能：python -m unittest test_plan_resume_reset test_screenshot_retention test_task_notifications -q
相关流程：python -m unittest test_store test_contact_queue test_contact_submit test_username_contact test_username_number_read test_selection_scope test_pinned_batch test_batch_plan_preparation test_target_only_members -q
PowerShell校验：powershell -NoProfile -File .\test_zero_number_guards.ps1
顶部数字读取：powershell -NoProfile -File .\test_profile_number_probe.ps1
页面衔接：powershell -NoProfile -File .\test_contact_result_transition.ps1
完整源目录：python -m unittest discover -s . -p "test_*.py" -q
需要原助手完整源文件及识别环境。本补丁不是独立安装包。
869项Python检查通过，零跳过；49项新数字校验及此前37项PowerShell模拟检查通过。Windows界面输入和语音需本机检查。
