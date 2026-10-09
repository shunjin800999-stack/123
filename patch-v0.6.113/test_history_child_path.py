import copy,unittest
from selection_header_scan import bound_history_path
class HistoryChildPathTests(unittest.TestCase):
    def setUp(self):
        self.chain=[{'runtime_id':str(i),'class_name':c,'process_id':4580} for i,c in enumerate(['class HistoryInner','class Ui::ElasticScroll','class HistoryWidget','class MainWindow'])]
        self.child={'runtime_id':'child','class_name':'','process_id':4580}
    def test_original_and_actual_child_match(self):
        self.assertTrue(bound_history_path(self.chain,self.chain,4580))
        self.assertTrue(bound_history_path([self.child]+self.chain,self.chain,4580))
    def test_foreign_child_changed_chain_or_duplicate_root_rejected(self):
        self.assertFalse(bound_history_path([dict(self.child,process_id=999)]+self.chain,self.chain,4580))
        changed=copy.deepcopy(self.chain);changed[2]['runtime_id']='wrong'
        self.assertFalse(bound_history_path([self.child]+changed,self.chain,4580))
        self.assertFalse(bound_history_path(self.chain+self.chain,self.chain,4580))
    def test_excessive_or_unbound_child_prefix_rejected(self):
        self.assertFalse(bound_history_path([dict(self.child,runtime_id=str(100+i)) for i in range(7)]+self.chain,self.chain,4580))
        self.assertFalse(bound_history_path([dict(self.child,runtime_id='')]+self.chain,self.chain,4580))
