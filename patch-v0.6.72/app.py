"""Windows UIA helper. Guarded contact Create; final group invite stays manual."""
import csv
import json
import os
import sys
import traceback
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from store import LABELS, Store, read_text_file
from visual_members import numeric_label, parse_member_labels
from windows_scan import scan_windows
from member_batch import select_members_test
from window_queue import select_window_queue
from contact_queue import unregistered_evidence, ContactQueue, complete_report, reserved_phone_fill, addition_windows, awaiting_form_window
from groups import GroupCatalog, GROUP_TYPES
from pinned_groups import PinnedGroupBindings, consistent_pair, pinned_pair
from pinned_scan import scan_pinned_windows, recheck_pinned_preview
from contact_open import open_contact_forms
from contact_submit import submission_payload
from contact_profile_close import profile_close_payload
from group_navigation import open_pinned_groups
from pinned_members import PinnedMemberPlans, select_pinned_member_queue
from batch_plan_preparation import BatchPlanPreparation
from target_replacement import TargetReplacement
from group_cleanup import saved_contact_snapshot,saved_addition_contacts,clean_group_pages
from backup_ocr import inspect_backup_ocr
from controls_probe import inspect_controls, inspect_groups, summarize, fill_contact_test, verify_contact_profile, inspect_member_visual, select_member_test, run_window_script

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('LOCALAPPDATA') or BASE) / 'TelegramContactAssistant'


