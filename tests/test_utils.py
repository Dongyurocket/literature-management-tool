import unittest

from literature_manager.utils import build_attachment_name, build_bib_entry, build_cite_key, sanitize_filename


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


if __name__ == '__main__':
    unittest.main()
