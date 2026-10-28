import copy
import unittest

from benchlib.contracts import validate_config, validate_event


def config():
    return {"schema_version": "1.0", "campaign": {"name": "bench", "currency": "USD", "region": "us-ashburn-1", "compartment_id": "ocid1.compartment.oc1..example", "budget_limit": 100, "access_method": "resource_principal"},
            "artifact": {"release": "3.0.1", "image": "registry.example/fluxnova@sha256:" + "a" * 64, "source_commit": "b" * 40},
            "runtime": {"cpu": 4, "memory_gib": 8, "heap_gib": 4, "base_url": "http://127.0.0.1:8080", "auth_username_env": "FNV_USER", "auth_password_env": "FNV_PASSWORD"},
            "workload": {"scenario": "minimal", "rate": 1, "duration_s": 10, "warmup_s": 0, "drain_s": 5, "deadline_s": 1, "seed": 42, "payload_bytes": 1024},
            "run": {"id": "test", "mode": "fixture"}}


class ConfigTests(unittest.TestCase):
    def test_late_poll_interval_is_bounded_and_never_faster(self):
        for interval in (100, 500, 1000):
            value = config()
            value['workload']['late_poll_interval_ms'] = interval
            self.assertEqual(validate_config(value), [])
        for interval in (0, -1, True, float('nan'), '1000', 50, 6000):
            value = config()
            value['workload']['late_poll_interval_ms'] = interval
            self.assertTrue(any('late_poll_interval_ms' in x for x in validate_config(value)))

    def test_warmup_guard_requires_boolean(self):
        for setting in (True, False):
            value = config()
            value['workload']['abort_on_warmup_unknown'] = setting
            self.assertEqual(validate_config(value), [])
        for setting in ('false', 0, 1, None):
            value = config()
            value['workload']['abort_on_warmup_unknown'] = setting
            self.assertIn('workload.abort_on_warmup_unknown must be a boolean', validate_config(value))

    def test_cloud_configuration_requires_immutable_artifacts_and_budget(self):
        valid = config()
        self.assertEqual(validate_config(valid), [])
        invalid = copy.deepcopy(valid)
        invalid["artifact"]["image"] = "fluxnova:latest"
        invalid["campaign"]["budget_limit"] = 0
        errors = validate_config(invalid)
        self.assertTrue(any("image" in error for error in errors))
        self.assertTrue(any("budget_limit" in error for error in errors))

    def test_local_validation_does_not_require_cloud_identifiers(self):
        value = config()
        value["campaign"].pop("compartment_id")
        value["artifact"].pop("source_commit")
        value["artifact"]["image"] = "fixture"
        self.assertEqual(validate_config(value, require_cloud=False), [])

    def test_declared_financial_scenario_ids_are_valid(self):
        for scenario in ("minimal", "variables", "async", "kyc", "risk", "settlement"):
            value = config()
            value["workload"]["scenario"] = scenario
            self.assertEqual(validate_config(value), [])

    def test_nonfinite_values_and_booleans_are_not_valid_numbers(self):
        for invalid in (float("nan"), float("inf"), True, -1):
            value = config()
            value["workload"]["rate"] = invalid
            self.assertTrue(validate_config(value, require_cloud=False))

    def test_heap_and_credentials_guardrails(self):
        value = config()
        value["runtime"]["heap_gib"] = 8
        value["runtime"]["base_url"] = "https://person:secret@example.com"
        value["runtime"]["auth_password"] = "never-echo-this"
        errors = validate_config(value)
        self.assertTrue(any("non-heap" in error for error in errors))
        self.assertTrue(any("credentials" in error for error in errors))
        self.assertNotIn("never-echo-this", " ".join(errors))

    def test_unknown_oci_extensions_are_preserved(self):
        value = config()
        value["oci"] = {"terraform_settings": {"shape": "test"}}
        self.assertEqual(validate_config(value), [])

    def test_oci_auth_modes_match_supported_controller_identities(self):
        for mode in ("resource_principal", "instance_principal", "config_file"):
            value = config()
            value["campaign"]["access_method"] = mode
            self.assertEqual(validate_config(value), [])

    def test_bootstrap_accepts_verified_build_inputs_but_run_requires_image_digest(self):
        value = config()
        value["artifact"].update({"image": "", "image_source": "maven-distribution", "distribution_url": "https://repo.maven.apache.org/maven2/example.zip", "distribution_sha256": "c" * 64, "java_image": "eclipse-temurin@sha256:" + "d" * 64})
        self.assertEqual(validate_config(value, require_built_image=False), [])
        self.assertTrue(any("artifact.image" in error for error in validate_config(value)))
        value["artifact"]["distribution_sha256"] = ""
        self.assertTrue(any("distribution_sha256" in error for error in validate_config(value, require_built_image=False)))

    def test_bootstrap_does_not_accept_a_floating_image_without_build_recipe(self):
        value = config()
        value["artifact"]["image"] = "fluxnova:latest"
        self.assertTrue(any("artifact.image" in error for error in validate_config(value, require_built_image=False)))

    def test_event_must_have_identity_and_finite_timing(self):
        self.assertEqual(validate_event({"event": "scheduled", "run_id": "test", "logical_request_id": "r1", "scenario": "minimal", "elapsed_s": 0}), [])
        self.assertTrue(validate_event({"event": "completed", "status": "success"}))
        errors = validate_event({"event": "scheduled", "run_id": "test", "logical_request_id": "r1", "scenario": "minimal", "elapsed_s": float("inf")})
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
