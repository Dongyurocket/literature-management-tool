import unittest

from PySide6.QtCore import Qt

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
                entry_type="期刊文章",
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
        self.assertEqual(model.headerData(0, orientation=Qt.Orientation.Horizontal), "标题")

    def test_model_supports_custom_columns(self):
        model = LiteratureTableModel(column_keys=["title", "note_count", "rating"])
        rows = [
            LiteratureTableRow(
                literature_id=1,
                title="Custom Table",
                year="2026",
                entry_type="期刊文章",
                authors="Alice Example",
                subject="UI",
                reading_status="在读",
                attachment_count=2,
                note_count=3,
                rating=4,
            )
        ]

        model.set_rows(rows)

        self.assertEqual(model.columnCount(), 3)
        self.assertEqual(model.headerData(1, orientation=Qt.Orientation.Horizontal), "笔记数")
        self.assertEqual(model.data(model.index(0, 1)), "3")
        self.assertEqual(model.data(model.index(0, 2)), "4")


if __name__ == "__main__":
    unittest.main()
