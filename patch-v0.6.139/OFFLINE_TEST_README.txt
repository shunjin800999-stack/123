v0.6.139离线检查，需要完整助手源目录及OCR环境。
数字范围与实际生产像素：python -m unittest test_member_numeric_bounds test_numeric_name_guard -q
PowerShell基础场景：powershell -NoProfile -File .\test_numeric_member_guard.ps1
Linux没有PATH内PowerShell时，将TELEGRAM_TEST_POWERSHELL设为pwsh路径。
相关选人：python -m unittest test_member_speed test_member_numeric_bounds test_numeric_name_guard test_member_click_completion test_target_list_refresh test_member_icons -q
完整：python -m unittest discover -s . -p "test_*.py" -q
901项Python检查通过，零跳过；其中生产像素测试涵盖147种场景。109个Python文件及21个PowerShell文件语法通过。
真实Windows GDI/UIA及Telegram输入需本机试用。上传2416报告未含原PNG，测试使用原报告坐标及构造字形；本包是覆盖补丁。
