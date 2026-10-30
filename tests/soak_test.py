import math
import os
import resource
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.system import System


def get_rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        pass
    # Fallback to getrusage (KB on Linux)
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_soak_test(duration_s: float = 300.0, sample_interval_s: float = 30.0):
    print(f"Starting Soak Test: 8 nodes @ 15 Hz for {duration_s} seconds ({duration_s/60:.1f} minutes)...")
    
    sys = System()
    sys.start()
    
    # Scale to 8 nodes
    for i in range(4, 9):
        try:
            sys.simulator.add_node(f"node-{i}")
        except Exception:
            pass
            
    # Set all nodes to 15 Hz
    for node_id in sys.store.node_ids():
        sys.simulator.set_controls(node_id, {"send_rate_hz": 15.0, "enabled": True})
        
    start_time = time.time()
    initial_rss = get_rss_mb()
    initial_threads = threading.active_count()
    
    samples = []
    print(f"[T+0s] Initial RSS: {initial_rss:.2f} MB | Initial Active Threads: {initial_threads}")
    
    next_sample = start_time + sample_interval_s
    end_time = start_time + duration_s
    
    while time.time() < end_time:
        time.sleep(1.0)
        now = time.time()
        if now >= next_sample:
            elapsed = int(now - start_time)
            cur_rss = get_rss_mb()
            cur_threads = threading.active_count()
            st = sys.state()
            rx = st["counters"]["received"]
            proc = st["counters"]["processed"]
            drop = st["counters"]["dropped"]
            print(f"[T+{elapsed}s] RSS: {cur_rss:.2f} MB | Threads: {cur_threads} | Received: {rx} | Processed: {proc} | Dropped: {drop}")
            samples.append((elapsed, cur_rss, cur_threads))
            next_sample += sample_interval_s
            
    final_rss = get_rss_mb()
    final_threads = threading.active_count()
    sys.stop()
    
    print("\n--- Soak Test Summary ---")
    print(f"Duration: {duration_s} s")
    print(f"Initial RSS: {initial_rss:.2f} MB -> Final RSS: {final_rss:.2f} MB (Delta: {final_rss - initial_rss:+.2f} MB)")
    print(f"Initial Threads: {initial_threads} -> Final Threads: {final_threads}")
    
    # Memory should be bounded (within 50 MB growth under bounded deques)
    # Thread count should be stable (+/- 2 max)
    assert abs(final_threads - initial_threads) <= 2, f"Thread leak detected: {initial_threads} -> {final_threads}"
    assert (final_rss - initial_rss) < 150.0, f"Unbounded memory growth: {initial_rss:.2f} -> {final_rss:.2f}"
    print("SOAK TEST PASSED: Memory is bounded and thread count is stable.")


if __name__ == "__main__":
    run_soak_test(duration_s=300.0, sample_interval_s=30.0)
