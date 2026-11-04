"""Checks for stage ordering and stale inference metadata."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_training import train
if train is not None:
    from src import model_registry


@unittest.skipIf(train is None, "Install requirements-train.txt for registry checks")
class ModelRegistryTest(unittest.TestCase):
    def test_stage_order_requires_staging_and_records_transitions(self):
        record = {"model_name": "example", "selected_version": "2", "active_version": None,
                  "stage": "None", "transition_history": []}
        current = SimpleNamespace(current_stage="None")
        client = Mock()
        client.get_model_version.return_value = current
        client.transition_model_version_stage.side_effect = lambda name, version, stage, **kwargs: setattr(current, "current_stage", stage)
        with self.assertRaises(ValueError):
            model_registry.advance_stage(deepcopy(record), "Production", client)
        client.transition_model_version_stage.assert_not_called()
        model_registry.advance_stage(record, "Staging", client)
        self.assertIsNone(record["active_version"])
        model_registry.advance_stage(record, "Production", client)
        self.assertEqual(record["active_version"], "2")
        self.assertEqual([(item["from"], item["to"]) for item in record["transition_history"]],
                         [("None", "Staging"), ("Staging", "Production")])
        client.transition_model_version_stage.assert_called_with("example", "2", "Production", archive_existing_versions=True)

    def test_inference_rejects_wrong_stage_multiple_versions_or_stale_metadata(self):
        record = {"model_name": "example", "selected_version": "2", "active_version": "2", "stage": "Production"}
        client = Mock()
        v1 = SimpleNamespace(version="1", current_stage="None")
        v2 = SimpleNamespace(version="2", current_stage="Production")
        client.search_model_versions.return_value = [v1, v2]
        self.assertEqual(model_registry.production_version(record, client), "2")
        for bad_record in ({**record, "stage": "Staging"}, {**record, "active_version": "1"},
                           {**record, "selected_version": "1"}):
            with self.assertRaises(ValueError):
                model_registry.production_version(bad_record, client)
        v1.current_stage = "Production"
        with self.assertRaises(ValueError):
            model_registry.production_version(record, client)


if __name__ == "__main__":
    unittest.main()
