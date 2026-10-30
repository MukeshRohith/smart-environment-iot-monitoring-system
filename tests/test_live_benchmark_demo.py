"""Live verification that both benchmarks and demo run against the real System and restore state."""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.system import System


def test_live_benchmarks_and_demo():
    print("Initializing real System...")
    sys_instance = System()
    sys_instance.start()
    
    try:
        initial_settings = sys_instance.get_settings()
        initial_strategy = initial_settings["strategy"]
        initial_mode = initial_settings["pipeline_mode"]
        
        # --- 1. Test Strategy Benchmark ---
        print("\n[Benchmark 1/2] Running 'strategy' benchmark with duration_s=1.0...")
        sys_instance.benchmark._sleep_fn = lambda s: time.sleep(0.05)
        sys_instance.benchmark.run("strategy", duration_s=1.0)
        
        # Wait for benchmark completion
        for _ in range(50):
            st = sys_instance.benchmark.status()
            if st["state"] in ("done", "error"):
                break
            time.sleep(0.1)
            
        st = sys_instance.benchmark.status()
        assert st["state"] == "done", f"Strategy benchmark failed: {st}"
        results = sys_instance.benchmark.results()
        assert "strategy" in results
        print("Strategy benchmark results:", results["strategy"]["improvement_pct"], "% improvement")
        
        # Verify settings restored
        curr_settings = sys_instance.get_settings()
        assert curr_settings["strategy"] == initial_strategy, f"Strategy not restored: {curr_settings}"
        print("Strategy benchmark settings restored successfully.")

        # --- 2. Test Pipeline Benchmark ---
        print("\n[Benchmark 2/2] Running 'pipeline' benchmark with duration_s=1.0...")
        sys_instance.benchmark.run("pipeline", duration_s=1.0)
        
        for _ in range(50):
            st = sys_instance.benchmark.status()
            if st["state"] in ("done", "error"):
                break
            time.sleep(0.1)
            
        st = sys_instance.benchmark.status()
        assert st["state"] == "done", f"Pipeline benchmark failed: {st}"
        results = sys_instance.benchmark.results()
        assert "pipeline" in results
        print("Pipeline benchmark results comparison present:", list(results["pipeline"]["comparison"].keys()))
        
        curr_settings = sys_instance.get_settings()
        assert curr_settings["pipeline_mode"] == initial_mode, f"Pipeline mode not restored: {curr_settings}"
        print("Pipeline benchmark settings restored successfully.")
        
        # Verify CSV export
        csv_text = sys_instance.benchmark.results_csv()
        assert "benchmark,variant,metric,value" in csv_text
        print("CSV export verified, length:", len(csv_text))
        
        # --- 3. Test Guided Demo End-to-End ---
        print("\n[Demo] Running guided demo end-to-end with fast step durations...")
        fast_durations = {i: 0.2 for i in range(1, 7)}
        sys_instance.demo._step_durations = fast_durations
        sys_instance.demo.start()
        
        # Wait for demo to complete all 6 steps
        for _ in range(60):
            d_st = sys_instance.demo.state()
            if not d_st["running"]:
                break
            time.sleep(0.1)
            
        d_st = sys_instance.demo.state()
        assert not d_st["running"], "Demo should have finished"
        print("Guided demo finished all steps. Verifying state restoration...")
        
        # Verify state restoration
        end_settings = sys_instance.get_settings()
        assert end_settings["strategy"] == initial_strategy
        assert end_settings["pipeline_mode"] == initial_mode
        print("Guided demo state restored successfully.")
        
        print("\nALL BENCHMARKS AND DEMO TESTS PASSED!")

    finally:
        sys_instance.stop()


if __name__ == "__main__":
    test_live_benchmarks_and_demo()
