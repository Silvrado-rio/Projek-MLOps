"""Training checks run separately from the standard-library ingestion tests."""

import unittest

try:
    import train
except ModuleNotFoundError as error:
    if error.name not in {"mlflow", "pandas", "numpy", "matplotlib", "sklearn", "dvc"}:
        raise
    train = None


@unittest.skipIf(train is None, "Install requirements-train.txt for training checks")
class TrainingTest(unittest.TestCase):
    def test_split_purges_unavailable_future_labels(self):
        pd = train.pd
        frame = pd.DataFrame({
            "snapshot_date": pd.to_datetime(["2026-10-02", "2026-10-02", "2026-10-03", "2026-10-09", "2026-10-09"]),
            train.TARGET: [0.1, 0.2, 0.3, 0.4, 0.5],
        })
        training, validation, purged = train.temporal_split(frame)
        self.assertEqual((len(training), len(validation), purged), (2, 2, 1))
        self.assertTrue(((training.snapshot_date + pd.Timedelta(days=7)) <= validation.snapshot_date.min()).all())
        with self.assertRaises(ValueError):
            train.temporal_split(frame.iloc[2:])

    def test_ndcg_groups_dates_and_clips_negative_relevance(self):
        pd = train.pd
        frame = pd.DataFrame({
            "snapshot_date": ["2026-10-09"] * 3 + ["2026-10-10"] * 3,
            train.TARGET: [-0.1, 0.2, 0.3, -0.2, 0.4, 0.6],
        })
        score = train.scores(frame, frame[train.TARGET].to_numpy())
        self.assertEqual(score["mae"], 0)
        self.assertAlmostEqual(score["ndcg_at_10"], 1)


if __name__ == "__main__":
    unittest.main()
