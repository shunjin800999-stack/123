import unittest
from visual_members import analyze_member_visual, numeric_label


class VisualMemberTests(unittest.TestCase):
    def test_portuguese_member_titles_are_supported_without_accepting_other_dialogs(self):
        for title in ('Adicionar Membros','Adicionar participantes'):
            r=self.report();r['ocr']['text']=title+' 1 Adicionar Cancelar'
            r['ocr']['words']=[self.word('1',675,320)]
            a=analyze_member_visual(r,'1')
            self.assertTrue(a['usable']);self.assertEqual(a['list_matches'],1)
        for title in ('Adicionar Contato','Adicionar','Cancelar','Membros'):
            r=self.report();r['ocr']['text']=title
            self.assertFalse(analyze_member_visual(r,'1')['usable'])

    def report(self):
        # Coordinates model the actual list/header/search relationship, at 2x capture scale.
        return {'ok':True,'read_only':True,'scope':'member_visual','scale':2,
                'capture':{'left':600,'top':200,'width':360,'height':580},
                'regions':{'list':{'left':600,'top':300,'width':360,'height':5000},
                           'viewport':{'left':600,'top':300,'width':360,'height':430},
                           'header':{'left':600,'top':250,'width':360,'height':50},
                           'search':{'left':690,'top':260,'width':260,'height':25}},
                'ocr':{'available':True,'text':'Add Members 001 Add Cancel','words':[]}}

    def word(self,text,x,y):
        return {'text':text,'left':(x-600)*2,'top':(y-200)*2,'width':20,'height':20}

    def test_search_value_is_not_a_selected_chip(self):
        report=self.report();report['ocr']['words']=[self.word('001',710,265)]
        result=analyze_member_visual(report,'001')
        self.assertTrue(result['usable']);self.assertEqual(result['chip_matches'],0)
        self.assertEqual(result['selected_numbers'],[])

    def test_distinguishes_list_result_and_chip_from_search_field(self):
        report=self.report();report['ocr']['words']=[self.word('001',645,265),self.word('001',675,320),self.word('001',710,265)]
        result=analyze_member_visual(report,'001')
        self.assertEqual((result['list_matches'],result['chip_matches']),(1,1))
        self.assertEqual(result['selected_numbers'],['001'])

    def test_offscreen_rows_are_excluded_by_viewport_not_long_list_content(self):
        report=self.report();report['ocr']['words']=[self.word('001',675,750)]
        self.assertEqual(analyze_member_visual(report,'001')['list_matches'],0)

    def test_does_not_guess_letters_as_digits_or_collapse_duplicate_results(self):
        report=self.report();report['ocr']['words']=[self.word('OO1',675,320),self.word('001',675,350),self.word('001',675,410)]
        self.assertEqual(analyze_member_visual(report,'001')['list_matches'],2)

    def test_plain_name_is_exact_and_does_not_match_legacy_or_longer_name(self):
        report=self.report()
        report['ocr']['words']=[self.word('1',645,265),self.word('1',675,320),
                               self.word('1',710,265),self.word('001',675,350),
                               self.word('11',675,390),self.word('I',675,420)]
        plain=analyze_member_visual(report,1)
        self.assertEqual(plain['number'],'1')
        self.assertEqual((plain['list_matches'],plain['chip_matches']),(1,1))
        legacy=analyze_member_visual(report,'001')
        self.assertEqual((legacy['list_matches'],legacy['chip_matches']),(1,0))

    def test_numeric_label_preserves_explicit_zeros_and_rejects_invalid_names(self):
        self.assertEqual(numeric_label('001'),'001')
        self.assertEqual(numeric_label(1),'1')
        self.assertEqual(numeric_label(0),'0')
        for name in ('1.0','-1','1 2','1000000','I',True):
            with self.assertRaises(ValueError):numeric_label(name)

    def test_status_dates_do_not_match_contact_names_but_name_line_still_matches(self):
        report=self.report()
        report['ocr']['words']=[self.word('1',645,265),self.word('1',675,320),
            self.word('last',675,350),self.word('seen',700,353),
            self.word('2026',725,352),self.word('/',750,353),
            self.word('7',775,353),self.word('/',800,353),
            self.word('1',825,353),self.word('1',845,353),
            self.word('2026',675,400),self.word('/',700,400),
            self.word('1',725,400),self.word('/',750,400),self.word('1',775,400)]
        result=analyze_member_visual(report,'1')
        self.assertEqual((result['list_matches'],result['chip_matches']),(1,1))
        self.assertEqual(result['ignored_list_fragments'],4)
        self.assertEqual(analyze_member_visual(report,'2026')['list_matches'],0)

    def test_split_numeric_line_is_not_guessed_or_counted_as_two_people(self):
        report=self.report()
        report['ocr']['words']=[self.word('1',675,320),self.word('1',700,320)]
        self.assertEqual(analyze_member_visual(report,'1')['list_matches'],0)
        self.assertEqual(analyze_member_visual(report,'11')['list_matches'],0)

    def test_observed_split_title_allows_exact_unselected_contact(self):
        report=self.report()
        report['ocr']['text']='Ad d Members 6 / 200000 1 last seen 2026 / 9 / 28 Cancel Ad d'
        report['ocr']['words']=[self.word('1',675,320),self.word('1',710,265)]
        result=analyze_member_visual(report,'1')
        self.assertTrue(result['usable'])
        self.assertEqual((result['list_matches'],result['chip_matches']),(1,0))
        self.assertEqual(result['selected_numbers'],[])

    def test_split_title_fix_does_not_guess_wrong_title_or_numeric_label(self):
        report=self.report();report['ocr']['text']='Ad b Members'
        report['ocr']['words']=[self.word('1',675,320)]
        self.assertFalse(analyze_member_visual(report,'1')['usable'])
        report['ocr']['text']='Ad d Members'
        report['ocr']['words']=[self.word('I',675,320),self.word('001',675,350)]
        self.assertEqual(analyze_member_visual(report,'1')['list_matches'],0)

    def test_unavailable_ocr_wrong_capture_and_invalid_geometry_are_not_usable(self):
        for change in ('unavailable','missing_title','bad_geometry'):
            report=self.report()
            if change=='unavailable':report['ocr']['available']=False
            elif change=='missing_title':report['ocr']['text']='001'
            else:report['regions']['search']['width']=float('inf')
            self.assertFalse(analyze_member_visual(report,'001')['usable'],change)


if __name__=='__main__':unittest.main(verbosity=2)
