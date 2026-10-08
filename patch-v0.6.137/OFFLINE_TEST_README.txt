v0.6.137离线检查（需要原助手完整源目录及OCR环境）
新规则：python -m unittest test_member_click_completion -q
相关选人：python -m unittest test_target_only_members test_target_list_refresh test_member_batch test_window_queue test_pinned_batch test_pinned_start_account -q
v136功能：python -m unittest test_plan_resume_reset test_screenshot_retention test_task_notifications -q
完整：python -m unittest discover -s . -p "test_*.py" -q
891项Python离线测试通过，零跳过；106个Python文件语法通过，20个PowerShell文件未改。
未执行真实Windows Telegram输入。本包是覆盖补丁，不是独立安装包。
