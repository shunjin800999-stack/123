v0.6.138离线检查，需要完整助手源目录及OCR环境。
新像素规则：python -m unittest test_numeric_name_guard -q
PowerShell基础场景：powershell -NoProfile -File .\test_numeric_member_guard.ps1
Linux没有PATH内PowerShell时，将TELEGRAM_TEST_POWERSHELL环境变量设为pwsh路径。
相关选人：python -m unittest test_member_speed test_numeric_name_guard test_member_click_completion test_target_list_refresh test_member_icons -q
完整：python -m unittest discover -s . -p "test_*.py" -q
892项Python检查通过，零跳过；新测试含121种生产像素场景，107个Python文件与21个PowerShell文件语法通过。
真实Windows位图读取与Telegram输入需本机试用；云端使用实际生产算法及内存位图。本包是覆盖补丁。
