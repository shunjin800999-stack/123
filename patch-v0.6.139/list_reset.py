"""Retire the current displayed list without deleting historical database rows."""
import json
import uuid


def reset_current_list(queue):
    store=queue.store;db=store.db
    if db.execute("SELECT 1 FROM contact_queues WHERE state='running'").fetchone():
        raise ValueError('请先停止计划并等待当前操作结束，再清空名单')
    backup=store.path.with_name(store.path.stem+'-before-list-reset-'+uuid.uuid4().hex+'.sqlite3')
    store.backup(backup)
    store._begin()
    try:
        if db.execute("SELECT 1 FROM contact_queues WHERE state='running'").fetchone():raise ValueError('计划正在运行，未清空名单')
        count=db.execute('SELECT COUNT(*) FROM items WHERE list_visible=1').fetchone()[0]
        old_epoch=store.number_epoch();epoch=old_epoch+1
        db.execute("UPDATE items SET status='void',reason='用户清空当前名单：未确认任务作废，不登记成功' WHERE status IN ('reserved','uncertain')")
        db.execute('UPDATE items SET list_visible=0')
        db.execute("UPDATE batches SET status='archived',reason='用户清空名单并重新编号；原始记录保留' WHERE status IN ('active','waiting','paused','review')")
        db.execute("UPDATE contact_queues SET state='cleared',reason='用户清空名单，旧添加计划失效' WHERE state<>'cleared'")
        # An empty retired plan prevents legacy fallback to historic successes.
        empty_queue=db.execute("INSERT INTO contact_queues(state,target_mode,replace_selection,reason) VALUES('cleared','contact',1,'清空名单后不使用旧选人名单')").lastrowid
        from selection_scope import activate
        activate(store,empty_queue)
        db.execute('UPDATE list_generation SET epoch=? WHERE id=1',(epoch,))
        db.execute('UPDATE number_sequence SET next_value=0 WHERE id=1')
        db.execute('UPDATE batches SET next_number=0')
        result={'hidden_items':count,'old_number_epoch':old_epoch,'number_epoch':epoch,'next_number':0,
            'historical_rows_deleted':False,'old_selection_lists_retired':True,'backup_path':str(backup)}
        store._event('current_list_reset',detail=json.dumps(result,ensure_ascii=False))
        db.commit();return result
    except Exception:db.rollback();raise
