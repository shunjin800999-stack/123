"""Cancel old selection plans; retain exactly the verified current 66 contacts."""
import os,sqlite3,json
from pathlib import Path
from datetime import datetime

def reset(path):
    path=Path(path)
    if not path.is_file():raise ValueError('找不到原数据库')
    db=sqlite3.connect(path,timeout=2);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT * FROM items WHERE contact_number BETWEEN 362 AND 427 ORDER BY contact_number').fetchall()
        expected={'账号1':list(range(362,382)),'账号2':list(range(382,388)),'账号3':list(range(388,408)),'账号4':list(range(408,428))}
        batches={}
        for account,numbers in expected.items():
            selected=[r for r in rows if r['account']==account]
            if [r['contact_number'] for r in selected]!=numbers or any(r['status'] not in ('added','pending_invite') or not r['added_at'] for r in selected):
                raise ValueError(account+'本轮成功记录与报告不一致，未取消任何任务')
            ids={r['batch_id'] for r in selected}
            if len(ids)!=1 or None in ids:raise ValueError('本轮批次关联异常')
            bid=ids.pop();batches[account]=bid
            if db.execute("SELECT 1 FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(bid,)).fetchone():raise ValueError('本轮仍有待核查记录')
        if len(rows)!=66:raise ValueError('本轮记录数异常')
        q=db.execute("SELECT q.id FROM contact_queues q JOIN contact_queue_jobs j ON j.queue_id=q.id WHERE j.batch_id=? AND q.state='done' ORDER BY q.id DESC LIMIT 1",(batches['账号1'],)).fetchone()
        if not q:raise ValueError('找不到已结束的本轮队列')
        qid=q[0]
        if db.execute("SELECT 1 FROM contact_queues WHERE state='running'").fetchone():raise ValueError('仍有运行队列，请先停止并关闭助手')
        backup=path.with_name('progress-before-invitation-reset-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        with sqlite3.connect(path) as reader,sqlite3.connect(backup) as out:reader.backup(out)
        placeholders=','.join('?' for _ in batches)
        db.execute(f"UPDATE batches SET status='archived',reason='用户取消旧任务批次，历史保留' WHERE id NOT IN ({placeholders})",tuple(batches.values()))
        db.execute(f"UPDATE batches SET status='waiting',reason='本轮添加已结束，重新生成搜索选人计划' WHERE id IN ({placeholders})",tuple(batches.values()))
        db.execute("UPDATE contact_queues SET state='cleared',reason='用户取消旧任务' WHERE id!=?",(qid,))
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('pinned_member_states','pinned_member_plans','pinned_batch_member_plans','pinned_group_navigation_states','pinned_group_observations'):
            if table in tables:db.execute('DELETE FROM '+table)
        db.execute("INSERT INTO events(action,detail) VALUES('reset_current_invitation',?)",(json.dumps({'batches':batches,'queue':qid,'backup':str(backup),'numbers':'362-427','contacts_modified':False},ensure_ascii=False),))
        db.commit();return backup,batches
    except Exception:db.rollback();raise
    finally:db.close()

if __name__=='__main__':
    base=Path(__file__).resolve().parent
    try:
        backup,batches=reset(Path(os.environ.get('LOCALAPPDATA') or base)/'TelegramContactAssistant'/'progress.sqlite3')
        print('整理完成。保留本轮66人；旧计划已取消。\n备份：'+str(backup)+'\n本轮批次：'+str(batches)+'\n重新启动助手，重新扫描保存置顶群，再预览并生成各账号两群名单。')
    except Exception as e:print('未执行：'+str(e));raise SystemExit(1)
