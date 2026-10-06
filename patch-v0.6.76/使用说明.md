v0.6.76：移除用户名Done鼠标提交路径，改为核验姓氏输入框焦点后按Enter提交。
本次两份报告确认 @andrei_ribeeiro 仍显示ADD TO CONTACTS，无法登记287成功。
旧鼠标报告的UIAutomation命中为Write a message聊天输入框。窗口及按钮边界不足以证明实际点击目标正确，移除该路径。
提交前核对原表单、字段和前台窗口，将焦点移到Last name/Sobrenome；两次检查FocusedElement运行编号、类、姓名和HasKeyboardFocus匹配姓氏输入框。
只在焦点完整核验后发一次Enter按下/释放。焦点在聊天输入框、窗口变化或字段变化时不发送按键。
Telegram官方EditContactBox中，正常英文/葡萄牙语顺序下姓氏输入框的Enter触发_save；代码依据boxes/peers/edit_contact_box.cpp。
发出Enter只标记等待核验，仍由独立只读检查两次匹配正确用户名、数字备注及Edit/Delete后登记成功、关闭资料页并继续。
当前287恢复：备份后关闭助手、解压覆盖；保留 @andrei_ribeeiro 未添加资料页。
点击「核验用户名仍未添加并重新提交一次」。不要点击核验已提交资料页，因为当前报告没有已添加状态；不要清空队列。
原记录和287编号保留。每位原用户名仅允许核验未添加后恢复一次；已成功或身份不明的项目不得重新提交。
如果已经手动成功添加287，请改用「核验已提交资料页并继续」。
执行期间不要操作键盘或切换窗口。
547项自动测试：536通过、11跳过。Python3.10语法、PowerShell语法和C#键盘辅助代码编译通过；真实Windows焦点和Enter提交仍需现场验证。
