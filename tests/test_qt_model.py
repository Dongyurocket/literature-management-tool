import unittest

from literature_manager.models import LiteratureTableModel
from literature_manager.viewmodels import LiteratureTableRow


class LiteratureTableModelTests(unittest.TestCase):
    def test_model_batches_rows_for_lazy_loading(self):
        model = LiteratureTableModel()
        rows = [
            LiteratureTableRow(
                literature_id=index,
                title=f"Title {index}",
                year="2026",
                entry_type="Journal",
                authors="Alice Example",
                subject="UI",
                reading_status="在读",
                attachment_count=index % 3,
            )
            for index in range(250)
        ]

        model.set_rows(rows)

        self.assertEqual(model.rowCount(), model.BATCH_SIZE)
        self.assertTrue(model.canFetchMore())
        model.append_more_if_needed()
        self.assertGreater(model.rowCount(), model.BATCH_SIZE)
        self.assertEqual(model.total_count(), 250)


if __name__ == "__main__":
    unittest.main()