class App:
    def __init__(self, root):
        self.root = root
        root.title('联系人名单助手 v0.6.72 — 快速搜索选人')
        root.geometry('1080x740')
        root.minsize(820, 580)
        self.store = Store(DATA / 'progress.sqlite3')
        self.group_catalog = GroupCatalog(self.store)
        self.pinned_groups = PinnedGroupBindings(self.store)
        self.pinned_member_plans = PinnedMemberPlans(self.store)
        self.adding_queue = ContactQueue(self.store,group_catalog=self.group_catalog,pinned_groups=self.pinned_groups)
        from account_names import migrate_account_names
        migrate_account_names(self.store)
        self.batch_plan_preparation=BatchPlanPreparation(self.store,self.pinned_member_plans,self.pinned_groups,self.adding_queue)
        self.target_replacement=TargetReplacement(self.store,self.pinned_groups,self.pinned_member_plans)
        self.batch_plan_preview=None;self.group_cleanup_report=None
        self.contact_results = queue.Queue()
        self.windows = []
        self.control_report = None
        self.probe_queue = queue.Queue()
        self.probing = False
        self.member_pause=threading.Event();self.member_selection_active=False
        self.pinned_scan_preview = None
        self.contact_open_report = None
        self.batch_id = tk.StringVar()
        self.root.report_callback_exception = self.on_error
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        ttk.Label(root, text='v0.6.72：新增用户名直达资料页并自动添加，成功后备注编号递增。',
                  padding=12).pack(fill='x')
        tabs = ttk.Notebook(root)
        self.tabs = tabs
        tabs.pack(fill='both',expand=True,padx=12,pady=(0,8))
        self.import_tab = ttk.Frame(tabs,padding=12)
        self.records_tab = ttk.Frame(tabs,padding=12)
        self.windows_tab = ttk.Frame(tabs,padding=12)
        self.adding_tab = ttk.Frame(tabs,padding=12)
        self.groups_tab = ttk.Frame(tabs,padding=12)
        self.help_tab = ttk.Frame(tabs,padding=12)
        for tab, title in ((self.import_tab,'导入名单'),(self.records_tab,'使用记录'),
                           (self.groups_tab,'账号群聊'),(self.adding_tab,'账号添加队列'),(self.windows_tab,'扫描窗口'),(self.help_tab,'说明')):
            tabs.add(tab,text=title)
        self.status = tk.StringVar(value='就绪。数据每次修改后自动保存。')
        ttk.Label(root,textvariable=self.status,padding=8,wraplength=1000).pack(fill='x')
        self.make_import()
        self.make_records()
        self.make_windows()
        self.make_adding_queue()
        self.make_groups()
        self.make_help()
        self.refresh()
        self.root.after(30000,self.tick_addition_stats)
        self.root.after(1000,self.tick_contact_queue)

    def guarded(self, fn):
        def run():
            try:
                fn()
            except (ValueError, RuntimeError, OSError) as error:
                messagebox.showwarning('未执行',str(error),parent=self.root)
        return run

    def make_import(self):
        frame = self.import_tab
        frame.columnconfigure(0,weight=1)
        frame.columnconfigure(1,weight=1)
        frame.rowconfigure(1,weight=1)
        self.inputs = {}
        self.origins = {}
        self.source_progress = {}
        for col, (source,name,hint) in enumerate((('phone','手机号','每行一个完整国际手机号，例如 +5516991234567'),
                                                ('username','用户名','每行一个 @用户名，例如 @example_user'))):
            bar = ttk.Frame(frame)
            bar.grid(row=0,column=col,sticky='ew',padx=(0,12) if col==0 else 0,pady=(0,8))
            ttk.Label(bar,text=name+'名单').pack(side='left')
            ttk.Button(bar,text='读取 TXT 到输入框',command=self.guarded(lambda s=source:self.load_file(s))).pack(side='right')
            box = ttk.Frame(frame)
            box.grid(row=1,column=col,sticky='nsew',padx=(0,12) if col==0 else 0)
            text = tk.Text(box,wrap='none',font=('Consolas',11),height=10,undo=True)
            scroll = ttk.Scrollbar(box,command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right',fill='y')
            text.pack(fill='both',expand=True)
            self.inputs[source]=text
            self.origins[source]='粘贴输入'
            ttk.Label(frame,text=hint,wraplength=450).grid(row=2,column=col,sticky='w',pady=6)
            ttk.Button(frame,text='检查数据库并导入 '+name,command=self.guarded(lambda s=source:self.import_input(s))).grid(row=3,column=col,sticky='ew',padx=(0,12) if col==0 else 0,pady=4)
            variable = tk.StringVar()
            self.source_progress[source]=variable
            ttk.Label(frame,textvariable=variable,wraplength=440).grid(row=4,column=col,sticky='nw',pady=12)
        ttk.Label(frame,text='导入只新增有效且未收录的项目。已有记录的占用、使用状态和编号保持原样。两份名单独立检查。',wraplength=960).grid(row=5,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Button(frame,text='备份数据库',command=self.guarded(self.backup)).grid(row=6,column=0,sticky='w')

    def load_file(self, source):
        path = filedialog.askopenfilename(title='选择 TXT 名单',filetypes=[('文本名单','*.txt'),('所有文件','*.*')])
        if path:
            text = read_text_file(path)
            self.inputs[source].delete('1.0','end')
            self.inputs[source].insert('1.0',text)
            self.origins[source]=Path(path).name
            self.status.set('文件已读入；点击“检查数据库并导入”才会保存到数据库。')

    def import_input(self, source):
        text = self.inputs[source].get('1.0','end-1c')
        if not text.strip():
            raise ValueError('请先读取文件或粘贴名单')
        result = self.store.import_text(source,text,self.origins[source])
        occupied = sum(row['status']!='ready' and not row['within_batch'] for row in result['duplicates'])
        unused = sum(row['status']=='ready' and not row['within_batch'] for row in result['duplicates'])
        summary = (f'新增：{result["new"]}\n本批重复：{result["within_batch"]}\n'
                   f'数据库已有但未使用：{unused}\n已占用／使用过：{occupied}\n'
                   f'格式错误：{len(result["invalid"])}')
        details = [summary,'','已有项不会重新导入，下面是检查明细：']
        for row in result['duplicates']:
            tag='本批重复' if row['within_batch'] else LABELS[row['status']]
            details.append(f'第{row["line"]}行  {row["value"]}  — {tag}，名单序号{row["seq"]}，账号：{row["account"] or "未分配"}')
        for line, error in result['invalid']:
            details.append(f'第{line}行 — {error}')
        self.show_text('导入检查结果','\n'.join(details))
        self.status.set(summary.replace('\n','；'))
        self.refresh()

    def make_records(self):
        frame = self.records_tab
        top=ttk.Frame(frame)
        top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='账号备注').pack(side='left')
        self.account=tk.StringVar(value='账号1')
        ttk.Entry(top,textvariable=self.account,width=14).pack(side='left',padx=5)
        ttk.Label(top,text='本账号计划人数').pack(side='left')
        self.target=tk.StringVar(value='20')
        ttk.Spinbox(top,from_=1,to=40,textvariable=self.target,width=5).pack(side='left',padx=5)
        ttk.Label(top,text='下一个全局编号').pack(side='left')
        self.global_number=tk.StringVar()
        ttk.Label(top,textvariable=self.global_number,width=8).pack(side='left',padx=5)
        ttk.Button(top,text='校准全局起点',command=self.guarded(self.calibrate_number)).pack(side='left',padx=5)
        ttk.Button(top,text='建立批次（仅记录）',command=self.guarded(self.create_batch)).pack(side='left',padx=5)
        self.addition_summary=tk.StringVar()
        ttk.Label(frame,textvariable=self.addition_summary,wraplength=960).pack(anchor='w',pady=5)
        picker=ttk.Frame(frame)
        picker.pack(fill='x',pady=5)
        ttk.Label(picker,text='当前批次').pack(side='left')
        self.batch_picker=ttk.Combobox(picker,textvariable=self.batch_id,state='readonly',width=60)
        self.batch_picker.pack(side='left',padx=5)
        ttk.Button(picker,text='领取下一位（仅记录）',command=self.guarded(self.reserve)).pack(side='left',padx=5)
        ttk.Label(frame,text='下面的按钮只保存你手动操作后的真实结果，不会点击 Telegram。待邀请用户仍被占用。',wraplength=960).pack(anchor='w',pady=6)
        area=ttk.Frame(frame)
        area.pack(fill='both',expand=True)
        columns=('source','seq','value','account','number','status','reason')
        self.records=ttk.Treeview(area,columns=columns,show='headings',selectmode='extended')
        for col,title,width in zip(columns,('来源','名单序号','用户信息','账号备注','备注编号','状态','原因'),(70,75,180,90,70,160,220)):
            self.records.heading(col,text=title)
            self.records.column(col,width=width,minwidth=50,stretch=col in ('value','reason'))
        vertical=ttk.Scrollbar(area,command=self.records.yview)
        horizontal=ttk.Scrollbar(area,orient='horizontal',command=self.records.xview)
        self.records.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        area.rowconfigure(0,weight=1); area.columnconfigure(0,weight=1)
        self.records.grid(row=0,column=0,sticky='nsew')
        vertical.grid(row=0,column=1,sticky='ns')
        horizontal.grid(row=1,column=0,sticky='ew')
        actions=ttk.Frame(frame)
        actions.pack(fill='x',pady=8)
        for i,(label,action) in enumerate((
            ('记录已添加联系人',lambda:self.add_result('added')),
            ('普通失败 → 换用户名',lambda:self.add_result('ordinary_failure')),
            ('限制／结果不明 → 暂停',self.stop_record),
            ('记录本批已停在邀请页',self.waiting),
            ('确认选中用户邀请成功',self.invited),
            ('导出使用记录',self.export_records),
            ('导出日志',self.export_logs),
        )):
            ttk.Button(actions,text=label,command=self.guarded(action)).grid(row=i//4,column=i%4,sticky='ew',padx=3,pady=3)
        for column in range(4): actions.columnconfigure(column,weight=1)

    def current_batch(self):
        if not self.batch_id.get():
            raise ValueError('请先建立或选择批次')
        return int(self.batch_id.get().split(' · ',1)[0].removeprefix('#'))

    def selected_one(self):
        selection=self.records.selection()
        if len(selection)!=1:
            raise ValueError('请只选择一位用户')
        return int(selection[0])

    def create_batch(self):
        self.group_operation_ready()
        batch=self.store.create_batch(self.account.get(),int(self.target.get()))
        self.refresh(select_batch=batch)
        self.status.set(f'批次 #{batch} 已保存；此版本由你手动领取并记录结果。')

    def calibrate_number(self):
        if self.probing:raise ValueError('正在执行界面操作，请等待结束再校准编号')
        if self.adding_queue.current():raise ValueError('添加队列执行中不能校准编号；先停止并核查已试填联系人')
        current=self.store.next_contact_number()
        if current>999999:raise ValueError('全局编号已用完，不能退回已使用的编号')
        value=simpledialog.askinteger('校准下一个全局编号',
            f'当前下一个编号为 {current}。所有账号共用，不按天归零。\n'
            '若有未登记的手动备注，请填其最大编号加 1。\n'
            '只能提高编号；不会修改已有联系人的备注。',
            initialvalue=current,minvalue=current,maxvalue=999999,parent=self.root)
        if value is None:return
        self.store.set_next_number(value);self.refresh()
        self.status.set(f'下一个全局编号已校准为 {value}，所有账号继续使用这一序列。')

    def reserve(self):
        self.group_operation_ready()
        row,is_new=self.store.reserve_next(self.current_batch())
        self.refresh()
        if not row:
            self.status.set('本批已达到目标，或可用名单已用尽。请核对已添加联系人。')
            return
        iid=str(row['id'])
        self.records.selection_set(iid); self.records.see(iid)
        messagebox.showinfo('已占用' if is_new else '发现未确认任务',
                            f'{row["value"]}\n账号：{row["account"]}\n名单序号：{row["seq"]}\n'
                            +('已保存占用记录，请手动操作后记录结果。' if is_new else '这位用户之前已占用。请核对实际界面，不要直接重新添加。'),parent=self.root)

    def add_result(self,outcome):
        if self.probing:raise ValueError('正在执行界面操作，请等待结束再记录结果')
        item=self.selected_one()
        self.guard_manual_queue_record(item)
        reason=''
        if outcome=='ordinary_failure':
            reason=simpledialog.askstring('普通失败','请记录原因。明确限制或原因不明，请取消并使用暂停按钮：',parent=self.root)
            if not reason: return
        elif not messagebox.askyesno('核实添加结果','你已在 Telegram 确认此用户添加为联系人成功吗？\n此按钮只保存记录并分配备注编号，不会替你修改联系人名称。',parent=self.root):
            return
        number=self.store.record_add_result(item,outcome,reason)
        self.refresh()
        self.status.set('已记录普通失败；该批次后续使用用户名名单。' if number is None else f'联系人编号已记录为 {number}。请在 Telegram 手动使用这个备注；自动填写待后续接入。')

    def stop_record(self):
        if self.probing:raise ValueError('正在执行界面操作，请等待结束再记录结果')
        item=self.selected_one()
        self.guard_manual_queue_record(item)
        reason=simpledialog.askstring('暂停并核查','请输入限制提示或不确定原因：',parent=self.root)
        if not reason: return
        restricted=messagebox.askyesno('异常类型','是否明确出现频率或账号限制提示？\n“是”记录为限制；“否”记录为结果不明。',parent=self.root)
        self.store.record_add_result(item,'restriction' if restricted else 'uncertain',reason)
        self.refresh()
        self.status.set('该批次已暂停，记录保留占用。此版本不提供自动重试或自动解除限制。')

    def guard_manual_queue_record(self,item):
        job=self.adding_queue.current()
        if job and job['item_id']==item:
            raise ValueError('此联系人正在添加队列中；请先停止队列，再人工核查并登记，避免同时更新')

    def make_groups(self):
        frame=self.groups_tab
        top=ttk.Frame(frame);top.pack(fill='x',pady=6)
        ttk.Label(top,text='账号备注（与添加批次保持一致）').pack(side='left')
        self.group_account=tk.StringVar(value='账号1')
        self.group_account_picker=ttk.Combobox(top,textvariable=self.group_account,width=22)
        self.group_account_picker.pack(side='left',padx=6)
        modes=ttk.Notebook(frame);modes.pack(fill='both',expand=True)
        self.group_modes=modes
        self.pinned_scan_frame=ttk.Frame(modes,padding=10)
        modes.add(self.pinned_scan_frame,text='批量扫描核对')
        self.make_pinned_scan()
        pinned_frame=ttk.Frame(modes,padding=10);catalog_frame=ttk.Frame(modes,padding=10)
        diagnostics=ttk.Frame(modes,padding=10)
        self.batch_plan_frame=ttk.Frame(modes,padding=10)
        modes.add(self.batch_plan_frame,text='批次名单');self.make_batch_plan_preparation()
        modes.add(pinned_frame,text='置顶群');modes.add(catalog_frame,text='完整群目录');modes.add(diagnostics,text='检测工具')
        ttk.Label(pinned_frame,text='先在扫描窗口页选择窗口，再识别该窗口第一、第二个置顶群。群名相同也按窗口分别记录。',wraplength=930).pack(anchor='w',pady=6)
        bar=ttk.Frame(pinned_frame);bar.pack(fill='x',pady=6)
        ttk.Button(bar,text='识别并记录两个置顶群',command=self.guarded(self.probe_pinned_groups)).pack(side='left')
        ttk.Button(bar,text='导出置顶群记录',command=self.guarded(self.export_pinned_groups)).pack(side='left',padx=6)
        self.pinned_summary=tk.StringVar()
        ttk.Label(pinned_frame,textvariable=self.pinned_summary,wraplength=930).pack(anchor='w',pady=10)
        bar=ttk.Frame(pinned_frame);bar.pack(fill='x',pady=6)
        ttk.Label(bar,text='手填测试备注').pack(side='left')
        self.pinned_member_input=tk.StringVar(value='1,2')
        ttk.Entry(bar,textvariable=self.pinned_member_input,width=18).pack(side='left',padx=6)
        ttk.Button(bar,text='保存手填测试名单',command=self.guarded(self.save_pinned_member_plan)).pack(side='left')
        ttk.Button(bar,text='从当前批次生成两群名单',command=self.guarded(self.make_pinned_batch_plan)).pack(side='left',padx=8)
        self.pinned_member_summary=tk.StringVar()
        ttk.Label(pinned_frame,textvariable=self.pinned_member_summary,wraplength=930).pack(anchor='w',pady=4)
        bar=ttk.Frame(pinned_frame);bar.pack(fill='x',pady=6)
        ttk.Button(bar,text='打开群①并选择联系人',command=self.guarded(lambda:self.select_pinned_members(1))).pack(side='left')
        ttk.Button(bar,text='打开群②并选择同批联系人',command=self.guarded(lambda:self.select_pinned_members(2))).pack(side='left',padx=8)
        ttk.Button(bar,text='暂停选人',command=self.pause_member_selection).pack(side='left',padx=6)
        ttk.Button(bar,text='导出两群选人报告',command=self.guarded(self.export_pinned_members)).pack(side='left')
        bar=ttk.Frame(pinned_frame);bar.pack(fill='x',pady=4)
        ttk.Button(bar,text='整理所选窗口页面（不选人）',command=self.guarded(self.cleanup_selected_groups)).pack(side='left')
        ttk.Button(bar,text='导出页面整理报告',command=self.guarded(self.export_group_cleanup)).pack(side='left',padx=8)
        bar=ttk.Frame(pinned_frame);bar.pack(fill='x',pady=4)
        for slot in (1,2):
            ttk.Button(bar,text=f'确认群{["①","②"][slot-1]}全部邀请成功',
                command=self.guarded(lambda s=slot:self.confirm_pinned_group_invited(s))).pack(side='left',padx=(0,8))
        ttk.Label(pinned_frame,text='在使用记录选定批次，再生成两群名单：只读取本批已确认添加成功的实际备注，不按计划人数猜区间。\n'
            '选人前自动整理已识别的成员弹窗、目标群／已保存联系人资料页及左侧搜索；未知弹窗保留并停止。\n'
            '选人结束后你点击 Add，核实本群全部成员已加入，再按对应群的确认按钮。只选人或取消不能确认成功。\n'
            '你处理完群① Add 后，手动启动群②；会先自动整理页面。仍开的已识别成员弹窗会被取消，不推断邀请结果。\n'
            '开始确认空白；点击前核对上一位，点击后核对新增编号，中途不滚动，结束核对整批。\n'
            '多窗口 A 选好即继续 B；暂停后保留页面，下次从头执行。窗口保持还原。',wraplength=930).pack(anchor='w',pady=6)
        bar=ttk.Frame(diagnostics);bar.pack(fill='x',pady=8)
        ttk.Button(bar,text='打开群①到添加成员（不选人）',command=self.guarded(lambda:self.navigate_pinned_groups(1))).pack(side='left')
        ttk.Button(bar,text='打开群②到添加成员（不选人）',command=self.guarded(lambda:self.navigate_pinned_groups(2))).pack(side='left',padx=8)
        ttk.Button(diagnostics,text='导出群打开报告',command=self.guarded(self.export_group_navigation)).pack(anchor='w',pady=6)
        ttk.Button(diagnostics,text='只读检查所选窗口的群列表',command=self.guarded(self.probe_group_list)).pack(anchor='w',pady=4)
        ttk.Label(diagnostics,text='本页保留旧版导航检测：只打开页面，不搜索或选人。需要选人请用“置顶群”页新版按钮。',wraplength=930).pack(anchor='w',pady=8)
        ttk.Label(pinned_frame,text='手填名单保留用于测试，不登记邀请结果。只有数据库批次名单支持两群分别确认；选人不增加添加人数。',wraplength=930).pack(anchor='w',pady=4)
        top=ttk.Frame(catalog_frame);top.pack(fill='x',pady=6)
        ttk.Button(top,text='导入该账号 result.json',command=self.guarded(self.import_group_catalog)).pack(side='left',padx=4)
        ttk.Button(top,text='绑定扫描页选中的窗口',command=self.guarded(self.bind_group_window)).pack(side='left',padx=4)
        ttk.Label(catalog_frame,text='完整目录方式：勾选个人信息、私有群及公开群，选择 JSON 格式，导出成功后读取 result.json。\n'
            '只保留群目录；数量为本次导出记录数量，实时全部群数尚未验证。此配置用于现有添加队列，与置顶导航分开。',wraplength=930).pack(anchor='w',pady=6)
        self.group_summary=tk.StringVar();ttk.Label(catalog_frame,textvariable=self.group_summary,wraplength=930).pack(anchor='w',pady=6)
        cols=('name','kind','id')
        area=ttk.Frame(catalog_frame);area.pack(fill='both',expand=True)
        self.group_tree=ttk.Treeview(area,columns=cols,show='headings',selectmode='extended',height=6)
        for col,title,width in zip(cols,('群名称','群类型','群 ID（区分同名群）'),(410,160,220)):
            self.group_tree.heading(col,text=title);self.group_tree.column(col,width=width)
        scroll=ttk.Scrollbar(area,command=self.group_tree.yview);self.group_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.group_tree.pack(fill='both',expand=True)
        bar=ttk.Frame(catalog_frame);bar.pack(fill='x',pady=8)
        for text,fn in (('保存选中的两个目标群',self.save_group_targets),('生成当前批次的两群名单（仅记录）',self.make_group_plan),
                        ('导出群配置报告',self.export_group_config)):
            ttk.Button(bar,text=text,command=self.guarded(fn)).pack(side='left',padx=4)
        self.group_targets_label=tk.StringVar();ttk.Label(catalog_frame,textvariable=self.group_targets_label,wraplength=930).pack(anchor='w',pady=4)
        ttk.Label(catalog_frame,text='按 Ctrl 点选恰好两个不同群，保存顺序按列表从上到下。两个群使用同批联系人，添加人数只计一次。',wraplength=930).pack(anchor='w')
        self.group_account.trace_add('write',lambda *args:self.refresh_groups())
        self.group_account.trace_add('write',lambda *args:self.load_pinned_member_input())
        self.refresh_groups()

    def make_pinned_scan(self):
        frame=self.pinned_scan_frame
        ttk.Label(frame,text='所有账号先打开普通聊天列表，关闭弹窗和资料面板，清空搜索，两个目标群放在列表第一、第二。\n'
            '一次读取全部 Telegram 窗口；旧账号备注按程序路径沿用，新窗口先分配可修改的账号备注。上方单账号下拉不限制本页。',
            wraplength=940).pack(anchor='w',pady=6)
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=6)
        ttk.Button(bar,text='扫描全部窗口及置顶群',command=self.guarded(self.scan_all_pinned_groups)).pack(side='left')
        self.pinned_scan_confirm=ttk.Button(bar,text='确认保存所选账号',command=self.guarded(self.confirm_pinned_scan),state='disabled')
        self.pinned_scan_confirm.pack(side='left',padx=8)
        ttk.Button(bar,text='导出批量识别报告',command=self.guarded(self.export_pinned_scan)).pack(side='left')
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=4)
        ttk.Button(bar,text='预览更换本批目标群',command=self.guarded(self.preview_target_replacement)).pack(side='left')
        ttk.Button(bar,text='导出更换目标群报告',command=self.guarded(self.export_target_replacement)).pack(side='left',padx=8)
        self.pinned_scan_summary=tk.StringVar(value='尚未扫描。扫描结果只供核对，点击确认后才保存配置。')
        ttk.Label(frame,textvariable=self.pinned_scan_summary,wraplength=940).pack(anchor='w',pady=8)
        area=ttk.Frame(frame);area.pack(fill='both',expand=True)
        columns=('account','window','path','group1','group2','state')
        self.pinned_scan_tree=ttk.Treeview(area,columns=columns,show='headings',selectmode='extended',height=8)
        for col,title,width in zip(columns,('账号备注','窗口 / 进程','程序路径','群①','群②','状态／原因'),(100,140,280,180,180,270)):
            self.pinned_scan_tree.heading(col,text=title);self.pinned_scan_tree.column(col,width=width,minwidth=80)
        vertical=ttk.Scrollbar(area,command=self.pinned_scan_tree.yview)
        horizontal=ttk.Scrollbar(area,orient='horizontal',command=self.pinned_scan_tree.xview)
        self.pinned_scan_tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        area.rowconfigure(0,weight=1);area.columnconfigure(0,weight=1)
        self.pinned_scan_tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        self.pinned_scan_tree.bind('<Double-1>',self.edit_pinned_scan_account)
        ttk.Label(frame,text='识别成功的行默认全部选中，核对后点击一次确认。失败行不保存，可处理后重新扫描；按 Ctrl 可以调整本次确认范围。\n'
            '新账号需要改备注时，双击“账号备注”列。旧账号名称保留，避免旧批次名单串号。此页不添加联系人、不打开群、不邀请。',
            wraplength=940).pack(anchor='w',pady=8)

    def scan_all_pinned_groups(self):
        self.group_operation_ready()
        self.scan()
        known=[{'account':a,'window':self.pinned_groups.get(a)['window']} for a in self.pinned_groups.accounts()]
        known += [{'account':r['account'],'window':catalog['window']} for r in self.group_catalog.accounts()
            if (catalog:=self.group_catalog.catalog(r['account'])) and catalog.get('window')]
        windows=[dict(w) for w in self.windows if w['candidate']]
        if not windows:raise ValueError('没有扫描到 Telegram 窗口，请先登录并打开各账号')
        self.pinned_scan_preview=None;self.pinned_scan_tree.delete(*self.pinned_scan_tree.get_children())
        self.pinned_scan_confirm.configure(state='disabled')
        self.tabs.select(self.groups_tab);self.group_modes.select(self.pinned_scan_frame)
        self.probing=True;self.probe_button.configure(state='disabled')
        self.pinned_scan_summary.set(f'正在读取 {len(windows)} 个候选窗口；完成后统一核对，旧配置暂不改变。')
        def worker():
            try:
                result=scan_pinned_windows(windows,known,lambda text:self.probe_queue.put((True,{'progress_result':text})))
                self.probe_queue.put((True,{'pinned_scan_result':result}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def render_pinned_scan(self):
        self.pinned_scan_tree.delete(*self.pinned_scan_tree.get_children())
        preview=self.pinned_scan_preview
        if not preview:return
        ready=[]
        for job in preview['jobs']:
            targets=(job.get('observation') or {}).get('targets',[])
            names=[g['name'] for g in targets] if len(targets)==2 else ['未确认','未确认']
            window=job['window'];key=str(job['index'])
            state=('已确认保存' if job['account'] in preview['saved_accounts'] else
                '待核对' if job['state']=='ready' else '未确认')
            self.pinned_scan_tree.insert('','end',iid=key,values=(job['account'],f'0x{window["hwnd"]:X} / {window["pid"]}',
                window['path'],*names,state+' · '+job['reason']))
            if job['state']=='ready' and job['account'] not in preview['saved_accounts']:ready.append(key)
        self.pinned_scan_tree.selection_set(ready)
        self.pinned_scan_confirm.configure(state='normal' if ready and not preview.get('needs_rescan') else 'disabled')
        failed=sum(j['state']!='ready' for j in preview['jobs'])
        self.pinned_scan_summary.set(f'共 {len(preview["jobs"])} 个窗口；识别成功 {len(preview["jobs"])-failed} 个，未确认 {failed} 个；'
            f'已保存 {len(preview["saved_accounts"])} 个。'+('复核失败，请重新扫描。' if preview.get('needs_rescan') else '核对账号路径、群①和群②，确认按钮仅保存所选成功行。'))

    def edit_pinned_scan_account(self,event):
        if self.probing or not self.pinned_scan_preview:return
        key=self.pinned_scan_tree.identify_row(event.y)
        if not key or self.pinned_scan_tree.identify_column(event.x)!='#1':return
        job=next(j for j in self.pinned_scan_preview['jobs'] if j['index']==int(key))
        if job['existing_account'] or job['account'] in self.pinned_scan_preview['saved_accounts']:
            self.status.set('已有账号备注沿用旧记录，不能在本次扫描中改名；新账号可双击修改。');return
        name=simpledialog.askstring('修改新账号备注','请填写稳定、唯一的账号备注：',initialvalue=job['account'],parent=self.root)
        if name is None:return
        name=name.strip()
        if (not name or len(name)>100 or name in self.pinned_groups.accounts()
                or name in {r['account'] for r in self.group_catalog.accounts()}
                or any(j is not job and j['account']==name for j in self.pinned_scan_preview['jobs'])):
            messagebox.showwarning('未改名','备注须为 1–100 字且不能与其他账号重复。',parent=self.root);return
        job['account']=name;self.render_pinned_scan()

    def confirm_pinned_scan(self):
        self.group_operation_ready()
        preview=self.pinned_scan_preview
        if not preview or preview.get('needs_rescan'):raise ValueError('请先重新扫描并核对列表')
        indices=[int(key) for key in self.pinned_scan_tree.selection()]
        jobs=[j for j in preview['jobs'] if j['index'] in indices]
        if not jobs or any(j['state']!='ready' or j['account'] in preview['saved_accounts'] for j in jobs):
            raise ValueError('请选择尚未保存且识别成功的账号行；失败行不能确认')
        import copy
        frozen=copy.deepcopy(preview)
        self.probing=True;self.probe_button.configure(state='disabled');self.pinned_scan_confirm.configure(state='disabled')
        self.status.set('正在复核所选窗口和置顶群；全部匹配后统一保存。')
        def worker():
            try:
                records=recheck_pinned_preview(frozen,indices,lambda text:self.probe_queue.put((True,{'progress_result':text})))
                self.probe_queue.put((True,{'pinned_scan_confirm_result':{'scan_id':frozen['scan_id'],'records':records}}))
            except Exception as error:self.probe_queue.put((True,{'pinned_scan_confirm_result':{'scan_id':frozen['scan_id'],'error':str(error)}}))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def export_pinned_scan(self):
        if not self.pinned_scan_preview:raise ValueError('请先扫描全部窗口及置顶群')
        if self.probing:raise ValueError('请等待识别或确认结束再导出')
        path=filedialog.asksaveasfilename(title='保存批量识别报告',initialfile='批量置顶群识别.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(self.pinned_scan_preview,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('批量识别报告已导出。')

    def preview_target_replacement(self):
        self.group_operation_ready();scan=self.pinned_scan_preview
        if not scan or scan.get('needs_rescan'):raise ValueError('请先重新扫描全部窗口及置顶群')
        indices=[int(k) for k in self.pinned_scan_tree.selection()]
        jobs=[j for j in scan['jobs'] if j['index'] in indices]
        if not jobs or any(j['state']!='ready' or j['account'] in scan['saved_accounts'] for j in jobs):
            raise ValueError('请选择尚未保存且识别成功的新目标群账号行')
        import copy
        records=[dict(copy.deepcopy(j['observation']),account=j['account'],window=copy.deepcopy(j['window'])) for j in jobs]
        preview=self.target_replacement.preview(records);frozen=copy.deepcopy(scan)
        lines=['更换本批的两个目标群，保留已经成功添加的联系人和编号。','']
        for row in preview['rows']:
            lines.extend([f'{row["account"]} · 批次 #{row["batch_id"]} · 保留备注：'+', '.join(row['numbers']),
                '旧群①／②：'+' / '.join(t['name'] for t in row['old_record']['targets']),
                '新群①／②：'+' / '.join(t['name'] for t in row['new_record']['targets']),''])
        lines.extend(['旧群的选人和邀请状态留作历史；两个新群都要重新搜索选人。',
            '不会重新添加联系人、消耗名单、改变编号或登记邀请成功。',
            '确认后将再次读取新群两次；任一账号不匹配，全部不更换。'])
        dialog=tk.Toplevel(self.root);dialog.title('核对更换本批目标群');dialog.geometry('860x510');dialog.transient(self.root)
        text=tk.Text(dialog,wrap='word',height=18);text.pack(fill='both',expand=True,padx=12,pady=10)
        text.insert('1.0','\n'.join(lines));text.configure(state='disabled')
        def confirm():
            self.group_operation_ready()
            if not self.pinned_scan_preview or self.pinned_scan_preview['scan_id']!=frozen['scan_id']:
                raise ValueError('扫描结果已变化，请关闭此页重新扫描')
            dialog.destroy();self.probing=True;self.probe_button.configure(state='disabled')
            self.pinned_scan_confirm.configure(state='disabled');self.status.set('正在重新核对新群，联系人和编号保持原样。')
            def worker():
                try:
                    live=recheck_pinned_preview(frozen,indices,lambda t:self.probe_queue.put((True,{'progress_result':t})))
                    value={'scan_id':frozen['scan_id'],'preview':preview,'records':live}
                except Exception as error:value={'scan_id':frozen['scan_id'],'error':str(error)}
                self.probe_queue.put((True,{'target_replace_confirm_result':value}))
            threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)
        ttk.Button(dialog,text='确认更换并保留联系人',command=self.guarded(confirm)).pack(pady=(0,12))

    def export_target_replacement(self):
        self.group_operation_ready();result=self.target_replacement.latest()
        if not result:raise ValueError('请先完成更换本批目标群')
        path=filedialog.asksaveasfilename(title='导出更换目标群报告',initialfile='更换目标群.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');self.status.set('更换目标群报告已导出。')

    def open_contact_forms_test(self):
        self.group_operation_ready()
        observations=self.selected_pinned_observations(require_account_picker=False)
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'contact_open_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.probing=True;self.probe_button.configure(state='disabled')
        self.contact_open_report=None
        self.status.set('正在依次直达所选账号的空白 New Contact；请等待结束，不点击 Create。')
        def worker():
            try:
                result=open_contact_forms(observations,path,lambda text:self.probe_queue.put((True,{'progress_result':text})))
                self.probe_queue.put((True,{'contact_open_result':result}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def export_contact_open(self):
        if self.probing:raise ValueError('请等待表单打开测试完成')
        if not self.contact_open_report:raise ValueError('请先测试打开所选账号 New Contact')
        path=filedialog.asksaveasfilename(title='保存直达联系人表单报告',initialfile='直达联系人表单.json',
            defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(self.contact_open_report,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('直达联系人表单报告已导出。')

    def selected_group_window(self):
        selected=self.window_tree.selection()
        if len(selected)!=1:raise ValueError('先在扫描窗口页扫描，并只选中当前账号的一个 Telegram 窗口')
        window=dict(self.windows[int(selected[0])])
        from groups import valid_window
        if not valid_window(window):raise ValueError('请选择已还原的 Telegram.exe 候选窗口')
        return window

    def group_operation_ready(self):
        if self.probing or self.adding_queue.current():raise ValueError('先等待界面操作结束并停止添加队列，再识别或配置群聊')

    def import_group_catalog(self):
        self.group_operation_ready()
        account=self.group_account.get().strip()
        path=filedialog.askopenfilename(title=f'读取 {account} 导出的完整 result.json',filetypes=[('JSON','*.json')])
        if not path:return
        catalog=self.group_catalog.import_file(account,path)
        self.refresh_groups();self.status.set(f'{account}：本次导出目录识别到 {len(catalog["groups"])} 个群；请选择两个目标群。')

    def bind_group_window(self):
        self.group_operation_ready()
        window=self.selected_group_window();account=self.group_account.get().strip()
        self.group_catalog.bind_window(account,window);self.refresh_groups()
        self.status.set(f'{account} 已绑定窗口 0x{window["hwnd"]:X}；窗口所属账号由你核对，不通过标题猜测。')

    def refresh_groups(self):
        if not hasattr(self,'group_tree'):return
        self.group_account_picker['values']=sorted(set([r['account'] for r in self.group_catalog.accounts()]+self.pinned_groups.accounts()))
        self.group_tree.delete(*self.group_tree.get_children())
        account=self.group_account.get().strip();catalog=self.group_catalog.catalog(account)
        plan=self.pinned_member_plans.get(account)
        if plan:
            states=self.pinned_member_plans.states(account)
            labels={'waiting_for_manual_invite':'已选，等待手动 Add／核实结果','confirmed_invited':'人工已确认邀请成功'}
            progress='；'.join(f'群 {slot}：'+labels.get(job['state'],'待核查')
                for slot,job in states.items() if job.get('plan_id')==plan['plan_id'])
            source=f'使用记录批次 #{plan["batch_id"]} 的 {len(plan["numbers"])} 位成功联系人' if plan.get('source')=='confirmed_batch' else '手填测试名单'
            if plan.get('legacy_item_ids'):source+='（含旧版成功记录，沿用原备注）'
            self.pinned_member_summary.set(account+' · '+source+' · 两群同批备注：'+', '.join(plan['numbers'])+'。'+(progress or '尚未选人'))
        else:self.pinned_member_summary.set('此账号尚未保存本次名单。先在使用记录选定批次，再点击“从当前批次生成两群名单”。')
        pinned=self.pinned_groups.get(account)
        if pinned:
            names='；'.join(f'群 {g["slot"]}「{g["name"]}」' for g in pinned['targets'])
            states=self.pinned_groups.navigation_states(account)
            previous=[]
            for slot,job in states.items():
                if all(job['window'].get(k)==pinned['window'].get(k) for k in ('hwnd','pid','path')) and job['target']['runtime_id']==pinned['targets'][slot-1]['runtime_id']:
                    state='添加成员页面已打开（未邀请）' if job['state']=='members_open' else '打开结果待核查'
                    previous.append(f'群 {slot}：{state}')
            self.pinned_summary.set(f'已记录 {account} · 窗口 0x{pinned["window"]["hwnd"]:X} · {names}\n'
                '窗口重启、群或顺序改变后需重新识别；上次打开结果：'+('；'.join(previous) or '尚未测试'))
        else:self.pinned_summary.set('置顶方式：只置顶本次两个目标群，搜索为空，列表回到顶部；选中对应窗口后点击识别。')
        if not catalog:
            self.group_summary.set('完整群目录方式（可选）：尚未导入该账号 result.json。置顶识别记录可直接使用上方按钮，不需要导出聊天。')
            self.group_targets_label.set('尚未设置两个目标群。');return
        summary=catalog['summary'];counts=summary['counts']
        bound=f'0x{catalog["window"]["hwnd"]:X}' if catalog['window'] else '尚未绑定'
        self.group_summary.set(f'账号：{account} · {catalog["owner_label"]} · Telegram ID {catalog["owner_id"]} · 窗口 {bound}\n'
            f'本次目录 {len(catalog["groups"])} 个群；频道排除 {counts["channels"]} 个；未知类型 {counts["unknown_types"]} 个；来源 {summary["source"]} · {catalog["imported"]} UTC。实时全部群数尚未验证。')
        for group in catalog['groups']:
            self.group_tree.insert('','end',iid=group['peer_key'],values=(group['name'] or '（名称缺失，需核查）',GROUP_TYPES[group['type']],group['peer_key']))
        if catalog['targets_ready']:
            keys=catalog['target_keys'];self.group_tree.selection_set(keys)
            self.group_targets_label.set('已保存：群 1「'+catalog['targets'][0]['name']+'」；群 2「'+catalog['targets'][1]['name']+'」。本页完整目录尚未与置顶导航联动。')
        else:self.group_targets_label.set('请选两个目标群。已有配置若在最新目录缺失，则需要重新核查和选择。')

    def save_group_targets(self):
        self.group_operation_ready()
        selected=set(self.group_tree.selection())
        keys=[key for key in self.group_tree.get_children() if key in selected]
        self.group_catalog.save_targets(self.group_account.get().strip(),keys);self.refresh_groups()
        self.status.set('此账号的两个目标群已保存；没有选择用户或点击邀请。')

    def make_group_plan(self):
        self.group_operation_ready()
        batch_id=self.current_batch()
        if self.store.batch(batch_id)['account']!=self.group_account.get().strip():raise ValueError('使用记录当前批次不属于这里的账号，请先切换到对应批次')
        plan=self.group_catalog.plan_for_batch(batch_id)
        names='\n'.join(f'群 {g["slot"]}：{g["name"]} · {g["peer_key"]}' for g in plan['groups'])
        numbers='、'.join(m['number'] for m in plan['groups'][0]['members'])
        self.show_text('本批两个群的同一份联系人名单',f'{names}\n\n同一批 {plan["contact_count"]} 位联系人，备注：{numbers}\n\n名单已冻结并保存；未打开群、未选择用户、未邀请。两群执行进度将分别记录。')
        self.status.set('本批两群名单已保存；导出群配置报告可以一起查看。')

    def export_group_config(self):
        account=self.group_account.get().strip()
        path=filedialog.asksaveasfilename(title='保存该账号群配置报告',initialfile='账号群配置.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:self.group_catalog.export(account,path);self.status.set('账号群配置报告已保存；只包含群目录、窗口绑定和本批两群名单。')

    def probe_group_list(self):
        self.group_operation_ready();window=self.selected_group_window()
        account=self.group_account.get().strip()
        path=filedialog.asksaveasfilename(title='保存窗口群列表只读检测报告',initialfile='群列表检测.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在只读检查该窗口的聊天列表；请保持普通主界面，搜索为空、弹窗关闭。')
        def worker():
            try:
                report=inspect_groups(window,path);report.update(account_label=account,account_identity_verified=False)
                Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                self.probe_queue.put((True,{'group_probe_result':report,'report_path':path}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def export_pinned_groups(self):
        result=self.pinned_groups.get(self.group_account.get().strip())
        if not result:raise ValueError('先识别并记录这个账号的两个置顶群')
        path=filedialog.asksaveasfilename(title='保存置顶群识别记录',initialfile='置顶群记录.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('置顶群记录已导出；只含群名称和窗口控件标识，不含聊天预览。')

    def probe_pinned_groups(self):
        self.group_operation_ready();window=self.selected_group_window()
        account=self.group_account.get().strip()
        if not account or len(account)>100:raise ValueError('请填写稳定的账号备注，长度 1–100 字')
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'正在两次只读检查 {account} 的前两个置顶群；请保持普通主界面、搜索为空。')
        def worker():
            try:
                from controls_probe import run_window_script
                first=run_window_script(window,'inspect_groups.ps1')
                time.sleep(0.5)
                second=run_window_script(window,'inspect_groups.ps1')
                consistent_pair(first,second,window)
                self.probe_queue.put((True,{'pinned_probe_result':{'account':account,'window':window,'first':first,'second':second}}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def navigate_pinned_groups(self, slot):
        self.group_operation_ready()
        observations=self.selected_pinned_observations()
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'group_open_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'正在按窗口顺序打开群 {slot} 的添加成员页面；请暂时不要操作 Telegram。')
        def worker():
            try:
                result=open_pinned_groups(observations,slot,path,
                    lambda message:self.probe_queue.put((True,{'progress_result':message})))
                self.probe_queue.put((True,{'group_navigation_result':result}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def selected_pinned_observations(self, *, require_account_picker=True):
        selected=set(self.window_tree.selection())
        order=[key for key in self.window_tree.get_children() if key in selected]
        if not order:raise ValueError('先在扫描窗口页选择要测试的窗口，本轮先只选账号 A')
        observations=[]
        for key in order:
            window=dict(self.windows[int(key)])
            matches=[r for a in self.pinned_groups.accounts() if (r:=self.pinned_groups.get(a)) and
                all(r['window'].get(k)==window.get(k) for k in ('hwnd','pid','path'))]
            if len(matches)!=1:raise ValueError('所选窗口没有唯一的置顶群记录，请先为它识别并记录')
            if require_account_picker and len(order)==1 and matches[0]['account']!=self.group_account.get().strip():
                raise ValueError(f'所选窗口记录为 {matches[0]["account"]}，请将上方账号备注切换为这个账号')
            observations.append(matches[0])
        return observations

    def make_batch_plan_preparation(self):
        frame=self.batch_plan_frame
        ttk.Label(frame,text='添加队列结束后，一次预览并生成每个账号各自的成功联系人名单。已完成和无成功记录的批次跳过；不邀请、不增加编号。',wraplength=950).pack(anchor='w',pady=6)
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=6)
        ttk.Button(bar,text='预览当前队列名单',command=self.guarded(self.preview_batch_plans)).pack(side='left')
        ttk.Button(bar,text='确认生成全部可用名单',command=self.guarded(self.confirm_batch_plans)).pack(side='left',padx=8)
        ttk.Button(bar,text='导出批量名单报告',command=self.guarded(self.export_batch_plans)).pack(side='left')
        columns=('account','batch','count','numbers','group1','group2','state')
        self.batch_plan_tree=ttk.Treeview(frame,columns=columns,show='headings',height=12)
        for col,label,width in zip(columns,('账号','批次','成功人数','实际备注','群①','群②','状态'),(90,65,70,230,140,140,210)):
            self.batch_plan_tree.heading(col,text=label);self.batch_plan_tree.column(col,width=width,minwidth=55)
        scroll=ttk.Scrollbar(frame,command=self.batch_plan_tree.yview);self.batch_plan_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.batch_plan_tree.pack(fill='both',expand=True)
        self.batch_plan_summary=tk.StringVar(value='先完成添加队列，再预览所有账号名单。')
        ttk.Label(frame,textvariable=self.batch_plan_summary,wraplength=950).pack(anchor='w',pady=8)

    def render_batch_plans(self,result):
        for key in self.batch_plan_tree.get_children():self.batch_plan_tree.delete(key)
        states={'ready':'可生成','completed':'已完成，跳过','empty':'无成功记录，跳过'}
        for row in result['rows']:
            label=states[row['state']]+('；结束本批剩余添加' if row['finish_adding'] else '')
            self.batch_plan_tree.insert('', 'end',values=(row['account'],row['batch_id'],len(row['numbers']),
                ', '.join(row['numbers']),row['record']['targets'][0]['name'],row['record']['targets'][1]['name'],label))
        ready=sum(r['state']=='ready' for r in result['rows'])
        self.batch_plan_summary.set(f'队列 #{result["queue_id"]}：{len(result["rows"])} 个账号，{ready} 个可生成。'+
            ('已统一保存；群①、群②沿用这些名单。' if result['state']=='saved' else '核对表格后点击确认；预览尚未保存。'))

    def preview_batch_plans(self):
        self.group_operation_ready();self.batch_plan_preview=None
        result=self.batch_plan_preparation.preview();self.batch_plan_preview=result
        self.render_batch_plans(result);self.status.set('各账号实际成功名单已预览，未保存或邀请。')

    def confirm_batch_plans(self):
        self.group_operation_ready()
        if not self.batch_plan_preview:raise ValueError('请先预览当前队列名单，再统一确认')
        result=self.batch_plan_preparation.confirm(self.batch_plan_preview);self.batch_plan_preview=None
        self.render_batch_plans(result);self.load_pinned_member_input();self.refresh_groups()
        self.status.set(f'已统一生成 {len(result["saved"])} 个账号的两群名单；其余批次跳过，未邀请。')

    def export_batch_plans(self):
        result=self.batch_plan_preview or self.batch_plan_preparation.latest()
        if not result:raise ValueError('请先预览当前队列名单')
        path=filedialog.asksaveasfilename(title='导出批量名单报告',initialfile='批量两群名单.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');self.status.set('批量名单报告已导出。')

    def cleanup_selected_groups(self):
        self.group_operation_ready()
        records=self.selected_pinned_observations(require_account_picker=False)
        contacts={r['account']:saved_addition_contacts(self.store,r,self.pinned_member_plans.get(r['account'])) for r in records}
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'group_cleanup_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在整理所选窗口页面，不选人、不邀请；请暂时不要操作 Telegram。')
        def worker():
            try:
                result=clean_group_pages(records,contacts,path,lambda message:self.probe_queue.put((True,{'progress_result':message})))
                self.probe_queue.put((True,{'group_cleanup_result':result}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def export_group_cleanup(self):
        if not self.group_cleanup_report:raise ValueError('请先执行整理所选窗口页面')
        path=filedialog.asksaveasfilename(title='导出页面整理报告',initialfile='群页面整理.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:Path(path).write_text(json.dumps(self.group_cleanup_report,ensure_ascii=False,indent=2),encoding='utf-8');self.status.set('页面整理报告已导出。')

    def save_pinned_member_plan(self):
        self.group_operation_ready()
        account=self.group_account.get().strip()
        record=self.pinned_groups.get(account)
        if not record:raise ValueError('先识别并记录该账号的两个置顶群')
        window=self.selected_group_window()
        if not all(record['window'].get(k)==window.get(k) for k in ('hwnd','pid','path')):
            raise ValueError('扫描页所选窗口与上方账号备注不对应，请重新选择')
        labels=parse_member_labels(self.pinned_member_input.get())
        self.pinned_member_plans.save(record,labels);self.refresh_groups()
        self.status.set(f'{account} 本次两个群共用备注：'+', '.join(labels)+'。之后运行群②不用再次保存。')

    def pause_member_selection(self):
        if not self.member_selection_active:
            self.status.set('当前没有正在执行的群选人任务。');return
        self.member_pause.set()
        self.status.set('已请求暂停；当前操作完成后停止后续动作，保留已选页面和报告。没有续跑，下一次从头选人。')

    def select_pinned_members(self,slot):
        self.group_operation_ready()
        plans=self.pinned_member_plans.entries(self.selected_pinned_observations(),slot)
        contacts={p['record']['account']:saved_contact_snapshot(self.store,p['record'],p) for p in plans}
        from backup_ocr import require_environment
        require_environment()
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'pinned_members_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.member_pause.clear();self.member_selection_active=True
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'正在打开群 {slot} 并搜索选人；请暂时不要操作 Telegram，最终 Add 由你点击。')
        def worker():
            try:
                result=select_pinned_member_queue(plans,slot,path,
                    lambda message:self.probe_queue.put((True,{'progress_result':message})),
                    prepare_pages=True,contacts_by_account=contacts,fast_visible=True,should_stop=self.member_pause.is_set)
                self.probe_queue.put((True,{'pinned_members_result':result}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def pinned_account_record(self):
        account=self.group_account.get().strip()
        record=self.pinned_groups.get(account)
        if not record:raise ValueError('先识别并记录该账号的两个置顶群')
        window=self.selected_group_window()
        if not all(record['window'].get(k)==window.get(k) for k in ('hwnd','pid','path')):
            raise ValueError('扫描页所选窗口与上方账号备注不对应，请只选择当前账号窗口')
        return account,record

    def make_pinned_batch_plan(self):
        self.group_operation_ready()
        account,record=self.pinned_account_record()
        batch_id=self.current_batch()
        batch,members=self.pinned_member_plans.batch_members(batch_id,allow_partial=True)
        if batch['account']!=account:raise ValueError('使用记录当前批次不属于上方账号，请切换到对应批次')
        finish=batch['status']=='active' and len(members)<batch['target']
        if finish and not messagebox.askyesno('结束本批剩余添加',
                f'{account} · 批次 #{batch_id}：计划 {batch["target"]} 位，目前仅 {len(members)} 位已登记成功。\n'
                '确定结束这批剩余添加，仅使用已经成功的联系人生成两群名单吗？\n'
                '不会补记成功人数，也不会执行邀请。',parent=self.root):return
        plan=self.pinned_member_plans.save_batch(record,batch_id,finish_adding=finish)
        self.load_pinned_member_input();self.refresh_groups()
        self.status.set(f'{account} · 批次 #{plan["batch_id"]}：已生成 {len(plan["numbers"])} 位成功联系人的两群名单，未执行邀请。')

    def confirm_pinned_group_invited(self,slot):
        self.group_operation_ready()
        account,record=self.pinned_account_record()
        plan=self.pinned_member_plans.get(account)
        if not plan or plan.get('source')!='confirmed_batch':raise ValueError('此按钮只适用于从使用记录生成的批次名单，手填测试不登记邀请结果')
        from pinned_members import same_binding
        if not same_binding(record,plan['record']):raise ValueError('当前窗口或目标群与冻结批次不同，请核查后再登记')
        job=self.pinned_member_plans.confirmation_job(account,slot)
        if job.get('plan_id')!=plan['plan_id'] or job.get('state') not in ('waiting_for_manual_invite','confirmed_invited'):
            raise ValueError('请先完成本批名单在这个群的搜索选人核对')
        group=plan['record']['targets'][slot-1]['name']
        recovered=('已找到本批此前通过的选人记录；后续失败未进入选人。\n'
                   if job.get('selection_evidence_recovered_from_run') is not None else '')
        if not messagebox.askyesno('核实本群全部邀请结果',
                f'{account} · 批次 #{plan["batch_id"]} · 群 {slot}「{group}」\n'
                f'本批 {len(plan["numbers"])} 位，备注：'+', '.join(plan['numbers'])+'\n\n'
                +recovered+'你已在 Telegram 点击 Add，并核实这些成员全部加入这个群了吗？\n'
                '只是选中、取消弹窗或部分加入，不能确认全部成功。',parent=self.root):return
        result=self.pinned_member_plans.confirm_group_invited(account,slot)
        self.refresh();self.refresh_groups()
        if result.get('all_groups_confirmed'):
            self.status.set(f'{account} 两群成功结果均已登记。'+
                ('本批仍有未确认任务，保留待核查。' if result.get('held_items') else '本批完成，可以建立下一批。'))
        else:self.status.set(f'{account} 群 {slot} 成功结果已登记；另一群还未确认，本批尚未完成。')

    def load_pinned_member_input(self):
        plan=self.pinned_member_plans.get(self.group_account.get().strip())
        self.pinned_member_input.set(', '.join(plan['numbers']) if plan else '')

    def export_pinned_members(self):
        result=self.pinned_member_plans.latest()
        if not result:raise ValueError('先运行一次“打开群并选择联系人”')
        path=filedialog.asksaveasfilename(title='导出两群选人汇总',initialfile='两群选人报告.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('两群选人报告已导出；失败时先反馈这一份 JSON，不用上传全部截图。')

    def export_group_navigation(self):
        result=self.pinned_groups.latest_navigation()
        if not result:raise ValueError('先运行一次群打开测试')
        path=filedialog.asksaveasfilename(title='导出最近一次群打开报告',initialfile='群打开测试.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('群打开报告已导出；失败时反馈这一个 JSON 即可。')

    def make_adding_queue(self):
        frame=self.adding_tab
        ttk.Label(frame,text='支持手机号或用户名两种独立添加队列，数字备注共用；添加联系人不需要置顶群；在“扫描窗口”选择执行窗口，再设置账号备注和人数。入群选人前再确认两个目标群。',wraplength=960).pack(anchor='w')
        ttk.Label(frame,text='已接入：自动领取手机号、向空白 New Contact 表单试填、一次完整资料页核验登记；手机号连续两次失败后准备下一账号；明确限制或未知错误暂停核查。\n'
            '从普通聊天页自动打开、填写 New Contact，核对原表单后只执行一次 Create / Criar，再打开资料页核验。\n'
            '上一位成功联系人资料页完整匹配后自动关闭，再打开下一位；群资料页、其他联系人及未知弹窗不会自动关闭。\n'
            '本轮只使用手机号；完成添加后在“账号群聊 → 批次名单”统一生成两群名单。最终 Add / Invite 由你点击。',wraplength=960).pack(anchor='w',pady=8)
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=8)
        for text,fn in (('绑定所选窗口并设置人数',self.configure_adding_queue),('开始添加队列测试',self.start_adding_queue),
                        ('核验原手机号失败弹窗',self.recover_unregistered),('停止队列',self.stop_adding_queue),('清空账号队列',self.clear_adding_queue),('导出添加队列报告',self.export_adding_queue)):
            ttk.Button(bar,text=text,command=self.guarded(fn)).pack(side='left',padx=4)
        ttk.Button(frame,text='核验用户名不存在并继续',command=self.guarded(self.recover_username_missing)).pack(anchor='w',pady=4)
        ttk.Button(frame,text='重新核验未完成用户名（弹窗已关闭／重启）',command=self.guarded(self.restart_unsubmitted_username)).pack(anchor='w',pady=4)
        ttk.Button(frame,text='核验已提交资料页并继续',command=self.guarded(self.recover_submitted_profile)).pack(anchor='w')
        ttk.Button(frame,text='核验用户名仍未添加并重新提交一次',command=self.guarded(self.retry_unsaved_username)).pack(anchor='w',pady=4)
        ttk.Button(frame,text='恢复未填写队列并开始',command=self.guarded(self.restore_adding_queue)).pack(anchor='w')
        self.adding_summary=tk.StringVar(value='尚未配置账号添加队列。')
        ttk.Label(frame,textvariable=self.adding_summary,wraplength=960).pack(anchor='w',pady=8)
        cols=('order','account','window','target','actual','state','number')
        self.adding_tree=ttk.Treeview(frame,columns=cols,show='headings',height=12)
        for col,title,width in zip(cols,('顺序','账号备注','窗口','计划人数','已成功人数','状态','当前备注预览'),(50,110,180,75,90,230,110)):
            self.adding_tree.heading(col,text=title);self.adding_tree.column(col,width=width)
        self.adding_tree.pack(fill='both',expand=True)
        ttk.Label(frame,text='受限或结果不明的那一条保留占用，不交给其他账号重复使用。已成功联系人保留；旧账号窗口不会被关闭。\n'
            '未知错误、窗口身份变化、试填失败或程序中断会停止队列；重启后不会自行恢复未确认操作。',wraplength=960).pack(anchor='w',pady=8)
        self.refresh_adding_queue()

    def configure_adding_queue(self):
        if self.probing or self.adding_queue.current():raise ValueError('先等待界面操作结束并停止现有添加队列')
        selected=set(self.window_tree.selection())
        rows=[dict(self.windows[int(i)]) for i in self.window_tree.get_children() if i in selected]
        rows=addition_windows(rows)
        dialog=tk.Toplevel(self.root);dialog.title('账号窗口与计划人数');dialog.geometry('980x520');dialog.transient(self.root)
        ttk.Label(dialog,text='按窗口列表顺序执行。请自己核对每个窗口的账号，在对应行填写稳定账号备注；人数是本次新批次的成功添加目标；旧批次名单和未完成邀请保留，不预占编号。',wraplength=920,padding=10).pack(fill='x')
        start_bar=ttk.Frame(dialog);start_bar.pack(fill='x',padx=10,pady=6)
        ttk.Label(start_bar,text='名单类型：').pack(side='left')
        contact_source=tk.StringVar(value='手机号')
        ttk.Combobox(start_bar,textvariable=contact_source,values=('手机号','用户名'),state='readonly',width=8).pack(side='left',padx=5)
        ttk.Label(start_bar,text='起始名单序号：').pack(side='left')
        start_phone_seq=tk.StringVar(value='1')
        ttk.Entry(start_bar,textvariable=start_phone_seq,width=10).pack(side='left')
        ttk.Label(start_bar,text='手机号例266；手机号连续失败2次后同账号改用用户名（从1开始）；用户名连续不存在2次换账号。').pack(side='left',padx=8)
        outer=ttk.Frame(dialog);outer.pack(fill='both',expand=True,padx=10)
        canvas=tk.Canvas(outer,highlightthickness=0);scroll=ttk.Scrollbar(outer,command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        grid=ttk.Frame(canvas);canvas.create_window((0,0),window=grid,anchor='nw')
        grid.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        entries=[]
        for index,window in enumerate(rows,1):
            ttk.Label(grid,text=f'{index}. 0x{window["hwnd"]:X} · {window["title"][:48]}',width=55).grid(row=index,column=0,sticky='w',pady=6)
            bound=[a for a in self.pinned_groups.accounts() if all(self.pinned_groups.get(a)['window'].get(k)==window.get(k) for k in ('hwnd','pid','path'))]
            account=tk.StringVar(value=bound[0] if len(bound)==1 else '')
            target=tk.StringVar(value='20')
            ttk.Entry(grid,textvariable=account,width=16,state='readonly' if len(bound)==1 else 'normal').grid(row=index,column=1,padx=6)
            ttk.Spinbox(grid,from_=1,to=40,textvariable=target,width=6).grid(row=index,column=2,padx=6)
            entries.append((window,account,target))
        def save():
            plan=[{'window':w,'account':a.get(),'target':int(t.get())} for w,a,t in entries]
            source='username' if contact_source.get()=='用户名' else 'phone'
            start=int(start_phone_seq.get())
            self.adding_queue.configure(plan,auto_submit=True,auto_close_profile=True,new_batch=True,independent=True,contact_source=source,start_phone_seq=start if source=='phone' else 1,start_username_seq=start if source=='username' else 1,phone_username_fallback=source=='phone');self.refresh();dialog.destroy()
            self.status.set(f'已保存{contact_source.get()}添加队列，从名单序号{start_phone_seq.get()}开始，旧记录保留；联系人编号不重置。点击开始添加队列测试。')
        ttk.Button(dialog,text='保存账号队列',command=self.guarded(save)).pack(anchor='e',padx=12,pady=12)

    def start_adding_queue(self):
        if self.probing:raise ValueError('界面操作尚未结束')
        qid=self.adding_queue.latest()
        if not qid:raise ValueError('请先绑定所选窗口并设置计划人数')
        if self.adding_queue.snapshot(qid)['target_mode'] not in ('pinned','contact'):raise ValueError('这是旧版队列，请重新绑定所选窗口并设置人数')
        if not self.adding_queue.snapshot(qid)['auto_submit']:raise ValueError('这是旧版手动 Create 队列。请重新“绑定所选窗口并设置人数”，新队列才启用自动 Create；原记录保留')
        if not self.adding_queue.snapshot(qid)['auto_close_profile']:raise ValueError('这是旧版不自动关闭资料页的队列，请重新“绑定所选窗口并设置人数”，原成功记录保留')
        snapshot=self.adding_queue.snapshot(qid)
        if snapshot['state']!='configured':raise ValueError('请配置新的添加队列；未确认操作需先核查，不能自动重试')
        if snapshot['contact_source']=='username':
            job=self.adding_queue.start(qid)
            self.refresh(select_batch=job['batch_id'] if job else None)
            self.status.set('用户名队列已开始；请保持执行账号为普通聊天页，不手动操作。')
            self.root.after(0,self.drive_contact_queue)
            return
        records=[]
        independent=snapshot['target_mode']=='contact'
        for job in snapshot['jobs']:
            if independent:
                record={'account':job['account'],'window':job['window'],'targets':[],
                        'list_runtime_id':None,'contact_only':True}
            else:
                record=self.pinned_groups.get(job['account'])
                if not record or any(record['window'].get(k)!=job['window'].get(k) for k in ('hwnd','pid','path')):
                    raise ValueError('账号窗口群绑定已变化，请重新核对')
            records.append(record)
        contacts={r['account']:saved_addition_contacts(self.store,r,
            None if independent else self.pinned_member_plans.get(r['account'])) for r in records}
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'contact_start_cleanup_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在自动取消未提交选人、关闭已核对资料页并清空搜索；整理通过后自动开始添加。')
        def worker():
            try:
                result=clean_group_pages(records,contacts,path,lambda message:self.probe_queue.put((True,{'progress_result':message})))
                self.probe_queue.put((True,{'adding_cleanup_result':result,'queue_id':qid}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def retry_unsaved_username(self):
        if self.probing:raise ValueError('请等待当前操作结束')
        qid=self.adding_queue.latest();snap=self.adding_queue.snapshot(qid)
        jobs=[j for j in (snap or {}).get('jobs',[]) if j['state'] in ('submitting','submitted') and j.get('item') and j['item']['source']=='username']
        if not snap or snap['state'] not in ('review','stopped') or len(jobs)!=1:
            raise ValueError('需要一条提交后暂停的用户名任务，保留未添加的原资料页')
        job=jobs[0];self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('两次核验原用户名仍未添加，通过后重新提交原任务一次。')
        def worker():
            try:
                reports=[inspect_controls(job['window'],queue_probe=True)]
                time.sleep(.5);reports.append(inspect_controls(job['window'],queue_probe=True))
                self.contact_results.put(('retry_unsaved_username',qid,job,reports,None))
            except Exception as e:self.contact_results.put(('retry_unsaved_username',qid,job,[],str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def recover_submitted_profile(self):
        self.recover_unregistered(profile=True)

    def recover_unregistered(self,*,profile=False):
        if self.probing:raise ValueError('请等待当前操作结束')
        qid=self.adding_queue.latest();snap=self.adding_queue.snapshot(qid)
        jobs=[j for j in (snap or {}).get('jobs',[]) if (j['state'] in ('submitting','submitted') or (profile and j['state']=='opening' and j.get('retry_unsaved_profile_runtime_id'))) and j['item']]
        if not snap or snap['state'] not in ('review','stopped') or len(jobs)!=1:
            raise ValueError('需要有且仅有一条提交后待核查的原联系人任务')
        job=jobs[0]
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'{job["account"]}：核验原联系人'+('已提交资料页' if profile else '手机号失败结果')+'；不重新 Create。')
        def worker():
            try:
                reports=[inspect_controls(job['window'],queue_probe=True)]
                time.sleep(.5);reports.append(inspect_controls(job['window'],queue_probe=True))
                self.contact_results.put(('recover_profile' if profile else 'recover_unregistered',qid,job,reports,None))
            except Exception as error:self.contact_results.put(('recover_profile' if profile else 'recover_unregistered',qid,job,[],str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def restart_unsubmitted_username(self):
        if self.probing:raise ValueError('请等待当前操作结束')
        selected=list(self.window_tree.selection())
        if len(selected)!=1:raise ValueError('请在扫描窗口中选中原账号的一个窗口，并确认账号未切换')
        window=dict(self.windows[int(selected[0])])
        qid=self.adding_queue.latest()
        job=self.adding_queue.restart_unsubmitted_username(qid,window)
        self.refresh()
        self.start_contact_open(qid,job['id'])

    def recover_username_missing(self,*,auto=False):
        if self.probing:raise ValueError('请等待当前操作结束')
        qid=self.adding_queue.latest();snap=self.adding_queue.snapshot(qid)
        jobs=[j for j in (snap or {}).get('jobs',[]) if j['state']=='opening' and j.get('item') and j['item']['source']=='username']
        if not snap or snap['state'] not in ('review','stopped') or len(jobs)!=1:
            raise ValueError('需要有且仅有一个解析用户名后暂停的原任务；保留对应不存在弹窗，不要清空队列')
        job=jobs[0];self.probing=True;self.probe_button.configure(state='disabled')
        mode='recover_username_missing_auto' if auto else 'recover_username_missing'
        self.status.set('正在两次只读核验原用户名不存在弹窗；不会重新打开用户名或提交联系人。')
        def worker():
            try:
                reports=[inspect_controls(job['window'],queue_probe=True)]
                time.sleep(.5);reports.append(inspect_controls(job['window'],queue_probe=True))
                self.contact_results.put((mode,qid,job,reports,None))
            except Exception as error:self.contact_results.put((mode,qid,job,[],str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_username_missing_dismiss(self,qid,job,reports):
        claimed,payload=self.adding_queue.claim_username_missing(qid,job['id'],reports)
        self.probing=True;self.probe_button.configure(state='disabled')
        def worker():
            try:self.contact_results.put(('dismiss_username_missing',qid,claimed,run_window_script(claimed['window'],'username_contact.ps1',payload),None))
            except Exception as error:self.contact_results.put(('dismiss_username_missing',qid,claimed,error.report if hasattr(error,'report') else None,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_unregistered_dismiss(self,qid,job,reports,*,recovery=False):
        claimed,payload=self.adding_queue.claim_unregistered(qid,job['id'],reports,recovery=recovery)
        self.probing=True;self.probe_button.configure(state='disabled')
        def worker():
            try:self.contact_results.put(('dismiss_unregistered',qid,claimed,run_window_script(claimed['window'],'unregistered_contact.ps1',payload),None))
            except Exception as error:self.contact_results.put(('dismiss_unregistered',qid,claimed,error.report if hasattr(error,'report') else None,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def stop_adding_queue(self):
        qid=self.adding_queue.latest()
        if not qid:return
        self.adding_queue.stop(qid);self.refresh_adding_queue()
        self.status.set('已停止后续任务；当前界面操作结束后保留报告。Create 可能已执行，核查实际页面，不重复创建。')

    def restore_adding_queue(self):
        if self.probing:raise ValueError('当前界面操作尚未结束，请等待')
        qid=self.adding_queue.latest() or self.adding_queue.latest(include_cleared=True)
        if not qid:raise ValueError('没有可恢复的历史队列')
        new_id=self.adding_queue.restore_waiting(qid)
        job=self.adding_queue.start(new_id)
        self.refresh(select_batch=job['batch_id'] if job else None)
        self.status.set('未填写任务已恢复；保留同一条占用手机号与已成功记录，回普通聊天页后自动打开并试填。')

    def export_adding_queue(self):
        path=filedialog.asksaveasfilename(title='保存添加队列报告',initialfile='添加队列测试.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:self.adding_queue.export(path);self.status.set('添加队列报告已保存；包含窗口绑定、进度和已识别限制的只读控件报告。')

    def clear_adding_queue(self):
        if self.probing:raise ValueError('请先停止队列，等待当前界面操作结束后再清空')
        qid=self.adding_queue.latest()
        if not qid:
            self.status.set('账号队列已经为空；名单与全局编号保留。');return
        if self.adding_queue.snapshot(qid)['state']=='running':
            raise ValueError('请先点击“停止队列”，等待当前操作结束后再清空')
        import uuid
        folder=DATA/'reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'contact_queue_before_clear_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json'
        self.adding_queue.export(path,queue_id=qid)
        result=self.adding_queue.clear()
        self.refresh()
        text=f'账号队列已清空。名单、添加状态、历史记录和全局编号保留。\n下一个编号：{result["next_number"]}。'
        if result['held_items']:
            text+=f'\n\n还有 {len(result["held_items"])} 条已占用或待核查记录。已创建但尚未核验的联系人不要重复添加。\n请导出添加队列报告核查；这些记录不会重新交给队列。'
        text+=f'\n\n清空前报告已保存：\n{path}\n“导出添加队列报告”仍可导出最近一次清空的历史。'
        self.show_text('账号队列已清空',text)
        self.status.set(f'账号队列已清空；保留 {len(result["held_items"])} 条待核查记录，下一个编号 {result["next_number"]}。')

    def refresh_adding_queue(self):
        if not hasattr(self,'adding_tree'):return
        self.adding_tree.delete(*self.adding_tree.get_children())
        snapshot=self.adding_queue.snapshot()
        if not snapshot:
            held=sum(r['status'] in ('reserved','uncertain') for r in self.store.rows())
            self.adding_summary.set(f'账号队列为空；下一个全局编号 {self.store.next_contact_number()}。'+
                (f'还有 {held} 条已占用或待核查记录，请先核验，不能重复添加。' if held else '可以重新绑定窗口并设置人数。'))
            return
        states={'queued':'未执行','waiting_form':'准备下一位表单','closing_profile':'正在关闭上一位已核验资料页','opening':'正在打开添加表单','filling':'正在填写，未提交','filled':'已填写 / 准备提交','dismissing_username_missing':'正在核验并关闭用户名不存在弹窗','dismissing_unregistered':'正在登记手机号失败结果','submitting':'正在创建并打开资料页','submitted':'已提交，等待资料页核验',
                'paused':'本账号已暂停／跳过','target_reached':'已达到计划人数','no_list':'无可用名单'}
        for job in snapshot['jobs']:
            self.adding_tree.insert('','end',values=(job['position'],job['account'],f'0x{job["window"]["hwnd"]:X}',job['target'],len(job['added_numbers']),
                states.get(job['state'],job['state']),job['preview_number'] or ''))
        state={'configured':'待开始','running':'执行中','stopped':'已停止','review':'待核查','done':'添加队列结束'}.get(snapshot['state'],snapshot['state'])
        job=self.adding_queue.current(snapshot['id'])
        current=f'；当前 {job["account"]}：{job["item"]["value"]}' if job and job['item'] else ''
        if job:current+=f'；当前名单类型：{job["active_source"]}；手机号连续失败{job["lookup_failures"]}次／用户名连续不存在{job["username_failures"]}次'
        if job and job.get('target_groups'):current+='；本批目标群：'+'、'.join(g['name'] for g in job['target_groups'])
        source_label="用户名" if snapshot["contact_source"]=="username" else "手机号→用户名" if snapshot["phone_username_fallback"] else "手机号"
        start_seq=snapshot["start_username_seq"] if snapshot["contact_source"]=="username" else snapshot["start_phone_seq"]
        self.adding_summary.set(f'队列 #{snapshot["id"]}：{state}；下一个全局编号 {snapshot["next_number"]}'+current+f'；{source_label}起始名单序号 {start_seq}\n'+snapshot['reason'])

    def tick_contact_queue(self):
        self.root.after(4000,self.tick_contact_queue)
        self.drive_contact_queue()

    def auto_check_missing_username(self):
        # One read-only check for an error-paused resolution; never resume a user Stop.
        snap=self.adding_queue.snapshot()
        if not snap or snap['state']!='review':return
        jobs=[j for j in snap['jobs'] if j['state']=='opening' and j.get('item') and j['item']['source']=='username'
            and j['item']['status']=='reserved' and j['preview_number'] is None and j['batch_status']=='active']
        if len(jobs)!=1:return
        job=jobs[0];old=job.get('opening_report') or {}
        if (old.get('mode')!='open' or old.get('stage')!='resolve_requested'
                or any(old.get(k) is not False for k in ('fields_written','add_invoked','create_attempted','create_invoked'))):return
        checked=getattr(self,'username_missing_auto_checked',set())
        key=(snap['id'],job['id'],job['item_id'])
        if key in checked:return
        checked.add(key);self.username_missing_auto_checked=checked
        self.recover_username_missing(auto=True)

    def drive_contact_queue(self):
        # Completion callbacks drive ready operations; the periodic tick only
        # handles passive waiting. No second periodic chain is created.
        if self.probing:return
        job=self.adding_queue.current()
        if not job:
            self.auto_check_missing_username()
            return
        qid=self.adding_queue.latest()
        if job['state'] in ('closing_profile','opening','filling','submitting','dismissing_unregistered','dismissing_username_missing'):
            self.adding_queue.stop(qid,'打开、填写或提交曾中断；Create 可能已执行，核查实际页面，不自动重试',review=True);self.refresh();return
        if job['state'] in ('filled','submitted') and job['preview_number']!=self.store.next_contact_number():
            self.adding_queue.stop(qid,'全局编号已被其他记录改变，请核查已填联系人',review=True);self.refresh();return
        if job['item']['source']=='username' and job['state']=='waiting_form':
            try:self.start_contact_open(qid,job['id'])
            except Exception as error:
                self.adding_queue.stop(qid,str(error),review=True);self.refresh();self.status.set(str(error))
            return
        auto_close=self.adding_queue.snapshot(qid)['auto_close_profile']
        self.probing=True;self.probe_button.configure(state='disabled')
        def worker():
            try:
                reports=[inspect_controls(job['window'],queue_probe=True)]
                if (job['state']=='filled' or (job['state']=='submitted'
                        and reports[0].get('scope')!='profile')):
                    time.sleep(0.5);reports.append(inspect_controls(job['window'],queue_probe=True))
                self.contact_results.put(('observe',qid,job,reports,None))
            except Exception as error:self.contact_results.put(('observe',qid,job,[error.report] if hasattr(error,'report') else [],str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def poll_contact_worker(self):
        try:mode,qid,job,result,error=self.contact_results.get_nowait()
        except queue.Empty:self.root.after(100,self.poll_contact_worker);return
        self.probing=False;self.probe_button.configure(state='normal')
        if mode=='retry_unsaved_username':
            try:
                if error:raise ValueError(error)
                claimed=self.adding_queue.retry_unsaved_username(qid,result)
                self.start_contact_open(qid,claimed['id'])
            except Exception as e:self.status.set('重新提交核验未通过：'+str(e))
            self.refresh();return
        if mode=='close_saved_username':
            from username_contact import saved_profile_close_verified
            with self.store.db:
                self.store._event('username_saved_profile_close_result',job['item_id'],job['batch_id'],json.dumps({'report':result,'error':error},ensure_ascii=False))
            if error or not saved_profile_close_verified(result,job):
                self.adding_queue.stop(qid,'联系人已成功添加，但资料页关闭未核验；请手动关闭后再继续',review=True)
                self.status.set('添加成功记录保留；资料页关闭未核验，请检查当前窗口。')
            else:
                self.status.set('用户名联系人已核验添加成功，资料页已关闭。')
                self.root.after(0,self.drive_contact_queue)
            self.refresh();return
        if mode=='open':
            try:self.adding_queue.save_open_result(qid,job['id'],job['item_id'],result,error)
            except Exception as e:
                self.adding_queue.stop(qid,str(e),review=True);self.refresh();return
        if mode=='submit':
            try:self.adding_queue.save_submit_result(qid,job['id'],job['item_id'],result,error)
            except Exception as e:
                self.adding_queue.stop(qid,str(e),review=True);self.refresh();return
        if mode=='close_profile':
            try:self.adding_queue.save_profile_close_result(qid,job['id'],job['item_id'],result,error)
            except Exception as e:
                self.adding_queue.stop(qid,str(e),review=True);self.refresh();return
        if mode=='recover_profile':
            try:
                if error:raise ValueError(error)
                recovered=self.adding_queue.recover_submitted_profile(qid,job['id'],result)
                if recovered:job=recovered
                next_job=self.adding_queue.current(qid)
                self.refresh(select_batch=next_job['batch_id'] if next_job else None)
                self.status.set('原提交资料页核验通过，已登记成功并继续原队列；未再次 Create。')
                if job['item']['source']=='username':self.start_saved_username_close(qid,job,result)
                else:self.root.after(0,self.drive_contact_queue)
            except Exception as e:self.status.set('已提交资料页核验未通过：'+str(e))
            return
        if mode in ('recover_username_missing','recover_username_missing_auto'):
            try:
                if error:raise ValueError(error)
                self.start_username_missing_dismiss(qid,job,result)
            except Exception as e:self.status.set('用户名不存在核验未通过：'+str(e))
            return
        if mode=='dismiss_username_missing':
            self.adding_queue.save_username_missing_result(qid,job['id'],job['item_id'],result,error)
        if mode=='recover_unregistered':
            try:
                if error:raise ValueError(error)
                self.start_unregistered_dismiss(qid,job,result,recovery=True)
            except Exception as e:self.status.set('手机号失败弹窗核验未通过：'+str(e))
            return
        if mode=='dismiss_unregistered':
            self.adding_queue.save_unregistered_result(qid,job['id'],job['item_id'],result,error)
        current=self.adding_queue.current(qid)
        if not current or current['id']!=job['id'] or current['item_id']!=job['item_id']:
            self.refresh_adding_queue();return
        try:
            if mode=='observe':self.adding_queue.save_read(qid,job['id'],result)
            if error:raise RuntimeError(error)
            if mode=='dismiss_username_missing':
                self.adding_queue.finish_username_missing(qid,job['id'],job['item_id'])
                self.status.set('原用户名不存在已核验并记录失败；编号不变，继续下一用户名。')
                self.root.after(0,self.drive_contact_queue)
            elif mode=='dismiss_unregistered':
                self.adding_queue.finish_unregistered(qid,job['id'],job['item_id'])
                snapshot=self.adding_queue.snapshot(qid)
                if snapshot['state']=='review':
                    self.status.set(snapshot['reason'])
                    messagebox.showwarning('添加队列已暂停',snapshot['reason']+'\n\n失败号码保留待核查；请核查上述队列暂停原因。',parent=self.root)
                else:
                    finished_job=next(j for j in snapshot['jobs'] if j['id']==job['id'])
                    if finished_job['state']=='paused':
                        following=self.adding_queue.current(qid)
                        self.status.set(finished_job['reason']+(f' 下一账号：{following["account"]}。' if following else ' 没有剩余账号，队列已结束。'))
                    else:
                        self.status.set(finished_job['reason'] or f'{job["account"]}：手机号添加失败，已保留待核查；编号不递增。')
                    self.root.after(0,self.drive_contact_queue)
            elif mode=='open':
                outcome=self.adding_queue.finish_open(qid,job['id'],job['item_id'])
                if outcome=='username_not_found':
                    self.status.set('用户名不存在，已记录失败并跳过；备注编号不递增，继续下一用户名。')
                    self.root.after(0,self.drive_contact_queue)
                else:self.start_contact_fill(qid,job['id'])
            elif mode=='close_profile':
                self.adding_queue.finish_profile_close(qid,job['id'],job['item_id'])
                self.start_contact_open(qid,job['id'])
            elif mode=='submit':
                self.adding_queue.finish_submit(qid,job['id'],job['item_id'])
                self.status.set(f'{job["account"]}：Create 已执行，等待两次资料页或限制弹窗核验；编号暂不递增。')
                self.root.after(0,self.drive_contact_queue)
            elif mode=='fill':
                self.adding_queue.finish_fill(qid,job['id'],result)
                if self.adding_queue.snapshot(qid)['auto_submit']:self.start_contact_submit(qid,job['id'])
                else:self.status.set(f'{job["account"]} 已试填备注 {job["preview_number"]}；请手动点击 Create / Criar，再打开此联系人的资料页，等待自动核验。')
            elif (job['state'] in ('waiting_form','filled','submitted') and len(result)==(1 if job['state']=='waiting_form' else 2)
                    and any(awaiting_form_window(r,job['window']) for r in result)
                    and all(awaiting_form_window(r,job['window']) or complete_report(r,job['window']) for r in result)):
                if job['state']=='waiting_form':
                    self.start_contact_open(qid,job['id'])
                elif job['state']=='submitted':raise ValueError('提交后已打开的资料页不再可见，核查实际联系人，不自动重试')
                else:self.status.set(f'{job["account"]}：等待你打开联系人资料页；完整匹配前不登记成功。')
            elif not all(complete_report(r,job['window']) for r in result):
                raise ValueError('只读控件报告不完整，未自动识别或添加；请导出报告核查')
            elif job['state']=='submitted' and unregistered_evidence(result,job['window']):
                self.start_unregistered_dismiss(qid,job,result)
            elif job['state'] in ('filled','submitted'):
                outcome=self.adding_queue.accept(qid,job['id'],result)
                if outcome:
                    self.status.set(f'{job["account"]}：'+('已确认限制，整个添加队列已暂停。' if outcome['outcome']=='restriction' else '已确认添加成功，准备下一位。'))
                    if outcome['outcome']=='restriction':
                        messagebox.showwarning('添加队列已暂停',self.adding_queue.snapshot(qid)['reason'],parent=self.root)
                    following=self.adding_queue.current(qid)
                    if outcome['outcome']=='added' and job['item']['source']=='username':
                        self.start_saved_username_close(qid,job,result)
                    elif (outcome['outcome']=='added' and following and following['state']=='waiting_form'
                            and following['account']==job['account']
                            and following['window']==job['window']
                            and job['item']['source']=='phone'
                            and self.adding_queue.snapshot(qid)['auto_close_profile']):
                        # These two fresh reads already proved the previous
                        # saved profile. The native close rechecks it before input.
                        self.start_contact_profile_close(qid,following['id'],result)
                    else:self.root.after(0,self.drive_contact_queue)
                elif result[-1]['scope']=='dialog' or job['state']=='submitted':
                    raise ValueError('资料页或弹窗未完整确认，停止核查，不登记成功或猜测为限制')
            elif result[0]['scope']=='contact_dialog':
                self.start_contact_fill(qid,job['id'])
            elif job['state']=='waiting_form' and result[0]['scope']=='profile':
                if self.adding_queue.snapshot(qid)['auto_close_profile']:self.start_contact_profile_close(qid,job['id'],result)
                else:self.status.set(f'{job["account"]}：请关闭资料面板回普通聊天页，随后会自动打开下一位的表单。')
            elif job['state']=='waiting_form':
                raise ValueError('当前页面不是普通聊天页或空白联系人表单，请核查；未打开或填写')
            next_job=self.adding_queue.current(qid)
            self.refresh(select_batch=next_job['batch_id'] if next_job else None)
        except Exception as e:
            self.adding_queue.stop(qid,str(e)+'；已占用联系人保留，不自动重试',review=True)
            self.refresh();self.status.set('添加队列已停止：'+str(e))

    def start_contact_profile_close(self,qid,job_id,reports):
        claimed=self.adding_queue.claim_profile_close(qid,job_id,reports)
        payload=profile_close_payload(claimed)
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'{claimed["account"]}：正在关闭已核验联系人 {payload["number"]} 的资料页，随后准备下一位。')
        def worker():
            try:self.contact_results.put(('close_profile',qid,claimed,run_window_script(claimed['window'],'close_contact_profile.ps1',payload),None))
            except Exception as e:self.contact_results.put(('close_profile',qid,claimed,e.report if hasattr(e,'report') else None,str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_saved_username_close(self,qid,job,reports):
        payload={'mode':'close_saved','username':job['item']['value'],'number':str(job['preview_number']),
            'executable_path':job['window']['path'],'main_runtime_id':job['opening_report']['main_runtime_id'],
            'profile_runtime_id':reports[-1]['scope_runtime_id']}
        job=dict(job,close_profile_runtime_id=payload['profile_runtime_id'])
        with self.store.db:
            self.store._event('username_saved_profile_close_started',job['item_id'],job['batch_id'],json.dumps(payload,ensure_ascii=False))
        self.probing=True;self.probe_button.configure(state='disabled')
        def worker():
            try:self.contact_results.put(('close_saved_username',qid,job,run_window_script(job['window'],'username_contact.ps1',payload),None))
            except Exception as e:self.contact_results.put(('close_saved_username',qid,job,e.report if hasattr(e,'report') else None,str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_contact_open(self,qid,job_id):
        claimed=self.adding_queue.claim_open(qid,job_id)
        self.probing=True;self.probe_button.configure(state='disabled')
        script='open_contact.ps1';payload={'executable_path':claimed['window']['path']}
        if claimed['item']['source']=='username':
            from username_contact import normalized_username
            script='username_contact.ps1';payload.update(mode='open',username=normalized_username(claimed['item']['value']))
            cleanup=self.adding_queue.username_phone_form_cleanup(claimed)
            if cleanup:payload['phone_form_cleanup']=cleanup
            if claimed.get('retry_unsaved_profile_runtime_id'):payload['retry_unsaved_profile_runtime_id']=claimed['retry_unsaved_profile_runtime_id']
            previous=self.store.db.execute("SELECT value,contact_number FROM items WHERE batch_id=? AND account=? AND source='username' AND status IN ('added','pending_invite','completed') AND contact_number IS NOT NULL ORDER BY contact_number DESC LIMIT 1",(claimed['batch_id'],claimed['account'])).fetchone()
            if previous:payload['previous']={'username':previous['value'],'number':str(previous['contact_number'])}
        self.status.set(f'{claimed["account"]}：正在打开'+('用户名资料页及添加表单。' if claimed['item']['source']=='username' else '空白 New Contact。'))
        def worker():
            try:self.contact_results.put(('open',qid,claimed,run_window_script(claimed['window'],script,payload),None))
            except Exception as e:self.contact_results.put(('open',qid,claimed,e.report if hasattr(e,'report') else None,str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_contact_fill(self,qid,job_id):
        claimed=self.adding_queue.claim_fill(qid,job_id)
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'{claimed["account"]}：正在填写备注 {claimed["preview_number"]} 和已领取联系人；不要操作表单。')
        payload=None
        if claimed['item']['source']=='username':
            opened=claimed['opening_report']
            payload={'mode':'fill','username':claimed['item']['value'],'number':str(claimed['preview_number']),
                'executable_path':claimed['window']['path'],'main_runtime_id':opened['main_runtime_id'],
                'contact_runtime_id':opened['contact_runtime_id'],'profile_runtime_id':opened['profile_runtime_id']}
        def worker():
            try:
                report=run_window_script(claimed['window'],'username_contact.ps1',payload) if payload else fill_contact_test(claimed['window'],claimed['item']['value'],str(claimed['preview_number']),require_empty=True)
                self.contact_results.put(('fill',qid,claimed,report,None))
            except Exception as e:self.contact_results.put(('fill',qid,claimed,None,str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def start_contact_submit(self,qid,job_id):
        claimed=self.adding_queue.claim_submit(qid,job_id)
        payload=submission_payload(claimed)
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'{claimed["account"]}：重新核对备注 {claimed["preview_number"]} 与联系人，自动提交并核验资料页。')
        def worker():
            try:self.contact_results.put(('submit',qid,claimed,run_window_script(claimed['window'],'username_contact.ps1' if claimed['item']['source']=='username' else 'submit_contact.ps1',payload),None))
            except Exception as e:self.contact_results.put(('submit',qid,claimed,e.report if hasattr(e,'report') else None,str(e)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_contact_worker)

    def waiting(self):
        self.group_operation_ready()
        if not messagebox.askyesno('人工核对','你已在指定群的添加成员页面选中本批成功添加的用户，并停在最终邀请按钮前吗？',parent=self.root):return
        self.store.mark_waiting(self.current_batch())
        self.refresh(); self.status.set('本批等待人工邀请。用户保持占用，可为其他账号建立批次。')

    def invited(self):
        self.group_operation_ready()
        ids=[int(i) for i in self.records.selection()]
        if not ids: raise ValueError('请选择已确认邀请成功的用户')
        if not messagebox.askyesno('核实实际结果','请以群成员实际结果为准。\n确认选中用户已成功加入群，而不只是点击过邀请按钮吗？',parent=self.root):return
        self.store.confirm_invited(ids)
        self.refresh(); self.status.set('已保存你确认的邀请结果。未确认的用户保持等待状态。')

    def export_records(self):
        path=filedialog.asksaveasfilename(title='导出使用记录',defaultextension='.csv',initialfile='使用记录.csv')
        if path:self.store.export_csv(path); self.status.set('使用记录已导出。')

    def export_logs(self):
        path=filedialog.asksaveasfilename(title='导出日志',defaultextension='.csv',initialfile='操作日志.csv')
        if path:self.store.export_events(path); self.status.set('操作日志已导出。')

    def backup(self):
        path=filedialog.asksaveasfilename(title='备份数据库',defaultextension='.sqlite3',initialfile='名单进度备份.sqlite3')
        if path:self.store.backup(path); self.status.set('数据库已完整备份。')

    def make_windows(self):
        frame=self.windows_tab
        bar=ttk.Frame(frame); bar.pack(fill='x')
        ttk.Button(bar,text='扫描 Telegram 候选窗口',command=self.guarded(self.scan)).pack(side='left')
        self.all_windows=tk.BooleanVar(value=False)
        ttk.Checkbutton(bar,text='显示全部应用窗口（用于查找改名分身）',variable=self.all_windows).pack(side='left',padx=12)
        ttk.Button(bar,text='选择全部 Telegram 窗口',command=self.guarded(self.select_all_windows)).pack(side='left')
        ttk.Button(bar,text='导出窗口清单',command=self.guarded(self.export_windows)).pack(side='right')
        ttk.Button(frame,text='扫描全部窗口及置顶群',command=self.guarded(self.scan_all_pinned_groups)).pack(anchor='w',pady=6)
        open_bar=ttk.Frame(frame);open_bar.pack(fill='x',pady=4)
        ttk.Button(open_bar,text='打开所选账号 New Contact（不填写）',command=self.guarded(self.open_contact_forms_test)).pack(side='left')
        ttk.Button(open_bar,text='导出直达联系人表单报告',command=self.guarded(self.export_contact_open)).pack(side='left',padx=8)
        probes=ttk.Frame(frame);probes.pack(fill='x',pady=8)
        self.probe_button=ttk.Button(probes,text='检查选中窗口控件（只读）',command=self.guarded(self.probe))
        self.probe_button.pack(side='left')
        ttk.Button(probes,text='导出控件报告',command=self.guarded(self.export_controls)).pack(side='left',padx=8)
        ttk.Button(probes,text='试填一位手机号（停在 Create 前）',command=self.guarded(self.open_fill_test)).pack(side='left',padx=8)
        ttk.Button(frame,text='核验已占用联系人资料页（只读）',command=self.guarded(self.open_verify)).pack(anchor='w')
        ttk.Button(frame,text='群选人图像检测（不点击）',command=self.guarded(self.member_visual)).pack(anchor='w',pady=6)
        ttk.Button(frame,text='读取完整已选名单（不选人）',command=self.guarded(self.full_header_test)).pack(anchor='w',pady=6)
        ttk.Button(frame,text='备用文字识别测试（不点击）',command=self.guarded(self.backup_visual)).pack(anchor='w',pady=6)
        ttk.Button(frame,text='自动搜索并选择一位（停在 Add 前）',command=self.guarded(self.member_select)).pack(anchor='w')
        ttk.Button(frame,text='连续选人测试（停在 Add 前）',command=self.guarded(self.members_batch)).pack(anchor='w',pady=6)
        ttk.Button(frame,text='两窗口顺序测试（停在 Add 前）',command=self.guarded(self.members_windows)).pack(anchor='w')
        self.window_count=tk.StringVar(value='尚未扫描。窗口数量不等于已登录账号数量。')
        ttk.Label(frame,textvariable=self.window_count,wraplength=950).pack(anchor='w',pady=12)
        cols=('hwnd','pid','title','path','minimized')
        area=ttk.Frame(frame); area.pack(fill='both',expand=True)
        self.window_tree=ttk.Treeview(area,columns=cols,show='headings',selectmode='extended')
        for col,title,width in zip(cols,('窗口编号','进程编号','窗口标题','程序路径','最小化'),(100,80,220,480,60)):
            self.window_tree.heading(col,text=title); self.window_tree.column(col,width=width,minwidth=50)
        vertical=ttk.Scrollbar(area,command=self.window_tree.yview)
        horizontal=ttk.Scrollbar(area,orient='horizontal',command=self.window_tree.xview)
        self.window_tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        area.rowconfigure(0,weight=1); area.columnconfigure(0,weight=1)
        self.window_tree.grid(row=0,column=0,sticky='nsew'); vertical.grid(row=0,column=1,sticky='ns'); horizontal.grid(row=1,column=0,sticky='ew')
        ttk.Label(frame,text='默认按 Telegram.exe 程序识别，不再按窗口标题匹配。扫描只读取窗口信息，不切换窗口或读取聊天。候选窗口需由你核对；重启后窗口编号可能改变。',wraplength=960).pack(anchor='w',pady=12)

    def scan(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待结束再扫描')
        self.windows=scan_windows(self.all_windows.get())
        self.window_tree.delete(*self.window_tree.get_children())
        for i,row in enumerate(self.windows):
            self.window_tree.insert('', 'end',iid=str(i),values=(f'0x{row["hwnd"]:X}',row['pid'],row['title'],row['path'] or '无法读取', '是' if row['minimized'] else '否'))
        self.window_count.set(f'扫描到 {len(self.windows)} 个'+('应用窗口' if self.all_windows.get() else 'Telegram 候选窗口')+'。这不是已验证账号数。')

    def select_all_windows(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待结束')
        values=[item for item in self.window_tree.get_children()
            if self.windows[int(item)]['candidate'] and not self.windows[int(item)]['minimized']]
        if not values:raise ValueError('请先扫描并还原 Telegram 窗口')
        self.window_tree.selection_set(values)
        self.status.set(f'已选择 {len(values)} 个 Telegram 窗口；顺序按列表从上到下，本轮先用两个。')

    def export_windows(self):
        if not self.windows:raise ValueError('请先扫描窗口')
        path=filedialog.asksaveasfilename(title='导出窗口清单',defaultextension='.json',initialfile='窗口扫描.json')
        if path:
            Path(path).write_text(json.dumps({'created':datetime.now().astimezone().isoformat(),'verified_accounts':False,'windows':self.windows},ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('窗口清单已导出。')

    def probe(self):
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描，并选择一个 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate']:raise ValueError('请选择已识别为 Telegram.exe 的窗口')
        if window['minimized']:raise ValueError('请先手动还原该 Telegram 窗口，再重新扫描')
        if self.probing:return
        self.control_report=None
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在读取所选窗口控件，最多等待 15 秒。请保持页面打开。')
        def worker():
            try:self.probe_queue.put((True,inspect_controls(window)))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start()
        self.root.after(100,self.poll_probe)

    def member_visual(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描并选择已打开 Add Members 的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:
            raise ValueError('请选择已还原的 Telegram.exe 窗口，并保持 Add Members 打开')
        number=simpledialog.askstring('群选人图像检测','请填写联系人实际完整备注（默认 1）：\n旧备注 001 必须输入 001，不能输入 1。\n仅截图和识别，不选择用户或点击 Add。',
            initialvalue='1',parent=self.root)
        if number is None:return
        number=numeric_label(number)
        path=filedialog.asksaveasfilename(title='保存弹窗截图（同时生成同名 JSON 报告）',
            defaultextension='.png',initialfile='群选人图像.png',filetypes=[('PNG 图像','*.png')])
        if not path:return
        report_path=Path(path).with_suffix('.json')
        if report_path.exists() and not messagebox.askyesno('已有报告',f'同名报告 {report_path.name} 已存在，是否替换？',parent=self.root):return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在读取弹窗和顶部放大图，最多等待 45 秒；不会改变搜索或选择。')
        def worker():
            try:self.probe_queue.put((True,{'visual_result':inspect_member_visual(window,path,number,header_diagnostic=True)}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def full_header_test(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请在窗口列表只选中一个已打开 Add Members 的账号')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:raise ValueError('请选择已还原的 Telegram.exe 窗口')
        from backup_ocr import require_environment, inspect_selection_visual
        require_environment()
        path=filedialog.asksaveasfilename(title='保存完整已选名单读取报告',defaultextension='.json',
            initialfile='完整已选名单.json',filetypes=[('JSON 报告','*.json')])
        if not path:return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在滚动读取顶部已选标签，两轮完整核对；不搜索、选人或点击 Add。请勿操作 Telegram。')
        def worker():
            try:self.probe_queue.put((True,{'full_header_result':inspect_selection_visual(window,Path(path).with_suffix('.png'),'1')}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def backup_visual(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描并选择已打开 Add Members 的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:raise ValueError('请选择已还原的 Telegram.exe 窗口')
        if not (BASE/'.ocr-env'/'Scripts'/'python.exe').is_file():
            raise ValueError('先关闭助手并运行本版 SETUP_OCR.bat，安装成功后再打开 START.bat。')
        path=filedialog.asksaveasfilename(title='保存备用识别截图和 JSON',defaultextension='.png',
            initialfile='备用检测12.png',filetypes=[('PNG 图像','*.png')])
        if not path:return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在只读截图并运行备用识别，最多约 120 秒；不会修改搜索、选择或点击 Add。')
        def worker():
            try:self.probe_queue.put((True,{'backup_visual_result':inspect_backup_ocr(window,path)}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def member_select(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描并选中已打开 Add Members 的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:
            raise ValueError('请选择已还原的 Telegram.exe 窗口，并保持 Add Members 打开')
        name=simpledialog.askstring('单人自动选择测试',
            '请保持 Telegram 的 Add Members 打开，顶部已选用户保持为空。\n'
            '填写他的完整数字备注（默认 1）。脚本自动搜索并选择一位，不点击 Add。\n'
            '开始后请暂时不要操作鼠标键盘，等待结果提示。',initialvalue='1',parent=self.root)
        if name is None:return
        name=numeric_label(name)
        path=filedialog.asksaveasfilename(title='保存单人选择测试结果',defaultextension='.png',
            initialfile='单人选择测试.png',filetypes=[('PNG 图像','*.png')])
        if not path:return
        path=Path(path)
        generated=[path,path.with_name(path.stem+'_before.png'),path.with_suffix('.json')]
        if any(p.exists() for p in generated) and not messagebox.askyesno('已有测试文件',
            '同名测试图片或报告已存在，是否替换？',parent=self.root):return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在自动搜索并选择一位联系人；最多等待约 180 秒。请不要操作鼠标键盘；脚本停在 Add 前。')
        def worker():
            try:self.probe_queue.put((True,{'selection_result':select_member_test(window,path,name)}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def members_batch(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描并选中已打开 Add Members 的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:
            raise ValueError('请选择已还原的 Telegram.exe 窗口，并保持目标群 Add Members 打开')
        text=simpledialog.askstring('连续选人测试',
            '填写已添加测试联系人的完整数字备注，首次用 1,2。\n'
            '逗号或换行分隔，也支持 1-20；每次最多 40 位。\n'
            '已选的本次联系人会保留，不会重复点击。最终 Add 由你点击。\n'
            '执行期间请暂时不要操作鼠标键盘。',initialvalue='1,2',parent=self.root)
        if text is None:return
        labels=parse_member_labels(text)
        path=filedialog.asksaveasfilename(title='保存连续选人汇总报告',defaultextension='.json',
            initialfile='连续选人测试.json',filetypes=[('JSON 报告','*.json')])
        if not path:return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set(f'正在连续选择 {len(labels)} 位联系人；请暂时不要操作鼠标键盘，等待结果提示。')
        def progress(message):self.probe_queue.put((True,{'progress_result':message}))
        def worker():
            try:self.probe_queue.put((True,{'batch_selection_result':select_members_test(window,path,labels,progress)}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def members_windows(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=set(self.window_tree.selection())
        order=[item for item in self.window_tree.get_children() if item in selection]
        if len(order)!=2:raise ValueError('本轮请选择两个 Telegram 窗口；可先扫描，再点“选择全部 Telegram 窗口”')
        windows=[dict(self.windows[int(item)]) for item in order]
        if any(not row['candidate'] or row['minimized'] for row in windows):
            raise ValueError('请还原两个 Telegram.exe 窗口，并分别打开目标群的 Add Members')
        preview='\n'.join(f'{i}. 0x{row["hwnd"]:X}  {row["title"][:80]}' for i,row in enumerate(windows,1))
        lists=[]
        for index,window in enumerate(windows,1):
            text=simpledialog.askstring(f'窗口 {index} 的实际数字备注',
                '执行顺序按列表从上到下：\n'+preview+'\n\n'
                f'现在填写窗口 {index}（{window["title"][:80]}）自己的联系人备注。\n'
                '例如第一个账号 1,2，第二个账号 3,4；按已有名字填写。\n'
                '两个账号各自打开目标群 Add Members；最终 Add 由你点击。\n'
                '运行中不要操作鼠标键盘，等两个窗口全部结束。',
                initialvalue='1,2' if index==1 else '',parent=self.root)
            if text is None:return
            lists.append(parse_member_labels(text))
        path=filedialog.asksaveasfilename(title='保存两窗口队列汇总',defaultextension='.json',
            initialfile='两窗口测试.json',filetypes=[('JSON 报告','*.json')])
        if not path:return
        self.probing=True;self.probe_button.configure(state='disabled')
        self.status.set('正在按窗口列表顺序执行；请等待两个窗口全部结束，暂时不要点击任何 Add。')
        def progress(message):self.probe_queue.put((True,{'progress_result':message}))
        def worker():
            try:self.probe_queue.put((True,{'window_queue_result':select_window_queue(windows,path,progress=progress,numbers_by_window=lists)}))
            except Exception as error:self.probe_queue.put((False,str(error)))
        threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)

    def poll_probe(self):
        try:ok,value=self.probe_queue.get_nowait()
        except queue.Empty:
            self.root.after(100,self.poll_probe);return
        if ok and 'progress_result' in value:
            self.status.set(value['progress_result']);self.root.after(100,self.poll_probe);return
        self.probing=False;self.member_selection_active=False;self.probe_button.configure(state='normal')
        if not ok:
            messagebox.showwarning('检查未完成',value,parent=self.root)
            self.status.set('操作未完成，详见提示；程序未提交联系人，也未更新使用状态。');return
        if 'full_header_result' in value:
            result=value['full_header_result'];a=result.get('analysis') or {}
            if a.get('usable') and not a.get('other_header_words'):
                numbers=a.get('selected_numbers',[])
                self.show_text('完整已选名单读取结果',f'完整读到 {len(numbers)} 位：'+', '.join(numbers)+
                    '\n未搜索、选人或点击 Add，也未修改数据库。请反馈刚才保存的完整已选名单 JSON。')
                self.status.set(f'完整读取通过：{len(numbers)} 位；报告已保存，未提交邀请。')
            else:
                self.show_text('完整已选名单读取未通过',str(a.get('reason') or '读取失败')+
                    '\n请保留当前页面并反馈刚才保存的 JSON；未选人或提交邀请。')
                self.status.set('完整读取未通过，请反馈 JSON，不要重新选人。')
            return
        if 'adding_cleanup_result' in value:
            result=value['adding_cleanup_result'];self.group_cleanup_report=result
            qid=value['queue_id'];snapshot=self.adding_queue.snapshot(qid)
            if not result.get('ok'):
                self.status.set('添加前页面整理未通过，尚未开始添加；请导出页面整理报告。');return
            if self.adding_queue.latest()!=qid or snapshot['state']!='configured':
                self.status.set('添加队列已停止或变化，页面整理后未开始添加。');return
            job=self.adding_queue.start(qid)
            self.refresh(select_batch=job['batch_id'] if job else None)
            self.status.set('页面整理完成，手机号添加队列开始；运行期间不要操作 Telegram。')
            self.root.after(0,self.drive_contact_queue);return
        if 'group_cleanup_result' in value:
            result=value['group_cleanup_result'];self.group_cleanup_report=result
            lines=[f'{j["account"]}：'+{'ready':'已整理回普通聊天页','review':'待核查','not_started':'未执行'}.get(j['state'],j['state']) for j in result['jobs']]
            if result.get('reason'):lines.append(result['reason'])
            lines.append('未选人、未点击 Add，也未推断或登记邀请结果。')
            self.show_text('页面整理结果','\n'.join(lines));self.status.set('页面整理'+('完成。' if result['ok'] else '未完成，请导出页面整理报告。'));return
        if 'contact_open_result' in value:
            result=value['contact_open_result'];self.contact_open_report=result
            lines=[result['reason'],'']
            for job in result['jobs']:
                label={'empty_form_open':'空白表单已核对','review':'待核查','paused':'已暂停','not_started':'未执行'}.get(job['state'],job['state'])
                lines.append(f'{job["account"]} · 窗口 0x{job["window"]["hwnd"]:X}：{label}')
                if job.get('reason'):lines.append(job['reason'])
            lines.extend(['','本轮不要填写或点击 Create／Criar。核对各账号自己的空白表单，导出“直达联系人表单报告”反馈。',
                '没有领取手机号、分配编号或登记添加成功。失败时查看实际窗口，不直接重复运行。'])
            self.show_text('直达 New Contact 测试结果','\n'.join(lines))
            self.status.set('直达表单报告已保存；'+('全部已核对，未填写或提交。' if result['ok'] else '请核查实际窗口。'));return
        if 'pinned_scan_result' in value:
            self.pinned_scan_preview=value['pinned_scan_result'];self.render_pinned_scan()
            self.status.set('批量扫描完成，配置尚未保存；请核对列表后点击“确认保存所选账号”。');return
        if 'target_replace_confirm_result' in value:
            result=value['target_replace_confirm_result']
            try:
                if not self.pinned_scan_preview or self.pinned_scan_preview['scan_id']!=result['scan_id']:
                    raise ValueError('扫描结果已变化，请重新扫描')
                if result.get('error'):raise ValueError(result['error'])
                saved=self.target_replacement.confirm(result['preview'],result['records'])
            except Exception as error:
                if self.pinned_scan_preview:self.pinned_scan_preview['needs_rescan']=True
                self.render_pinned_scan();self.status.set('目标群未更换，原联系人、编号和群记录保留。')
                messagebox.showwarning('目标群未更换',str(error),parent=self.root);return
            self.pinned_scan_preview['saved_accounts'].extend(r['account'] for r in saved['saved'])
            self.batch_plan_preview=None;self.render_pinned_scan();self.refresh_groups()
            self.status.set(f'已更换 {len(saved["saved"])} 个账号的本批目标群，原联系人与编号保留；从新群①重新选人。')
            self.show_text('本批目标群已更换','\n'.join(f'{r["account"]}：保留备注 '+', '.join(r['numbers']) for r in saved['saved'])+
                '\n两个新群均需重新选人。请先导出更换目标群报告；未添加联系人或点击 Add。');return
        if 'pinned_scan_confirm_result' in value:
            result=value['pinned_scan_confirm_result']
            try:
                if not self.pinned_scan_preview or self.pinned_scan_preview['scan_id']!=result['scan_id']:
                    raise ValueError('扫描结果已改变，未保存旧结果，请重新扫描')
                if result.get('error'):raise ValueError(result['error'])
                records=self.pinned_groups.save_many(result['records'])
            except Exception as error:
                if self.pinned_scan_preview:self.pinned_scan_preview['needs_rescan']=True
                self.render_pinned_scan();self.status.set('本次所选配置全部未保存，旧配置保留。')
                messagebox.showwarning('配置未保存',str(error),parent=self.root);return
            self.pinned_scan_preview['saved_accounts'].extend(r['account'] for r in records)
            self.render_pinned_scan();self.refresh_groups()
            keys=[str(i) for i,w in enumerate(self.windows) if any(all(w.get(k)==r['window'].get(k) for k in ('hwnd','pid','path')) for r in records)]
            self.window_tree.selection_set(keys)
            self.status.set(f'已统一确认保存 {len(records)} 个账号的两个置顶群；未添加联系人、未执行邀请。');return
        if 'group_probe_result' in value:
            result=value['group_probe_result'];self.control_report=result
            try:
                pair=pinned_pair(result)
                targets='\n'.join(f'置顶群 {g["slot"]}：{g["name"]}（{g["language"]}）' for g in pair['targets'])
                count=pair['observed_chat_rows']
            except ValueError as error:
                targets='两个置顶群未确认：'+str(error);count=result.get('observed_chat_rows','未确认')
            self.show_text('群列表只读检测结果',f'读取聊天条目：{count}\n'+targets+'\n'
                '稳定群 ID 和全部群数量尚未确认。\n'+result.get('error',result.get('reason',''))+
                f'\n\n检测报告已保存：{value["report_path"]}\n请反馈这一份 JSON；不用上传全部截图。此检测不会选择用户或邀请。')
            self.status.set('群列表只读报告已保存；需要 Windows 报告验证后，才能接入完整窗口扫描。');return
        if 'pinned_probe_result' in value:
            data=value['pinned_probe_result']
            try:
                result=self.pinned_groups.save(data['account'],data['window'],data['first'],data['second'])
            except Exception as error:
                self.status.set('置顶群记录未更新。')
                self.show_text('置顶群未记录',str(error));return
            self.refresh_groups()
            names='\n'.join(f'群 {g["slot"]}：{g["name"]}' for g in result['targets'])
            self.show_text('两个置顶群已记录',f'{result["account"]} · 窗口 0x{result["window"]["hwnd"]:X}\n{names}\n\n'
                '请核对群名称是否正确。这次只读识别并记录，没有打开群或邀请用户。\n填入实际数字备注，保存本次两群名单后，可用新版按钮搜索选人。')
            self.status.set(f'{result["account"]} 的两个置顶群已记录，未邀请。');return
        if 'group_navigation_result' in value:
            result=value['group_navigation_result']
            self.pinned_groups.save_navigation(result);self.refresh_groups()
            lines=[result['reason'],'']
            for job in result['jobs']:
                state={'members_open':'添加成员页面已打开（未选人、未邀请）','review':'待核查','paused':'已暂停','not_started':'未执行'}.get(job['state'],job['state'])
                lines.append(f'{job["account"]} · 群 {job["slot"]}「{job["target"]["name"]}」：{state}')
            lines.extend(['','本次不要点击最终 Add。成功时核对 Telegram 中的群名称；测试另一群前手动取消空白弹窗并关闭资料面板。',
                '失败时点击“导出群打开报告”，只反馈该 JSON，不需要一批截图。'])
            self.show_text('群打开测试结果','\n'.join(lines))
            self.status.set('群打开结果已记录；未选择联系人、未邀请。');return
        if 'pinned_members_result' in value:
            result=value['pinned_members_result']
            preserved=self.pinned_member_plans.save_run(result);self.refresh_groups()
            lines=[result['reason'],'']
            if preserved:
                lines.append('以下账号本次未进入选人，保留此前同批核验记录：'+
                    '；'.join(f'{p["account"]} · 群 {p["slot"]}' for p in preserved))
            for job in result['jobs']:
                state={'waiting_for_manual_invite':'已核对，等待你点击 Add','review':'待核查','paused':'已暂停','not_started':'未执行'}.get(job['state'],job['state'])
                lines.append(f'{job["account"]} · 群 {job["slot"]}「{job["target"]["name"]}」· 备注 '+', '.join(job['numbers'])+'：'+state)
            lines.extend(['','脚本未点击最终 Add，未登记已邀请，也未改变添加人数。',
                '处理完群① Add 后再运行群②；会先自动整理已识别页面，沿用已保存名单，不用再次保存。',
                '失败时点击“导出两群选人报告”，先反馈这一份 JSON。'])
            self.show_text('两群搜索选人结果','\n'.join(lines))
            self.status.set('选人报告已保存；'+('停在最终 Add 前。' if result['ok'] else '请检查提示。'));return
        if 'window_queue_result' in value:
            result=value['window_queue_result'];self.control_report=result
            state='两个窗口均已停在 Add 前' if result['selection_verified'] else '窗口顺序测试已停止，请核查'
            lines=[state,result.get('reason',''),'']
            names={'waiting_for_manual_invite':'已核对，等待你点击 Add','review':'未确认，请检查','not_started':'未执行','running':'执行中'}
            for job in result['jobs']:
                lines.append(f'窗口 {job["index"]}（0x{job["window"]["hwnd"]:X}）：{names.get(job["state"],job["state"])}')
            lines.extend(['','最终 Add 未由脚本点击，名单数据库未更新。',
                f'队列汇总：{result["report_path"]}',f'逐窗口报告与截图：{result["files_directory"]}',
                '先反馈队列汇总 JSON；不必上传全部截图。'])
            self.show_text('两窗口顺序测试结果','\n'.join(lines));self.status.set(state+'；队列结果已保存。')
            return
        if 'batch_selection_result' in value:
            result=value['batch_selection_result'];self.control_report=result
            state='全部备注已核对，停在 Add 前' if result['selection_verified'] else '连续选人已停止，请核查'
            selected='、'.join(result['selected_numbers']) or '无'
            remaining='、'.join(result['remaining_numbers']) or '无'
            text=(f'{state}\n已逐位核对：{selected}\n尚未核对：{remaining}\n'
                  f'{result.get("reason", "")}\n\n最终 Add 未由脚本点击，数据库状态未自动更新。\n'
                  f'汇总报告：{result["report_path"]}\n'
                  f'图片和逐位报告目录：{result["files_directory"]}\n'
                  f'最终图片：{result.get("final_image_path", "未生成")}\n\n'
                  '请先反馈汇总 JSON 和最终图片；若提前停止，反馈汇总及最后一位的图片。')
            self.show_text('连续选人测试结果',text);self.status.set(state+'；汇总和逐位结果已保存。')
            return
        if 'selection_result' in value:
            result=value['selection_result'];self.control_report=result
            state='选中结果已核对' if result['selection_verified'] else '结果未确认，请检查 Telegram'
            text=(f'备注：{result["number"]}\n{state}\n{result.get("reason", "")}\n\n'
                  f'最终 Add 未由脚本点击，名单使用状态未改变。\n'
                  f'报告：{result["report_path"]}\n'
                  f'操作前图片：{result["before_image_path"]}\n'
                  f'操作后图片：{result["image_path"]}（只有完成截图时才存在）。\n\n'
                  '请反馈报告和生成的图片。结果未确认时不要连续重试。')
            self.show_text('单人自动选择测试结果',text)
            self.status.set(state+'；已停在 Add 前，测试报告已保存。')
            return
        if 'backup_visual_result' in value:
            report=value['backup_visual_result'];self.control_report=report
            backup=report.get('backup_ocr') or {}
            labels='、'.join(backup.get('selected_numbers',[])) or '未读到'
            unknown='、'.join(backup.get('other_header_words',[])) or '无'
            text=(f'备用识别完成：{backup.get("ok",False)}\n顶部数字检测：{labels}\n'
                f'其他标签文字：{unknown}\n{backup.get("error", "")}\n\n'
                f'截图：{report["image_path"]}\n同名 JSON 已保存，请反馈这两份文件。\n\n'
                '本次只读，不修改搜索或选人，不点击 Add，不更新数据库。\n'
                '此按钮仅诊断；连续选人已接入备用顶部识别，核对通过也不代表邀请成功。')
            self.show_text('备用文字识别诊断结果',text)
            self.status.set('备用只读检测结束；请反馈 PNG 和 JSON，先保持已选联系人。')
            return
        if 'visual_result' in value:
            report=value['visual_result'];analysis=report['analysis']
            self.control_report=report
            text=(f'备注：{analysis["number"]}\n图像文字检测可用：{analysis["usable"]}\n'
                  f'联系人列表匹配数：{analysis["list_matches"]}\n顶部已选标签匹配数：{analysis["chip_matches"]}\n'
                  f'列表检测：仅计入完整的数字备注行；日期及上线状态不计入。\n'
                  f'识别语言：{report["ocr"].get("language") or "无"}\n{analysis["reason"]}\n\n'
                  f'已生成：{report["image_path"]}\n和同名 JSON。\n未点击 Add、未更新使用记录。')
            header=report.get('header_diagnostic') or {}
            if header:
                text+=(f'\n\n顶部放大检测可用：{header.get("available",False)}\n'
                       f'顶部识别原文：{header.get("text", "")}\n'
                       f'顶部放大图：{header.get("image_path", "")}\n'
                       f'{header.get("error", "")}\n请反馈弹窗 PNG、顶部 _header.png 和同名 JSON。'
                       '\n放大检测仅供诊断，不用于判定选择成功。')
            self.show_text('群选人图像检测结果',text)
            self.status.set('群选人图像检测结束；弹窗、顶部放大图和 JSON 已保存，仅用于诊断。')
            return
        if 'verify_result' in value:
            result=value['verify_result']
            self.control_report=value['report']
            if result['verified']:
                try:
                    number=self.store.record_add_result(value['item_id'],'added','只读资料页核验匹配',
                        expected_phone=value['phone'],expected_number=value['number'])
                except (ValueError,RuntimeError,OSError) as error:
                    messagebox.showwarning('记录未更新',str(error),parent=self.root)
                    self.status.set('资料页匹配，但记录已变化；保留现状，请核查。')
                    self.refresh();return
                self.refresh()
                self.records.selection_set(str(value['item_id']))
                self.records.see(str(value['item_id']))
                messagebox.showinfo('核验通过',f'手机号、备注 {result["number"]}、编辑和删除联系人标记均匹配。\n已记录为“已添加联系人”。尚未邀请进群。',parent=self.root)
                self.status.set(f'联系人备注 {result["number"]} 核验通过并已保存，下一步可测试群成员页面。')
            else:
                labels={'profile':'完整资料页报告','phone':'手机号','number':'数字备注','edit_contact':'编辑联系人标记','delete_contact':'删除联系人标记'}
                missing='、'.join(labels[key] for key,passed in result['checks'].items() if not passed)
                messagebox.showwarning('尚未核验通过','未匹配：'+missing+'。\n\n名单记录保持原样。可导出本次控件报告继续排查，不要重复添加该联系人。',parent=self.root)
                self.status.set('核验未通过；记录未更新。本次报告已保留，可以导出。')
            return
        if 'fill_result' in value:
            result=value['fill_result']
            messagebox.showinfo('试填完成，停在 Create 前',
                                f'数字备注：{result["number"]}\n手机号回读匹配：{result["phone_matches"]}\n姓氏已清空。\n\n请检查 Telegram 页面，并截图反馈。本次未点击 Create，数据库仍为待处理。',parent=self.root)
            self.status.set('自动试填完成；未提交联系人，名单使用状态保持原样。')
            return
        self.control_report=value
        self.show_text('控件检查结果（只读）',summarize(value))
        self.status.set('控件报告已生成，可检查内容并导出。')

    def export_controls(self):
        if not self.control_report:raise ValueError('请先检查选中窗口的控件')
        path=filedialog.asksaveasfilename(title='导出控件报告',defaultextension='.json',initialfile='控件检查.json')
        if path:
            Path(path).write_text(json.dumps(self.control_report,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('控件报告已导出。报告中的控件名称可以先检查再分享。')

    def open_fill_test(self):
        if self.probing:raise ValueError('当前界面检查尚未完成，请稍候')
        if self.adding_queue.current():raise ValueError('添加队列执行中，请使用队列的试填流程，避免覆盖表单')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('先扫描并选择要测试的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate']:raise ValueError('请选择 Telegram.exe 窗口')
        if window['minimized']:raise ValueError('请还原 Telegram，保持空白 New Contact 页面打开，再重新扫描')
        reserved=None
        if self.batch_id.get():
            batch=self.store.batch(self.current_batch())
            held=[row for row in self.store.rows() if row['batch_id']==batch['id'] and row['status'] in ('reserved','uncertain')]
            if held:reserved=reserved_phone_fill(self.store,batch['id'],window,self.pinned_groups.get(batch['account']))
        phones=[reserved['item']] if reserved else [row for row in self.store.rows() if row['source']=='phone' and row['status']=='ready']
        if not phones:raise ValueError('请先在“导入名单”的手机号区域导入 1–2 条有效测试号码')
        dialog=tk.Toplevel(self.root);dialog.title('自动填写测试 — 停在 Create 前');dialog.geometry('630x280')
        dialog.transient(self.root)
        frame=ttk.Frame(dialog,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='目标窗口：'+window['title'],wraplength=580).pack(anchor='w')
        label=f'当前批次 #{reserved["batch_id"]} · {reserved["account"]} · 已领取手机号：' if reserved else '请选择导入名单中的一条待处理手机号：'
        ttk.Label(frame,text=label).pack(anchor='w',pady=(12,3))
        selected=tk.StringVar()
        choices=[f'第{row["seq"]}项 · {row["value"]}' for row in phones]
        pick=ttk.Combobox(frame,textvariable=selected,values=choices,state='readonly',width=56);pick.pack(fill='x');pick.current(0)
        row=ttk.Frame(frame);row.pack(fill='x',pady=12)
        ttk.Label(row,text='测试数字备注').pack(side='left')
        number=tk.StringVar(value=str(self.store.next_contact_number()));ttk.Entry(row,textvariable=number,width=12,state='readonly').pack(side='left',padx=8)
        ttk.Label(frame,text='仅填写手机号、名字和姓氏；不会点击 Create，不会标记为已使用。请保持空白 New Contact 页面打开。',wraplength=580).pack(anchor='w')
        def start():
            try:
                if self.probing:raise ValueError('已有界面检查或试填正在运行，请等待完成')
                if self.adding_queue.current():raise ValueError('添加队列已经开始，请关闭单人试填对话框')
                item=phones[pick.current()]
                live=next((x for x in self.store.rows() if x['id']==item['id']),None)
                if reserved:
                    batch=self.store.batch(reserved['batch_id'])
                    fresh=reserved_phone_fill(self.store,reserved['batch_id'],window,self.pinned_groups.get(batch['account']))
                    if self.current_batch()!=reserved['batch_id'] or fresh['item']!=item or fresh['preview_number']!=reserved['preview_number']:
                        raise ValueError('当前批次、占用用户或编号已变化，请重新打开试填窗口')
                elif not live or live['status']!='ready' or live['value']!=item['value']:
                    raise ValueError('该名单项状态已改变，请重新打开试填窗口')
                value=numeric_label(number.get())
                if int(value)!=self.store.next_contact_number():raise ValueError('全局编号已经改变，请重新打开试填窗口')
                self.probing=True;self.probe_button.configure(state='disabled')
                dialog.destroy()
                self.status.set('正在向选中窗口的 New Contact 页面试填；不提交联系人。')
                def worker():
                    try:self.probe_queue.put((True,{'fill_result':fill_contact_test(window,item['value'],value,require_empty=bool(reserved))}))
                    except Exception as error:self.probe_queue.put((False,str(error)))
                threading.Thread(target=worker,daemon=True).start()
                self.root.after(100,self.poll_probe)
            except (ValueError,RuntimeError,OSError) as error:messagebox.showwarning('未执行',str(error),parent=dialog)
        ttk.Button(frame,text='开始试填（不提交）',command=start).pack(anchor='e',pady=12)

    def open_verify(self):
        if self.probing:raise ValueError('已有界面操作正在运行，请等待完成')
        selection=self.window_tree.selection()
        if len(selection)!=1:raise ValueError('请先扫描并选择已打开联系人资料页的 Telegram 窗口')
        window=dict(self.windows[int(selection[0])])
        if not window['candidate'] or window['minimized']:
            raise ValueError('请选择已还原的 Telegram.exe 窗口')
        batch=self.store.batch(self.current_batch())
        if batch['status']!='active':raise ValueError('请在使用记录中选择处理中批次')
        held=[row for row in self.store.rows() if row['batch_id']==batch['id'] and row['status']=='reserved']
        if len(held)!=1 or held[0]['source']!='phone':
            raise ValueError('当前批次需要有且仅有一位已占用的手机号用户。若已记录为已添加，无需再核验。')
        item=held[0];number=batch['next_number']
        self.guard_manual_queue_record(item['id'])
        dialog=tk.Toplevel(self.root);dialog.title('只读核验 — 匹配后登记');dialog.geometry('640x350');dialog.transient(self.root)
        frame=ttk.Frame(dialog,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=f'账号备注：{batch["account"]}\n窗口：{window["title"]}\n已占用手机号：{item["value"]}\n应有数字备注：{number}',wraplength=600).pack(anchor='w')
        contact_name=tk.StringVar(value=str(number))
        label_row=ttk.Frame(frame);label_row.pack(fill='x',pady=8)
        ttk.Label(label_row,text='资料页实际备注（旧备注可填 001）').pack(side='left')
        ttk.Entry(label_row,textvariable=contact_name,width=12).pack(side='left',padx=8)
        ttk.Label(frame,text='请核对这是该账号的窗口，并保持这位联系人资料页打开。只读取资料页，不点击按钮；手机号、数字备注、Edit contact 和 Delete contact 全部匹配后保存“已添加联系人”。',wraplength=600).pack(anchor='w',pady=12)
        def start():
            if self.probing:return
            try:
                self.guard_manual_queue_record(item['id'])
                name=numeric_label(contact_name.get())
                if int(name)!=number:raise ValueError('资料页备注必须对应当前全局编号；请核对联系人，不要跳号')
            except ValueError as error:
                messagebox.showwarning('未执行',str(error),parent=dialog);return
            self.probing=True;self.probe_button.configure(state='disabled');dialog.destroy()
            self.status.set('正在只读核验资料页，最多等待 15 秒。请保持资料页打开。')
            def worker():
                try:
                    report=inspect_controls(window)
                    result=verify_contact_profile(report,item['value'],name)
                    self.probe_queue.put((True,{'verify_result':result,'report':report,
                        'item_id':item['id'],'phone':item['value'],'number':number}))
                except Exception as error:self.probe_queue.put((False,str(error)))
            threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll_probe)
        ttk.Button(frame,text='开始只读核验并登记',command=start).pack(anchor='e',pady=8)

    def make_help(self):
        text=(
            '当前版本用途\n\n'
            '1. 导入两份 TXT 或粘贴名单，检查数据库重复项，保留原有使用状态。\n'
            '2. 查看名单序号、原文件行号、账号占用、联系人编号和人工确认结果。\n'
            '3. 一次扫描全部独立窗口及两个置顶群，核对后统一确认保存。\n\n'
            'v0.6.72：重新打开邀请页确认空白，点击前核对上一位，点击后核对新增编号，中途不滚动；结束触发一次整批完整核对，最终 Add 手动。暂停按钮保留页面和报告，下次从头选人，不续跑。\n'
            'v0.6.28：多个可见位置及控件路径核对。\n'
            'v0.6.27：顶部被遮住时完整读两轮，不按历史补推；新增读取完整已选名单按钮。\n'
            'v0.6.26：按状态文字位置排除头像数字干扰；低置信度、碎片和重名仍停止。\n'
            'v0.6.25：补全葡萄牙语 visto há 数字 minuto/minutos 状态行；数字备注、截图绑定、头像排除和重名停止规则保持。最终 Add 手动。\n'
            'v0.6.24：支持更换本批目标群并保留成功联系人；旧群证据归档，新群使用新名单身份重新选人。\n'
            'v0.6.20：支持现场报告中的葡萄牙语 visto em 日期、visto há 小时、visto na última semana 和 visto recentemente 状态行。备注仍须完整匹配，不转换相似字符；群 Add 始终手动。\n'
            'v0.6.19：自动关闭已核验资料页并连续添加已由 A14/15、B16/17 现场报告验证。\n'
            '只关闭当前账号本批最近成功联系人的资料页，陌生页面和未知弹窗保留核查；旧队列保持原模式。\n'
            '旧版 v0.6.17：重新保存的新队列自动打开、填写、核对原表单、Create 一次并打开 Info；两次完整资料页核验后才登记成功。\n'
            '运行期间不要手动点击 Create 或打开资料页。超时和中断需核查实际联系人，不重复创建。旧队列不会因升级自动提交。\n'
            '旧版 v0.6.16：添加队列在普通聊天页自动打开并试填；Create 和打开资料页仍由你操作。两次核验成功后递增编号并推进下一账号。\n'
            '旧版同账号下一位暂需关闭资料面板；本版新队列自动核对并关闭。打开结果先保存，不确定时不重试，导出添加队列报告即可反馈。\n'
            'v0.6.15：在“扫描窗口”选择已有群绑定的账号，点击“打开所选账号 New Contact（不填写）”，按顺序调用各自程序路径直达表单。\n'
            '只读核对原窗口、进程路径、默认实例路由、联系人字段和空白内容，不领取名单、不填写、不点击 Create、不改编号。失败停止，不自动重试或清空现有表单。\n'
            '自定义 -workdir、-many 或同一进程多个主窗口目前停止提示，需单独验证后适配。报告不导出进程启动参数和表单内容。\n'
            '多台电脑独立运行：各自安装环境、使用不同名单并独立记录编号；当前不共享数据库。\n\n'
            'v0.6.14：批量扫描核对\n'
            '打开全部账号的普通聊天列表，关闭弹窗和资料页，清空搜索并滚到顶部。\n'
            '在“账号群聊 → 批量扫描核对”点击“扫描全部窗口及置顶群”，核对所有账号路径与群①、群②后，点击一次“确认保存所选账号”。\n'
            '成功行默认全部选中；失败行不保存。确认前再读取两次，任一所选账号变化则全部不保存。已有未完成批次的窗口或群改变时，禁止覆盖冻结绑定。\n'
            '旧账号备注按程序路径沿用，新账号可双击备注列修改。扫描不新增联系人、不改编号、不邀请。\n'
            '新流程：所有账号先添加并记录各自成功名单，再由你分别启动群①与群②的搜索选人；最终 Add 由你点击。\n'
            '批量群核对与独立直达表单已实测通过；本版测试自动打开后试填的队列衔接。自动 Create 与资料页已接入本版现场测试；用户名实际添加尚未接入；群操作前自动整理已通过现场测试。\n\n'
            'v0.6.13：资料页未关闭而在开始阶段拒绝操作时，保留此前同批核验；旧版被覆盖的记录可在确认按钮中按历史核验恢复，不自动登记邀请成功。\n'
            'v0.6.12：标签识别框在限定范围内留白，避免把字体阴影当作背景；核验阈值不降低。\n'
            'v0.6.11：顶部灰色数字标签可局部识别，搜索框和头像不作为备注；识别冲突或低置信度仍停止。\n'
            'v0.6.10：等待下一位空白表单时，普通聊天页即使达到扫描上限也只等待，不写入或判断成功。已清空／停止的未填写手机号任务可复制到新队列恢复；曾开始试填的任务必须先核验。\n'
            '旧版记录有同批次、同添加日期的成功事件时，沿用原编号生成两群名单。\n'
            '成功联系人记录、旧版编号标记、添加日期及添加人数均保留原值。\n'
            '最终 Add 由你点击；核实全部加入后分别确认两个群，只有两群都确认才完成这些联系人的任务。\n'
            '手填名单用于测试，不更新数据库邀请结果。未确认添加结果的用户不加入成功名单，保留待核查。\n'
            '群名可以重复：置顶方式按已绑定窗口与条目控件核对；完整 JSON 目录另有稳定群 ID。\n'
            '置顶识别已由两个窗口验证；置顶群导航已接入测试，实时全部群目录扫描仍未验证。\n'
            '添加队列先检查两个目标群与窗口绑定，并保存本队列的群配置快照。\n'
            '空白表单自动试填，Create 仍由你点击；打开资料页后两次核验成功才分配编号。\n'
            '明确限制提示两次核对通过后，该账号暂停、未确认用户保留占用，自动准备下一账号。\n'
            '未知提示、窗口变化或报告不完整时停止核查；程序中断后不自行恢复。\n'
            '群选人图像检测截取当前 Add Members 弹窗，测试本机 OCR，保存 PNG 和 JSON。不会选择用户、点击 Add 或更新进度。\n'
            '检查控件优先读取弹窗，并在可用时读取勾选／选中状态；检查不会选择联系人或点击 Add。\n'
            '新增只读资料页核验：选中当前批次和窗口，核验已占用手机号、数字备注及编辑／删除标记；全部匹配后记录已添加。\n'
            '此版本可测试单人和连续数字备注选人；不会提交联系人或自动搜索用户名添加好友。试填和选人测试不更新名单使用状态。\n'
            '“使用记录”按钮仅用于手动记录或测试，不会操作 Telegram。\n\n'
            '首次建议使用测试名单和“测试账号A”备注，先检查导入、重复提醒和重启恢复。\n'
            '领取用户之前保存占用；待邀请不等于成功。结果不明的任务保留核查。\n'
            '普通手机号添加失败后，该批次切换到用户名名单；明确限制不会自动切换。\n'
            '编号在确认添加成功时登记；所有账号、批次及重启后共用连续序号，不按天归零。\n今日添加人数按电脑本地日期统计已确认记录；邀请状态变化不会重复计数。\n旧备注保持原样；未登记的手动备注可用“校准全局起点”提高下一个编号。\n'
            '同一类型的手机号或用户名可文本去重；手机号与用户名是否属于同一人，单凭两个列表不能确认。\n'
            '名单序号是数据库首次导入时的稳定序号。重新排序的文件另有原行号，不能将两者混用。\n\n'
            f'数据位置：{DATA}\n'
            '所有数据和日志保存在本机；通过已登录的 Telegram 窗口操作，不提供自动登录、Telegram API 或消息发送功能。\n'
            '备份请使用“备份数据库”，不要在程序运行时只复制 sqlite3 文件。\n\n'
            '单窗口自动选择 1、2 已验证；两窗口测试分别填写各自的实际备注，例如 A 为 1、2，B 为 3、4。\n'
            '后续继续接入自动新增联系人及名单数据库联动。'
        )
        box=tk.Text(self.help_tab,wrap='word',font=('Microsoft YaHei',10),padx=10,pady=10)
        scroll=ttk.Scrollbar(self.help_tab,command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');box.pack(fill='both',expand=True)
        box.insert('1.0',text);box.configure(state='disabled')

    def show_text(self,title,content):
        window=tk.Toplevel(self.root); window.title(title); window.geometry('780x480')
        window.transient(self.root)
        frame=ttk.Frame(window,padding=10);frame.pack(fill='both',expand=True)
        text=tk.Text(frame,wrap='word',font=('Microsoft YaHei',10))
        scroll=ttk.Scrollbar(frame,command=text.yview);text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True)
        text.insert('1.0',content);text.configure(state='disabled')
        ttk.Button(window,text='关闭',command=window.destroy).pack(pady=8)

    def refresh(self,select_batch=None):
        self.refresh_addition_stats()
        self.refresh_adding_queue()
        self.records.delete(*self.records.get_children())
        for row in self.store.rows():
            number='' if row['contact_number'] is None else str(row['contact_number'])
            self.records.insert('', 'end',iid=str(row['id']),values=('手机号' if row['source']=='phone' else '用户名',row['seq'],row['value'],row['account'] or '',number,LABELS[row['status']],row['reason']))
        batches=self.store.batches()
        batch_labels={'active':'处理中','waiting':'待人工邀请','paused':'已暂停','review':'待核查','completed':'已完成'}
        values=[f'#{row["id"]} · {row["account"]} · {batch_labels.get(row["status"],row["status"])} · {"手机号" if row["mode"]=="phone" else "用户名"} · 目标{row["target"]}' for row in batches]
        previous=None
        if self.batch_id.get():previous=self.current_batch()
        choice=select_batch or previous
        self.batch_picker['values']=values
        selected=next((value for value in values if value.startswith(f'#{choice} · ')),values[0] if values else '')
        self.batch_id.set(selected)
        for source,variable in self.source_progress.items():
            p=self.store.progress(source);counts=p['counts'];next_row=p['next']
            next_text=f'第{next_row["seq"]}项（{next_row["value"]}）' if next_row else '暂无可用项'
            variable.set(f'数据库共 {p["total"]} 项；已分配到第 {p["allocated_to"]} 项\n'
                         f'已完成 {counts["completed"]}，待邀请 {counts["pending_invite"]}，占用／已添加 {counts["reserved"]+counts["added"]}，失败／待核查 {counts["failed"]+counts["uncertain"]}\n下一条：{next_text}')

    def tick_addition_stats(self):
        self.refresh_addition_stats()
        self.root.after(30000,self.tick_addition_stats)

    def refresh_addition_stats(self):
        stats=self.store.addition_stats()
        self.global_number.set(str(stats['next_number']))
        unknown=f'；历史缺添加日期 {stats["unknown_add_date"]} 人' if stats['unknown_add_date'] else ''
        self.addition_summary.set(f'{stats["day"]}（电脑本地日期）今日已确认添加 {stats["today_added"]} 人；累计已确认添加 {stats["total_added"]} 人；下一个全局编号 {stats["next_number"]}'+unknown)

    def on_error(self,exception,value,tb):
        DATA.mkdir(parents=True,exist_ok=True)
        with (DATA/'error.log').open('a',encoding='utf-8') as log:
            log.write(datetime.now().astimezone().isoformat()+'\n'+''.join(traceback.format_exception(exception,value,tb))+'\n')
        messagebox.showerror('程序错误',f'{value}\n详细信息已保存到本机 error.log。',parent=self.root)

    def close(self):
        if self.probing:
            self.status.set('界面操作尚未结束，请等待结果后再关闭助手。');return
        self.store.close();self.root.destroy()


def main():
    root=tk.Tk()
    try:
        App(root)
    except Exception as error:
        messagebox.showerror('启动失败',str(error),parent=root)
        root.destroy();raise
    root.mainloop()


if __name__=='__main__':
    main()
