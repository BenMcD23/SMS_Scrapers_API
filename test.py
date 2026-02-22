import threading
import json
import time
from scripts.scraper_calls import event_317_scraper

def debug_scraper():
    # 1. Setup mock state to match your FastAPI global variables
    test_messages = []
    test_lock = threading.Lock()

    print("--- Starting Scraper Debug Session ---")

    # 2. Define a background thread to print messages as they arrive
    # This mimics your SSE (Server Sent Events) stream
    def message_monitor():
        last_idx = 0
        while True:
            with test_lock:
                # Check if there are new messages
                if last_idx < len(test_messages):
                    for i in range(last_idx, len(test_messages)):
                        print(f"[SCRAPER LOG]: {test_messages[i]}")
                    last_idx = len(test_messages)
                
                # Exit monitor if scraper sends a completion or error message
                # (Adjust strings based on what your actual utils functions push)
                if any("completed" in str(m).lower() or "error" in str(m).lower() for m in test_messages):
                    break
            
            time.sleep(0.5)

    monitor_thread = threading.Thread(target=message_monitor, daemon=True)
    monitor_thread.start()

    # 3. Execute the scraper
    try:
        # We pass the same objects FastAPI would pass
        event_317_scraper(test_messages, test_lock)
    except Exception as e:
        print(f"\n[CRITICAL ERROR]: {str(e)}")
    finally:
        print("\n--- Scraper Finished ---")
        # Give the monitor thread a moment to print the final messages
        time.sleep(1)
        
        print("\nFull Message History Log:")
        for msg in test_messages:
            print(msg)

if __name__ == "__main__":
    debug_scraper()