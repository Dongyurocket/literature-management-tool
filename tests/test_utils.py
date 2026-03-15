import unittest

from literature_manager.utils import (
    build_attachment_name,
    build_bib_entry,
    build_cite_key,
    build_csl_entry,
    build_gbt_reference,
    sanitize_filename,
)


class UtilityTests(unittest.TestCase):
    def test_sanitize_filename_removes_windows_invalid_chars(self):
        self.assertEqual(sanitize_filename('A<B>:C*D?E|F"G'), 'A B C D E F G')

    def test_build_attachment_name_uses_role_suffix(self):
        name = build_attachment_name(['张三', '李四'], 2024, '深度学习综述', 'translation', '.PDF')
        self.assertEqual(name, '张三等_2024_深度学习综述_翻译.pdf')

    def test_build_cite_key(self):
        self.assertEqual(build_cite_key(['Wang Lei'], 2024, 'Deep Learning Survey')[:12], 'WangLei2024D')

    def test_build_bib_entry_contains_required_fields(self):
        entry = build_bib_entry(
            {
                'entry_type': 'journal_article',
                'cite_key': 'Wang2024Deep',
                'authors': ['Wang Lei', 'Li Ming'],
                'title': 'Deep Learning Survey',
                'year': 2024,
                'publication_title': 'Journal of AI',
                'doi': '10.1000/test',
                'keywords': 'deep learning, survey',
            }
        )
        self.assertIn('@article{Wang2024Deep,', entry)
        self.assertIn('author = {Wang Lei and Li Ming},', entry)
        self.assertIn('journal = {Journal of AI},', entry)
        self.assertIn('doi = {10.1000/test},', entry)

    def test_build_bib_entry_and_csl_cover_extended_metadata_fields(self):
        entry = {
            'entry_type': 'book',
            'cite_key': 'Wang2024Book',
            'authors': ['Wang Lei'],
            'title': '知识组织导论',
            'subtitle': '方法与实践',
            'translated_title': 'Introduction to Knowledge Organization',
            'editors': 'Zhao Qian',
            'translators': 'Li Ming',
            'year': 2024,
            'month': '03',
            'day': '15',
            'publisher': '科学出版社',
            'publication_place': '北京',
            'edition': '2',
            'isbn': '9787300000000',
            'url': 'https://example.com/book',
            'access_date': '2026-03-15',
            'remarks': '馆藏版本',
            'keywords': '知识组织, 编目',
        }
        bib = build_bib_entry(entry)
        csl = build_csl_entry(entry)

        self.assertIn('subtitle = {方法与实践},', bib)
        self.assertIn('editor = {Zhao Qian},', bib)
        self.assertIn('translator = {Li Ming},', bib)
        self.assertIn('location = {北京},', bib)
        self.assertIn('edition = {2},', bib)
        self.assertIn('urldate = {2026-03-15},', bib)
        self.assertEqual(csl['publisher-place'], '北京')
        self.assertEqual(csl['edition'], '2')
        self.assertEqual(csl['accessed']['date-parts'], [[2026, 3, 15]])

    def test_build_gbt_reference_uses_type_specific_fields(self):
        reference = build_gbt_reference(
            {
                'entry_type': 'webpage',
                'authors': ['张三'],
                'title': '元数据规范说明',
                'subtitle': 'GB/T 7714-2015 字段对照',
                'publication_title': '项目文档',
                'publisher': '文献管理软件',
                'year': 2026,
                'month': '03',
                'day': '15',
                'access_date': '2026-03-16',
                'url': 'https://example.com/spec',
            }
        )
        self.assertIn('[EB/OL]', reference)
        self.assertIn('2026-03-15', reference)
        self.assertIn('[2026-03-16]', reference)
        self.assertIn('https://example.com/spec', reference)


if __name__ == '__main__':
    unittest.main()
