"""Offline test: temporary database, fabricated reports, no Telegram operations."""
import queue
import tkinter as tk
from tkinter import ttk, messagebox
from types import SimpleNamespace
from unittest.mock import patch
from app import App
from test_unregistered_contact import UnregisteredTests, result, dismissed


class Simulation:
    def __init__(self):
        self.fixture=UnregisteredTests();self.fixture.setUp()
        with self.fixture.store.db:
            self.fixture.store.db.execute("UPDATE batches SET account=CASE account WHEN 'A' THEN '账号2' ELSE '账号3' END")
        self.count=0

    def step(self,app):
        if self.count>=2:raise ValueError('已完成账号切换测试，不再模拟添加')
        fixture=self.fixture
        job=fixture.submission();report=result(job)
        claimed,_=fixture.queue.claim_unregistered(fixture.qid,job['id'],[report,report])
        app.contact_results.put(('dismiss_unregistered',fixture.qid,claimed,dismissed(job),None))
        # Use the production result handler and verify next-account scheduling.
        App.poll_contact_worker(app)
        self.count+=1
        snapshot=fixture.queue.snapshot(fixture.qid)
        assert snapshot['next_number']==12
        assert snapshot['jobs'][1]['state']==('waiting_form' if self.count==2 else 'queued')
        assert snapshot['state']=='running'
        assert snapshot['jobs'][0]['state']==('paused' if self.count==2 else 'waiting_form')
        assert fixture.queue.current(fixture.qid)['account']==('账号3' if self.count==2 else '账号2')
        return snapshot

    def close(self):self.fixture.tearDown()


def main():
    root=tk.Tk();root.title('离线账号切换测试 — 不操作Telegram');root.geometry('650x320')
    simulation=Simulation()
    status=tk.StringVar(value='模拟账号2；临时编号12，与正式编号231无关。')
    progress=tk.StringVar(value='点击下面按钮，逐次模拟失败。')
    frame=ttk.Frame(root,padding=20);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='本测试仅使用临时数据库和模拟报告。正式记录保持原样。',wraplength=600).pack(pady=10)
    ttk.Label(frame,textvariable=progress,wraplength=600).pack(pady=10)
    ttk.Label(frame,textvariable=status,wraplength=600).pack(pady=10)
    app=App.__new__(App);app.root=root;app.store=simulation.fixture.store
    app.adding_queue=simulation.fixture.queue;app.contact_results=queue.Queue();app.probing=False
    app.status=status;app.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
    app.refresh=lambda **kwargs:None;app.refresh_adding_queue=lambda:None
    app.drive_contact_queue=lambda:None
    def step():
        try:
            snapshot=simulation.step(app)
            progress.set(f'已模拟{simulation.count}次失败；队列：'+('已切换到账号3' if simulation.count==2 else '等待下一次模拟')+'；模拟编号仍为12。')
            if simulation.count==2:
                button.configure(state='disabled')
                status.set('已跳过模拟账号2，账号3任务已准备；编号仍为12。测试完成。')
        except Exception as error:button.configure(state='disabled');messagebox.showerror('测试失败',str(error),parent=root)
    button=ttk.Button(frame,text='模拟一次手机号添加失败',command=step);button.pack(pady=12)
    def close():simulation.close();root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.mainloop()

if __name__=='__main__':main()
